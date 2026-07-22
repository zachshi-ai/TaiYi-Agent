"""Trajectory analysis — the Observe/Orient of the OODA outer loop.

Records a compact summary of each finished task and surfaces cross-task patterns:
recurring failure classes (candidates for a new permanent check) and repeated
skill-less task shapes (candidates for an auto-generated skill).

Persistence: trajectories are stored in SQLite (``<base>/iteration.db``) so the
OODA loop accumulates across process restarts — the "周行不殆" property. When no
base dir is given the DB is in-memory (tests stay file-free). On construction
the store loads its history into memory; analysis methods (``failures``,
``failure_tool_classes``, ``repeat_candidates``) read that in-memory list, so the
signal-rich ``TaskRecord`` (now carrying args/output/verdict, not just tool
names) feeds Orient/Decide with real evidence.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StepRecord:
    """One step's trail, persisted for richer Orient signal."""

    tool: str
    args: tuple[str, ...] = ()
    verdict: str = ""           # ALLOW | DENY | NEEDS_REVIEW | ALLOW(human) | ...
    output: str | None = None
    matched_rule_id: str | None = None
    executed: bool = False

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": list(self.args),
            "verdict": self.verdict,
            "output": self.output,
            "matched_rule_id": self.matched_rule_id,
            "executed": self.executed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StepRecord:
        return cls(
            tool=d["tool"],
            args=tuple(d.get("args") or ()),
            verdict=d.get("verdict", ""),
            output=d.get("output"),
            matched_rule_id=d.get("matched_rule_id"),
            executed=bool(d.get("executed")),
        )


@dataclass
class TaskRecord:
    task_id: str
    scenario: str
    skill: str | None
    state: str
    prompt: str
    tools: tuple[str, ...]
    fail_reason: str | None = None
    # Signal-rich additions for Orient/Decide — previously dropped.
    steps: tuple[StepRecord, ...] = field(default_factory=tuple)
    value_contribution: float | None = None
    ts: float = field(default_factory=time.time)

    @property
    def signature(self) -> tuple[str, tuple[str, ...]]:
        return (self.scenario, self.tools)


def _extract_steps(ctx) -> tuple[StepRecord, ...]:
    """Pull the full per-step trail from a TaskContext (duck-typed)."""
    out = []
    for sr in getattr(ctx, "step_results", []) or []:
        step = getattr(sr, "step", None)
        tool = getattr(step, "tool", "") if step else ""
        args = tuple(getattr(step, "args", []) or [])
        out.append(StepRecord(
            tool=tool, args=args,
            verdict=getattr(sr, "verdict", "") or "",
            output=getattr(sr, "output", None),
            matched_rule_id=getattr(sr, "matched_rule_id", None),
            executed=bool(getattr(sr, "executed", False)),
        ))
    return tuple(out)


def _extract_value(ctx) -> float | None:
    vc = getattr(ctx, "value_contribution", None)
    if vc is None:
        return None
    score = getattr(vc, "score", None)
    return float(score) if score is not None else None


def record_from_ctx(ctx) -> TaskRecord:
    """Build a TaskRecord from a TaskContext (duck-typed; no runtime import)."""
    skill = getattr(ctx, "selected_skill", None) or (
        ctx.plan.skill_name if getattr(ctx, "plan", None) else None
    )
    steps = _extract_steps(ctx)
    tools = tuple(s.tool for s in steps)
    state = ctx.state.value if hasattr(ctx.state, "value") else str(ctx.state)
    fail_reason = None
    if state == "REJECTED":
        fail_reason = next(
            (s.matched_rule_id for s in reversed(steps) if s.matched_rule_id),
            "rejected",
        )
    elif state == "FAILED":
        fail_reason = getattr(ctx, "validation_summary", None) or getattr(ctx, "error", None) or "failed"
    return TaskRecord(
        ctx.task_id, ctx.scenario, skill, state, ctx.prompt, tools,
        fail_reason=fail_reason, steps=steps, value_contribution=_extract_value(ctx),
    )


class TrajectoryStore:
    """SQLite-backed trajectory store with an in-memory mirror for analysis."""

    def __init__(self, base_dir: str | Path | None = None):
        if base_dir is None:
            db_path = ":memory:"
            self.base: Path | None = None
        else:
            self.base = Path(base_dir)
            (self.base / "memory").mkdir(parents=True, exist_ok=True)
            db_path = str(self.base / "iteration.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.records: list[TaskRecord] = []
        self._load()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trajectories(
                id INTEGER PRIMARY KEY,
                task_id TEXT, scenario TEXT, skill TEXT, state TEXT,
                prompt TEXT, tools TEXT, fail_reason TEXT,
                steps TEXT, value_contribution REAL, ts REAL);
            """
        )
        self.conn.commit()

    def _load(self) -> None:
        """Hydrate the in-memory list from disk so analysis sees full history."""
        rows = self.conn.execute(
            "SELECT task_id, scenario, skill, state, prompt, tools, fail_reason, "
            "steps, value_contribution, ts FROM trajectories ORDER BY id"
        ).fetchall()
        self.records = [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(r) -> TaskRecord:
        steps = tuple(StepRecord.from_dict(s) for s in json.loads(r["steps"] or "[]"))
        tools = tuple(json.loads(r["tools"] or "[]"))
        return TaskRecord(
            task_id=r["task_id"], scenario=r["scenario"], skill=r["skill"],
            state=r["state"], prompt=r["prompt"], tools=tools,
            fail_reason=r["fail_reason"], steps=steps,
            value_contribution=r["value_contribution"], ts=r["ts"] or time.time(),
        )

    def record(self, ctx) -> TaskRecord:
        rec = record_from_ctx(ctx)
        self._persist(rec)
        self.records.append(rec)
        return rec

    def _persist(self, rec: TaskRecord) -> None:
        self.conn.execute(
            "INSERT INTO trajectories(task_id, scenario, skill, state, prompt, tools, "
            "fail_reason, steps, value_contribution, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                rec.task_id, rec.scenario, rec.skill, rec.state, rec.prompt,
                json.dumps(list(rec.tools)), rec.fail_reason,
                json.dumps([s.to_dict() for s in rec.steps]),
                rec.value_contribution, rec.ts,
            ),
        )
        self.conn.commit()

    def add(self, record: TaskRecord) -> None:
        """Add a pre-built record (used by tests / replay)."""
        self._persist(record)
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

    def close(self) -> None:
        self.conn.close()
