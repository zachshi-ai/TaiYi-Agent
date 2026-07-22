"""The Scheduler Engine — plans, then clears each step through governance.

This module owns the *boundary semantics* of the system:

  * No step is cleared to run without an ALLOW permit.
  * A DENY halts the plan immediately; nothing after it is cleared.
  * A NEEDS_REVIEW suspends the plan — steps already cleared are preserved, so a
    half-finished multi-step task does not lose the work it legitimately did.

The scheduler deliberately has no ``execute`` method. It produces a
``PlanClearance`` describing which steps were cleared; actually running cleared
steps is the job of the Task Runtime (M3) and the Tool Runtime (M5). Separating
"may I?" from "do it" is the whole point.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from taiyi.core.types import PermitRequest, PermitResponse, Verdict
from taiyi.governance.client import PermitClient
from taiyi.llm.base import LLMProvider
from taiyi.scheduler.planner import ExecutionPlan, KeywordPlanner, PlanStep, Planner


@dataclass
class StepDecision:
    step: PlanStep
    response: PermitResponse


@dataclass
class PlanClearance:
    """The outcome of walking a plan through the governance boundary."""

    terminal_verdict: Verdict
    cleared_steps: list[PlanStep] = field(default_factory=list)
    decisions: list[StepDecision] = field(default_factory=list)
    halted_step: PlanStep | None = None
    halted_response: PermitResponse | None = None

    @property
    def fully_cleared(self) -> bool:
        return self.terminal_verdict is Verdict.ALLOW


class SchedulerEngine:
    """Decision-maker. Plans tasks and clears steps; never executes."""

    def __init__(self, permit_client: PermitClient, planner: Planner | None = None):
        self._permits = permit_client
        self._planner = planner or KeywordPlanner()

    def plan(
        self,
        prompt: str,
        scenario: str,
        *,
        context: str | None = None,
        provider: LLMProvider | None = None,
    ) -> ExecutionPlan:
        if provider is not None and hasattr(self._planner, "plan_with_provider"):
            return self._planner.plan_with_provider(
                prompt,
                scenario,
                context=context,
                provider=provider,
            )
        if context and hasattr(self._planner, "plan_with_context"):
            return self._planner.plan_with_context(prompt, scenario, context=context)
        return self._planner.plan(prompt, scenario)

    def request_permit(
        self,
        step: PlanStep,
        scenario: str,
        *,
        actor: str = "scheduler",
        user_id: str = "unknown",
        task_id: str | None = None,
    ) -> PermitResponse:
        """Ask the governance boundary whether a single step may run.

        This is the scheduler's only path to clearance — it goes through the
        PermitClient and has no fallback. The Task Runtime calls this per step to
        drive the interleaved permit→execute PDCA loop.
        """
        return self._permits.issue_permit(
            PermitRequest(
                tool=step.tool,
                args=step.args,
                scenario=scenario,
                actor=actor,
                user_id=user_id,
                task_id=task_id,
            )
        )

    def clear_plan(
        self,
        plan: ExecutionPlan,
        scenario: str,
        *,
        actor: str = "scheduler",
        user_id: str = "unknown",
        task_id: str | None = None,
    ) -> PlanClearance:
        """Request a permit for each step in order, stopping at the first
        non-ALLOW verdict. Returns what was cleared and why it stopped."""
        clearance = PlanClearance(terminal_verdict=Verdict.ALLOW)

        for step in plan.steps:
            resp = self.request_permit(
                step, scenario, actor=actor, user_id=user_id, task_id=task_id
            )
            clearance.decisions.append(StepDecision(step=step, response=resp))

            if resp.verdict is Verdict.ALLOW:
                clearance.cleared_steps.append(step)
                continue

            # DENY or NEEDS_REVIEW: stop. Already-cleared steps are preserved.
            clearance.terminal_verdict = resp.verdict
            clearance.halted_step = step
            clearance.halted_response = resp
            break

        return clearance

    def plan_and_clear(
        self, prompt: str, scenario: str, **kw
    ) -> tuple[ExecutionPlan, PlanClearance]:
        plan = self.plan(prompt, scenario)
        return plan, self.clear_plan(plan, scenario, **kw)
