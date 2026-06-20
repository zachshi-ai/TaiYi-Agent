"""LLM provider layer (offline-first).

A provider-agnostic interface plus deterministic offline providers, so the whole
agent loop can be exercised with zero tokens and zero network. Live providers
(Anthropic / OpenAI-compatible / Ollama) implement the same ``LLMProvider``
interface and are a later opt-in — they require an API key and a token budget and
are intentionally not built here.

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
from taiyi.llm.offline import KeywordOfflineProvider, ScriptedProvider

__all__ = [
    "DEFAULT_LIVE_MODEL",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "KeywordOfflineProvider",
    "ScriptedProvider",
]
