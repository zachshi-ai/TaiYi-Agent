"""LLM provider layer (offline-first).

A provider-agnostic interface plus deterministic offline providers, so the whole
agent loop can be exercised with zero tokens and zero network. The live
OpenAI-compatible adapter (``OpenAICompatProvider``) implements the same
``LLMProvider`` interface and covers Ollama / DeepSeek / 智谱 / Moonshot / OpenAI
through one ``base_url`` — opt in via config + the ``[live]`` extra (httpx).

The point this module proves: **whatever a model proposes still passes through
governance.** An LLM-driven planner cannot grant clearance, so even a
prompt-injected request to run a red-line action is denied. When you switch on a
live provider, that property does not change — only where the tool calls come from.
"""

from taiyi.llm.base import (
    DEFAULT_LIVE_MODEL,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ToolCall,
)
from taiyi.llm.live import OpenAICompatProvider, make_provider
from taiyi.llm.offline import KeywordOfflineProvider, ScriptedProvider

__all__ = [
    "DEFAULT_LIVE_MODEL",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "KeywordOfflineProvider",
    "ScriptedProvider",
    "OpenAICompatProvider",
    "make_provider",
]
