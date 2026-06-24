"""Live LLM provider — the OpenAI-compatible adapter, plus the wiring factory.

This is a *real* adapter, not a skeleton: ``OpenAICompatProvider.complete()``
issues an HTTP POST to any OpenAI-compatible ``/chat/completions`` endpoint and
parses the response. One adapter covers Ollama (``http://localhost:11434/v1``,
no key), DeepSeek, 智谱, Moonshot, OpenAI, SiliconFlow, and any other service
that speaks the OpenAI chat protocol — the only difference between them is the
``base_url``/``model``/``api_key`` you configure.

Design notes:
* Uses ``httpx`` (pure Python) rather than the ``openai`` SDK, so there is no
  heavyweight dependency and the request/response shape is fully under our
  control. httpx is an opt-in ``[live]`` extra.
* The API key lives in the config file (gitignored) — never in git, never echoed
  back by the config endpoint. An empty key (local Ollama) simply omits the
  Authorization header.
* Tool calls: OpenAI function-calling is spotty across providers (Ollama models
  vary). So the adapter prefers the native ``tool_calls`` field when present, and
  otherwise parses a ``tool: <name> <args...>`` line from the model's text. The
  AgentRuntime's ReAct loop works with either — it just reads ``tool_calls``.
* Failures surface as exceptions the runtime catches into a FAILED task — the
  adapter never fabricates a response (that would break the governance invariant).
"""
from __future__ import annotations

import json
import re
from typing import Any

from taiyi.llm.base import DEFAULT_LIVE_MODEL, LLMMessage, LLMProvider, LLMResponse, ToolCall

# Matches the tool-call convention the system prompt teaches the model to emit:
#   tool: shell:git status
#   tool: notify:feishu --msg hello world
_TOOL_LINE = re.compile(r"^\s*tool:\s*(\S+)(?:\s+(.*))?$", re.MULTILINE)


def _messages_to_openai(messages: list[LLMMessage]) -> list[dict]:
    """taiyi LLMMessage -> OpenAI {role, content}.

    taiyi uses a ``system`` role for both the system prompt and the scenario
    injection; OpenAI accepts multiple system messages, so we pass them through.
    """
    return [{"role": m.role, "content": m.content} for m in messages]


def _parse_tool_calls_from_text(text: str) -> list[ToolCall]:
    """Fall back to text parsing when the provider returns no native tool_calls.

    The system prompt asks the model to emit ``tool: <name> <args...>`` on its own
    line when it wants to call a tool. We split args on whitespace (taiyi tool
    args are string tokens). Only the first such line is honored — the ReAct loop
    calls one tool per turn.
    """
    m = _TOOL_LINE.search(text)
    if not m:
        return []
    tool = m.group(1)
    rest = (m.group(2) or "").strip()
    args = rest.split() if rest else []
    return [ToolCall(tool=tool, args=args)]


class OpenAICompatProvider(LLMProvider):
    """Calls any OpenAI-compatible /chat/completions endpoint via httpx."""

    def __init__(
        self,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        *,
        name: str = "openai_compat",
        timeout: float = 60.0,
        transport: Any = None,  # injected for tests (httpx MockTransport)
    ):
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_LIVE_MODEL
        self._api_key = api_key or None
        self._timeout = timeout
        self._transport = transport  # None in production → real network

    def complete(
        self, messages: list[LLMMessage], *, tools: list[str] | None = None
    ) -> LLMResponse:
        import httpx  # local import: offline deployments never need httpx

        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body: dict = {
            "model": self._model,
            "messages": _messages_to_openai(messages),
            "temperature": 0,
        }
        # When the caller lists tool names, describe them in the prompt so models
        # without native function-calling can still emit a tool line. (Native
        # tool-calling is attempted only if the provider supports it; we don't
        # send a `tools` schema to keep this portable across Ollama models.)
        if tools:
            body["messages"] = _with_tool_hint(body["messages"], tools)

        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"LLM endpoint {url} returned {e.response.status_code}: "
                               f"{e.response.text[:300]}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"LLM request to {url} failed: {e}") from e

        data = resp.json()
        return self._to_response(data, self._model)

    @staticmethod
    def _to_response(data: dict, model: str) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        text = msg.get("content") or ""
        tool_calls: list[ToolCall] = []

        # Prefer native tool_calls when the provider returns them.
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            name = fn.get("name")
            if not name:
                continue
            raw_args = fn.get("arguments", "")
            args = _coerce_args(raw_args)
            tool_calls.append(ToolCall(tool=name, args=args))

        # Fall back to parsing a `tool:` line from the text.
        if not tool_calls and text:
            tool_calls = _parse_tool_calls_from_text(text)

        return LLMResponse(text=text, tool_calls=tool_calls, model=model)


def _coerce_args(raw: Any) -> list[str]:
    """Normalise OpenAI function arguments (a JSON string) to taiyi's list[str]."""
    if isinstance(raw, list):
        return [str(a) for a in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(a) for a in parsed]
            if isinstance(parsed, dict):
                # A single-object arg: flatten values to positional tokens.
                return [str(v) for v in parsed.values()]
            return [str(parsed)]
        except json.JSONDecodeError:
            return raw.split()
    return []


def _with_tool_hint(messages: list[dict], tools: list[str]) -> list[dict]:
    """Append a system note describing available tools and the call syntax.

    Kept as a separate message so the model's own system/scenario context is
    untouched. This is what makes the adapter portable: a model that supports
    native function-calling ignores the hint and returns tool_calls; one that
    doesn't follows the hint and emits a `tool:` line we parse.
    """
    hint = (
        "When you want to call a tool, reply with a single line in the form "
        "`tool: <name> <arg1> <arg2> ...` and nothing else. Available tools: "
        + ", ".join(tools)
        + ". If you have the answer (no tool needed), reply with the answer text."
    )
    return messages + [{"role": "system", "content": hint}]


def make_provider(cfg) -> LLMProvider | None:
    """Construct the LLM provider selected by ``cfg``.

    Returns ``None`` for ``offline`` (the default) — the gateway then falls back
    to the keyword planner and offline providers, so the whole agent loop still
    runs with zero tokens and zero network.

    For ``openai_compat`` / ``ollama`` it returns a live ``OpenAICompatProvider``
    wired to ``cfg.base_url`` / ``cfg.model`` / ``cfg.api_key`` (or the env var
    named by ``cfg.api_key_env``). A live provider with no base_url is a
    configuration error and raises clearly rather than silently failing.
    """
    provider = (getattr(cfg, "provider", "offline") or "offline").lower()
    if provider == "offline":
        return None

    if provider in ("openai_compat", "ollama"):
        base_url = getattr(cfg, "base_url", None)
        if not base_url:
            raise ValueError(
                f"provider={provider!r} requires base_url "
                f"(e.g. http://localhost:11434/v1 for Ollama). Set it in taiyi.yaml."
            )
        model = getattr(cfg, "model", None)
        # api_key_env (a variable name) takes precedence over api_key (a value),
        # so a deployment can avoid storing the key in the file.
        import os

        key = getattr(cfg, "api_key", None)
        env_name = getattr(cfg, "api_key_env", None)
        if env_name:
            key = os.environ.get(env_name) or key
        return OpenAICompatProvider(
            base_url=base_url, model=model, api_key=key,
            name=f"live:{provider}",
        )

    # Unknown provider — refuse rather than fabricate.
    raise ValueError(
        f"unknown provider {provider!r}; use offline | openai_compat | ollama"
    )


__all__ = ["OpenAICompatProvider", "make_provider"]
