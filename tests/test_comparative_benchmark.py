import json
from dataclasses import replace

from taiyi.benchmark.comparative import (
    COMPARATIVE_PROMPT,
    comparative_manifest,
    run_taiyi_cell,
)
from taiyi.benchmark.model_fixture import CONTROLLED_MODEL_ID, ControlledModelServer
from taiyi.benchmark.runner import build_comparative_report, run_comparative_smoke
from taiyi.benchmark.schema import (
    COMPARATIVE_MANIFEST_SCHEMA,
    COMPARATIVE_RECEIPT_SCHEMA,
    COMPARATIVE_REPORT_SCHEMA,
    MeasurementStatus,
    verify_artifact,
)
from taiyi.llm import LLMMessage, OpenAICompatProvider


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
    assert receipt.evidence["isolation"] == {
        "separate_process": True,
        "isolated_home": True,
        "minimal_environment": True,
        "tool_workspace_confined": True,
        "kernel_sandbox": False,
    }


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
    report = run_comparative_smoke(tmp_path, pi_executable=tmp_path / "missing-pi")
    cells = {item["harness_id"]: item for item in report["cells"]}

    assert report["cell_count"] == 4
    assert report["comparable_cell_count"] == 1
    assert report["all_comparable_cells_passed"] is True
    assert cells["taiyi"]["task_passed"] is True
    assert cells["pi"]["measurement_status"] == "NOT_COMPARABLE"
    assert cells["pi"]["blockers"]
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
