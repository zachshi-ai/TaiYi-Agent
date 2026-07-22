"""Task contract and evidence ledger.

Quality is task-specific.  Before "done" can mean anything, the task needs an
objective and explicit acceptance criteria.  The evidence ledger then records
which independent check supports each criterion; prose confidence is not
evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from taiyi.policy.modes import TaskPolicy

CONTRACT_SCHEMA_VERSION = "taiyi.task-contract/v2"


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    required: bool = True
    evidence_kind: str = "deterministic"
    scope: str = "baseline"  # baseline hygiene | objective-specific correctness
    authority: str = "taiyi.validation"
    environment: str = "harness"
    configuration_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.criterion_id,
            "description": self.description,
            "required": self.required,
            "evidence_kind": self.evidence_kind,
            "scope": self.scope,
            "authority": self.authority,
            "environment": self.environment,
            "configuration_digest": self.configuration_digest,
        }


@dataclass(frozen=True)
class EvidenceRecord:
    criterion_id: str
    outcome: str
    detail: str
    source: str
    attempt: int
    subject_digest: str = ""
    contract_id: str = ""
    authority: str = "taiyi.validation"
    environment: str = "harness"
    configuration_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "outcome": self.outcome,
            "detail": self.detail,
            "source": self.source,
            "attempt": self.attempt,
            "subject_digest": self.subject_digest,
            "contract_id": self.contract_id,
            "authority": self.authority,
            "environment": self.environment,
            "configuration_digest": self.configuration_digest,
        }


@dataclass
class EvidenceLedger:
    records: list[EvidenceRecord] = field(default_factory=list)

    def record_validation(self, result, *, attempt: int, contract_id: str) -> None:
        for item in result.results:
            self.records.append(
                EvidenceRecord(
                    criterion_id=item.check_id,
                    outcome=item.outcome.value,
                    detail=item.detail,
                    source=item.kind.value,
                    attempt=attempt,
                    subject_digest=result.subject_digest,
                    contract_id=contract_id,
                    authority=item.authority,
                    environment=item.environment,
                    configuration_digest=item.configuration_digest,
                )
            )

    def latest(self, criterion_id: str) -> EvidenceRecord | None:
        return next((r for r in reversed(self.records) if r.criterion_id == criterion_id), None)

    def satisfies(
        self,
        criterion: AcceptanceCriterion,
        *,
        subject_digest: str | None = None,
        contract_id: str | None = None,
    ) -> bool:
        evidence = self.latest(criterion.criterion_id)
        if evidence is None or evidence.outcome != "PASS":
            return False
        if evidence.source != criterion.evidence_kind:
            return False
        if evidence.authority != criterion.authority:
            return False
        if evidence.environment != criterion.environment:
            return False
        if evidence.configuration_digest != criterion.configuration_digest:
            return False
        if subject_digest is not None and evidence.subject_digest != subject_digest:
            return False
        if contract_id is not None and evidence.contract_id != contract_id:
            return False
        return True

    def to_dict(self) -> dict:
        return {"records": [r.to_dict() for r in self.records]}


@dataclass(frozen=True)
class TaskContract:
    objective: str
    scenario: str
    task_type: str
    operating_mode: str
    contract_id: str
    checklist_id: str
    task_parameters: tuple[tuple[str, str], ...] = ()
    selected_skill: str | None = None
    deliverable: str = "A usable result that satisfies the user's objective"
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    constraints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    validation_required: bool = True
    objective_evidence_required: bool = False

    @property
    def objective_covered(self) -> bool:
        return any(c.required and c.scope == "objective" for c in self.acceptance_criteria)

    @property
    def parameters(self) -> dict[str, str]:
        return dict(self.task_parameters)

    @property
    def coverage_problem(self) -> str | None:
        if (
            self.validation_required
            and self.objective_evidence_required
            and not self.objective_covered
        ):
            return (
                f"{self.operating_mode} mode cannot certify this task: no objective-specific "
                f"acceptance checker is registered for scenario {self.scenario!r}"
            )
        return None

    def missing_evidence(
        self,
        ledger: EvidenceLedger,
        *,
        subject_digest: str | None = None,
    ) -> list[str]:
        return [
            c.criterion_id
            for c in self.acceptance_criteria
            if c.required and not ledger.satisfies(
                c,
                subject_digest=subject_digest,
                contract_id=self.contract_id,
            )
        ]

    def prompt_block(self) -> str:
        criteria = "; ".join(
            f"[{c.scope}] {c.criterion_id}: {c.description}"
            for c in self.acceptance_criteria
        ) or "validation disabled"
        return (
            "Task contract:\n"
            f"- contract_id: {self.contract_id}\n"
            f"- objective: {self.objective}\n"
            f"- task_type: {self.task_type}\n"
            f"- task_parameters: {json.dumps(self.parameters, ensure_ascii=False, sort_keys=True)}\n"
            f"- deliverable: {self.deliverable}\n"
            f"- acceptance criteria: {criteria}\n"
            f"- constraints: {'; '.join(self.constraints)}\n"
            f"- assumptions: {'; '.join(self.assumptions)}"
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "contract_id": self.contract_id,
            "checklist_id": self.checklist_id,
            "immutable": True,
            "validation_required": self.validation_required,
            "objective_evidence_required": self.objective_evidence_required,
            "objective_covered": self.objective_covered,
            "coverage": "objective" if self.objective_covered else "baseline_only",
            "objective": self.objective,
            "scenario": self.scenario,
            "task_type": self.task_type,
            "task_parameters": self.parameters,
            "operating_mode": self.operating_mode,
            "selected_skill": self.selected_skill,
            "deliverable": self.deliverable,
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "constraints": list(self.constraints),
            "assumptions": list(self.assumptions),
        }


def build_task_contract(
    prompt: str,
    scenario: str,
    task_type: str,
    policy: TaskPolicy,
    *,
    task_parameters: tuple[tuple[str, str], ...] = (),
    selected_skill: str | None = None,
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = (),
    checklist_id: str = "validation-disabled",
    validation_required: bool = True,
) -> TaskContract:
    assumption = {
        "confirm_material_assumptions": "Material assumptions require confirmation.",
        "make_reversible_assumptions": "Reversible assumptions may be made and disclosed.",
        "lead_with_reasonable_defaults": "Use reasonable reversible defaults unless blocked.",
    }[policy.assumption_strategy]
    objective = prompt.strip()
    constraints = (
        "Every side-effecting action requires a governance permit.",
        "Operating mode cannot weaken red lines, authorization, or ownership rules.",
    )
    assumptions = (assumption,)
    payload = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "objective": objective,
        "scenario": scenario,
        "task_type": task_type,
        "task_parameters": dict(task_parameters),
        "operating_mode": policy.requested_mode.value,
        "selected_skill": selected_skill,
        "deliverable": "A usable result that satisfies the user's objective",
        "acceptance_criteria": [c.to_dict() for c in acceptance_criteria],
        "constraints": list(constraints),
        "assumptions": list(assumptions),
        "checklist_id": checklist_id,
        "validation_required": validation_required,
        "objective_evidence_required": (
            validation_required
            and (
                policy.requested_mode.value == "quality"
                or policy.risk_level.name.lower() in {"high", "critical"}
            )
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    contract_id = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return TaskContract(
        objective=objective,
        scenario=scenario,
        task_type=task_type,
        operating_mode=policy.requested_mode.value,
        contract_id=contract_id,
        checklist_id=checklist_id,
        task_parameters=task_parameters,
        selected_skill=selected_skill,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        assumptions=assumptions,
        validation_required=validation_required,
        objective_evidence_required=payload["objective_evidence_required"],
    )
