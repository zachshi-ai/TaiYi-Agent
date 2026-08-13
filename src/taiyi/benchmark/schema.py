"""Versioned benchmark contracts and tamper-evident JSON artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

BENCHMARK_SCHEMA = "taiyi.harness-benchmark/v1"
RECEIPT_SCHEMA = "taiyi.harness-run-receipt/v1"
REPORT_SCHEMA = "taiyi.harness-benchmark-report/v1"
COMPARATIVE_MANIFEST_SCHEMA = "taiyi.harness-comparative-manifest/v1"
COMPARATIVE_RECEIPT_SCHEMA = "taiyi.harness-comparative-receipt/v1"
COMPARATIVE_REPORT_SCHEMA = "taiyi.harness-comparative-report/v1"
COMPARATIVE_WORKER_SCHEMA = "taiyi.harness-comparative-worker/v1"
COMPARATIVE_FAULT_REPORT_SCHEMA = "taiyi.harness-comparative-fault-report/v1"
COMPARATIVE_FAULT_RECEIPT_SCHEMA = "taiyi.harness-comparative-fault-receipt/v1"
TOOL_FAULT_MANIFEST_SCHEMA = "taiyi.tool-fault-manifest/v1"
TOOL_FAULT_RECEIPT_SCHEMA = "taiyi.tool-fault-receipt/v1"
TOOL_FAULT_REPORT_SCHEMA = "taiyi.tool-fault-report/v1"
LARGE_REPO_MANIFEST_SCHEMA = "taiyi.large-repo-fault-manifest/v1"
LARGE_REPO_RECEIPT_SCHEMA = "taiyi.large-repo-fault-receipt/v1"
LARGE_REPO_REPORT_SCHEMA = "taiyi.large-repo-fault-report/v1"
DURABLE_INDEX_VALIDATION_SCHEMA = "taiyi.durable-index-validation/v1"
PARKED_INDEX_VALIDATION_SCHEMA = "taiyi.parked-index-validation/v1"
DURABLE_EVENT_VALIDATION_SCHEMA = "taiyi.durable-event-validation/v1"


class MeasurementStatus(str, Enum):
    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    ERROR = "ERROR"


class ExpectedOutcome(str, Enum):
    DELIVER = "DELIVER"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SAFE_HANDOFF = "SAFE_HANDOFF"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    description: str
    fault: str
    acceptance_path: str = "result.txt"
    acceptance_content: str = "verified\n"
    repository_files: int = 0
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["tags"] = list(self.tags)
        return value


@dataclass(frozen=True)
class HarnessProbe:
    harness_id: str
    adapter: str
    status: MeasurementStatus
    version: str | None = None
    model: str | None = None
    batch_command: tuple[str, ...] = ()
    isolated_workspace: bool = False
    same_model_configurable: bool = False
    reason: str | None = None
    source_url: str | None = None
    observed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["batch_command"] = list(self.batch_command)
        return value


@dataclass(frozen=True)
class RunReceipt:
    run_id: str
    case_id: str
    harness_id: str
    harness_version: str
    adapter: str
    model_id: str
    operating_mode: str
    measurement_status: MeasurementStatus
    reported_state: str
    expected_outcome: ExpectedOutcome
    task_passed: bool
    protocol_passed: bool
    claimed_complete: bool
    false_completion: bool
    fault_injected: bool
    recovered: bool
    human_handoffs: int
    connector_attempts: int
    applied_effects: int
    duplicate_effects: int
    llm_calls: int
    duration_seconds: float
    failure_kind: str | None
    case_digest: str
    environment_digest: str
    evidence: Mapping[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["measurement_status"] = self.measurement_status.value
        value["expected_outcome"] = self.expected_outcome.value
        value["evidence"] = dict(self.evidence)
        return value


@dataclass(frozen=True)
class ComparativeReceipt:
    run_id: str
    harness_id: str
    harness_version: str | None
    adapter: str
    measurement_status: MeasurementStatus
    comparable: bool
    blockers: tuple[str, ...]
    model_id: str
    case_id: str
    reported_state: str
    task_passed: bool
    claimed_complete: bool
    false_completion: bool
    timed_out: bool
    exit_code: int | None
    duration_seconds: float
    budget_passed: bool
    model_requests: int
    tool_calls: int
    initial_workspace_digest: str
    final_workspace_digest: str
    comparability_signature: str
    evidence: Mapping[str, Any]
    error: str | None = None
    failure_phase: str | None = None
    failure_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["measurement_status"] = self.measurement_status.value
        value["blockers"] = list(self.blockers)
        value["evidence"] = dict(self.evidence)
        if self.failure_phase is None:
            value.pop("failure_phase")
        if self.failure_kind is None:
            value.pop("failure_kind")
        return value


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def artifact_envelope(schema_version: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    content = dict(payload)
    return {
        "schema_version": schema_version,
        "payload": content,
        "digest": canonical_digest({"schema_version": schema_version, "payload": content}),
    }


def verify_artifact(value: Mapping[str, Any], *, schema_version: str) -> dict[str, Any]:
    if value.get("schema_version") != schema_version:
        raise ValueError(f"unsupported benchmark artifact schema: {value.get('schema_version')!r}")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("benchmark artifact has no object payload")
    expected = canonical_digest({"schema_version": schema_version, "payload": payload})
    if value.get("digest") != expected:
        raise ValueError("benchmark artifact digest mismatch")
    return dict(payload)


def write_artifact(path: str | Path, schema_version: str, payload: Mapping[str, Any]) -> Path:
    """Atomically persist one self-verifying benchmark artifact."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    envelope = artifact_envelope(schema_version, payload)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


__all__ = [
    "BENCHMARK_SCHEMA",
    "COMPARATIVE_MANIFEST_SCHEMA",
    "COMPARATIVE_FAULT_REPORT_SCHEMA",
    "COMPARATIVE_FAULT_RECEIPT_SCHEMA",
    "COMPARATIVE_RECEIPT_SCHEMA",
    "COMPARATIVE_REPORT_SCHEMA",
    "COMPARATIVE_WORKER_SCHEMA",
    "DURABLE_INDEX_VALIDATION_SCHEMA",
    "DURABLE_EVENT_VALIDATION_SCHEMA",
    "PARKED_INDEX_VALIDATION_SCHEMA",
    "REPORT_SCHEMA",
    "RECEIPT_SCHEMA",
    "TOOL_FAULT_MANIFEST_SCHEMA",
    "TOOL_FAULT_RECEIPT_SCHEMA",
    "TOOL_FAULT_REPORT_SCHEMA",
    "LARGE_REPO_MANIFEST_SCHEMA",
    "LARGE_REPO_RECEIPT_SCHEMA",
    "LARGE_REPO_REPORT_SCHEMA",
    "BenchmarkCase",
    "ComparativeReceipt",
    "ExpectedOutcome",
    "HarnessProbe",
    "MeasurementStatus",
    "RunReceipt",
    "artifact_envelope",
    "canonical_digest",
    "verify_artifact",
    "write_artifact",
]
