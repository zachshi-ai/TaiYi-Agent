"""TaskContext — the object that flows through the PDCA loop.

It accumulates the plan, the per-step verdicts and outputs, and the final state.
A trimmed version of the full TaskContext in the Technical Architecture; value
stream fields (H4) arrive in M10.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from taiyi.policy import EvidenceLedger, TaskContract, TaskPolicy
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

    def to_dict(self) -> dict:
        return {
            "tool": self.step.tool,
            "args": list(self.step.args),
            "verdict": self.verdict,
            "reason": self.reason,
            "matched_rule_id": self.matched_rule_id,
            "executed": self.executed,
            "output": self.output,
        }


@dataclass
class TaskContext:
    task_id: str
    prompt: str
    scenario: str
    session_id: str = "s1"
    user_id: str = "u1"
    channel: str = "cli"
    state: TaskState = TaskState.PENDING
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

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "scenario": self.scenario,
            "state": self.state.value,
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
            "contract": self.contract.to_dict() if self.contract else None,
            "evidence": self.evidence.to_dict(),
            "goal": self.goal.to_dict() if self.goal else None,
            "value_contribution": self.value_contribution.to_dict() if self.value_contribution else None,
        }
