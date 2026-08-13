import json
from dataclasses import replace
from pathlib import Path

import pytest

from taiyi.benchmark.comparative import (
    COMPARATIVE_PROMPT,
    FAILURE_KIND_FIRST_TOKEN_TIMEOUT,
    FAILURE_KIND_HARNESS_STARTUP_TIMEOUT,
    FAILURE_KIND_STREAM_IDLE_TIMEOUT,
    FAILURE_PHASE_FIRST_TOKEN,
    FAILURE_PHASE_PROCESS_START,
    FAILURE_PHASE_STREAM_IDLE,
    _classify_controlled_failure,
    _read_json_object,
    _requests_match_tool_surface,
    _write_openclaw_config,
    comparative_manifest,
    run_taiyi_cell,
)
from taiyi.benchmark.model_fixture import (
    CONTROLLED_MODEL_ID,
    FAULT_FIRST_TOKEN_STALL,
    FAULT_STREAM_IDLE_STALL,
    ControlledModelServer,
)
from taiyi.benchmark.runner import (
    build_comparative_fault_report,
    build_comparative_report,
    run_comparative_smoke,
)
from taiyi.benchmark.schema import (
    COMPARATIVE_MANIFEST_SCHEMA,
    COMPARATIVE_RECEIPT_SCHEMA,
    COMPARATIVE_REPORT_SCHEMA,
    MeasurementStatus,
    verify_artifact,
)
from taiyi.llm import LLMErrorKind, LLMMessage, LLMRequestError, OpenAICompatProvider


def test_controlled_model_endpoint_has_a_fixed_two_turn_policy():
    with ControlledModelServer() as server:
        provider = OpenAICompatProvider(
            server.base_url,
            model=CONTROLLED_MODEL_ID,
            api_key="non-secret",
        )
        first = provider.complete(
            [LLMMessage("user", COMPARATIVE_PROMPT)],
            tools=["file:write"],
        )
        final = provider.complete([
            LLMMessage("user", COMPARATIVE_PROMPT),
            LLMMessage("assistant", "tool_call: file:write result.txt verified"),
            LLMMessage("user", "[tool result] file:write\nfixed content written"),
        ])

        assert first.tool_calls[0].tool == "file:write"
        assert first.tool_calls[0].args == ["result.txt", "verified"]
        assert final.text == "delivery prepared for independent verification"
        assert server.request_count == 2


def test_comparability_signature_does_not_depend_on_ephemeral_port():
    with ControlledModelServer() as first, ControlledModelServer() as second:
        one = comparative_manifest(first.policy_digest)
        two = comparative_manifest(second.policy_digest)

    assert one == two
    assert "127.0.0.1" not in json.dumps(one)
    assert one["ranking_eligible"] is False
    baseline_path = (
        Path(__file__).parents[1]
        / "research/benchmark/results/comparative-smoke-v2/manifest.json"
    )
    baseline = verify_artifact(
        json.loads(baseline_path.read_text()),
        schema_version=COMPARATIVE_MANIFEST_SCHEMA,
    )
    assert one == baseline


def test_openclaw_config_pins_only_the_controlled_provider(tmp_path):
    path = tmp_path / "openclaw.json"
    _write_openclaw_config(path, "http://127.0.0.1:43210/v1")
    value = json.loads(path.read_text())

    model_ref = f"taiyi-benchmark/{CONTROLLED_MODEL_ID}"
    provider = value["models"]["providers"]["taiyi-benchmark"]
    assert value["agents"]["defaults"]["model"]["primary"] == model_ref
    assert provider["baseUrl"] == "http://127.0.0.1:43210/v1"
    assert provider["api"] == "openai-completions"
    assert provider["models"][0]["id"] == CONTROLLED_MODEL_ID
    assert value["tools"]["allow"] == ["read", "write"]
    assert "apply_patch" in value["tools"]["deny"]
    assert value["tools"]["toolSearch"] is False
    assert value["tools"]["codeMode"] is False
    assert value["tools"]["fs"]["workspaceOnly"] is True
    assert "api.openai.com" not in path.read_text()


def test_openclaw_envelope_parser_rejects_non_object_json(tmp_path):
    path = tmp_path / "stdout.log"
    path.write_text('{"ok":true,"status":"ok"}')
    value, error = _read_json_object(path)
    assert value == {"ok": True, "status": "ok"}
    assert error is None

    path.write_text("[]")
    value, error = _read_json_object(path)
    assert value is None
    assert error == "invalid JSON response: expected an object"


