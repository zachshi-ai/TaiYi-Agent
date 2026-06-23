"""Human-in-the-loop: pending approvals for suspended tasks.

When governance returns NEEDS_REVIEW, the task suspends mid-flight. Rather than
abandoning it, the runtime parks a `PendingApproval` here keyed by approval id; a
human (via the gateway) then approves or rejects, and the runtime resumes the task
from exactly where it stopped — the steps already done are kept.

Deliberately free of runtime imports (it holds the live context by duck type), so
there is no import cycle. This is an in-process store; persisting it to disk for
resume-across-restart is a later refinement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PendingApproval:
    approval_id: str
    task_id: str
    tool: str
    reason: str
    scenario: str
    ctx: Any              # the live TaskContext, suspended
    held_index: int       # index of the held step within the plan
    steps: list           # the full plan steps

    def summary(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "tool": self.tool,
            "reason": self.reason,
            "scenario": self.scenario,
            "held_step": self.held_index,
            "total_steps": len(self.steps),
        }


class ApprovalStore:
    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}

    def add(self, pending: PendingApproval) -> None:
        self._pending[pending.approval_id] = pending

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)

    def remove(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)

    def list(self) -> list[PendingApproval]:
        return list(self._pending.values())

    def __len__(self) -> int:
        return len(self._pending)
