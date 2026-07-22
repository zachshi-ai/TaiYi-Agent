"""L4 Output Validation — an independent gate, not the executor grading itself.

Selects per-task checklists, runs them cheapest-first, and isolates model judging.
A failed validation bounces the task back into the PDCA loop for correction.
"""

from taiyi.validation.checks import Check, deterministic, external, select_checks
from taiyi.validation.authority import ExternalAuthority
from taiyi.validation.engine import ValidationChecklist, ValidationEngine
from taiyi.validation.external_git import GitAuthority, GitSnapshot
from taiyi.validation.external_git_remote import GitRemoteAuthority, GitRemoteSnapshot
from taiyi.validation.external_github import GitHubAuthority, GitHubSnapshot
from taiyi.validation.model_judge import JudgeStats, ModelJudge
from taiyi.validation.types import (
    CheckKind,
    CheckResult,
    Outcome,
    ValidationContext,
    ValidationResult,
    validation_subject_digest,
)

__all__ = [
    "Check",
    "ExternalAuthority",
    "deterministic",
    "external",
    "select_checks",
    "ValidationEngine",
    "ValidationChecklist",
    "GitAuthority",
    "GitSnapshot",
    "GitRemoteAuthority",
    "GitRemoteSnapshot",
    "GitHubAuthority",
    "GitHubSnapshot",
    "JudgeStats",
    "ModelJudge",
    "CheckKind",
    "CheckResult",
    "Outcome",
    "ValidationContext",
    "ValidationResult",
    "validation_subject_digest",
]
