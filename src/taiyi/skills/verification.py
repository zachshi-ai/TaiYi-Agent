"""Safe execution of Skill quality-gate cases.

The built-in runner uses Taiyi's real workflow runtime, governance engine and
validator with the side-effect-free ``MockExecutor``.  It proves that a Skill's
declared task shape still behaves correctly in the current harness; it does not
claim that deferred external connectors are live-certified.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.policy import VerificationDepth
from taiyi.runtime import TaskRuntime
from taiyi.scheduler import ExecutionPlan, PlanStep, SchedulerEngine
from taiyi.skills.loader import Skill
from taiyi.skills.quality_gate import (
    LOCK_FILENAME,
    RUNNER_VERSION,
    GateAttestation,
)
from taiyi.validation import ValidationEngine, deterministic, select_checks


@dataclass(frozen=True)
class GateCaseResult:
    case_id: str
    outcome: str
    detail: str
    observed: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.case_id,
            "outcome": self.outcome,
            "detail": self.detail,
            "observed": self.observed,
        }


@dataclass
class GateRunReport:
    skill: str
    artifact_digest: str
    environment: str
    results: list[GateCaseResult]

    @property
    def passes(self) -> bool:
        return bool(self.results) and all(r.outcome == "PASS" for r in self.results)

    @property
    def failed_case_ids(self) -> list[str]:
        return [r.case_id for r in self.results if r.outcome != "PASS"]

    def to_dict(self) -> dict:
        return {
            "skill": self.skill,
            "artifact_digest": self.artifact_digest,
            "environment": self.environment,
            "runner_version": RUNNER_VERSION,
            "outcome": "PASS" if self.passes else "FAIL",
            "results": [r.to_dict() for r in self.results],
        }

    def attestation(self) -> GateAttestation:
        if not self.passes:
            raise ValueError("cannot attest a failing quality gate")
        return GateAttestation(
            skill=self.skill,
            artifact_digest=self.artifact_digest,
            environment=self.environment,
            verified_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            results=[r.to_dict() for r in self.results],
        )

    def write_lock(self, skill_dir: str | Path) -> Path:
        path = Path(skill_dir) / LOCK_FILENAME
        payload = self.attestation().to_dict()
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        return path


class SkillGateRunner:
    """Execute declarative cases against a fresh governed runtime per case."""

    environment = "mock"

    def run(self, skill: Skill) -> GateRunReport:
        gate = skill.gate
        if gate is None or skill.gate_problems:
            detail = "; ".join(skill.gate_problems) or "missing quality gate"
            return GateRunReport(
                skill.name,
                skill.artifact_digest,
                self.environment,
                [GateCaseResult("__declaration__", "FAIL", detail)],
            )

        results = [self._run_case(skill, case) for case in gate.verification]
        return GateRunReport(skill.name, skill.artifact_digest, self.environment, results)

    def _run_case(self, skill: Skill, case: dict) -> GateCaseResult:
        case_id = str(case["id"])
        runner = case.get("runner")
        if runner not in {"keyword_workflow", "declared_plan_workflow"}:
            return GateCaseResult(
                case_id, "FAIL", f"unsupported runner: {runner!r}"
            )

        plan = [
            PlanStep(str(step["tool"]), [str(a) for a in step.get("args", [])])
            for step in case.get("plan", [])
        ]
        if case.get("purpose") == "skill_contract":
            undeclared = [step.tool for step in plan if not _tool_declared(skill.body, step.tool)]
            if undeclared:
                return GateCaseResult(
                    case_id,
                    "FAIL",
                    f"declared plan tool(s) absent from SKILL.md: {', '.join(undeclared)}",
                )

        audit = AuditLog()
        governance = GovernanceEngine(audit_log=audit)
        planner = _DeclaredPlanPlanner(skill.name, plan) if runner == "declared_plan_workflow" else None
        scheduler = SchedulerEngine(LocalPermitClient(governance), planner=planner)
        objective_check = _case_objective_check(case_id, case.get("expect", {}))

        def selector(task_type: str, scenario: str, parameters: dict[str, str]):
            return [*select_checks(task_type, scenario, parameters), objective_check]

        runtime = TaskRuntime(
            scheduler,
            audit_log=audit,
            validator=ValidationEngine(selector=selector),
        )
        ctx = runtime.run(
            str(case["prompt"]),
            str(case["scenario"]),
            operating_mode=str(case.get("operating_mode", "quality")),
            skill_name=skill.name,
            skill_instructions=skill.body,
        )

        executed = [s.step.tool for s in ctx.executed_steps]
        held = next(
            (s.step.tool for s in reversed(ctx.step_results) if not s.executed),
            None,
        )
        matched_rules = [
            s.matched_rule_id for s in ctx.step_results if s.matched_rule_id
        ]
        passed_evidence = sorted({
            r.criterion_id for r in ctx.evidence.records if r.outcome == "PASS"
        })
        failed_evidence = sorted({
            r.criterion_id for r in ctx.evidence.records if r.outcome == "FAIL"
        })
        observed = {
            "state": ctx.state.value,
            "executed_tools": executed,
            "held_tool": held,
            "matched_rule_ids": matched_rules,
            "approval_required": bool(ctx.approval_id),
            "evidence_checks_pass": passed_evidence,
            "evidence_checks_fail": failed_evidence,
            "selected_skill": ctx.selected_skill,
        }
        failures = self._compare(case.get("expect", {}), observed, ctx.final_output or "")
        return GateCaseResult(
            case_id,
            "FAIL" if failures else "PASS",
            "; ".join(failures) if failures else "all declared expectations satisfied",
            observed,
        )

    @staticmethod
    def _compare(expect: dict, observed: dict, final_output: str) -> list[str]:
        failures: list[str] = []

        def same(key: str) -> None:
            if key in expect and observed.get(key) != expect[key]:
                failures.append(
                    f"{key}: expected {expect[key]!r}, observed {observed.get(key)!r}"
                )

        same("state")
        same("held_tool")
        same("approval_required")
        same("selected_skill")

        if "matched_rule_id" in expect:
            rule = expect["matched_rule_id"]
            if rule not in observed["matched_rule_ids"]:
                failures.append(
                    f"matched_rule_id: expected {rule!r}, observed {observed['matched_rule_ids']!r}"
                )
        if "executed_tools_exact" in expect:
            wanted = list(expect["executed_tools_exact"])
            if observed["executed_tools"] != wanted:
                failures.append(
                    f"executed_tools_exact: expected {wanted!r}, "
                    f"observed {observed['executed_tools']!r}"
                )
        if "executed_tools_contains" in expect:
            missing = [
                t for t in expect["executed_tools_contains"]
                if t not in observed["executed_tools"]
            ]
            if missing:
                failures.append(f"executed_tools_contains: missing {missing!r}")
        if "evidence_checks_pass" in expect:
            missing = [
                check for check in expect["evidence_checks_pass"]
                if check not in observed["evidence_checks_pass"]
            ]
            if missing:
                failures.append(f"evidence_checks_pass: missing {missing!r}")
        if "evidence_checks_fail" in expect:
            missing = [
                check for check in expect["evidence_checks_fail"]
                if check not in observed["evidence_checks_fail"]
            ]
            if missing:
                failures.append(f"evidence_checks_fail: missing {missing!r}")
        if "final_output_contains" in expect:
            fragment = str(expect["final_output_contains"])
            if fragment not in final_output:
                failures.append(f"final_output_contains: missing {fragment!r}")
        return failures


class _DeclaredPlanPlanner:
    """Case-local deterministic planner; the gate owns the executable contract."""

    def __init__(self, skill_name: str, steps: list[PlanStep]):
        self.skill_name = skill_name
        self.steps = steps

    def plan(self, prompt: str, scenario: str) -> ExecutionPlan:
        return ExecutionPlan(
            self.skill_name,
            list(self.steps),
            "quality-gate declared plan",
        )


def _tool_declared(body: str, tool: str) -> bool:
    """Conservatively tie a successful case plan back to procedural prose."""

    candidate = tool.removeprefix("shell:")
    if candidate in body:
        return True
    if ":" in candidate:
        family = candidate.split(":", 1)[0] + ":*"
        return family in body
    return False


def _case_objective_check(case_id: str, expect: dict):
    """Turn the gate's declared outcome into a pre-execution objective criterion.

    The outer comparison remains authoritative for state/governance assertions.
    This check gives quality-mode completion cases a contract-bound objective so
    generic demo Skills cannot be certified by universal hygiene checks alone.
    """

    def predicate(ctx):
        exact = expect.get("executed_tools_exact")
        if exact is not None and ctx.executed_tools != list(exact):
            return False, f"executed tools differ from gate declaration: {ctx.executed_tools!r}"
        required = list(expect.get("executed_tools_contains", []))
        missing = [tool for tool in required if tool not in ctx.executed_tools]
        if missing:
            return False, f"gate-declared tools did not execute: {missing!r}"
        fragment = expect.get("final_output_contains")
        if fragment is not None and str(fragment) not in ctx.final_output:
            return False, f"gate-declared output fragment is missing: {fragment!r}"
        if not required and exact is None and fragment is None and not ctx.final_output.strip():
            return False, "gate case produced no objective artifact"
        return True, "gate-declared objective evidence is present"

    return deterministic(
        f"skill_gate:{case_id}:objective",
        predicate,
        description=f"Skill Gate case {case_id} produced its declared objective evidence.",
        depth=VerificationDepth.CRITICAL,
        scope="objective",
    )
