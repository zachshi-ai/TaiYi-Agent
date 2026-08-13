"""TaskContext — the object that flows through the PDCA loop.

It accumulates the plan, the per-step verdicts and outputs, and the final state.
A trimmed version of the full TaskContext in the Technical Architecture; value
stream fields (H4) arrive in M10.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from taiyi.policy import EvidenceLedger, TaskContract, TaskPolicy
from taiyi.runtime.effects import EffectRecord
from taiyi.runtime.protocol import RunPhase
from taiyi.runtime.state import TaskState
from taiyi.scheduler import ExecutionPlan, PlanStep
from taiyi.value_stream.goals import TaskGoal, ValueContribution


@dataclass
class StepResult:
    """One step's journey through the loop: its verdict and, if cleared, output."""

    step: PlanStep
    verdict: str                  # ALLOW | DENY | NEEDS_REVIEW
    reason: str = ""
    matched_rule_id: str | None = None
    output: str | None = None     # set only when the step was cleared and executed
    executed: bool = False
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None
    job_id: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    failure_kind: str | None = None
    timeout_kind: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_artifact_bytes: int = 0
    stderr_artifact_bytes: int = 0
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    stdout_artifact_truncated: bool = False
    stderr_artifact_truncated: bool = False
    output_truncated: bool = False
    termination_reason: str | None = None
    termination_escalated: bool = False
    owned_process_group_settled: bool | None = None
    duration_seconds: float | None = None
    error: str | None = None
    operation_id: str | None = None
    effect_status: str | None = None
    effect_evidence: str | None = None
    original_failure_kind: str | None = None

    def to_dict(self) -> dict:
        return {
            "tool": self.step.tool,
            "args": list(self.step.args),
            "verdict": self.verdict,
            "reason": self.reason,
            "matched_rule_id": self.matched_rule_id,
            "executed": self.executed,
            "output": self.output,
            "stdout_artifact": self.stdout_artifact,
            "stderr_artifact": self.stderr_artifact,
            "job_id": self.job_id,
            "exit_code": self.exit_code,
            "signal": self.signal,
            "failure_kind": self.failure_kind,
            "timeout_kind": self.timeout_kind,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "stdout_artifact_bytes": self.stdout_artifact_bytes,
            "stderr_artifact_bytes": self.stderr_artifact_bytes,
            "stdout_digest": self.stdout_digest,
            "stderr_digest": self.stderr_digest,
            "stdout_artifact_truncated": self.stdout_artifact_truncated,
            "stderr_artifact_truncated": self.stderr_artifact_truncated,
            "output_truncated": self.output_truncated,
            "termination_reason": self.termination_reason,
            "termination_escalated": self.termination_escalated,
            "owned_process_group_settled": self.owned_process_group_settled,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "operation_id": self.operation_id,
            "effect_status": self.effect_status,
            "effect_evidence": self.effect_evidence,
            "original_failure_kind": self.original_failure_kind,
        }


@dataclass
class TaskContext:
    task_id: str
    prompt: str
    scenario: str
    runtime_mode: str = "workflow"
    session_id: str = "s1"
    user_id: str = "u1"
    channel: str = "cli"
    state: TaskState = TaskState.PENDING
    phase: RunPhase = RunPhase.READY
    attempt_id: int = 1
    checkpoint_revision: int = 0
    failure_kind: str | None = None
    plan: ExecutionPlan | None = None
    step_results: list[StepResult] = field(default_factory=list)
    final_output: str | None = None
    error: str | None = None
    approval_id: str | None = None
    round: int = 0
    executed_action_count: int = 0
    validation_attempts: int = 0
    validation_summary: str | None = None
    operating_mode: str = "balanced"
    execution_environment: str = "unknown"
    selected_skill: str | None = None
    scenario_definition: str | None = field(default=None, repr=False)
    skill_instructions: str | None = field(default=None, repr=False)
    policy: TaskPolicy | None = None
    provider_route: dict | None = None
    repository_context: dict | None = None
    context_state: dict | None = None
    effects: list[EffectRecord] = field(default_factory=list)
    contract: TaskContract | None = None
    validation_checklist: object | None = field(default=None, repr=False)
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    goal: TaskGoal | None = None
    value_contribution: ValueContribution | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self, state: TaskState) -> None:
        self.state = state
        self.updated_at = time.time()

    @property
    def executed_steps(self) -> list[StepResult]:
        return [s for s in self.step_results if s.executed]

    @property
    def settled(self) -> bool:
        """True only when no continuation, approval, retry, or tool remains."""

        return self.phase.is_settled

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "runtime_mode": self.runtime_mode,
            "prompt": self.prompt,
            "scenario": self.scenario,
            "state": self.state.value,
            "phase": self.phase.value,
            "settled": self.settled,
            "attempt_id": self.attempt_id,
            "checkpoint_revision": self.checkpoint_revision,
            "failure_kind": self.failure_kind,
            "skill": self.selected_skill or (self.plan.skill_name if self.plan else None),
            "steps": [s.to_dict() for s in self.step_results],
            "final_output": self.final_output,
            "error": self.error,
            "approval_id": self.approval_id,
            "round": self.round,
            "executed_action_count": self.executed_action_count,
            "validation_attempts": self.validation_attempts,
            "validation_summary": self.validation_summary,
            "operating_mode": self.operating_mode,
            "execution_environment": self.execution_environment,
            "selected_skill": self.selected_skill,
            "policy": self.policy.to_dict() if self.policy else None,
            "provider_route": self.provider_route,
            "repository_context": self.repository_context,
            "context_state": self.context_state,
            "effects": [effect.to_dict() for effect in self.effects],
            "contract": self.contract.to_dict() if self.contract else None,
            "evidence": self.evidence.to_dict(),
            "goal": self.goal.to_dict() if self.goal else None,
            "value_contribution": self.value_contribution.to_dict() if self.value_contribution else None,
        }
