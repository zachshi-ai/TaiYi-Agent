"""L4 Output Validation — an independent gate, not the executor grading itself.

Selects per-task checklists, runs them cheapest-first, and isolates model judging.
A failed validation bounces the task back into the PDCA loop for correction.
"""

from taiyi.validation.checks import Check, deterministic, select_checks
from taiyi.validation.engine import ValidationEngine
from taiyi.validation.model_judge import JudgeStats, ModelJudge
from taiyi.validation.types import (
    CheckKind,
    CheckResult,
    Outcome,
    ValidationContext,
    ValidationResult,
)

__all__ = [
    "Check",
    "deterministic",
    "select_checks",
    "ValidationEngine",
    "JudgeStats",
    "ModelJudge",
    "CheckKind",
    "CheckResult",
    "Outcome",
    "ValidationContext",
    "ValidationResult",
]
