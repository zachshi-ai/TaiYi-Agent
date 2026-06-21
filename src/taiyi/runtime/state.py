"""The single-task state machine (Technical Architecture §3.3)."""
from __future__ import annotations

from enum import Enum


class TaskState(str, Enum):
    PENDING = "PENDING"
    PARSING = "PARSING"            # L1: load scenario / parse input
    PLANNING = "PLANNING"         # L3.2: scheduler builds the plan
    AWAITING_PERMIT = "AWAITING_PERMIT"  # L3.1: asking governance per step
    EXECUTING = "EXECUTING"       # L2: running a cleared step
    VALIDATING = "VALIDATING"     # L4: output check (full engine arrives in M6)
    COMPLETED = "COMPLETED"       # terminal: all steps cleared, executed, checked
    NEEDS_REVIEW = "NEEDS_REVIEW"  # terminal (suspended): a step needs human review
    REJECTED = "REJECTED"         # terminal: a red line denied a step
    FAILED = "FAILED"             # terminal: an error or a failed check

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskState.COMPLETED,
            TaskState.NEEDS_REVIEW,
            TaskState.REJECTED,
            TaskState.FAILED,
        }
