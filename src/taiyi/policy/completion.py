"""Evidence-based stopping rule, independent of the producing model."""
from __future__ import annotations

from enum import Enum


class CompletionAction(str, Enum):
    COMPLETE = "COMPLETE"
    REPAIR = "REPAIR"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class CompletionController:
    """Decide whether a result may be called complete.

    The controller consumes validator outcomes and criterion evidence.  It does
    not inspect model confidence and cannot execute or grant permits.
    """

    def assess(self, contract, ledger, validation_result) -> CompletionAction:
        outcome = validation_result.outcome.value
        if outcome == "NEEDS_HUMAN":
            return CompletionAction.NEEDS_HUMAN
        if outcome == "FAIL":
            return CompletionAction.REPAIR
        if contract.missing_evidence(
            ledger,
            subject_digest=validation_result.subject_digest,
        ):
            return CompletionAction.REPAIR
        return CompletionAction.COMPLETE
