"""Signed production tool-process fault matrix."""

from __future__ import annotations

import json

from taiyi.benchmark.schema import (
    TOOL_FAULT_MANIFEST_SCHEMA,
    TOOL_FAULT_RECEIPT_SCHEMA,
    TOOL_FAULT_REPORT_SCHEMA,
    verify_artifact,
)
from taiyi.benchmark.tool_faults import run_tool_fault_matrix, tool_fault_manifest


def test_tool_fault_manifest_freezes_shared_reliability_floor():
    manifest = tool_fault_manifest()

    assert manifest["measurement_scope"] == "taiyi_production_tool_process_reliability"
    assert manifest["operating_modes"] == ["quality", "balanced", "efficiency"]
    assert len(manifest["cases"]) == 5
    assert "bounded model and disk output" in manifest["reliability_floor"]
    assert manifest["contract_digest"].startswith("sha256:")


def test_tool_fault_matrix_is_complete_signed_and_has_no_false_completion(tmp_path):
    report = run_tool_fault_matrix(tmp_path)

    assert report["case_count"] == 5
    assert report["run_count"] == 15
    assert report["protocol_passed_count"] == 15
    assert report["all_protocol_passed"]
    assert report["false_completions"] == 0
    assert report["duplicate_effects"] == 0
    assert report["external_comparison"]["status"] == "NOT_COMPARABLE"
    assert report["ranking_eligible"] is False
    assert all(
        value == {"runs": 5, "passed": 5} for value in report["by_mode"].values()
    )

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    aggregate = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert verify_artifact(manifest, schema_version=TOOL_FAULT_MANIFEST_SCHEMA)
    assert verify_artifact(aggregate, schema_version=TOOL_FAULT_REPORT_SCHEMA) == report
    receipts = sorted((tmp_path / "runs").glob("*.json"))
    assert len(receipts) == 15
    for path in receipts:
        assert verify_artifact(
            json.loads(path.read_text(encoding="utf-8")),
            schema_version=TOOL_FAULT_RECEIPT_SCHEMA,
        )["protocol_passed"]

    hard = next(
        item
        for item in report["runs"]
        if item["case_id"] == "hard_timeout_sigterm_resistant_tree"
        and item["operating_mode"] == "balanced"
    )
    assert hard["evidence"]["termination_escalated"] is True
    assert hard["evidence"]["owned_process_group_settled"] is True

    flood = next(
        item
        for item in report["runs"]
        if item["case_id"] == "stdout_stderr_flood"
        and item["operating_mode"] == "balanced"
    )
    assert (
        flood["evidence"]["stdout_bytes"] > flood["evidence"]["stdout_artifact_bytes"]
    )
    assert (
        flood["evidence"]["stderr_bytes"] > flood["evidence"]["stderr_artifact_bytes"]
    )
    assert flood["evidence"]["stdout_digest"].startswith("sha256:")

    restart = next(
        item
        for item in report["runs"]
        if item["case_id"] == "gateway_restart_reattach_once"
        and item["operating_mode"] == "balanced"
    )
    assert restart["evidence"]["marker_line_count"] == 1
    assert restart["evidence"]["tool_started_count"] == 1
    assert restart["evidence"]["job_reattached_count"] == 1
