"""Signed combined large-repository/provider/context fault baseline."""
from __future__ import annotations

import json
from pathlib import Path

from taiyi.benchmark.large_repository import (
    CASES,
    _safe_origin,
    large_repository_manifest,
    run_large_repository_matrix,
)
from taiyi.benchmark.schema import (
    DURABLE_INDEX_VALIDATION_SCHEMA,
    LARGE_REPO_MANIFEST_SCHEMA,
    LARGE_REPO_RECEIPT_SCHEMA,
    LARGE_REPO_REPORT_SCHEMA,
    PARKED_INDEX_VALIDATION_SCHEMA,
    verify_artifact,
)


def test_committed_parked_index_validation_is_signed_and_real_scale():
    path = Path(
        "research/benchmark/results/parked-index-kubernetes-v1/validation.json"
    )
    payload = verify_artifact(
        json.loads(path.read_text(encoding="utf-8")),
        schema_version=PARKED_INDEX_VALIDATION_SCHEMA,
    )

    assert payload["measurement_scope"] == "taiyi_parked_repository_continuations"
    assert payload["source"]["file_count"] == 25_683
    assert payload["source"]["git_head"] == (
        "52ba90138eb40cab0987dac73e05c838149bdd1c"
    )
    assert payload["assertions"]["task_waiter_released_while_running"] is True
    assert payload["assertions"]["same_job_reattached_after_wake"] is True
    assert payload["assertions"]["settled_after_durable_wake"] is True
    assert payload["ranking_eligible"] is False


def test_committed_durable_index_validation_is_signed_and_real_scale():
    path = Path(
        "research/benchmark/results/durable-index-kubernetes-v1/validation.json"
    )
    payload = verify_artifact(
        json.loads(path.read_text(encoding="utf-8")),
        schema_version=DURABLE_INDEX_VALIDATION_SCHEMA,
    )

    assert payload["measurement_scope"] == "taiyi_durable_repository_index_jobs"
    assert payload["source"]["file_count"] == 25_683
    assert payload["source"]["git_head"] == (
        "52ba90138eb40cab0987dac73e05c838149bdd1c"
    )
    assert payload["assertions"]["distinct_generation_job_ids"] is True
    assert payload["assertions"]["fresh_scan_after_terminal_generation"] is True
    assert payload["ranking_eligible"] is False


def test_large_repository_manifest_freezes_combined_reliability_floor():
    source = {
        "kind": "deterministic_fixture",
        "origin": None,
        "git_head": None,
        "snapshot_id": "sha256:fixture",
        "file_count": 750,
        "chunk_count": 759,
        "skipped_files": 0,
        "omitted_files": 0,
        "complete": True,
        "query": "frozen_large_repo_target",
    }

    manifest = large_repository_manifest(source)

    assert manifest["measurement_scope"] == "taiyi_large_repository_combined_resilience"
    assert manifest["operating_modes"] == ["quality", "balanced", "efficiency"]
    assert len(manifest["cases"]) == 4
    assert "no model retry may replay a governed tool effect" in manifest["reliability_floor"]
    assert manifest["contract_digest"].startswith("sha256:")


def test_real_repository_origin_redacts_credentials_queries_and_local_paths():
    assert _safe_origin("https://user:secret@example.com/org/repo.git?token=x#fragment") == (
        "https://example.com/org/repo.git"
    )
    assert _safe_origin("/Users/private/repo") == "LOCAL_OR_REDACTED"
    assert _safe_origin("file:///private/repo") == "LOCAL_OR_REDACTED"
    assert _safe_origin("git@github.com:org/repo.git") == "git@github.com:org/repo.git"


def test_large_repository_matrix_is_complete_signed_and_has_no_false_completion(tmp_path):
    report = run_large_repository_matrix(tmp_path)

    assert report["case_count"] == 4
    assert report["run_count"] == 12
    assert report["protocol_passed_count"] == 12
    assert report["all_protocol_passed"]
    assert report["false_completions"] == 0
    assert report["duplicate_effects"] == 0
    assert report["source"]["file_count"] == 750
    assert report["source"]["complete"] is True
    assert report["external_comparison"]["status"] == "NOT_COMPARABLE"
    assert report["ranking_eligible"] is False
    assert all(
        value == {"runs": 4, "passed": 4} for value in report["by_mode"].values()
    )

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    aggregate = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert verify_artifact(manifest, schema_version=LARGE_REPO_MANIFEST_SCHEMA)
    assert verify_artifact(aggregate, schema_version=LARGE_REPO_REPORT_SCHEMA) == report
    receipts = sorted((tmp_path / "runs").glob("*.json"))
    assert len(receipts) == len(CASES) * 3
    for path in receipts:
        assert verify_artifact(
            json.loads(path.read_text(encoding="utf-8")),
            schema_version=LARGE_REPO_RECEIPT_SCHEMA,
        )["protocol_passed"]

    index_restart = next(
        item
        for item in report["runs"]
        if item["case_id"] == "index_process_restart"
        and item["operating_mode"] == "balanced"
    )
    assert index_restart["evidence"]["interrupted_phase"] == "INDEXING"
    assert index_restart["evidence"]["heartbeat_processed_files"] == 250
    assert index_restart["evidence"]["partial_snapshot_published"] is False

    overflow = next(
        item
        for item in report["runs"]
        if item["case_id"] == "large_tool_output_context_overflow"
        and item["operating_mode"] == "balanced"
    )
    assert overflow["evidence"]["marker_line_count"] == 1
    assert overflow["evidence"]["tool_started_count"] == 1
    assert overflow["evidence"]["context_compaction_count"] == 1
    assert overflow["evidence"]["stdout_bytes"] > 100_000

    model_restart = next(
        item
        for item in report["runs"]
        if item["case_id"] == "model_wait_process_restart"
        and item["operating_mode"] == "balanced"
    )
    assert model_restart["evidence"]["interrupted_phase"] == "LLM_WAITING"
    assert model_restart["evidence"]["frozen_projection_replayed"] is True
