"""Committee as a second permit gate — one-way tightening.

The expert committee reviews a step only after governance ALLOWs it, and can only
escalate (ALLOW → NEEDS_REVIEW). It must never loosen a governance DENY. This file
exercises both runtimes through that gate with a committee that vetoes a marker.
"""
from __future__ import annotations

from taiyi.agent import AgentRuntime
from taiyi.approvals import ApprovalStore
from taiyi.core.audit import AuditLog
from taiyi.core.types import Verdict
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.memory import MemoryEngine
from taiyi.multi_agent import (
    Authority,
    ExpertCommittee,
    MarkerExpert,
    OpinionVerdict,
    reconsider_permit,
)
from taiyi.multi_agent.arbitration import ArbitrationResult, Decision
from taiyi.runtime import TaskState
from taiyi.runtime.executor import MockExecutor
from taiyi.scheduler import SchedulerEngine


def _veto_committee(marker: str) -> ExpertCommittee:
    """A committee with one VETO-domain expert that vetoes ``marker``."""
    return ExpertCommittee([
        MarkerExpert("test", Authority.VETO, 100, veto_markers=[marker]),
    ])


# --- the one-way mapping itself ------------------------------------------------

def test_reconsider_loosens_nothing_on_deny():
    """A governance DENY must survive the committee unchanged."""
    from taiyi.core.types import PermitResponse

    permit = PermitResponse(verdict=Verdict.DENY, reason="red line")
    arb = ArbitrationResult(Decision.APPROVED)  # committee approves
    out = reconsider_permit(permit, arb)
    assert out.verdict is Verdict.DENY  # not loosened


def test_reconsider_escalates_allow_on_veto():
    from taiyi.core.types import PermitResponse

    permit = PermitResponse(verdict=Verdict.ALLOW, reason="ok")
    arb = ArbitrationResult(Decision.VETOED, notes="test veto")
    out = reconsider_permit(permit, arb, approval_id="appr-1")
    assert out.verdict is Verdict.NEEDS_REVIEW  # tightened, not denied
    assert out.approval_id == "appr-1"


def test_reconsider_keeps_allow_on_committee_approval():
    from taiyi.core.types import PermitResponse

    permit = PermitResponse(verdict=Verdict.ALLOW, reason="ok")
    arb = ArbitrationResult(Decision.APPROVED)
    assert reconsider_permit(permit, arb).verdict is Verdict.ALLOW


# --- AgentRuntime: committee escalates an allowed step -------------------------

def test_agent_committee_escalates_allowed_step_to_review():
    committee = _veto_committee("tool:risky")
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("tool:risky")]),
        LLMResponse(text="done"),
    ])
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    rt = AgentRuntime(
        sched, audit, provider, executor=MockExecutor(), validator=None,
        memory=MemoryEngine(), approvals=ApprovalStore(), committee=committee,
    )
    ctx = rt.run("do risky thing", "default")
    # Governance allowed (no rule on tool:risky), but the committee vetoed → review.
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert ctx.approval_id is not None
    # The risky step was never executed.
    assert all(not s.executed for s in ctx.step_results)


def test_agent_committee_passes_when_no_veto():
    # Default builtin committee has no marker for tool:safe → APPROVED → executes.
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("tool:safe")]),
        LLMResponse(text="ok"),
    ])
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    rt = AgentRuntime(
        sched, audit, provider, executor=MockExecutor(), validator=None,
        memory=MemoryEngine(), approvals=ApprovalStore(), committee=ExpertCommittee(),
    )
    ctx = rt.run("do safe thing", "default")
    assert ctx.state is TaskState.SIMULATED
    assert [s.step.tool for s in ctx.executed_steps] == ["tool:safe"]


# --- governance DENY still wins over a committee approval ----------------------

def test_agent_governance_deny_not_overridden_by_committee():
    # A committee that approves everything; governance still denies shell:rm -rf /.
    committee = ExpertCommittee()  # builtin, no veto on this
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:rm", ["-rf", "/"])]),
    ])
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    rt = AgentRuntime(
        sched, audit, provider, executor=MockExecutor(), validator=None,
        memory=MemoryEngine(), approvals=ApprovalStore(), committee=committee,
    )
    ctx = rt.run("delete everything", "default")
    # Governance red-line DENY — committee cannot loosen it.
    assert ctx.state is TaskState.REJECTED
    assert all(not s.executed for s in ctx.step_results)
