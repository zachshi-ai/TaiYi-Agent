"""Typed model-request failures shared by adapters and the durable runtime."""
from __future__ import annotations

from enum import Enum
from typing import Any


class LLMErrorKind(str, Enum):
    """Failure categories that carry an explicit retry contract."""

    LLM_TIMEOUT = "LLM_TIMEOUT"  # legacy/custom provider with no phase detail
    LLM_CONNECT_TIMEOUT = "LLM_CONNECT_TIMEOUT"
    LLM_FIRST_TOKEN_TIMEOUT = "LLM_FIRST_TOKEN_TIMEOUT"
    LLM_STREAM_IDLE_TIMEOUT = "LLM_STREAM_IDLE_TIMEOUT"
    LLM_HARD_TIMEOUT = "LLM_HARD_TIMEOUT"
    LLM_TRANSPORT_ERROR = "LLM_TRANSPORT_ERROR"
    LLM_SERVER_ERROR = "LLM_SERVER_ERROR"
    LLM_AUTH_ERROR = "LLM_AUTH_ERROR"
    LLM_PROTOCOL_ERROR = "LLM_PROTOCOL_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"


class LLMRequestError(RuntimeError):
    """A safe, classifiable provider failure.

    The message intentionally excludes API keys and request bodies.  Callers can
    decide whether to retry without scraping provider-specific exception text.
    """

    def __init__(
        self,
        kind: LLMErrorKind | str,
        message: str,
        *,
        retryable: bool,
        phase: str | None = None,
        provider: str | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        self.kind = LLMErrorKind(kind)
        self.retryable = bool(retryable)
        self.phase = phase
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)

    @property
    def failure_kind(self) -> str:
        return self.kind.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_kind": self.kind.value,
            "retryable": self.retryable,
            "phase": self.phase,
            "provider": self.provider,
            "status_code": self.status_code,
            "retry_after": self.retry_after,
            "error": str(self),
        }


def coerce_llm_error(exc: Exception, *, provider: str | None = None) -> LLMRequestError:
    """Normalize custom-provider exceptions without losing legacy behavior."""

    if isinstance(exc, LLMRequestError):
        return exc
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.casefold():
        return LLMRequestError(
            LLMErrorKind.LLM_TIMEOUT,
            str(exc) or "model request timed out",
            retryable=True,
            phase="unknown",
            provider=provider,
        )
    text = str(exc).casefold()
    if "context" in text and any(word in text for word in ("overflow", "length", "window")):
        return LLMRequestError(
            LLMErrorKind.CONTEXT_OVERFLOW,
            str(exc),
            retryable=False,
            provider=provider,
        )
    if "rate limit" in text or "status 429" in text:
        return LLMRequestError(
            LLMErrorKind.RATE_LIMIT,
            str(exc),
            retryable=True,
            provider=provider,
        )
    return LLMRequestError(
        LLMErrorKind.LLM_PROTOCOL_ERROR,
        str(exc) or type(exc).__name__,
        retryable=False,
        provider=provider,
    )


__all__ = ["LLMErrorKind", "LLMRequestError", "coerce_llm_error"]
