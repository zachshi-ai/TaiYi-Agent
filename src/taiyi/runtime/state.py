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
    SIMULATED = "SIMULATED"       # terminal: harness passed, but mock actions changed no real system
    NEEDS_INPUT = "NEEDS_INPUT"   # terminal for this turn: a material question needs an answer
    NEEDS_REVIEW = "NEEDS_REVIEW"  # terminal (suspended): a step needs human review
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"  # matched Skill failed its gate
    REJECTED = "REJECTED"         # terminal: a red line denied a step
    FAILED = "FAILED"             # terminal: an error or a failed check

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskState.COMPLETED,
            TaskState.SIMULATED,
            TaskState.NEEDS_INPUT,
            TaskState.NEEDS_REVIEW,
            TaskState.CAPABILITY_UNAVAILABLE,
            TaskState.REJECTED,
            TaskState.FAILED,
        }

    @classmethod
    def successful(
        cls,
        *,
        execution_environment: str,
        executed_actions: int,
    ) -> "TaskState":
        """Return an honest success state for the environment that did the work.

        A side-effect-free mock can prove planning, governance and validation
        behaviour, but it cannot truthfully claim that an action was delivered.
        Text-only tasks execute no tool action and may still complete normally.
        """

        if execution_environment == "mock" and executed_actions:
            return cls.SIMULATED
        return cls.COMPLETED
