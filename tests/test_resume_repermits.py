"""Pillar 3: resume must re-permit the held step — closing the governance gap.

Before this fix, `TaskRuntime.resume(approve=True)` executed the held step with
no preceding permit (the one place in the codebase where execute had no gate).
A human override of a NEEDS_REVIEW must NOT bypass governance: if the rule set
has since turned the step into a hard DENY, resume refuses to run it.

This file exercises that invariant with a fake scheduler that returns DENY on
the second permit (the re-check), and confirms the step is never executed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from taiyi.approvals import ApprovalStore
from taiyi.core.audit import AuditLog
from taiyi.core.types import PermitResponse, Verdict
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.runtime.context import StepResult
from taiyi.runtime.executor import MockExecutor
from taiyi.runtime.state import TaskState as _TS
from taiyi.scheduler import ExecutionPlan, PlanStep, SchedulerEngine
from taiyi.validation import ValidationEngine


class _ScriptedScheduler(SchedulerEngine):
    """Returns a scripted sequence of verdicts for each request_permit.

    Used to simulate a rule set that tightened while a task was suspended:
    the first permit (in-run) is NEEDS_REVIEW, the second (resume re-check)
    is DENY.
    """

    def __init__(self, verdicts: Sequence[Verdict], reason: str = "tightened"):
        # No real governance client is needed; request_permit is overridden.
        super().__init__(permit_client=_NoopClient())
        self._verdicts = list(verdicts)
        self._i = 0
        self._reason = reason

    def request_permit(self, step, scenario, *, actor="scheduler", user_id="unknown", task_id=None):
        verdict = self._verdicts[min(self._i, len(self._verdicts) - 1)]
        self._i += 1
        approval_id = "appr-1" if verdict is Verdict.NEEDS_REVIEW else None
        return PermitResponse(
            verdict=verdict, reason=self._reason, matched_rule_id="test.tighten",
            approval_id=approval_id,
        )


class _NoopClient:
    """A permit client that is never actually consulted (the scheduler overrides)."""

    def issue_permit(self, request):
        raise AssertionError("scripted scheduler should not delegate to a real client")


def _make_runtime(verdicts: Sequence[Verdict], *, validator: ValidationEngine | None = None) -> TaskRuntime:
    audit = AuditLog()
    sched = _ScriptedScheduler(verdicts)
    # Give the runtime a fixed plan via a fake planner so it tries one tool step.
    sched._planner = _OneStepPlanner()
    return TaskRuntime(
        sched, audit_log=audit, executor=MockExecutor(),
        validator=validator, approvals=ApprovalStore(),
    )


class _OneStepPlanner:
    """Emits a single notify step (one that NEEDS_REVIEW under the script)."""

    def plan(self, prompt, scenario):
        return ExecutionPlan(
            skill_name=None,
            steps=[PlanStep(tool="notify:feishu", args=["--msg", "hi"])],
            rationale="test",
        )


def test_resume_refuses_when_governance_now_denies():
    """The held step was NEEDS_REVIEW; after suspend the rule set denies it.

    Resume(approve=True) must NOT execute — governance re-check returns DENY,
    the task is rejected, and the step is marked not executed.
    """
    rt = _make_runtime([Verdict.NEEDS_REVIEW, Verdict.DENY])
    ctx = rt.run("notify the channel", "ops.report")
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert len(rt.approvals) == 1

    resumed = rt.resume(ctx.approval_id, approve=True)

    assert resumed.state is TaskState.REJECTED
    # The held step was never executed — governance refused it on re-check.
    assert all(not s.executed for s in resumed.step_results)
    assert "governance now denies" in (resumed.final_output or "")
    assert len(rt.approvals) == 0


def test_resume_still_executes_when_governance_still_allows():
    """Sanity: when the re-check still permits (ALLOW), resume executes as before.

    This guards against over-tightening — a human override should still work when
    governance has not actually tightened. No validator here (the point is the
    permit path, not the check phase).
    """
    rt = _make_runtime([Verdict.NEEDS_REVIEW, Verdict.ALLOW], validator=None)
    ctx = rt.run("notify the channel", "ops.report")
    assert ctx.state is TaskState.NEEDS_REVIEW

    resumed = rt.resume(ctx.approval_id, approve=True)

    assert resumed.state is TaskState.SIMULATED
    # The held step WAS executed this time.
    assert [s.step.tool for s in resumed.executed_steps] == ["notify:feishu"]
