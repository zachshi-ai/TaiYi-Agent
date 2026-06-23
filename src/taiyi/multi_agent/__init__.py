"""Multi-agent review — expert matrix with red-line veto and arbitration.

Not free-form agent chat: structured opinions, a binding veto for red-line
domains, advisory-only for optimization domains, and precedence-based conflict
resolution that fails closed to a human.
"""

from taiyi.multi_agent.arbitration import ArbitrationResult, Decision, arbitrate
from taiyi.multi_agent.committee import ExpertCommittee, ReviewOutcome, reconsider_once
from taiyi.multi_agent.experts import (
    Authority,
    Expert,
    ExpertOpinion,
    MarkerExpert,
    OpinionVerdict,
    builtin_experts,
)

__all__ = [
    "ArbitrationResult",
    "Decision",
    "arbitrate",
    "ExpertCommittee",
    "ReviewOutcome",
    "reconsider_once",
    "Authority",
    "Expert",
    "ExpertOpinion",
    "MarkerExpert",
    "OpinionVerdict",
    "builtin_experts",
]
