"""IterationEngine — the OODA outer loop (L5).

Observe (record trajectories) → Orient (find patterns) → Decide (suggest rules /
propose skills) → Act (human approves). Closes the loop that takes the system to
maturity L4: a new failure class can become a permanent check, repeated work can
sediment into a gated skill, and the validator accrues a regression set.

This is the **complete** OODA loop, not just the Observe half:

* Observe — ``record(ctx)`` persists a trajectory to SQLite (survives restart).
* Orient/Decide — ``record`` *automatically* runs ``suggest_rules`` and
  ``propose_skills`` after each task and files any hits into a ``pending_review``
  queue. The loop learns every task, not just when a human remembers to ask.
* Act — a human reviews the queue via ``approve``/``reject``; ``approve`` writes
  the rule/skill YAML into the ``auto`` directories, which governance/skills
  load read-only on the *next* start. Nothing auto-mutates the live set — the
  loop "only adds, never silently," and the governance/scheduling separation is
  preserved.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from taiyi.iteration.regression import RegressionSet
from taiyi.iteration.rule_patcher import RulePatchSuggestion, suggest_rules
from taiyi.iteration.skill_generator import SkillDraft, generate_skill_draft
from taiyi.iteration.trajectory import TaskRecord, TrajectoryStore
from taiyi.validation import Outcome


@dataclass
class PendingSuggestion:
    """A rule/skill suggestion awaiting human review — the OODA Act gate."""

    id: int
    kind: str               # "rule" | "skill"
    status: str             # "pending" | "approved" | "rejected"
    payload: dict           # serialised suggestion (rule dict or skill draft fields)
    created_ts: float

    def summary(self) -> dict:
        if self.kind == "rule":
            return {
                "id": self.id, "kind": "rule", "status": self.status,
                "rule_id": self.payload.get("rule_id"),
                "scenario": self.payload.get("scenario"),
                "tool": self.payload.get("tool"),
                "occurrences": self.payload.get("occurrences"),
                "rationale": self.payload.get("rationale"),
            }
        return {
            "id": self.id, "kind": "skill", "status": self.status,
            "name": self.payload.get("name"),
            "scenario": self.payload.get("scenario"),
            "tools": self.payload.get("tools"),
            "occurrences": self.payload.get("occurrences"),
        }


class IterationEngine:
    def __init__(self, base_dir: str | Path | None = None):
        self.store = TrajectoryStore(base_dir)
        # Reuse the trajectory DB connection for the pending-review queue so the
        # whole OODA state persists in one place.
        self._conn = self.store.conn
        self._init_queue()
        self.regression = RegressionSet()

    def _init_queue(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_review(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT, status TEXT, payload TEXT, created_ts REAL);
            """
        )
        self._conn.commit()

    # --- Observe --------------------------------------------------------------
    def record(self, ctx) -> TaskRecord:
        rec = self.store.record(ctx)
        if rec.state == "COMPLETED":
            self.regression.add(getattr(ctx, "final_output", "") or "", Outcome.PASS)
        elif rec.state == "FAILED":
            self.regression.add(getattr(ctx, "final_output", "") or getattr(ctx, "error", "") or "", Outcome.FAIL)

        # Orient/Decide: file any newly-actionable patterns into the review queue.
        # This is what makes the loop run "every task, not just on demand" — the
        # cost is a couple of in-memory counts plus one SQLite write per hit.
        self._file_suggestions()
        return rec

    def _file_suggestions(self) -> None:
        """Run Orient/Decide and persist any new suggestions as pending reviews."""
        now = time.time()
        for sug in self.suggest_rules():
            self._enqueue("rule", {
                "rule_id": sug.rule_id, "scenario": sug.scenario, "tool": sug.tool,
                "occurrences": sug.occurrences, "rationale": sug.rationale,
                "rule_dict": sug.to_rule_dict(), "yaml": sug.to_yaml(),
            }, now)
        for draft in self.propose_skills():
            self._enqueue("skill", {
                "name": draft.name, "scenario": draft.scenario,
                "tools": list(draft.tools), "occurrences": draft.occurrences,
                "skill_md": draft.skill_md(), "gate_md": draft.gate_md(),
            }, now)

    def _enqueue(self, kind: str, payload: dict, ts: float) -> int:
        # Dedup: don't file the same pending rule/skill twice. A re-filed
        # suggestion (same kind + key) that is still pending is a no-op.
        key = payload.get("rule_id") or payload.get("name")
        existing = self._conn.execute(
            "SELECT id FROM pending_review WHERE kind=? AND status='pending' AND payload LIKE ?",
            (kind, f'%"{key}"%'),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = self._conn.execute(
            "INSERT INTO pending_review(kind, status, payload, created_ts) VALUES (?,?,?,?)",
            (kind, "pending", json.dumps(payload, ensure_ascii=False), ts),
        )
        self._conn.commit()
        return cur.lastrowid

    # --- Orient/Decide (also callable directly) -------------------------------
    def suggest_rules(self, *, threshold: int = 3) -> list[RulePatchSuggestion]:
        return suggest_rules(self.store, threshold=threshold)

    def propose_skills(self, *, min_repeats: int = 3) -> list[SkillDraft]:
        return [
            generate_skill_draft(scenario, tools, n)
            for (scenario, tools), n, _ in self.store.repeat_candidates(min_repeats)
        ]

    # --- Act (human-gated) ----------------------------------------------------
    def list_pending(self) -> list[PendingSuggestion]:
        rows = self._conn.execute(
            "SELECT id, kind, status, payload, created_ts FROM pending_review "
            "WHERE status='pending' ORDER BY id"
        ).fetchall()
        return [PendingSuggestion(r["id"], r["kind"], r["status"],
                                  json.loads(r["payload"]), r["created_ts"]) for r in rows]

    def approve(self, suggestion_id: int, *, rules_dir: str | Path, skills_dir: str | Path) -> Path | None:
        """Human-approved: persist the suggestion as a loadable rule/skill file.

        Writes into the ``auto`` subdirectory of rules_dir / skills_dir; governance
        and skills load those dirs read-only on the *next* start. The live set is
        never mutated at runtime.
        """
        row = self._conn.execute(
            "SELECT kind, payload FROM pending_review WHERE id=?", (suggestion_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown suggestion: {suggestion_id}")
        kind, payload = row["kind"], json.loads(row["payload"])
        path = None
        if kind == "rule":
            from taiyi.iteration.rule_patcher import approve as approve_rule
            sug = RulePatchSuggestion(
                rule_id=payload["rule_id"], scenario=payload["scenario"],
                tool=payload["tool"], occurrences=payload["occurrences"],
                rationale=payload["rationale"],
            )
            path = approve_rule(sug, rules_dir)
        else:  # skill
            from taiyi.iteration.skill_generator import SkillDraft, write_draft
            draft = SkillDraft(
                name=payload["name"], scenario=payload["scenario"],
                tools=tuple(payload["tools"]), occurrences=payload["occurrences"],
            )
            path = write_draft(draft, skills_dir)
        self._conn.execute(
            "UPDATE pending_review SET status='approved' WHERE id=?", (suggestion_id,)
        )
        self._conn.commit()
        return path

    def reject(self, suggestion_id: int) -> None:
        row = self._conn.execute(
            "SELECT id FROM pending_review WHERE id=?", (suggestion_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown suggestion: {suggestion_id}")
        self._conn.execute(
            "UPDATE pending_review SET status='rejected' WHERE id=?", (suggestion_id,)
        )
        self._conn.commit()

    # --- reporting ------------------------------------------------------------
    def report(self) -> dict:
        return {
            "tasks": len(self.store.records),
            "failures": len(self.store.failures()),
            "rule_suggestions": len(self.suggest_rules()),
            "skill_candidates": len(self.propose_skills()),
            "regression_cases": len(self.regression),
            "pending_reviews": len(self.list_pending()),
        }

    # --- trajectory browsing (for the web UI) --------------------------------
    def list_trajectories(
        self, *, limit: int = 50, offset: int = 0, state: str | None = None
    ) -> list[dict]:
        """Historical task records (newest first), optionally filtered by state.

        Wraps the in-memory mirror of the SQLite trajectory store. Each record
        carries its signal-rich step trail (tool/args/verdict/output), so the web
        UI can render a task timeline without a second query.
        """
        recs = self.store.records
        if state:
            recs = [r for r in recs if r.state == state]
        sliced = list(reversed(recs))[offset:offset + limit]
        return [self._record_to_dict(r) for r in sliced]

    def get_trajectory(self, task_id: str) -> dict | None:
        """A single task record by id, with full step detail, or None."""
        for r in self.store.records:
            if r.task_id == task_id:
                return self._record_to_dict(r)
        return None

    @staticmethod
    def _record_to_dict(rec: TaskRecord) -> dict:
        return {
            "task_id": rec.task_id,
            "scenario": rec.scenario,
            "skill": rec.skill,
            "state": rec.state,
            "prompt": rec.prompt,
            "tools": list(rec.tools),
            "fail_reason": rec.fail_reason,
            "steps": [s.to_dict() for s in rec.steps],
            "value_contribution": rec.value_contribution,
            "ts": rec.ts,
        }
