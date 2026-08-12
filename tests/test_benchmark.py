import json
import sys

import pytest

from taiyi.benchmark.adapters import CommandHarnessAdapter
from taiyi.benchmark.runner import run_protocol_matrix, write_probe_report
from taiyi.benchmark.schema import (
    BENCHMARK_SCHEMA,
    REPORT_SCHEMA,
    MeasurementStatus,
    artifact_envelope,
    verify_artifact,
)
from taiyi.runtime import FailureKind, TaskState


def _adapter(argv):
    return CommandHarnessAdapter(
        harness_id="fixture",
        argv=tuple(argv),
        version_argv=(sys.executable, "--version"),
        model_id="fixed-model",
        isolated_workspace=True,
    )


def test_benchmark_artifacts_detect_tampering():
    artifact = artifact_envelope(BENCHMARK_SCHEMA, {"result": "PASS"})
    assert verify_artifact(artifact, schema_version=BENCHMARK_SCHEMA) == {"result": "PASS"}

    artifact["payload"]["result"] = "FAIL"
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_artifact(artifact, schema_version=BENCHMARK_SCHEMA)


def test_command_adapter_does_not_treat_exit_zero_as_completion(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("fixed task", encoding="utf-8")
    run = _adapter((sys.executable, "-c", "pass")).run(
        workspace=workspace,
        prompt_file=prompt,
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=2,
    )

    assert run.exit_code == 0
    assert run.status is MeasurementStatus.ERROR
    assert run.reported_state == "UNKNOWN"
    assert "normalized result" in run.error


def test_command_adapter_accepts_only_an_explicit_normalized_receipt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("fixed task", encoding="utf-8")
    code = (
        'import json,sys; '
        'json.dump(dict(reported_state="COMPLETED", task_passed=True), '
        'open(sys.argv[1], "w", encoding="utf-8"))'
    )
    run = _adapter((sys.executable, "-c", code, "{result_file}")).run(
        workspace=workspace,
        prompt_file=prompt,
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=2,
    )

    assert run.status is MeasurementStatus.MEASURED
    assert run.reported_state == "COMPLETED"
    assert run.result["task_passed"] is True


def test_command_adapter_timeout_overrides_an_early_completion_receipt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("fixed task", encoding="utf-8")
    code = (
        'import json,sys,time; '
        'json.dump(dict(reported_state="COMPLETED"), '
        'open(sys.argv[1], "w", encoding="utf-8")); '
        'time.sleep(60)'
    )
    run = _adapter((sys.executable, "-c", code, "{result_file}")).run(
        workspace=workspace,
        prompt_file=prompt,
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=0.2,
    )

    assert run.timed_out is True
    assert run.status is MeasurementStatus.ERROR
    assert run.reported_state == "COMPLETED"
    assert run.error == "outer benchmark timeout"


def test_command_adapter_rejects_a_stale_or_incomplete_receipt(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("fixed task", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "normalized-result.json").write_text(
        '{"reported_state": "COMPLETED"}', encoding="utf-8"
    )
    code = (
        'import json,sys; '
        'json.dump(dict(task_passed=True), open(sys.argv[1], "w", encoding="utf-8"))'
    )
    run = _adapter((sys.executable, "-c", code, "{result_file}")).run(
        workspace=workspace,
        prompt_file=prompt,
        artifact_dir=artifacts,
        timeout_seconds=2,
    )

    assert run.status is MeasurementStatus.ERROR
    assert run.reported_state == "UNKNOWN"
    assert run.error == "normalized result has no non-empty reported_state"


def test_probe_report_records_capability_cells_without_scores(tmp_path):
    payload = write_probe_report(tmp_path)
    assert {item["harness_id"] for item in payload["probes"]} == {
        "pi", "openclaw", "zcode"
    }
    assert all(
        item["status"] in {"MEASURED", "UNAVAILABLE", "NOT_COMPARABLE", "ERROR"}
        for item in payload["probes"]
    )
    artifact = json.loads((tmp_path / "external-harness-probes.json").read_text())
    assert verify_artifact(artifact, schema_version=BENCHMARK_SCHEMA) == payload


def test_taiyi_protocol_matrix_exercises_mode_budgets_and_fail_closed_states(tmp_path):
    report = run_protocol_matrix(tmp_path)

    assert report["run_count"] == 18
    assert report["measured_run_count"] == 18
    assert report["all_protocol_passed"] is True
    assert report["false_completions"] == 0
    assert report["duplicate_effects"] == 0
    assert report["by_mode"]["quality"]["task_pass_rate"] == pytest.approx(5 / 6)
    assert report["by_mode"]["balanced"]["task_pass_rate"] == pytest.approx(4 / 6)
    assert report["by_mode"]["efficiency"]["task_pass_rate"] == pytest.approx(3 / 6)

    runs = {
        (item["case_id"], item["operating_mode"]): item
        for item in report["runs"]
    }
    for mode in ("quality", "balanced", "efficiency"):
        ambiguous = runs[("effect_ambiguous", mode)]
        assert ambiguous["reported_state"] == TaskState.NEEDS_INPUT.value
        assert ambiguous["failure_kind"] == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
        assert ambiguous["claimed_complete"] is False
        assert ambiguous["human_handoffs"] == 1

        lost_receipt = runs[("effect_applied_receipt_timeout", mode)]
        assert lost_receipt["reported_state"] == TaskState.COMPLETED.value
        assert lost_receipt["connector_attempts"] == 1
        assert lost_receipt["applied_effects"] == 1
        assert lost_receipt["duplicate_effects"] == 0

        repository = runs[("large_repository_context", mode)]
        assert repository["evidence"]["initial_workspace"]["file_count"] == 1201
        assert repository["evidence"]["repository_context_seen_by_provider"] is True

    assert runs[("effect_two_preapply_failures", "quality")]["connector_attempts"] == 3
    assert runs[("effect_two_preapply_failures", "quality")]["task_passed"] is True
    for mode in ("balanced", "efficiency"):
        exhausted = runs[("effect_two_preapply_failures", mode)]
        assert exhausted["connector_attempts"] == 2
        assert exhausted["reported_state"] == TaskState.FAILED.value
        assert exhausted["task_passed"] is False

    report_artifact = json.loads((tmp_path / "report.json").read_text())
    assert verify_artifact(report_artifact, schema_version=REPORT_SCHEMA) == report
    run_artifacts = list((tmp_path / "runs").glob("*.json"))
    assert len(run_artifacts) == 18
    for path in run_artifacts:
        artifact = json.loads(path.read_text())
        verify_artifact(artifact, schema_version="taiyi.harness-run-receipt/v1")
