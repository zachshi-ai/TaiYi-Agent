"""Planners turn a request into an ExecutionPlan.

A planner is *pluggable*: the runtime depends on the ``Planner`` interface, not on
any one implementation. ``KeywordPlanner`` is the first one — a faithful port of
the Phase 0 demo's keyword router. A later module can drop in an LLM-driven
planner without touching the scheduler or the governance boundary.

Note the lesson the demo recorded: rule order is priority. More specific routes
(git *push*) must be checked before broader ones (git *commit*), or "git" matches
first and the push is misrouted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PlanStep:
    """One tool call the scheduler intends to request a permit for."""

    tool: str
    args: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """What the scheduler decided to do — not yet cleared to run."""

    skill_name: str | None
    steps: list[PlanStep]
    rationale: str


class Planner(Protocol):
    def plan(self, prompt: str, scenario: str) -> ExecutionPlan: ...


class KeywordPlanner:
    """Keyword routing ported from the Phase 0 demo scheduler."""

    def plan(self, prompt: str, scenario: str) -> ExecutionPlan:
        p = prompt.lower()

        # git push — check before the broader git/commit route.
        if any(k in p for k in ["git push", "push 到", "push到", "push 一下"]):
            return ExecutionPlan(
                skill_name="git_safe_commit",
                steps=[PlanStep("shell:git push", ["origin", "main"])],
                rationale="route: git push (single step; expected to need review)",
            )

        if any(k in p for k in ["commit", "git"]) and "push" not in p:
            return ExecutionPlan(
                skill_name="git_safe_commit",
                steps=[
                    PlanStep("shell:git status", []),
                    PlanStep("shell:git diff --staged --stat", []),
                    PlanStep("shell:git add -A", []),
                    PlanStep("shell:git commit", ["-m", self._commit_message(prompt)]),
                ],
                rationale="route: git_safe_commit, decomposed into 4 atomic steps",
            )

        if any(k in p for k in ["周报", "weekly", "report"]):
            return ExecutionPlan(
                skill_name="weekly_report",
                steps=[
                    PlanStep("sql:query", ["SELECT * FROM sales_analytics WHERE week=last"]),
                    PlanStep("notify:feishu", ["send", "ops-team", "weekly_report_v1.pdf"]),
                ],
                rationale="route: weekly_report (query then outbound notify)",
            )

        if any(k in p for k in ["退款", "refund"]):
            amount = self._extract_amount(p)
            return ExecutionPlan(
                skill_name="refund_request",
                steps=[PlanStep("tool:refund", ["refund", f"amount={amount}"])],
                rationale=f"route: refund_request, amount={amount}",
            )

        if any(k in p for k in ["rm -rf", "删除", "delete", "drop"]):
            target = "/tmp/test" if ("/" in prompt and "tmp" in p) else "/"
            return ExecutionPlan(
                skill_name=None,
                steps=[PlanStep("shell:rm -rf", [target])],
                rationale=f"route: dangerous delete rm -rf {target} (expected to be denied)",
            )

        return ExecutionPlan(
            skill_name=None,
            steps=[PlanStep("echo", [prompt])],
            rationale="no matching skill; echo",
        )

    @staticmethod
    def _commit_message(prompt: str) -> str:
        return prompt[:80]

    @staticmethod
    def _extract_amount(prompt_lower: str) -> str:
        m = re.search(r"(\d+)\s*元|(\d+)\s*rmb|amount=(\d+)", prompt_lower)
        if not m:
            return "100"
        return m.group(1) or m.group(2) or m.group(3)
