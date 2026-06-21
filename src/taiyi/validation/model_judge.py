"""Isolated, version-tracked model judge.

When a check is genuinely ambiguous (tone, design quality) and no deterministic or
external check can settle it, a model may judge — but under strict conditions
(§5.2):

  * It uses its **own** provider instance, never the one that produced the output
    (no self-evaluation, passed in by the caller).
  * Its rubric is **narrow and versioned**, so judgements are reproducible and
    regressions are detectable.
  * Its **false-pass / false-block** rates are tracked against a labelled set, so
    we can tell whether the judge itself is trustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass

from taiyi.llm.base import LLMMessage, LLMProvider
from taiyi.validation.types import CheckKind, CheckResult, Outcome, ValidationContext


@dataclass
class JudgeStats:
    calibration_cases: int = 0
    false_pass: int = 0   # judge said PASS, truth was FAIL  (the dangerous error)
    false_block: int = 0  # judge said FAIL, truth was PASS  (the trust-eroding error)

    @property
    def false_pass_rate(self) -> float:
        return self.false_pass / self.calibration_cases if self.calibration_cases else 0.0

    @property
    def false_block_rate(self) -> float:
        return self.false_block / self.calibration_cases if self.calibration_cases else 0.0


class ModelJudge:
    def __init__(
        self,
        provider: LLMProvider,
        rubric: str,
        *,
        rubric_version: str = "v1",
        check_id: str = "model_judge:quality",
    ):
        self.provider = provider
        self.rubric = rubric
        self.rubric_version = rubric_version
        self.check_id = check_id
        self.stats = JudgeStats()

    def evaluate(self, ctx: ValidationContext) -> CheckResult:
        messages = [
            LLMMessage("system", f"{self.rubric}\n(rubric {self.rubric_version}) "
                                 "Reply with exactly PASS, FAIL, or NEEDS_HUMAN."),
            LLMMessage("user", ctx.final_output),
        ]
        resp = self.provider.complete(messages)
        outcome = self._parse(resp.text)
        return CheckResult(
            check_id=f"{self.check_id}@{self.rubric_version}",
            kind=CheckKind.MODEL_JUDGE,
            outcome=outcome,
            detail=resp.text.strip()[:200],
        )

    @staticmethod
    def _parse(text: str) -> Outcome:
        up = text.strip().upper()
        if "FAIL" in up:
            return Outcome.FAIL
        if "NEEDS_HUMAN" in up or "HUMAN" in up:
            return Outcome.NEEDS_HUMAN
        if "PASS" in up:
            return Outcome.PASS
        return Outcome.NEEDS_HUMAN  # ambiguous -> escalate, never silently pass

    def calibrate(self, labelled: list[tuple[ValidationContext, Outcome]]) -> JudgeStats:
        """Run the judge against known-good/known-bad cases and record its error
        rates. This is how we 'validate the validator'."""
        self.stats = JudgeStats(calibration_cases=len(labelled))
        for ctx, truth in labelled:
            got = self.evaluate(ctx).outcome
            if truth is Outcome.FAIL and got is Outcome.PASS:
                self.stats.false_pass += 1
            elif truth is Outcome.PASS and got is Outcome.FAIL:
                self.stats.false_block += 1
        return self.stats
