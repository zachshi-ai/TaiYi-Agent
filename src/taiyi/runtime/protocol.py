"""Durable run protocol primitives.

``TaskState`` describes the user-visible outcome. ``RunPhase`` describes what
the harness is actually waiting on. Keeping them separate prevents a tool
deadline from being reported as an LLM timeout and prevents a suspended turn
from being mistaken for a fully settled run.
"""
from __future__ import annotations

import subprocess
from enum import Enum


class RunPhase(str, Enum):
    READY = "READY"
    PARSING = "PARSING"
    INDEXING = "INDEXING"
    PLANNING = "PLANNING"
    LLM_WAITING = "LLM_WAITING"
    AWAITING_PERMIT = "AWAITING_PERMIT"
    TOOL_RUNNING = "TOOL_RUNNING"
    TOOL_RESULT = "TOOL_RESULT"
    VALIDATING = "VALIDATING"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RETRY_BACKOFF = "RETRY_BACKOFF"
    COMPACTING = "COMPACTING"
    RECOVERING = "RECOVERING"
    SETTLED = "SETTLED"

    @property
    def is_settled(self) -> bool:
        return self is RunPhase.SETTLED


class FailureKind(str, Enum):
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_CONNECT_TIMEOUT = "LLM_CONNECT_TIMEOUT"
    LLM_FIRST_TOKEN_TIMEOUT = "LLM_FIRST_TOKEN_TIMEOUT"
    LLM_STREAM_IDLE_TIMEOUT = "LLM_STREAM_IDLE_TIMEOUT"
    LLM_HARD_TIMEOUT = "LLM_HARD_TIMEOUT"
    LLM_TRANSPORT_ERROR = "LLM_TRANSPORT_ERROR"
    LLM_SERVER_ERROR = "LLM_SERVER_ERROR"
    LLM_AUTH_ERROR = "LLM_AUTH_ERROR"
    LLM_PROTOCOL_ERROR = "LLM_PROTOCOL_ERROR"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_IDLE_TIMEOUT = "TOOL_IDLE_TIMEOUT"
    TOOL_HARD_TIMEOUT = "TOOL_HARD_TIMEOUT"
    TOOL_STARTUP_ERROR = "TOOL_STARTUP_ERROR"
    TOOL_EXIT_NONZERO = "TOOL_EXIT_NONZERO"
    TOOL_SIGNAL = "TOOL_SIGNAL"
    TOOL_CANCELLED = "TOOL_CANCELLED"
    TOOL_LOST = "TOOL_LOST"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    RATE_LIMIT = "RATE_LIMIT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    EXTERNAL_FAILURE = "EXTERNAL_FAILURE"
    CHECKPOINT_INCOMPATIBLE = "CHECKPOINT_INCOMPATIBLE"
    INTERNAL = "INTERNAL"


class CheckpointIncompatibleError(RuntimeError):
    """A persisted run cannot be resumed under the current frozen contract."""


def classify_exception(exc: BaseException, phase: RunPhase) -> FailureKind:
    """Classify a failure from the phase in which it actually happened.

    Provider libraries expose different timeout exception classes. The explicit
    phase is therefore the primary authority; exception naming is only used to
    decide whether the failure is timeout-like.
    """

    if isinstance(exc, PermissionError):
        return FailureKind.PERMISSION_DENIED

    explicit = getattr(exc, "failure_kind", None)
    if explicit:
        try:
            return FailureKind(str(explicit))
        except ValueError:
            pass

    timeout_like = isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) or (
        "timeout" in type(exc).__name__.casefold()
    )
    if timeout_like:
        if phase is RunPhase.LLM_WAITING:
            return FailureKind.LLM_TIMEOUT
        if phase is RunPhase.TOOL_RUNNING:
            return FailureKind.TOOL_TIMEOUT

    text = str(exc).casefold()
    if "context" in text and any(word in text for word in ("overflow", "length", "window")):
        return FailureKind.CONTEXT_OVERFLOW
    if "rate limit" in text or "status 429" in text:
        return FailureKind.RATE_LIMIT
    return FailureKind.INTERNAL
