"""Executors run cleared steps.

The runtime calls an executor ONLY after governance has cleared a step. The real
sandboxed executor (local/Docker, credential isolation, SSRF) is Module 5;
``MockExecutor`` stands in until then, with no side effects, so the loop and the
state machine can be exercised end-to-end at zero cost and zero risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from taiyi.scheduler import PlanStep


@dataclass
class ExecResult:
    output: str
    ok: bool = True


class Executor(Protocol):
    def execute(self, step: PlanStep) -> ExecResult: ...


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
