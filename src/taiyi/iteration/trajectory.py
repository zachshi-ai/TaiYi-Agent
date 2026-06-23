"""Trajectory analysis — the Observe/Orient of the OODA outer loop.

Records a compact summary of each finished task and surfaces cross-task patterns:
recurring failure classes (candidates for a new permanent check) and repeated
skill-less task shapes (candidates for an auto-generated skill).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class TaskRecord:
    task_id: str
    scenario: str
    skill: str | None
    state: str
    prompt: str
    tools: tuple[str, ...]
    fail_reason: str | None = None

    @property
    def signature(self) -> tuple[str, tuple[str, ...]]:
        return (self.scenario, self.tools)


def record_from_ctx(ctx) -> TaskRecord:
    """Build a TaskRecord from a TaskContext (duck-typed; no runtime import)."""
    skill = ctx.plan.skill_name if getattr(ctx, "plan", None) else None
    tools = tuple(sr.step.tool for sr in ctx.step_results)
    state = ctx.state.value if hasattr(ctx.state, "value") else str(ctx.state)
    fail_reason = None
    if state == "REJECTED":
        fail_reason = next(
            (sr.matched_rule_id for sr in reversed(ctx.step_results) if sr.matched_rule_id),
            "rejected",
        )
    elif state == "FAILED":
        fail_reason = getattr(ctx, "validation_summary", None) or getattr(ctx, "error", None) or "failed"
    return TaskRecord(ctx.task_id, ctx.scenario, skill, state, ctx.prompt, tools, fail_reason)


class TrajectoryStore:
    def __init__(self):
        self.records: list[TaskRecord] = []

    def record(self, ctx) -> TaskRecord:
        rec = record_from_ctx(ctx)
        self.records.append(rec)
        return rec

    def add(self, record: TaskRecord) -> None:
        self.records.append(record)

    def failures(self) -> list[TaskRecord]:
        return [r for r in self.records if r.state in ("FAILED", "REJECTED")]

    def failure_tool_classes(self) -> Counter:
        """(scenario, last_tool) -> count, over failed/rejected tasks."""
        c: Counter = Counter()
        for r in self.failures():
            if r.tools:
                c[(r.scenario, r.tools[-1])] += 1
        return c

    def repeat_candidates(self, min_count: int = 3) -> list[tuple[tuple, int, TaskRecord]]:
        """Skill-less task shapes that completed repeatedly — sediment candidates."""
        counts: Counter = Counter()
        example: dict[tuple, TaskRecord] = {}
        for r in self.records:
            if r.state == "COMPLETED" and r.skill is None and r.tools:
                counts[r.signature] += 1
                example.setdefault(r.signature, r)
        return [(sig, n, example[sig]) for sig, n in counts.items() if n >= min_count]
