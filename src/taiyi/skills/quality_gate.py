"""Executable Skill quality-gate declarations and release attestations.

A gate is not evidence merely because its Markdown is well formed.  Every
verification entry therefore declares an executable runner input and observable
expectations.  A passing run can be persisted as ``quality_gate.lock.json``;
the lock is bound to both ``SKILL.md`` and ``quality_gate.md`` so changing either
artifact invalidates old evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from taiyi.core.markdown import split_frontmatter

# Every production gate must declare these.
REQUIRED_SECTIONS = ("admission", "exit_criteria", "verification", "side_effects", "upgrade")
MIN_VERIFICATION_CASES = 3
LOCK_FILENAME = "quality_gate.lock.json"
LOCK_SCHEMA_VERSION = 1
RUNNER_VERSION = "taiyi.skill-gate/v5"
SUPPORTED_RUNNERS = {"keyword_workflow", "declared_plan_workflow"}
CASE_PURPOSES = {"skill_contract", "routing", "governance_regression"}
# v5 has exactly one trusted environment: the side-effect-free harness. Its
# successful action cases end in SIMULATED, never COMPLETED. Do not accept
# a hand-edited "production" label until a connector-aware runner and trust
# mechanism exist; otherwise the evidence level would be self-asserted metadata.
SUPPORTED_ENVIRONMENTS = {"mock"}
TERMINAL_STATES = {
    "COMPLETED",
    "SIMULATED",
    "REJECTED",
    "NEEDS_REVIEW",
    "NEEDS_INPUT",
    "FAILED",
}
EXPECTATION_KEYS = {
    "state",
    "executed_tools_contains",
    "executed_tools_exact",
    "held_tool",
    "matched_rule_id",
    "approval_required",
    "evidence_checks_pass",
    "evidence_checks_fail",
    "selected_skill",
    "final_output_contains",
}
LIST_EXPECTATIONS = {
    "executed_tools_contains",
    "executed_tools_exact",
    "evidence_checks_pass",
    "evidence_checks_fail",
}


class GateError(ValueError):
    pass


def artifact_digest(skill_text: str, gate_text: str) -> str:
    """Digest every executable/declared part of a Skill release artifact."""

    payload = skill_text.encode("utf-8") + b"\0" + gate_text.encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass
class QualityGate:
    admission: list[str] = field(default_factory=list)
    exit_criteria: list[str] = field(default_factory=list)
    verification: list[dict] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    upgrade: list[str] = field(default_factory=list)
    body: str = ""

    def problems(self) -> list[str]:
        """Return structural and executability problems (empty == well formed)."""
        issues: list[str] = []
        for section in REQUIRED_SECTIONS:
            value = getattr(self, section)
            if not value:
                issues.append(f"missing or empty section: {section}")
        if len(self.verification) < MIN_VERIFICATION_CASES:
            issues.append(
                f"verification needs at least {MIN_VERIFICATION_CASES} executable cases"
            )
        seen: set[str] = set()
        purposes: set[str] = set()
        for i, case in enumerate(self.verification):
            if not isinstance(case, dict) or "id" not in case or "description" not in case:
                issues.append(f"verification[{i}] needs an 'id' and a 'description'")
                continue
            case_id = str(case["id"])
            if case_id in seen:
                issues.append(f"duplicate verification id: {case_id}")
            seen.add(case_id)
            runner = case.get("runner")
            if runner not in SUPPORTED_RUNNERS:
                issues.append(
                    f"verification[{i}] runner must be one of: {', '.join(sorted(SUPPORTED_RUNNERS))}"
                )
            purpose = case.get("purpose")
            if purpose not in CASE_PURPOSES:
                issues.append(
                    f"verification[{i}] purpose must be one of: {', '.join(sorted(CASE_PURPOSES))}"
                )
            else:
                purposes.add(purpose)
            if runner == "declared_plan_workflow":
                plan = case.get("plan")
                if not isinstance(plan, list) or not plan:
                    issues.append(f"verification[{i}] needs a non-empty 'plan'")
                else:
                    for j, step in enumerate(plan):
                        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
                            issues.append(f"verification[{i}].plan[{j}] needs a string 'tool'")
                            continue
                        if "args" in step and not isinstance(step["args"], list):
                            issues.append(f"verification[{i}].plan[{j}].args must be a list")
            if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
                issues.append(f"verification[{i}] needs a non-empty 'prompt'")
            if not isinstance(case.get("scenario"), str) or not case["scenario"].strip():
                issues.append(f"verification[{i}] needs a non-empty 'scenario'")
            mode = case.get("operating_mode", "quality")
            if mode not in {"quality", "balanced", "efficiency"}:
                issues.append(f"verification[{i}] has invalid operating_mode: {mode!r}")
            expect = case.get("expect")
            if not isinstance(expect, dict):
                issues.append(f"verification[{i}] needs an 'expect' mapping")
                continue
            unknown = sorted(set(expect) - EXPECTATION_KEYS)
            if unknown:
                issues.append(
                    f"verification[{i}] has unknown expectation(s): {', '.join(unknown)}"
                )
            for key in LIST_EXPECTATIONS:
                if key in expect and not isinstance(expect[key], list):
                    issues.append(f"verification[{i}] expect.{key} must be a list")
            if "approval_required" in expect and not isinstance(expect["approval_required"], bool):
                issues.append(f"verification[{i}] expect.approval_required must be boolean")
            if expect.get("state") not in TERMINAL_STATES:
                issues.append(
                    f"verification[{i}] expect.state must be one of: "
                    f"{', '.join(sorted(TERMINAL_STATES))}"
                )
        if self.verification and "skill_contract" not in purposes:
            issues.append("verification needs at least one purpose=skill_contract case")
        return issues

    @property
    def passes(self) -> bool:
        return not self.problems()

    @property
    def case_ids(self) -> list[str]:
        return [str(c.get("id")) for c in self.verification if isinstance(c, dict) and c.get("id")]


@dataclass
class GateAttestation:
    """Persisted evidence from an executable quality-gate run."""

    skill: str
    artifact_digest: str
    environment: str
    results: list[dict]
    verified_at: str
    schema_version: int = LOCK_SCHEMA_VERSION
    runner_version: str = RUNNER_VERSION

    @classmethod
    def from_dict(cls, data: dict) -> "GateAttestation":
        if not isinstance(data, dict):
            raise GateError("quality gate lock must be a JSON object")
        return cls(
            skill=str(data.get("skill", "")),
            artifact_digest=str(data.get("artifact_digest", "")),
            environment=str(data.get("environment", "")),
            results=list(data.get("results", []) or []),
            verified_at=str(data.get("verified_at", "")),
            schema_version=int(data.get("schema_version", 0)),
            runner_version=str(data.get("runner_version", "")),
        )

    @classmethod
    def read(cls, path: str | Path) -> "GateAttestation":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def problems(self, *, skill: str, digest: str, case_ids: list[str]) -> list[str]:
        issues: list[str] = []
        if self.schema_version != LOCK_SCHEMA_VERSION:
            issues.append(f"unsupported quality gate lock schema: {self.schema_version}")
        if self.runner_version != RUNNER_VERSION:
            issues.append(
                f"quality gate lock runner mismatch: {self.runner_version!r} != {RUNNER_VERSION!r}"
            )
        if self.skill != skill:
            issues.append(f"quality gate lock belongs to {self.skill!r}, expected {skill!r}")
        if self.artifact_digest != digest:
            issues.append("quality gate lock is stale: SKILL.md or quality_gate.md changed")
        if self.environment not in SUPPORTED_ENVIRONMENTS:
            issues.append(f"unsupported verification environment: {self.environment!r}")
        if not self.verified_at:
            issues.append("quality gate lock has no verified_at timestamp")

        rows = [r for r in self.results if isinstance(r, dict)]
        result_ids = [str(r.get("id", "")) for r in rows]
        if result_ids != case_ids:
            issues.append("quality gate lock cases do not exactly match the declaration")
        for row in rows:
            if row.get("outcome") != "PASS":
                issues.append(f"verification case {row.get('id')!r} did not pass")
        if not rows:
            issues.append("quality gate lock has no case results")
        return issues

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "runner_version": self.runner_version,
            "skill": self.skill,
            "artifact_digest": self.artifact_digest,
            "environment": self.environment,
            "verified_at": self.verified_at,
            "results": self.results,
        }


def parse_gate(text: str) -> QualityGate:
    meta, body = split_frontmatter(text)
    if not meta:
        raise GateError("quality_gate.md has no YAML frontmatter")

    def section(name: str) -> list:
        value = meta.get(name, []) or []
        if not isinstance(value, list):
            raise GateError(f"quality gate section {name!r} must be a YAML list")
        return value

    return QualityGate(
        admission=section("admission"),
        exit_criteria=section("exit_criteria"),
        verification=section("verification"),
        side_effects=section("side_effects"),
        upgrade=section("upgrade"),
        body=body,
    )
