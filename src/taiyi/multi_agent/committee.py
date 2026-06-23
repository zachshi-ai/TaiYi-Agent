"""The expert committee and the reconsideration process.

`ExpertCommittee.review` gathers every expert's opinion and arbitrates. The design
also grants one reconsideration: if a vetoed proposal is amended and re-reviewed
and is *still* vetoed, that escalates to L5 as a recorded system defect rather than
looping forever.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from taiyi.multi_agent.arbitration import ArbitrationResult, Decision, arbitrate
from taiyi.multi_agent.experts import Expert, builtin_experts


class ExpertCommittee:
    def __init__(self, experts: list[Expert] | None = None):
        self.experts = list(experts) if experts is not None else builtin_experts()

    def review(self, subject: str, context: dict | None = None) -> ArbitrationResult:
        opinions = [e.review(subject, context or {}) for e in self.experts]
        return arbitrate(opinions)


@dataclass
class ReviewOutcome:
    result: ArbitrationResult
    reconsidered: bool
    system_defect: bool


def reconsider_once(
    committee: ExpertCommittee,
    subject: str,
    amend: Callable[[str], str] | None = None,
    context: dict | None = None,
) -> ReviewOutcome:
    """Review; if vetoed and an amendment is offered, allow exactly one retry.

    A persistent veto after reconsideration becomes a system defect (escalated to
    L5) — the loop is bounded, not infinite.
    """
    first = committee.review(subject, context)
    if first.decision is not Decision.VETOED or amend is None:
        return ReviewOutcome(first, reconsidered=False, system_defect=False)

    second = committee.review(amend(subject), context)
    if second.decision is Decision.VETOED:
        second.notes += " | still vetoed after one reconsideration → L5 system defect"
        return ReviewOutcome(second, reconsidered=True, system_defect=True)
    return ReviewOutcome(second, reconsidered=True, system_defect=False)