def test_every_model_request_must_have_the_frozen_tool_surface():
    expected = ["read", "write"]

    assert _requests_match_tool_surface([
        {"tool_names": ["write", "read"]},
        {"tool_names": ["read", "write"]},
    ], expected)
    assert not _requests_match_tool_surface([
        {"tool_names": ["read", "write"]},
        {"tool_names": ["read"]},
    ], expected)
    assert not _requests_match_tool_surface([], expected)


@pytest.mark.parametrize(
    ("fault", "expected_kind", "response_started"),
    [
        (FAULT_FIRST_TOKEN_STALL, LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT, False),
        (FAULT_STREAM_IDLE_STALL, LLMErrorKind.LLM_STREAM_IDLE_TIMEOUT, True),
    ],
)
def test_controlled_fault_endpoint_records_the_timeout_boundary(
    fault,
    expected_kind,
    response_started,
):
    with ControlledModelServer(
        fault=fault,
        fault_delay_seconds=0.2,
    ) as server:
        provider = OpenAICompatProvider(
            server.base_url,
            model=CONTROLLED_MODEL_ID,
            connect_timeout=0.1,
            first_token_timeout=0.05,
            stream_idle_timeout=0.05,
            hard_timeout=0.5,
        )
        with pytest.raises(LLMRequestError) as failure:
            provider.complete([LLMMessage("user", COMPARATIVE_PROMPT)])
        requests = server.request_summaries()

    assert failure.value.kind is expected_kind
    assert len(requests) == 1
    assert requests[0]["response_started"] is response_started
    assert requests[0]["response_completed"] is False


@pytest.mark.parametrize(
    ("fault", "phase", "kind", "observed_request"),
    [
        (
            FAULT_FIRST_TOKEN_STALL,
            FAILURE_PHASE_FIRST_TOKEN,
            FAILURE_KIND_FIRST_TOKEN_TIMEOUT,
            {"response_started": False, "response_completed": False},
        ),
        (
            FAULT_STREAM_IDLE_STALL,
            FAILURE_PHASE_STREAM_IDLE,
            FAILURE_KIND_STREAM_IDLE_TIMEOUT,
            {"response_started": True, "response_completed": False},
        ),
    ],
)
def test_controlled_failure_classification_requires_delivery_evidence(
    fault,
    phase,
    kind,
    observed_request,
):
    manifest = comparative_manifest(
        "sha256:model-policy",
        injected_fault=fault,
        expected_failure_phase=phase,
        expected_failure_kind=kind,
    )

    assert _classify_controlled_failure(
        manifest,
        [observed_request],
        task_passed=False,
        claimed_complete=False,
        timed_out=True,
    ) == (phase, kind)
    assert _classify_controlled_failure(
        manifest,
        [observed_request],
        task_passed=True,
        claimed_complete=True,
        timed_out=False,
    ) == (None, None)


def test_no_model_request_is_a_harness_startup_timeout_not_an_llm_timeout():
    manifest = comparative_manifest(
        "sha256:model-policy",
        injected_fault=FAULT_FIRST_TOKEN_STALL,
        expected_failure_phase=FAILURE_PHASE_FIRST_TOKEN,
        expected_failure_kind=FAILURE_KIND_FIRST_TOKEN_TIMEOUT,
    )

    assert _classify_controlled_failure(
        manifest,
        [],
        task_passed=False,
        claimed_complete=False,
        timed_out=True,
    ) == (FAILURE_PHASE_PROCESS_START, FAILURE_KIND_HARNESS_STARTUP_TIMEOUT)


def test_taiyi_comparative_cell_uses_live_http_and_independent_acceptance(tmp_path):
    with ControlledModelServer() as server:
        manifest = comparative_manifest(server.policy_digest)
        receipt = run_taiyi_cell(
            server=server,
            manifest=manifest,
            run_root=tmp_path,
        )

    assert receipt.measurement_status is MeasurementStatus.MEASURED
    assert receipt.comparable is True
    assert receipt.task_passed is True
    assert receipt.claimed_complete is True
    assert receipt.false_completion is False
    assert receipt.budget_passed is True
    assert receipt.model_requests == 2
    assert receipt.tool_calls == 1
    assert receipt.evidence["effect_statuses"] == ["CONFIRMED_APPLIED"]
    assert receipt.evidence["model_visible_tools"] == ["file:read", "file:write"]
    assert receipt.evidence["executor_allowed_tools"] == [
        "file:read",
        "file:write",
    ]
    assert receipt.evidence["repository_index_enabled"] is False
    assert receipt.evidence["tool_surface_matches"] is True
    assert "failure_phase" not in receipt.to_dict()
    assert "failure_kind" not in receipt.to_dict()
    assert receipt.evidence["isolation"] == {
        "separate_process": True,
        "isolated_home": True,
        "minimal_environment": True,
        "tool_workspace_confined": True,
        "kernel_sandbox": False,
    }


