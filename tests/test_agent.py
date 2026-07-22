"""Iterative agent loop (M16): reason → act → observe, gated at every step."""
from __future__ import annotations

from taiyi.agent import AgentRuntime
from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import TaskState
from taiyi.scheduler import SchedulerEngine
from taiyi.validation import ValidationEngine


def build(provider, *, max_steps=8, validator=None):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    return AgentRuntime(sched, audit, provider, validator=validator, max_steps=max_steps)


# --- Multi-step reason/act with results fed back ------------------------------

def test_agent_runs_multiple_steps_then_finishes():
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-m", "done"])]),
        LLMResponse(text="Committed the changes."),
    ])
    ctx = build(provider, validator=ValidationEngine()).run("commit my changes", "dev.git")
    assert ctx.state is TaskState.SIMULATED
    assert [s.step.tool for s in ctx.executed_steps] == ["shell:git status", "shell:git commit"]
    assert ctx.final_output == "Committed the changes."


# --- Governance gates a mid-loop action --------------------------------------

def test_agent_cannot_bypass_governance_mid_loop():
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-c", "user.name=Evil", "-m", "x"])]),
        LLMResponse(text="done"),
    ])
    ctx = build(provider).run("commit", "dev.git")
    assert ctx.state is TaskState.REJECTED
    # The first step ran; the identity-override step was denied and never executed.
    assert [s.step.tool for s in ctx.executed_steps] == ["shell:git status"]
    assert ctx.step_results[-1].matched_rule_id == "authorship.git_identity.no_override"


# --- A NEEDS_REVIEW action suspends the agent --------------------------------

def test_agent_suspends_on_needs_review():
    provider = ScriptedProvider([LLMResponse(tool_calls=[ToolCall("shell:git push", ["origin", "main"])])])
    ctx = build(provider).run("push", "dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert ctx.approval_id is not None


# --- Validation failure feeds back, the agent corrects -----------------------

def test_validation_failure_feeds_back_and_agent_recovers():
    provider = ScriptedProvider([
        LLMResponse(text="all done"),                                  # claims done, but no commit
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-m", "fix"])]),  # corrects
        LLMResponse(text="now actually done"),
    ])
    ctx = build(provider, validator=ValidationEngine()).run("commit my work", "dev.git")
    assert ctx.state is TaskState.SIMULATED
    assert any(s.step.tool == "shell:git commit" for s in ctx.executed_steps)


# --- Step budget bounds the loop ---------------------------------------------

def test_step_budget_is_enforced():
    # The model never stops proposing actions.
    provider = ScriptedProvider([LLMResponse(tool_calls=[ToolCall("shell:git status")])] * 10)
    ctx = build(provider, max_steps=3).run("loop forever", "default")
    assert ctx.state is TaskState.FAILED
    assert "step budget" in ctx.error
    assert len(ctx.executed_steps) == 3
