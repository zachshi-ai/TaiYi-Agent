"""Expert agents for multi-agent review.

The design's thesis: *collaboration without gates is no collaboration.* So experts
do not chat — each returns a structured opinion in its domain, and a red-line
expert's veto is binding (it pauses the task), while optimization experts only
advise. Whether an opinion is "heard" and whether it is "enforced" are different
things; the gate is what makes the difference.

Experts here are deterministic marker-based stand-ins (offline, zero cost). A real
LLM-backed expert implements the same ``Expert`` interface — the arbitration math
does not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class Authority(str, Enum):
    VETO = "veto"          # red-line domains (security/compliance/authorship/business)
    ADVISORY = "advisory"  # optimization domains (performance/UX)


class OpinionVerdict(str, Enum):
    APPROVE = "APPROVE"
    VETO = "VETO"
    ADVISE = "ADVISE"


# Default precedence (Design Doc §4.5): security > compliance > authorship >
# business > performance > UX. Higher wins on conflict.
DEFAULT_PRECEDENCE = {
    "security": 100,
    "compliance": 90,
    "authorship": 85,
    "business": 70,
    "performance": 40,
    "ux": 30,
}


@dataclass
class ExpertOpinion:
    domain: str
    verdict: OpinionVerdict
    reason: str
    precedence: int

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "precedence": self.precedence,
        }


@runtime_checkable
class Expert(Protocol):
    domain: str
    authority: Authority
    precedence: int

    def review(self, subject: str, context: dict) -> ExpertOpinion: ...


@dataclass
class MarkerExpert:
    """Deterministic expert: flags a subject if it contains known risk markers."""

    domain: str
    authority: Authority
    precedence: int
    veto_markers: tuple[str, ...] = ()
    advise_markers: tuple[str, ...] = ()

    def review(self, subject: str, context: dict | None = None) -> ExpertOpinion:
        low = subject.lower()
        hit = next((m for m in self.veto_markers if m.lower() in low), None)
        if hit:
            if self.authority is Authority.VETO:
                return ExpertOpinion(self.domain, OpinionVerdict.VETO,
                                     f"{self.domain}: {hit!r}", self.precedence)
            return ExpertOpinion(self.domain, OpinionVerdict.ADVISE,
                                 f"{self.domain} concern: {hit!r}", self.precedence)
        ahit = next((m for m in self.advise_markers if m.lower() in low), None)
        if ahit:
            return ExpertOpinion(self.domain, OpinionVerdict.ADVISE,
                                 f"{self.domain} suggests review: {ahit!r}", self.precedence)
        return ExpertOpinion(self.domain, OpinionVerdict.APPROVE,
                             f"{self.domain}: no objection", self.precedence)


def builtin_experts() -> list[MarkerExpert]:
    """The five-expert matrix from the design, with example markers."""
    return [
        MarkerExpert("security", Authority.VETO, DEFAULT_PRECEDENCE["security"],
                     veto_markers=("data ownership undefined", "ssh private key",
                                   "exfiltrate", "unencrypted secret")),
        MarkerExpert("compliance", Authority.VETO, DEFAULT_PRECEDENCE["compliance"],
                     veto_markers=("pii without consent", "gdpr breach",
                                   "no data processing agreement")),
        MarkerExpert("business", Authority.VETO, DEFAULT_PRECEDENCE["business"],
                     veto_markers=("exceeds budget", "over refund limit")),
        MarkerExpert("performance", Authority.ADVISORY, DEFAULT_PRECEDENCE["performance"],
                     advise_markers=("n+1 query", "full table scan")),
        MarkerExpert("ux", Authority.ADVISORY, DEFAULT_PRECEDENCE["ux"],
                     advise_markers=("confusing", "jargon")),
    ]
