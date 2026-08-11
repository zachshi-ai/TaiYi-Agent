"""Policy-budgeted retry and provider failover for one model turn.

Only the model request is retried.  A returned response is accepted exactly once
by the caller, outside this component, so tool execution is never replayed here.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from taiyi.llm.base import LLMMessage, LLMResponse
from taiyi.llm.errors import LLMRequestError, coerce_llm_error
from taiyi.llm.router import ProviderRouter, ProviderSelection

RetryObserver = Callable[[str, dict[str, Any]], None]


class ResilientProvider:
    """Expose the provider protocol while applying one task policy's retry budget."""

    def __init__(
        self,
        router: ProviderRouter,
        policy,
        *,
        observer: RetryObserver | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        initial_attempts: int = 0,
        deadline_at: float | None = None,
        retry_not_before: float | None = None,
    ):
        self.router = router
        self.policy = policy
        self.observer = observer or (lambda _event, _payload: None)
        self._sleep = sleep
        self._clock = clock
        self.initial_attempts = max(0, int(initial_attempts))
        self.deadline_at = deadline_at
        self.retry_not_before = retry_not_before
        primary = router.select(policy)
        self.name = primary.provider_name
        self.model = primary.model

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[str] | None = None,
    ) -> LLMResponse:
        candidates = self.router.candidates(self.policy)
        max_attempts = max(1, int(self.policy.max_llm_attempts))
        attempts_used = self.initial_attempts
        deadline_at = self.deadline_at or (
            self._clock() + float(self.policy.llm_retry_budget_seconds)
        )
        if attempts_used >= max_attempts or self._clock() >= deadline_at:
            raise LLMRequestError(
                "LLM_TIMEOUT",
                "model retry budget was already exhausted before recovery",
                retryable=True,
                phase="retry_budget",
            )
        if attempts_used and self.retry_not_before:
            resumed_delay = max(0.0, float(self.retry_not_before) - self._clock())
            if resumed_delay:
                if self._clock() + resumed_delay > deadline_at:
                    raise LLMRequestError(
                        "LLM_TIMEOUT",
                        "persisted model backoff exceeds the remaining retry budget",
                        retryable=True,
                        phase="retry_budget",
                    )
                resumed_state = {
                    "attempts_used": attempts_used,
                    "deadline_at": deadline_at,
                    "retry_not_before": self.retry_not_before,
                }
                self.observer("llm_retry_resumed", {
                    "attempt": attempts_used,
                    "next_attempt": attempts_used + 1,
                    "delay_seconds": resumed_delay,
                    "retry_state": resumed_state,
                })
                self._sleep(resumed_delay)

        while attempts_used < max_attempts:
            attempt = attempts_used + 1
            selected = self._candidate_for_attempt(candidates, attempt)
            self.observer("llm_attempt_started", {
                "attempt": attempt,
                "max_attempts": max_attempts,
                "deadline_at": deadline_at,
                "selection": selected.to_dict(),
            })
            started = self._clock()
            try:
                response = selected.provider.complete(messages, tools=tools)
            except Exception as exc:  # provider failures, never process-exit signals
                error = coerce_llm_error(exc, provider=selected.provider_name)
                attempts_used = attempt
                self.observer("llm_attempt_failed", {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "duration_seconds": max(0.0, self._clock() - started),
                    "deadline_at": deadline_at,
                    "selection": selected.to_dict(),
                    **error.to_dict(),
                })
                if not error.retryable or attempts_used >= max_attempts:
                    raise error from exc

                next_selection = self._candidate_for_attempt(candidates, attempt + 1)
                remaining = deadline_at - self._clock()
                delay = self._retry_delay(attempt, error)
                # Retry-After is a lower bound, not a hint to retry too early.
                if remaining <= 0 or delay > remaining:
                    self.observer("llm_retry_budget_exhausted", {
                        "attempt": attempt,
                        "remaining_seconds": max(0.0, remaining),
                        "required_delay_seconds": delay,
                        "failure_kind": error.failure_kind,
                    })
                    raise error from exc
                failover = self._selection_key(selected) != self._selection_key(next_selection)
                state = {
                    "attempts_used": attempts_used,
                    "deadline_at": deadline_at,
                    "retry_not_before": self._clock() + delay,
                }
                self.observer("llm_retry_scheduled", {
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "delay_seconds": delay,
                    "failover": failover,
                    "from_selection": selected.to_dict(),
                    "to_selection": next_selection.to_dict(),
                    "failure_kind": error.failure_kind,
                    "retry_state": state,
                })
                if delay:
                    self._sleep(delay)
                continue

            self.observer("llm_attempt_succeeded", {
                "attempt": attempt,
                "duration_seconds": max(0.0, self._clock() - started),
                "selection": selected.to_dict(),
                "response_model": response.model,
            })
            return response

        raise AssertionError("unreachable model retry loop")

    def _candidate_for_attempt(
        self,
        candidates: list[ProviderSelection],
        attempt: int,
    ) -> ProviderSelection:
        primary_attempts = max(1, int(self.policy.llm_primary_attempts))
        if attempt <= primary_attempts or len(candidates) == 1:
            return candidates[0]
        return candidates[1 + ((attempt - primary_attempts - 1) % (len(candidates) - 1))]

    def _retry_delay(self, attempt: int, error: LLMRequestError) -> float:
        base = float(self.policy.llm_retry_backoff_seconds)
        ceiling = float(self.policy.llm_retry_max_backoff_seconds)
        delay = min(ceiling, base * (2 ** max(0, attempt - 1)))
        if error.retry_after is not None:
            delay = max(delay, max(0.0, float(error.retry_after)))
        return delay

    @staticmethod
    def _selection_key(selection: ProviderSelection) -> tuple[int, str | None]:
        return id(selection.provider), selection.model


__all__ = ["ResilientProvider", "RetryObserver"]
