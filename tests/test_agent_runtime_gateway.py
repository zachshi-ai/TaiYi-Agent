"""Pillar 1: AgentRuntime wired as the default main path through build_gateway.

When a provider is supplied and mode=agent, the gateway assembles an
AgentRuntime (ReAct) rather than the plan-once TaskRuntime. This file drives a
full task through build_gateway with a scripted provider and asserts the
governance invariant holds end to end: every tool call passed through permit,
results fed back, and the task completes only after the model answers.
"""
from __future__ import annotations

from taiyi.gateway import build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import TaskState


def test_gateway_runs_agent_mode_with_provider():
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-m", "done"])]),
        LLMResponse(text="Committed."),
    ])
    gw = build_gateway(provider=provider, mode="agent")
    # An AgentRuntime is wired, not a TaskRuntime.
    from taiyi.agent import AgentRuntime
    assert isinstance(gw.runtime, AgentRuntime)

    ctx = gw.submit("commit my changes", scenario="dev.git")
    assert ctx.state is TaskState.SIMULATED
    assert [s.step.tool for s in ctx.executed_steps] == ["shell:git status", "shell:git commit"]
    assert ctx.final_output == "Committed."


def test_gateway_falls_back_to_workflow_without_provider():
    # mode=agent but no provider → must not crash; falls back to workflow so the
    # system still runs offline rather than refusing to start.
    gw = build_gateway(mode="agent")
    from taiyi.runtime import TaskRuntime
    assert isinstance(gw.runtime, TaskRuntime)


def test_gateway_agent_suspends_and_resumes():
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git push", ["origin", "main"])]),
        LLMResponse(text="Pushed."),
    ])
    # No validator: the point of this test is the permit/suspend/resume path,
    # not the check phase (a single push would not satisfy dev.git's checks).
    # validator=False requests "no check phase"; None would mean "default validator".
    gw = build_gateway(provider=provider, mode="agent", validator=False)
    ctx = gw.submit("push", scenario="dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert ctx.approval_id is not None

    resumed = gw.resume(ctx.approval_id, approve=True)
    assert resumed.state is TaskState.SIMULATED
    assert [s.step.tool for s in resumed.executed_steps] == ["shell:git push"]
