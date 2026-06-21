"""The Validation Engine (L4).

Runs the selected checks cheapest-first and short-circuits on the first failure —
if a one-line deterministic check already fails, there is no point spending an
external tool or a model-judge call. The model judge (if configured) runs only
after every cheaper check has passed.

This engine is independent of the executor and the planner: it judges the output,
it does not produce it. That separation is the point — a component that both does
the work and signs it off will sign off on its own work.
"""
from __future__ import annotations

from collections.abc import Callable

from taiyi.validation.checks import Check, select_checks
from taiyi.validation.model_judge import ModelJudge
from taiyi.validation.types import Outcome, ValidationContext, ValidationResult

Selector = Callable[[str, str], list[Check]]


class ValidationEngine:
    def __init__(
        self,
        *,
        selector: Selector | None = None,
        model_judge: ModelJudge | None = None,
    ):
        self._select = selector or select_checks
        self.model_judge = model_judge

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        checks = sorted(self._select(ctx.task_type, ctx.scenario), key=lambda c: c.tier)
        results = []

        for check in checks:
            r = check.run(ctx)
            results.append(r)
            if r.outcome is Outcome.FAIL:
                return ValidationResult(Outcome.FAIL, results)  # cheapest reliable failed

        # All cheaper checks passed; only now spend the model judge.
        if self.model_judge is not None:
            r = self.model_judge.evaluate(ctx)
            results.append(r)
            if r.outcome is Outcome.FAIL:
                return ValidationResult(Outcome.FAIL, results)

        outcome = (
            Outcome.NEEDS_HUMAN
            if any(r.outcome is Outcome.NEEDS_HUMAN for r in results)
            else Outcome.PASS
        )
        return ValidationResult(outcome, results)
