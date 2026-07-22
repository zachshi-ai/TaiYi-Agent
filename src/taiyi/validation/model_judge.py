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

import hashlib
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

    @property
    def criterion_id(self) -> str:
        return f"{self.check_id}@{self.rubric_version}"

    @property
    def authority(self) -> str:
        return f"model:{getattr(self.provider, 'name', type(self.provider).__name__)}"

    @property
    def configuration_digest(self) -> str:
        raw = f"{self.criterion_id}\0{self.rubric}".encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    def evaluate(self, ctx: ValidationContext) -> CheckResult:
        messages = [
            LLMMessage("system", f"{self.rubric}\n(rubric {self.rubric_version}) "
                                 "Reply with exactly PASS, FAIL, or NEEDS_HUMAN."),
            LLMMessage(
                "user",
                "Task objective:\n"
                f"{ctx.prompt}\n\n"
                f"Scenario: {ctx.scenario}\n"
                f"Task type: {ctx.task_type}\n"
                f"Executed calls: {ctx.executed_calls or ctx.executed_tools}\n\n"
                "Candidate output:\n"
                f"{ctx.final_output}",
            ),
        ]
        resp = self.provider.complete(messages)
        outcome = self._parse(resp.text)
        return CheckResult(
            check_id=self.criterion_id,
            kind=CheckKind.MODEL_JUDGE,
            outcome=outcome,
            detail=resp.text.strip()[:200],
            authority=self.authority,
            environment="model",
            configuration_digest=self.configuration_digest,
        )

    @staticmethod
    def _parse(text: str) -> Outcome:
        up = text.strip().upper()
        # The protocol asks for one exact verdict. Accept an optional explanation
        # after whitespace or a colon, but never let a word in that explanation
        # override the leading verdict (for example "NEEDS_HUMAN: cannot pass").
        verdict = up.split(maxsplit=1)[0].rstrip(":") if up else ""
        if verdict == "PASS":
            return Outcome.PASS
        if verdict == "FAIL":
            return Outcome.FAIL
        if verdict in {"NEEDS_HUMAN", "HUMAN"}:
            return Outcome.NEEDS_HUMAN
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
