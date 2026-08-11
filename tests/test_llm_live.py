"""Tests for the live OpenAI-compatible adapter.

Uses httpx.MockTransport so no real network is touched. Verifies request shape,
response parsing (native tool_calls + text fallback), Authorization handling, and
that make_provider wires config -> provider correctly.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from taiyi.config import TaiyiConfig
from taiyi.llm import LLMErrorKind, LLMRequestError, OpenAICompatProvider, make_provider
from taiyi.llm.base import LLMMessage


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _provider_with_mock(handler, *, api_key=None, model="m1"):
    return OpenAICompatProvider(
        base_url="http://x/v1", model=model, api_key=api_key,
        transport=_mock_transport(handler),
    )


class DelayedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for delay, chunk in self.chunks:
            await asyncio.sleep(delay)
            yield chunk

    async def aclose(self):
        return None


def test_complete_sends_openai_shape_and_parses_text():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "tool: shell:git status"}}]
        })

    prov = _provider_with_mock(handler)
    resp = prov.complete([LLMMessage("user", "do it")], tools=["shell:git"])

    assert seen["url"] == "http://x/v1/chat/completions"
    assert seen["body"]["model"] == "m1"
    assert seen["body"]["stream"] is True
    assert seen["body"]["messages"][0] == {"role": "user", "content": "do it"}
    # No key → no Authorization header.
    assert "authorization" not in seen["headers"]
    # Text fallback parsing produced a tool call.
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].tool == "shell:git"
    assert resp.tool_calls[0].args == ["status"]


def test_complete_parses_native_tool_calls():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "",
            "tool_calls": [{"function": {"name": "shell:git", "arguments": "[\"commit\"]"}}],
        }}]})

    prov = _provider_with_mock(handler)
    resp = prov.complete([LLMMessage("user", "commit")])
    assert resp.tool_calls[0].tool == "shell:git"
    assert resp.tool_calls[0].args == ["commit"]


def test_api_key_sent_as_bearer():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    prov = _provider_with_mock(handler, api_key="sk-secret")
    prov.complete([LLMMessage("user", "hi")])
    assert seen["auth"] == "Bearer sk-secret"


def test_http_error_surfaced_not_faked():
    def handler(req):
        return httpx.Response(500, text="upstream down")

    prov = _provider_with_mock(handler)
    # The adapter must raise, never fabricate a response.
    with pytest.raises(RuntimeError, match="500"):
        prov.complete([LLMMessage("user", "hi")])


def test_streaming_response_reassembles_content_and_tool_arguments():
    body = [
        (0, b'data: {"model":"m-stream","choices":[{"delta":{"content":"hel"}}]}\n\n'),
        (0, b'data: {"choices":[{"delta":{"content":"lo","tool_calls":[{"index":0,"function":{"name":"echo","arguments":"[\\"x"}}]}}]}\n\n'),
        (0, b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"]"}}]}}]}\n\n'),
        (0, b"data: [DONE]\n\n"),
    ]

    def handler(req):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=DelayedStream(body),
        )

    response = _provider_with_mock(handler).complete([LLMMessage("user", "go")])
    assert response.text == "hello"
    assert response.model == "m-stream"
    assert response.tool_calls[0].tool == "echo"
    assert response.tool_calls[0].args == ["x"]


def test_first_token_stream_idle_and_hard_deadlines_are_distinct():
    def first_handler(req):
        return httpx.Response(200, stream=DelayedStream([
            (0.05, b'{"choices":[{"message":{"content":"late"}}]}'),
        ]))

    first = OpenAICompatProvider(
        "http://x/v1", transport=_mock_transport(first_handler),
        first_token_timeout=0.01, stream_idle_timeout=1, hard_timeout=1,
    )
    with pytest.raises(LLMRequestError) as first_error:
        first.complete([LLMMessage("user", "go")])
    assert first_error.value.kind is LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT

    def idle_handler(req):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=DelayedStream([
                (0, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
                (0.05, b"data: [DONE]\n\n"),
            ]),
        )

    idle = OpenAICompatProvider(
        "http://x/v1", transport=_mock_transport(idle_handler),
        first_token_timeout=1, stream_idle_timeout=0.01, hard_timeout=1,
    )
    with pytest.raises(LLMRequestError) as idle_error:
        idle.complete([LLMMessage("user", "go")])
    assert idle_error.value.kind is LLMErrorKind.LLM_STREAM_IDLE_TIMEOUT

    hard = OpenAICompatProvider(
        "http://x/v1", transport=_mock_transport(idle_handler),
        first_token_timeout=1, stream_idle_timeout=1, hard_timeout=0.01,
    )
    with pytest.raises(LLMRequestError) as hard_error:
        hard.complete([LLMMessage("user", "go")])
    assert hard_error.value.kind is LLMErrorKind.LLM_HARD_TIMEOUT


def test_connect_rate_limit_auth_and_context_failures_are_typed():
    def connect_handler(req):
        raise httpx.ConnectTimeout("no route")

    with pytest.raises(LLMRequestError) as connect_error:
        _provider_with_mock(connect_handler).complete([LLMMessage("user", "go")])
    assert connect_error.value.kind is LLMErrorKind.LLM_CONNECT_TIMEOUT
    assert connect_error.value.retryable is True

    cases = [
        (429, {"Retry-After": "3"}, "busy", LLMErrorKind.RATE_LIMIT, True, 3.0),
        (401, {}, "bad key", LLMErrorKind.LLM_AUTH_ERROR, False, None),
        (400, {}, "context length exceeded", LLMErrorKind.CONTEXT_OVERFLOW, False, None),
        (500, {}, "down", LLMErrorKind.LLM_SERVER_ERROR, True, None),
    ]
    for status, headers, body, kind, retryable, retry_after in cases:
        provider = _provider_with_mock(
            lambda req, s=status, h=headers, b=body: httpx.Response(s, headers=h, text=b)
        )
        with pytest.raises(LLMRequestError) as failure:
            provider.complete([LLMMessage("user", "go")])
        assert failure.value.kind is kind
        assert failure.value.retryable is retryable
        assert failure.value.retry_after == retry_after


def test_make_provider_offline_returns_none():
    cfg = TaiyiConfig(provider="offline")
    assert make_provider(cfg) is None


def test_make_provider_ollama_wires_base_url_and_no_key():
    cfg = TaiyiConfig(provider="ollama", base_url="http://localhost:11434/v1",
                     model="qwen2.5:7b", api_key=None)
    prov = make_provider(cfg)
    assert isinstance(prov, OpenAICompatProvider)
    assert prov._base_url == "http://localhost:11434/v1"
    assert prov._model == "qwen2.5:7b"
    assert prov._api_key is None  # Ollama: no key


def test_make_provider_openai_compat_with_key():
    cfg = TaiyiConfig(provider="openai_compat", base_url="https://api.deepseek.com/v1",
                     model="deepseek-chat", api_key="sk-x")
    prov = make_provider(cfg)
    assert prov._api_key == "sk-x"
    assert prov._base_url == "https://api.deepseek.com/v1"


def test_make_provider_missing_base_url_degrades_to_offline():
    # A live provider with no base_url must NOT crash the gateway — it degrades
    # to offline (returns None) with a warning, so a bad LLM config never makes
    # the agent unstartable.
    cfg = TaiyiConfig(provider="ollama")  # no base_url
    assert make_provider(cfg) is None


def test_make_provider_missing_httpx_degrades(monkeypatch):
    # Simulate httpx not installed → degrade to offline, not crash.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "httpx":
            raise ImportError("no httpx")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cfg = TaiyiConfig(provider="ollama", base_url="http://localhost:11434/v1")
    assert make_provider(cfg) is None


def test_make_provider_api_key_env_overrides_value(monkeypatch):
    monkeypatch.setenv("MY_KEY", "sk-from-env")
    cfg = TaiyiConfig(provider="openai_compat", base_url="http://x/v1",
                     api_key="sk-in-file", api_key_env="MY_KEY")
    prov = make_provider(cfg)
    assert prov._api_key == "sk-from-env"  # env var wins


def test_make_provider_wires_phase_timeouts():
    cfg = TaiyiConfig(
        provider="openai_compat",
        base_url="http://x/v1",
        llm_connect_timeout=1,
        llm_first_token_timeout=2,
        llm_stream_idle_timeout=3,
        llm_hard_timeout=4,
    )
    provider = make_provider(cfg)
    assert provider._connect_timeout == 1
    assert provider._first_token_timeout == 2
    assert provider._stream_idle_timeout == 3
    assert provider._hard_timeout == 4

    with pytest.raises(ValueError, match="positive"):
        OpenAICompatProvider("http://x/v1", connect_timeout=0)