def test_taiyi_fault_cell_is_measured_as_a_safe_attributed_failure(tmp_path):
    with ControlledModelServer(
        fault=FAULT_FIRST_TOKEN_STALL,
        fault_delay_seconds=0.5,
    ) as server:
        manifest = comparative_manifest(
            server.policy_digest,
            case_id="model_first_token_timeout",
            injected_fault=FAULT_FIRST_TOKEN_STALL,
            expected_failure_phase=FAILURE_PHASE_FIRST_TOKEN,
            expected_failure_kind=FAILURE_KIND_FIRST_TOKEN_TIMEOUT,
            wall_timeout_seconds=2.0,
            phase_timeout_seconds=0.05,
            max_model_requests=3,
        )
        receipt = run_taiyi_cell(
            server=server,
            manifest=manifest,
            run_root=tmp_path,
        )

    assert receipt.measurement_status is MeasurementStatus.MEASURED
    assert receipt.comparable is True
    assert receipt.failure_phase == FAILURE_PHASE_FIRST_TOKEN
    assert receipt.failure_kind == FAILURE_KIND_FIRST_TOKEN_TIMEOUT
    assert receipt.claimed_complete is False
    assert receipt.task_passed is False
    assert receipt.false_completion is False
    assert receipt.budget_passed is True
    assert receipt.evidence["attribution_matches"] is True
    assert receipt.to_dict()["failure_phase"] == FAILURE_PHASE_FIRST_TOKEN
    assert receipt.to_dict()["failure_kind"] == FAILURE_KIND_FIRST_TOKEN_TIMEOUT

    report = build_comparative_fault_report(
        [receipt],
        {manifest["case_id"]: manifest},
    )
    assert report["complete_matrix"] is False
    assert report["all_comparable_failures_attributed"] is True
    assert report["all_attributed_failures_safe"] is True
    assert report["startup_latency_by_harness"]["taiyi"]["samples"] == 1


def test_comparative_aggregate_requires_delivery_and_completion(tmp_path):
    with ControlledModelServer() as server:
        manifest = comparative_manifest(server.policy_digest)
        passed = run_taiyi_cell(
            server=server,
            manifest=manifest,
            run_root=tmp_path,
        )
    report = build_comparative_report(
        [replace(passed, claimed_complete=False)],
        manifest,
    )

    assert report["comparable_cell_count"] == 1
    assert report["all_comparable_cells_passed"] is False
    assert report["ranking_eligible"] is False
    assert build_comparative_report(
        [replace(passed, budget_passed=False)], manifest
    )["all_comparable_cells_passed"] is False
    mismatched = build_comparative_report(
        [replace(passed, comparability_signature="sha256:different")], manifest
    )
    assert mismatched["signatures_match"] is False
    assert mismatched["all_comparable_cells_passed"] is False

    false_claim = replace(
        passed,
        comparable=False,
        task_passed=False,
        claimed_complete=True,
        false_completion=True,
    )
    assert build_comparative_report([false_claim], manifest)["false_completions"] == 1


def test_comparative_runner_emits_verified_cells_and_explicit_blockers(tmp_path):
    report = run_comparative_smoke(
        tmp_path,
        pi_executable=tmp_path / "missing-pi",
        openclaw_executable=tmp_path / "missing-openclaw",
    )
    cells = {item["harness_id"]: item for item in report["cells"]}

    assert report["cell_count"] == 4
    assert report["comparable_cell_count"] == 1
    assert report["all_comparable_cells_passed"] is True
    assert cells["taiyi"]["task_passed"] is True
    assert cells["pi"]["measurement_status"] == "NOT_COMPARABLE"
    assert cells["pi"]["blockers"]
    assert cells["openclaw"]["measurement_status"] == "NOT_COMPARABLE"
    assert cells["openclaw"]["blockers"]
    assert cells["zcode"]["blockers"]

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert verify_artifact(
        manifest, schema_version=COMPARATIVE_MANIFEST_SCHEMA
    )["comparability_signature"] == report["comparability_signature"]
    aggregate = json.loads((tmp_path / "report.json").read_text())
    assert verify_artifact(aggregate, schema_version=COMPARATIVE_REPORT_SCHEMA) == report
    for path in (tmp_path / "runs").glob("*.json"):
        verify_artifact(json.loads(path.read_text()), schema_version=COMPARATIVE_RECEIPT_SCHEMA)
