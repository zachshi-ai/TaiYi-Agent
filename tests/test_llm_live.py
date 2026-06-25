"""Tests for the live OpenAI-compatible adapter.

Uses httpx.MockTransport so no real network is touched. Verifies request shape,
response parsing (native tool_calls + text fallback), Authorization handling, and
that make_provider wires config -> provider correctly.
"""
from __future__ import annotations

import json

import httpx
import pytest

from taiyi.config import TaiyiConfig
from taiyi.llm import OpenAICompatProvider, make_provider
from taiyi.llm.base import LLMMessage


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _provider_with_mock(handler, *, api_key=None, model="m1"):
    return OpenAICompatProvider(
        base_url="http://x/v1", model=model, api_key=api_key,
        transport=_mock_transport(handler),
    )


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
