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

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass

from taiyi.policy import AcceptanceCriterion, VerificationDepth
from taiyi.validation.checks import Check, select_checks
from taiyi.validation.authority import ExternalAuthority
from taiyi.validation.model_judge import ModelJudge
from taiyi.validation.types import (
    CheckKind,
    Outcome,
    ValidationContext,
    ValidationResult,
    validation_subject_digest,
)

Selector = Callable[..., list[Check]]
CHECKLIST_SCHEMA_VERSION = "taiyi.validation-checklist/v2"


@dataclass(frozen=True)
class ValidationChecklist:
    """The immutable objective checklist selected before any task action runs."""

    checklist_id: str
    task_type: str
    scenario: str
    parameters: tuple[tuple[str, str], ...]
    depth: VerificationDepth
    checks: tuple[Check, ...]
    run_model_judge: bool
    acceptance_criteria: tuple[AcceptanceCriterion, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": CHECKLIST_SCHEMA_VERSION,
            "checklist_id": self.checklist_id,
            "task_type": self.task_type,
            "scenario": self.scenario,
            "parameters": dict(self.parameters),
            "verification_depth": self.depth.name.lower(),
            "run_model_judge": self.run_model_judge,
            "criteria": [c.to_dict() for c in self.acceptance_criteria],
        }


class ValidationEngine:
    def __init__(
        self,
        *,
        selector: Selector | None = None,
        model_judge: ModelJudge | None = None,
        external_authorities: tuple[ExternalAuthority, ...] = (),
    ):
        self._select = selector or select_checks
        try:
            inspect.signature(self._select).bind("task", "scenario", {})
            self._selector_accepts_parameters = True
        except (TypeError, ValueError):
            self._selector_accepts_parameters = False
        self.model_judge = model_judge
        self.external_authorities = tuple(external_authorities)

    def selected_checks(
        self,
        task_type: str,
        scenario: str,
        *,
        parameters: dict[str, str] | None = None,
        depth: VerificationDepth = VerificationDepth.EXHAUSTIVE,
    ) -> list[Check]:
        frozen_parameters = dict(parameters or {})
        candidates = list(
            self._select(task_type, scenario, frozen_parameters)
            if self._selector_accepts_parameters
            else self._select(task_type, scenario)
        )
        for authority in self.external_authorities:
            candidates.extend(authority.checks(task_type, scenario, frozen_parameters))
        selected: list[Check] = []
        seen: set[str] = set()
        for check in candidates:
            if check.id in seen:
                raise ValueError(f"duplicate validation criterion id: {check.id}")
            seen.add(check.id)
            if check.depth <= depth:
                selected.append(check)
        return selected

    def configured_authorities(self) -> list[dict]:
        return [
            authority.to_dict()
            if hasattr(authority, "to_dict")
            else {
                "name": authority.name,
                "environment": authority.environment,
                "read_only": True,
            }
            for authority in self.external_authorities
        ]

    def prepare(
        self,
        task_type: str,
        scenario: str,
        *,
        parameters: dict[str, str] | None = None,
        depth: VerificationDepth,
        run_model_judge: bool,
    ) -> ValidationChecklist:
        """Select and freeze the checklist before planning/execution.

        This is intentionally separate from :meth:`validate`: completion criteria
        cannot be invented or weakened after seeing the produced artifact.
        """

        frozen_parameters = tuple(sorted((parameters or {}).items()))
        checks = tuple(self.selected_checks(
            task_type,
            scenario,
            parameters=dict(frozen_parameters),
            depth=depth,
        ))
        include_judge = bool(self.model_judge is not None and run_model_judge)
        criteria = [
            AcceptanceCriterion(
                criterion_id=c.id,
                description=c.description or c.id,
                evidence_kind=c.kind.value,
                scope=c.scope,
                authority=c.authority,
                environment=c.environment,
                configuration_digest=c.configuration_digest,
            )
            for c in checks
        ]
        if include_judge:
            assert self.model_judge is not None
            criteria.append(AcceptanceCriterion(
                criterion_id=self.model_judge.criterion_id,
                description=(
                    "The configured independent model judge accepted the result "
                    f"under rubric {self.model_judge.rubric_version}."
                ),
                evidence_kind=CheckKind.MODEL_JUDGE.value,
                scope="objective",
                authority=self.model_judge.authority,
                environment="model",
                configuration_digest=self.model_judge.configuration_digest,
            ))
        if not criteria:
            raise ValueError(
                f"validation selected no acceptance criteria for {task_type!r}/{scenario!r}"
            )
        payload = {
            "schema_version": CHECKLIST_SCHEMA_VERSION,
            "task_type": task_type,
            "scenario": scenario,
            "parameters": dict(frozen_parameters),
            "verification_depth": depth.name.lower(),
            "run_model_judge": include_judge,
            "criteria": [c.to_dict() for c in criteria],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        checklist_id = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return ValidationChecklist(
            checklist_id=checklist_id,
            task_type=task_type,
            scenario=scenario,
            parameters=frozen_parameters,
            depth=depth,
            checks=checks,
            run_model_judge=include_judge,
            acceptance_criteria=tuple(criteria),
        )

    def validate(
        self,
        ctx: ValidationContext,
        *,
        depth: VerificationDepth = VerificationDepth.EXHAUSTIVE,
        checks: list[Check] | None = None,
        run_model_judge: bool | None = None,
        checklist: ValidationChecklist | None = None,
    ) -> ValidationResult:
        if checklist is not None:
            if checks is not None:
                raise ValueError("pass checklist or checks, not both")
            if (ctx.task_type, ctx.scenario) != (checklist.task_type, checklist.scenario):
                raise ValueError("validation context does not match the frozen checklist")
            depth = checklist.depth
            checks = list(checklist.checks)
            run_model_judge = checklist.run_model_judge
        else:
            checks = checks if checks is not None else self.selected_checks(
                ctx.task_type, ctx.scenario, depth=depth
            )
        checks = sorted(checks, key=lambda c: c.tier)
        results = []
        subject_digest = validation_subject_digest(ctx)

        for check in checks:
            r = check.run(ctx)
            results.append(r)
            if r.outcome is Outcome.FAIL:
                return ValidationResult(
                    Outcome.FAIL, results, subject_digest
                )  # cheapest reliable failed

        # All cheaper checks passed; only now spend the model judge.
        wants_model_judge = (
            depth >= VerificationDepth.EXHAUSTIVE
            if run_model_judge is None
            else run_model_judge
        )
        if self.model_judge is not None and wants_model_judge:
            r = self.model_judge.evaluate(ctx)
            results.append(r)
            if r.outcome is Outcome.FAIL:
                return ValidationResult(Outcome.FAIL, results, subject_digest)

        outcome = (
            Outcome.NEEDS_HUMAN
            if any(r.outcome is Outcome.NEEDS_HUMAN for r in results)
            else Outcome.PASS
        )
        return ValidationResult(outcome, results, subject_digest)
