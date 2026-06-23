"""Channels — the adapter interface for reaching Taiyi over a messaging platform.

A channel translates a platform's transport to/from a Taiyi task; it contains no
agent logic. The base `ChannelAdapter` already works (`InProcessChannel` is the
reference). A real Feishu/Telegram/Discord adapter subclasses it and overrides only
how raw events are received and replies are sent — "a new channel is one file."
Those live adapters need platform SDKs and credentials and are a deferred opt-in.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OutboundMessage:
    text: str
    state: str
    task_id: str


class ChannelAdapter:
    name = "base"

    def __init__(self, gateway):
        self.gateway = gateway

    def handle_text(
        self, text: str, *, user_id: str = "u1", session_id: str = "s1", scenario: str | None = None
    ) -> OutboundMessage:
        ctx = self.gateway.submit(text, scenario=scenario, user_id=user_id, session_id=session_id)
        return self.format(ctx)

    def format(self, ctx) -> OutboundMessage:
        return OutboundMessage(
            text=ctx.final_output or ctx.state.value,
            state=ctx.state.value,
            task_id=ctx.task_id,
        )


class InProcessChannel(ChannelAdapter):
    """Reference channel: call the gateway directly, in-process (tests, embedding)."""

    name = "inprocess"


__all__ = ["ChannelAdapter", "InProcessChannel", "OutboundMessage"]
