"""Bind model retry events to durable task checkpoints and audit evidence."""
from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from taiyi.llm import ProviderRouter, ResilientProvider
from taiyi.runtime.protocol import RunPhase
from taiyi.runtime.state import TaskState


def task_resilient_provider(
    ctx,
    router: ProviderRouter,
    *,
    record: Callable[..., None],
    audit,
    continuation: dict[str, Any],
    retry_state: dict[str, Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> ResilientProvider:
    """Create a retrying provider whose transient states remain recoverable."""

    state = dict(retry_state or {})

    def persisted_continuation() -> dict[str, Any]:
        return {**continuation, "llm_retry": dict(state)} if state else dict(continuation)

    def observe(event: str, payload: dict[str, Any]) -> None:
        nonlocal state
        attempt = int(payload.get("attempt", 0) or 0)
        deadline_at = payload.get("deadline_at")
        if deadline_at is not None and attempt:
            state = {"attempts_used": max(0, attempt - 1), "deadline_at": deadline_at}

        if event == "llm_attempt_failed":
            state = {"attempts_used": attempt, "deadline_at": deadline_at}
            if ctx.provider_route is not None:
                ctx.provider_route.setdefault("llm_failures", []).append({
                    "attempt": attempt,
                    "provider": (payload.get("selection") or {}).get("provider"),
                    "model": (payload.get("selection") or {}).get("model"),
                    "failure_kind": payload.get("failure_kind"),
                    "status_code": payload.get("status_code"),
                    "retryable": payload.get("retryable"),
                })
        elif event in {"llm_retry_scheduled", "llm_retry_resumed"}:
            state = dict(payload.get("retry_state") or state)
        elif event == "llm_attempt_succeeded" and attempt > 1 and ctx.provider_route is not None:
            selection = payload.get("selection") or {}
            ctx.provider_route["resolved_provider"] = selection.get("provider")
            ctx.provider_route["resolved_model"] = selection.get("model")
            ctx.provider_route["failover"] = (
                selection.get("provider") != ctx.provider_route.get("provider")
                or selection.get("model") != ctx.provider_route.get("model")
            )

        audit_payload = {k: v for k, v in payload.items() if k != "retry_state"}
        if "phase" in audit_payload:
            audit_payload["llm_phase"] = audit_payload.pop("phase")
        audit.append(event, task_id=ctx.task_id, **audit_payload)
        if event == "llm_attempt_started" and attempt <= 1:
            return
        phase = (
            RunPhase.RETRY_BACKOFF
            if event in {"llm_retry_scheduled", "llm_retry_resumed"}
            else RunPhase.LLM_WAITING
        )
        record(
            ctx,
            phase,
            event,
            state=TaskState.PLANNING,
            continuation=persisted_continuation(),
            **audit_payload,
        )

    return ResilientProvider(
        router,
        ctx.policy,
        observer=observe,
        sleep=sleep,
        clock=clock,
        initial_attempts=int(state.get("attempts_used", 0) or 0),
        deadline_at=(float(state["deadline_at"]) if state.get("deadline_at") else None),
        retry_not_before=(
            float(state["retry_not_before"]) if state.get("retry_not_before") else None
        ),
    )


__all__ = ["task_resilient_provider"]
