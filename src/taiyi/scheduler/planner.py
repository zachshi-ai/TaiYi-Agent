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


_GIT_PUSH_TERMS = (
    "git push",
    "push to",
    "push 到",
    "push到",
    "push 一下",
    "推送到",
    "推送代码",
)
_GIT_PUSH_NEGATIONS = (
    "do not push",
    "don't push",
    "dont push",
    "no push",
    "不要 push",
    "别 push",
    "不要推送",
    "别推送",
    "无需推送",
    "不推送",
)


def is_git_push_prompt(prompt: str) -> bool:
    low = prompt.casefold()
    return (
        not any(term in low for term in _GIT_PUSH_NEGATIONS)
        and any(term in low for term in _GIT_PUSH_TERMS)
    )


def git_push_target(prompt: str) -> tuple[str, str]:
    """Extract a simple ``remote ref`` target, with the planner's safe default."""

    patterns = (
        r"\bgit\s+push(?:\s+(?:to|到))?\s+([\w./-]+)\s+([\w./-]+)",
        r"(?:推送代码|推送)到\s*([\w./-]+)\s+([\w./-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    return "origin", "main"


def refund_amount(prompt: str) -> str:
    low = prompt.casefold()
    match = re.search(r"(\d+)\s*元|(\d+)\s*rmb|amount=(\d+)", low)
    if not match:
        return "100"
    return match.group(1) or match.group(2) or match.group(3)


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
    # Set by model-backed planners so the runtime can expose the model that
    # actually produced this plan without parsing a human-readable rationale.
    provider_model: str | None = None
    # A planner may return a direct answer or clarification when no tool is
    # needed. Workflow Runtime must not silently discard that response.
    planner_output: str | None = None


class Planner(Protocol):
    def plan(self, prompt: str, scenario: str) -> ExecutionPlan: ...


class KeywordPlanner:
    """Keyword routing ported from the Phase 0 demo scheduler."""

    def plan(self, prompt: str, scenario: str) -> ExecutionPlan:
        p = prompt.lower()

        # git push — check before the broader git/commit route.
        if is_git_push_prompt(prompt):
            remote, ref = git_push_target(prompt)
            return ExecutionPlan(
                skill_name="git_safe_commit",
                steps=[PlanStep("shell:git push", [remote, ref])],
                rationale=(
                    f"route: git push {remote} {ref} "
                    "(single step; expected to need review)"
                ),
            )

        if any(k in p for k in ["commit", "git"]):
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
            amount = refund_amount(prompt)
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
