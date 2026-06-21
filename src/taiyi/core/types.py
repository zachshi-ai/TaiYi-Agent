"""Shared types for the governance boundary.

These types define the *contract* between the scheduler (the decision-maker) and
the governance engine (the neutral referee). The scheduler may only ask; the
governance engine alone answers. Keeping the contract small and explicit is what
lets the two run as separate processes later without changing callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """The governance engine's answer to an execution request."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Severity(str, Enum):
    """How a rule is enforced when it fires."""

    RED_LINE = "red_line"   # hard, fail-closed
    ADVISORY = "advisory"   # soft, informs but does not block


class Action(str, Enum):
    """What `on_fail` does. Maps onto a Verdict."""

    BLOCK = "block"                          # -> DENY
    REQUEST_CONFIRMATION = "request_confirmation"  # -> NEEDS_REVIEW
    WARN = "warn"                            # -> ALLOW (with advisory attached)


class Trigger(str, Enum):
    """When a rule is evaluated in a task's lifecycle."""

    PRE_EXECUTION = "pre_execution"
    POST_EXECUTION = "post_execution"
    STEP_GATE = "step_gate"


class Domain(str, Enum):
    """Risk domain a rule belongs to. Used for reporting and precedence tuning."""

    SECURITY = "security"
    AUTHORSHIP = "authorship"
    COMPLIANCE = "compliance"
    SAFETY = "safety"
    BUSINESS = "business"
    OPTIMIZATION = "optimization"


@dataclass(frozen=True)
class PermitRequest:
    """A scheduler asking permission to run one tool call.

    The request is data only — it carries no ability to execute. The scheduler
    holds no high-risk execution capability of its own; it must obtain a permit.
    """

    tool: str                       # tool id, e.g. "shell:git commit"
    args: list[str] = field(default_factory=list)
    scenario: str = "default"
    actor: str = "scheduler"        # which agent is asking
    user_id: str = "unknown"
    task_id: str | None = None


@dataclass
class PermitResponse:
    """The governance engine's verdict, with evidence for audit/replay."""

    verdict: Verdict
    reason: str
    evidence: str = ""
    matched_rule_id: str | None = None
    precedence: int | None = None
    advisories: list[str] = field(default_factory=list)
    approval_id: str | None = None  # set when verdict == NEEDS_REVIEW

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evidence": self.evidence,
            "matched_rule_id": self.matched_rule_id,
            "precedence": self.precedence,
            "advisories": list(self.advisories),
            "approval_id": self.approval_id,
        }


def build_full_call(tool: str, args: list[str]) -> str:
    """Reconstruct the full intent string for matching.

    Lesson from the Phase 0 demo: red lines must match against the *whole call*,
    not individual tokens — splitting `rm -rf /` across args defeats the pattern.
    """
    return (tool + " " + " ".join(args)).strip()
