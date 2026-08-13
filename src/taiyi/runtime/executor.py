"""Executors run cleared steps.

The runtime calls an executor ONLY after governance has cleared a step. The real
sandboxed executor (local/Docker, credential isolation, SSRF) is Module 5;
``MockExecutor`` stands in until then, with no side effects, so the loop and the
state machine can be exercised end-to-end at zero cost and zero risk.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from taiyi.runtime.jobs import JobHandle, JobRecord
from taiyi.scheduler import PlanStep


@dataclass
class ExecResult:
    output: str
    ok: bool = True
    operation_id: str | None = None
    job_id: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    failure_kind: str | None = None
    timeout_kind: str | None = None
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_artifact_bytes: int = 0
    stderr_artifact_bytes: int = 0
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    stdout_artifact_truncated: bool = False
    stderr_artifact_truncated: bool = False
    output_truncated: bool = False
    termination_reason: str | None = None
    termination_escalated: bool = False
    owned_process_group_settled: bool | None = None
    duration_seconds: float | None = None
    error: str | None = None
    effect_status: str | None = None
    effect_evidence: str | None = None
    original_failure_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecResult":
        known = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in known if key in value})


class Executor(Protocol):
    def execute(self, step: PlanStep) -> ExecResult: ...


@runtime_checkable
class DurableExecutor(Protocol):
    def supports_jobs(self, step: PlanStep) -> bool: ...

    def start(self, step: PlanStep, *, operation_id: str) -> JobHandle: ...

    def wait(self, job_id: str) -> ExecResult: ...


@runtime_checkable
class RecoverableExecutor(DurableExecutor, Protocol):
    """Durable executor operations needed after gateway/client interruption."""

    def find(self, operation_id: str) -> JobHandle | None: ...

    def cancel(self, job_id: str) -> JobRecord: ...


@runtime_checkable
class IdempotentExecutor(Protocol):
    """Connector contract for server-enforced replay of one logical operation."""

    def supports_idempotency(self, step: PlanStep) -> bool: ...

    def execute_idempotent(
        self,
        step: PlanStep,
        *,
        operation_id: str,
        idempotency_key: str,
    ) -> ExecResult: ...


def execute_step(
    executor: Executor,
    step: PlanStep,
    *,
    operation_id: str,
    idempotency_key: str | None = None,
    on_started: Callable[[JobHandle], None] | None = None,
) -> ExecResult:
    """Execute through the durable job seam when the executor supports it."""

    if isinstance(executor, DurableExecutor) and executor.supports_jobs(step):
        handle = executor.start(step, operation_id=operation_id)
        if on_started is not None:
            on_started(handle)
        return executor.wait(handle.job_id)
    if (
        idempotency_key is not None
        and isinstance(executor, IdempotentExecutor)
        and executor.supports_idempotency(step)
    ):
        result = executor.execute_idempotent(
            step,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
        )
        if result.operation_id is None:
            result.operation_id = operation_id
        return result
    result = executor.execute(step)
    if result.operation_id is None:
        result.operation_id = operation_id
    return result


class MockExecutor:
    """Side-effect-free executor (ported from the Phase 0 demo)."""

    environment = "mock"

    def execute(self, step: PlanStep) -> ExecResult:
        tool, args = step.tool, step.args
        if tool.startswith("shell:git"):
            return ExecResult(f"[mock] ok: {tool} {args}")
        if tool.startswith("sql:"):
            return ExecResult(f"[mock] query returned 42 rows: {args}")
        if tool.startswith("notify:"):
            return ExecResult(f"[mock] notification sent: {args}")
        if tool.startswith("tool:refund"):
            return ExecResult(f"[mock] refund processed: {args}")
        return ExecResult(f"[mock] {tool} {args}")
