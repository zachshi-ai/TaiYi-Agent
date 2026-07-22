"""Validation types.

Three check kinds, ordered by trust and cost (cheapest, most reliable first):

  1. deterministic — string/state comparison. Highest trust, no model.
  2. external      — linter / type-checker / test suite / scanner. High trust.
  3. model_judge   — only for genuinely ambiguous things (tone, design quality).
                     Lowest trust; isolated instance, narrow rubric, calibrated.

``ValidationContext`` is deliberately decoupled from the runtime's TaskContext so
the validation layer depends on nothing above it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class CheckKind(str, Enum):
    DETERMINISTIC = "deterministic"
    EXTERNAL = "external"
    MODEL_JUDGE = "model_judge"


COST_TIER: dict[CheckKind, int] = {
    CheckKind.DETERMINISTIC: 1,
    CheckKind.EXTERNAL: 2,
    CheckKind.MODEL_JUDGE: 3,
}


@dataclass
class ValidationContext:
    prompt: str
    scenario: str
    task_type: str
    executed_tools: list[str] = field(default_factory=list)
    executed_calls: list[dict] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    final_output: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    check_id: str
    kind: CheckKind
    outcome: Outcome
    detail: str = ""
    authority: str = "taiyi.validation"
    environment: str = "harness"
    configuration_digest: str = ""

    @property
    def tier(self) -> int:
        return COST_TIER[self.kind]


@dataclass
class ValidationResult:
    outcome: Outcome
    results: list[CheckResult] = field(default_factory=list)
    # Binds every evidence record to the exact output/tool observations judged.
    # A PASS about an older repair attempt cannot certify a newer artifact.
    subject_digest: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS

    @property
    def failed_checks(self) -> list[str]:
        return [r.check_id for r in self.results if r.outcome is Outcome.FAIL]

    @property
    def summary(self) -> str:
        npass = sum(1 for r in self.results if r.outcome is Outcome.PASS)
        head = f"{self.outcome.value}: {npass}/{len(self.results)} checks passed"
        if self.failed_checks:
            head += f"; failed: {', '.join(self.failed_checks)}"
        return head

    @property
    def repair_feedback(self) -> str:
        """Concrete evidence for the next correction attempt."""
        actionable = [
            f"- {r.check_id}: {r.outcome.value} — {r.detail}"
            for r in self.results
            if r.outcome is not Outcome.PASS
        ]
        return self.summary if not actionable else self.summary + "\n" + "\n".join(actionable)


def validation_subject_digest(ctx: ValidationContext) -> str:
    payload = {
        "prompt": ctx.prompt,
        "scenario": ctx.scenario,
        "task_type": ctx.task_type,
        "executed_tools": list(ctx.executed_tools),
        "executed_calls": list(ctx.executed_calls),
        "outputs": list(ctx.outputs),
        "final_output": ctx.final_output,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
