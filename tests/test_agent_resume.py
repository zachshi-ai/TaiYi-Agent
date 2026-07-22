"""AgentRuntime resume (pillar 1 + pillar 3).

The ReAct loop must suspend on NEEDS_REVIEW (instead of abandoning the task),
and resume must re-check the held step against governance before executing it —
the same invariant TaskRuntime.resume now honours. A human override is not a
governance bypass: if the rule set has since denied the step, resume refuses.
"""
from __future__ import annotations

from typing import Sequence

from taiyi.agent import AgentRuntime
from taiyi.approvals import ApprovalStore
from taiyi.core.audit import AuditLog
from taiyi.core.types import PermitResponse, Verdict
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import TaskState
from taiyi.runtime.executor import MockExecutor
from taiyi.scheduler import SchedulerEngine
from taiyi.validation import ValidationEngine


def _build(provider, *, approvals=None, validator=None, max_steps=8):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    return AgentRuntime(
        sched, audit, provider, executor=MockExecutor(),
        validator=validator, approvals=approvals, max_steps=max_steps,
    )


# --- Normal resume: human approves, governance still permits, loop continues --

def test_agent_suspends_and_resumes_to_completion():
    approvals = ApprovalStore()
    # Step 1: propose git push → NEEDS_REVIEW → suspend.
    # After resume, the model observes the (mock) result and then declares done.
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git push", ["origin", "main"])]),
        LLMResponse(text="Pushed to origin/main."),
    ])
    rt = _build(provider, approvals=approvals, validator=None)
    ctx = rt.run("push", "dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert len(approvals) == 1

    resumed = rt.resume(ctx.approval_id, approve=True)
    assert resumed.state is TaskState.SIMULATED
    # The held step was executed on resume, then the loop finished.
    assert [s.step.tool for s in resumed.executed_steps] == ["shell:git push"]
    assert resumed.final_output == "Pushed to origin/main."
    assert len(approvals) == 0


def test_agent_resume_reject_marks_rejected():
    approvals = ApprovalStore()
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git push", ["origin", "main"])]),
    ])
    rt = _build(provider, approvals=approvals)
    ctx = rt.run("push", "dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW

    resumed = rt.resume(ctx.approval_id, approve=False)
    assert resumed.state is TaskState.REJECTED
    assert "rejected by human" in resumed.final_output
    # The held step was never executed.
    assert all(not s.executed for s in resumed.step_results)


# --- Pillar 3: re-permit refuses when governance has since denied -------------

class _ScriptedScheduler(SchedulerEngine):
    """Returns a scripted verdict sequence so we can simulate a rule that
    tightened from NEEDS_REVIEW (in-run) to DENY (on resume re-check)."""

    def __init__(self, verdicts: Sequence[Verdict]):
        super().__init__(permit_client=_NoopClient())
        self._verdicts = list(verdicts)
        self._i = 0

    def request_permit(self, step, scenario, *, actor="scheduler", user_id="unknown", task_id=None):
        verdict = self._verdicts[min(self._i, len(self._verdicts) - 1)]
        self._i += 1
        approval_id = "appr-x" if verdict is Verdict.NEEDS_REVIEW else None
        return PermitResponse(verdict=verdict, reason="tightened", matched_rule_id="test.tighten",
                              approval_id=approval_id)


class _NoopClient:
    def issue_permit(self, request):
        raise AssertionError("scripted scheduler should not delegate")


def test_agent_resume_refuses_when_governance_now_denies():
    approvals = ApprovalStore()
    audit = AuditLog()
    sched = _ScriptedScheduler([Verdict.NEEDS_REVIEW, Verdict.DENY])
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git push", ["origin", "main"])]),
        LLMResponse(text="done"),  # would-be continuation; must not be reached
    ])
    rt = AgentRuntime(sched, audit, provider, executor=MockExecutor(),
                      approvals=approvals, validator=None)
    ctx = rt.run("push", "dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW

    resumed = rt.resume(ctx.approval_id, approve=True)
    # Human approved, but governance re-check returned DENY → refused.
    assert resumed.state is TaskState.REJECTED
    assert "governance now denies" in resumed.final_output
    assert all(not s.executed for s in resumed.step_results)
