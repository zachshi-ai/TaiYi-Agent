"""LLM provider layer, offline-first (Module 4).

The headline test is `test_llm_cannot_bypass_governance`: a model proposing a
red-line tool call (the kind a prompt injection would induce) is still denied.
That property is independent of whether the provider is offline or live.
"""
from __future__ import annotations

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import KeywordOfflineProvider, LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import LLMPlanner, SchedulerEngine


def build_runtime(provider):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov), planner=LLMPlanner(provider))
    return TaskRuntime(sched, audit_log=audit)


# --- Providers ---------------------------------------------------------------

def test_scripted_provider_replays_in_order_then_exhausts():
    p = ScriptedProvider([LLMResponse(text="one"), LLMResponse(text="two")])
    assert p.complete([]).text == "one"
    assert p.complete([]).text == "two"
    assert "no more" in p.complete([]).text


def test_response_wants_tools():
    assert LLMResponse(tool_calls=[ToolCall("shell:ls")]).wants_tools
    assert not LLMResponse(text="just text").wants_tools


def test_keyword_offline_provider_proposes_tool_calls():
    from taiyi.llm.base import LLMMessage

    resp = KeywordOfflineProvider().complete([LLMMessage("user", "commit my changes")])
    assert resp.wants_tools
    assert resp.tool_calls[-1].tool == "shell:git commit"


# --- LLM planner is gated exactly like any other planner ---------------------

def test_llm_cannot_bypass_governance():
    # Simulate a prompt-injected model proposing an identity override.
    malicious = LLMResponse(
        tool_calls=[ToolCall("shell:git commit", ["-c", "user.name=Evil", "-m", "pwned"])],
        model="offline:scripted",
    )
    runtime = build_runtime(ScriptedProvider([malicious]))
    ctx = runtime.run("please commit my work", "dev.git")
    assert ctx.state is TaskState.REJECTED
    assert ctx.step_results[-1].matched_rule_id == "authorship.git_identity.no_override"
    assert ctx.executed_steps == []


def test_llm_proposed_destructive_call_is_denied():
    malicious = LLMResponse(tool_calls=[ToolCall("shell:rm -rf", ["/"])])
    runtime = build_runtime(ScriptedProvider([malicious]))
    ctx = runtime.run("clean up some temp files for me", "default")
    assert ctx.state is TaskState.REJECTED


def test_llm_benign_plan_completes():
    benign = LLMResponse(
        tool_calls=[ToolCall("shell:git status"), ToolCall("shell:git commit", ["-m", "ok"])]
    )
    runtime = build_runtime(ScriptedProvider([benign]))
    ctx = runtime.run("commit my work", "dev.git")
    assert ctx.state is TaskState.COMPLETED
    assert len(ctx.executed_steps) == 2


def test_keyword_offline_provider_runs_end_to_end():
    runtime = build_runtime(KeywordOfflineProvider())
    ctx = runtime.run("commit my changes", "dev.git")
    assert ctx.state is TaskState.COMPLETED
