"""Operating policy: how Taiyi trades certainty, autonomy, and speed.

Governance answers *may this action happen?*.  Operating policy answers a
different question: *how should this task be pursued and when is it done?*.
Keeping the two separate is essential: efficiency mode may reduce ceremony, but
it can never relax a red line or grant authority the user did not provide.
"""

from taiyi.policy.completion import CompletionAction, CompletionController
from taiyi.policy.contract import (
    AcceptanceCriterion,
    EvidenceLedger,
    EvidenceRecord,
    TaskContract,
    build_task_contract,
)
from taiyi.policy.modes import (
    OperatingMode,
    RiskLevel,
    TaskPolicy,
    VerificationDepth,
    resolve_policy,
)

__all__ = [
    "AcceptanceCriterion",
    "CompletionAction",
    "CompletionController",
    "EvidenceLedger",
    "EvidenceRecord",
    "OperatingMode",
    "RiskLevel",
    "TaskContract",
    "TaskPolicy",
    "VerificationDepth",
    "build_task_contract",
    "resolve_policy",
]
