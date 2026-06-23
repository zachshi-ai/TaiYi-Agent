"""Arbitration over expert opinions (Design Doc §5.3).

Rules, in order:
  * No veto → APPROVED (advisories attached, the executor/human may take or leave).
  * Any veto → the highest-precedence veto wins; the task is VETOED and escalated.
    A red line is a one-vote veto — it pauses, it is not retried to bypass.
  * A hard conflict (two vetoes at the same top precedence from different domains)
    cannot be auto-resolved → fail closed → NEEDS_HUMAN (the ultimate arbiter).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from taiyi.multi_agent.experts import ExpertOpinion, OpinionVerdict


class Decision(str, Enum):
    APPROVED = "APPROVED"
    VETOED = "VETOED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


@dataclass
class ArbitrationResult:
    decision: Decision
    opinions: list[ExpertOpinion] = field(default_factory=list)
    winning: ExpertOpinion | None = None
    advisories: list[str] = field(default_factory=list)
    escalate: bool = False
    conflict: bool = False
    notes: str = ""

    @property
    def approved(self) -> bool:
        return self.decision is Decision.APPROVED

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "escalate": self.escalate,
            "conflict": self.conflict,
            "winning": self.winning.to_dict() if self.winning else None,
            "advisories": list(self.advisories),
            "opinions": [o.to_dict() for o in self.opinions],
            "notes": self.notes,
        }


def arbitrate(opinions: list[ExpertOpinion]) -> ArbitrationResult:
    vetoes = [o for o in opinions if o.verdict is OpinionVerdict.VETO]
    advisories = [o.reason for o in opinions if o.verdict is OpinionVerdict.ADVISE]

    if not vetoes:
        return ArbitrationResult(Decision.APPROVED, opinions, None, advisories, escalate=False,
                                 notes="no veto; advisories are non-binding")

    vetoes.sort(key=lambda o: o.precedence, reverse=True)
    top = vetoes[0]
    same_precedence = [v for v in vetoes if v.precedence == top.precedence]
    if len(same_precedence) > 1 and len({v.domain for v in same_precedence}) > 1:
        return ArbitrationResult(
            Decision.NEEDS_HUMAN, opinions, top, advisories, escalate=True, conflict=True,
            notes="same-precedence veto conflict across domains; fail closed → human arbiter",
        )

    return ArbitrationResult(
        Decision.VETOED, opinions, top, advisories, escalate=True,
        notes=f"vetoed by {top.domain} (precedence {top.precedence}); task paused",
    )
