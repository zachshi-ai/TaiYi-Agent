"""Provider-agnostic LLM types and interface.

Kept deliberately small: a provider takes messages (and the names of tools it is
allowed to call) and returns text and/or tool calls. Anthropic, OpenAI-compatible,
and Ollama providers would each implement ``LLMProvider`` behind this same shape.

``DEFAULT_LIVE_MODEL`` is the model a live provider should default to when one is
eventually wired up; it is documentation only here — nothing in this module makes
a network call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# When a live provider is enabled, default to a current, capable Claude model.
# Configurable per deployment; unused until a live provider is built.
DEFAULT_LIVE_MODEL = "claude-fable-5"


@dataclass(frozen=True)
class ToolCall:
    """A tool the model wants to invoke. Still subject to a governance permit."""

    tool: str
    args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LLMMessage:
    role: str  # system | user | assistant | tool
    content: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = "offline"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(
        self, messages: list[LLMMessage], *, tools: list[str] | None = None
    ) -> LLMResponse: ...
