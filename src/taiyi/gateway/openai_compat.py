"""OpenAI-compatible translation.

Maps a chat-completions request onto a Taiyi task and shapes the result like an
OpenAI response, so existing OpenAI clients can talk to the gateway unmodified.
A `taiyi` block carries the governance-relevant detail (state, scenario, approval).
"""
from __future__ import annotations

import time

from taiyi.runtime import TaskContext, TaskState


def last_user_message(messages: list[dict]) -> str:
    return next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")


def _content(ctx: TaskContext) -> str:
    if ctx.state is TaskState.COMPLETED:
        return ctx.final_output or "(completed)"
    if ctx.state is TaskState.NEEDS_REVIEW:
        return f"[needs human review] {ctx.final_output}"
    if ctx.state is TaskState.REJECTED:
        return f"[rejected by governance] {ctx.final_output}"
    return f"[{ctx.state.value}] {ctx.error or ctx.final_output or ''}"


def to_openai_response(ctx: TaskContext, model: str) -> dict:
    return {
        "id": f"chatcmpl-{ctx.task_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _content(ctx)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "taiyi": {
            "task_id": ctx.task_id,
            "state": ctx.state.value,
            "scenario": ctx.scenario,
            "approval_id": ctx.approval_id,
        },
    }
