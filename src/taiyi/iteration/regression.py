"""Validator regression set.

Accumulates labelled validation cases from real runs (completed → PASS, failed →
FAIL). Running a model judge against this growing corpus is how we keep checking
that the *validator* itself stays trustworthy over time (its false-pass /
false-block rates) — the design's "validate the validator", made continuous.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from taiyi.validation import Outcome, ValidationContext


@dataclass
class RegressionSet:
    cases: list[tuple[ValidationContext, Outcome]] = field(default_factory=list)

    def add(self, output_or_ctx, expected: Outcome) -> None:
        vc = (
            output_or_ctx
            if isinstance(output_or_ctx, ValidationContext)
            else ValidationContext(prompt="", scenario="", task_type="", final_output=str(output_or_ctx))
        )
        self.cases.append((vc, expected))

    def evaluate(self, judge):
        """Calibrate a ModelJudge against the accumulated corpus; returns its stats."""
        return judge.calibrate(self.cases)

    def __len__(self) -> int:
        return len(self.cases)
