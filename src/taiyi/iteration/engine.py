"""IterationEngine — the OODA outer loop (L5).

Observe (record trajectories) → Orient (find patterns) → Decide (suggest rules /
propose skills) → Act (human approves). Closes the loop that takes the system to
maturity L4: a new failure class can become a permanent check, repeated work can
sediment into a gated skill, and the validator accrues a regression set.
"""
from __future__ import annotations

from taiyi.iteration.regression import RegressionSet
from taiyi.iteration.rule_patcher import RulePatchSuggestion, suggest_rules
from taiyi.iteration.skill_generator import SkillDraft, generate_skill_draft
from taiyi.iteration.trajectory import TaskRecord, TrajectoryStore
from taiyi.validation import Outcome


class IterationEngine:
    def __init__(self):
        self.store = TrajectoryStore()
        self.regression = RegressionSet()

    def record(self, ctx) -> TaskRecord:
        rec = self.store.record(ctx)
        if rec.state == "COMPLETED":
            self.regression.add(getattr(ctx, "final_output", "") or "", Outcome.PASS)
        elif rec.state == "FAILED":
            self.regression.add(getattr(ctx, "final_output", "") or getattr(ctx, "error", "") or "", Outcome.FAIL)
        return rec

    def suggest_rules(self, *, threshold: int = 3) -> list[RulePatchSuggestion]:
        return suggest_rules(self.store, threshold=threshold)

    def propose_skills(self, *, min_repeats: int = 3) -> list[SkillDraft]:
        return [
            generate_skill_draft(scenario, tools, n)
            for (scenario, tools), n, _ in self.store.repeat_candidates(min_repeats)
        ]

    def report(self) -> dict:
        return {
            "tasks": len(self.store.records),
            "failures": len(self.store.failures()),
            "rule_suggestions": len(self.suggest_rules()),
            "skill_candidates": len(self.propose_skills()),
            "regression_cases": len(self.regression),
        }
