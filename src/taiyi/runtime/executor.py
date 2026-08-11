"""Executors run cleared steps.

The runtime calls an executor ONLY after governance has cleared a step. The real
sandboxed executor (local/Docker, credential isolation, SSRF) is Module 5;
``MockExecutor`` stands in until then, with no side effects, so the loop and the
state machine can be exercised end-to-end at zero cost and zero risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from taiyi.runtime.jobs import JobHandle
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
    output_truncated: bool = False
    duration_seconds: float | None = None
    error: str | None = None


class Executor(Protocol):
    def execute(self, step: PlanStep) -> ExecResult: ...


@runtime_checkable
class DurableExecutor(Protocol):
    def supports_jobs(self, step: PlanStep) -> bool: ...

    def start(self, step: PlanStep, *, operation_id: str) -> JobHandle: ...

    def wait(self, job_id: str) -> ExecResult: ...


def execute_step(
    executor: Executor,
    step: PlanStep,
    *,
    operation_id: str,
    on_started: Callable[[JobHandle], None] | None = None,
) -> ExecResult:
    """Execute through the durable job seam when the executor supports it."""

    if isinstance(executor, DurableExecutor) and executor.supports_jobs(step):
        handle = executor.start(step, operation_id=operation_id)
        if on_started is not None:
            on_started(handle)
        return executor.wait(handle.job_id)
    return executor.execute(step)


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
