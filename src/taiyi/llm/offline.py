"""Deterministic offline providers — no network, no tokens.

``ScriptedProvider`` replays a fixed queue of responses. It is the workhorse for
CI and for reproducing a captured trace, and it is how we simulate adversarial
model behaviour (a prompt-injected model proposing a red-line tool call) without
any live model.

``KeywordOfflineProvider`` turns a prompt into tool calls using the same keyword
routing as ``KeywordPlanner`` — a stand-in for a real model's tool selection, made
deterministic, so the LLM-driven planning loop can be demonstrated end-to-end.
"""
from __future__ import annotations

from taiyi.llm.base import LLMMessage, LLMResponse, ToolCall
from taiyi.scheduler.planner import KeywordPlanner


class ScriptedProvider:
    name = "offline:scripted"

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self._i = 0

    def complete(
        self, messages: list[LLMMessage], *, tools: list[str] | None = None
    ) -> LLMResponse:
        if self._i >= len(self._responses):
            return LLMResponse(text="(no more scripted responses)", model=self.name)
        resp = self._responses[self._i]
        self._i += 1
        return resp


class KeywordOfflineProvider:
    name = "offline:keyword"

    def __init__(self):
        self._planner = KeywordPlanner()

    def complete(
        self, messages: list[LLMMessage], *, tools: list[str] | None = None
    ) -> LLMResponse:
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        plan = self._planner.plan(prompt, scenario="default")
        calls = [ToolCall(tool=s.tool, args=list(s.args)) for s in plan.steps]
        return LLMResponse(text=plan.rationale, tool_calls=calls, model=self.name)
