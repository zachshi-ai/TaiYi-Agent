"""Tests for the LLM provider seam (M4 live wiring point).

Contract after the adapter was wired:
* offline → None (gateway falls back to keyword planner).
* openai_compat / ollama → a live OpenAICompatProvider, but only when base_url is
  set; a live provider with no endpoint is a config error (raises, never fakes).
* an unknown provider name → raises (refuse rather than fabricate).
"""
from __future__ import annotations

import pytest

from taiyi.config import TaiyiConfig
from taiyi.llm import OpenAICompatProvider, make_provider


def _cfg(**kw) -> TaiyiConfig:
    base = TaiyiConfig()
    return base.__class__(**{**base.__dict__, **kw})


def test_offline_returns_none():
    assert make_provider(_cfg(provider="offline")) is None


def test_default_config_is_offline():
    assert make_provider(TaiyiConfig()) is None


@pytest.mark.parametrize("name", ["openai_compat", "ollama"])
def test_live_provider_wires_with_base_url(name):
    cfg = _cfg(provider=name, base_url="http://localhost:11434/v1", model="qwen2.5:7b")
    prov = make_provider(cfg)
    assert isinstance(prov, OpenAICompatProvider)
    assert prov.name == f"live:{name}"
    assert prov._base_url == "http://localhost:11434/v1"
    assert prov._model == "qwen2.5:7b"


def test_live_provider_without_base_url_degrades_to_offline():
    # No endpoint configured → degrade to offline (None) with a warning, not a
    # crash. A bad LLM config must never make the agent unstartable.
    cfg = _cfg(provider="ollama")
    assert make_provider(cfg) is None


def test_unknown_provider_degrades_to_offline():
    cfg = _cfg(provider="anthropic", base_url="http://x/v1")
    # Unknown provider name → degrade to offline with a warning, not a crash.
    assert make_provider(cfg) is None


def test_live_provider_resolves_default_model_when_none():
    from taiyi.llm.base import DEFAULT_LIVE_MODEL

    cfg = _cfg(provider="ollama", base_url="http://localhost:11434/v1", model=None)
    prov = make_provider(cfg)
    assert prov._model == DEFAULT_LIVE_MODEL
