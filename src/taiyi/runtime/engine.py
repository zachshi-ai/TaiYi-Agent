"""TaskRuntime — the PDCA main loop for a single task.

  P (Plan)  — load the scenario, ask the scheduler for a plan.
  D (Do)    — for each step: ask governance for a permit; if cleared, execute it;
              a DENY rejects the task, a NEEDS_REVIEW suspends it (keeping the
              steps already done).
  C (Check) — a minimal completeness check (the full Validation Engine is M6).
  A (Act)   — mark COMPLETED and archive.

The runtime shares one AuditLog with the GovernanceEngine, so each task's permit
decisions and execution events land in the same hash-chained trajectory and can
be replayed in order with ``replay_task``.
"""
from __future__ import annotations

import time

from taiyi.core.audit import AuditLog
from taiyi.core.types import Verdict
from taiyi.runtime.context import StepResult, TaskContext
from taiyi.runtime.executor import Executor, MockExecutor
from taiyi.runtime.state import TaskState
from taiyi.scheduler import SchedulerEngine


class TaskRuntime:
    def __init__(
        self,
        scheduler: SchedulerEngine,
        audit_log: AuditLog,
        executor: Executor | None = None,
    ):
        self.scheduler = scheduler
        self.audit = audit_log
        self.executor = executor or MockExecutor()

    def run(
        self,
        prompt: str,
        scenario: str = "default",
        *,
        user_id: str = "u1",
        session_id: str = "s1",
    ) -> TaskContext:
        ctx = TaskContext(
            task_id=f"t_{int(time.time() * 1000)}_{len(self.audit)}",
            prompt=prompt,
            scenario=scenario,
            user_id=user_id,
            session_id=session_id,
        )
        self.audit.append("task_start", task_id=ctx.task_id, prompt=prompt, scenario=scenario)

        try:
            self._plan(ctx)
            self._do(ctx)
        except Exception as e:  # noqa: BLE001 — convert any failure into a terminal state
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
            return ctx

        if ctx.state.is_terminal:
            return ctx  # rejected or suspended during Do

        self._check_and_act(ctx)
        return ctx

    # --- P -------------------------------------------------------------------
    def _plan(self, ctx: TaskContext) -> None:
        ctx.touch(TaskState.PARSING)
        ctx.touch(TaskState.PLANNING)
        ctx.plan = self.scheduler.plan(ctx.prompt, ctx.scenario)
        self.audit.append(
            "plan_created",
            task_id=ctx.task_id,
            skill=ctx.plan.skill_name,
            steps=[s.tool for s in ctx.plan.steps],
        )

    # --- D -------------------------------------------------------------------
    def _do(self, ctx: TaskContext) -> None:
        assert ctx.plan is not None
        for step in ctx.plan.steps:
            ctx.touch(TaskState.AWAITING_PERMIT)
            permit = self.scheduler.request_permit(
                step, ctx.scenario, user_id=ctx.user_id, task_id=ctx.task_id
            )
            sr = StepResult(
                step=step,
                verdict=permit.verdict.value,
                reason=permit.reason,
                matched_rule_id=permit.matched_rule_id,
            )
            ctx.step_results.append(sr)

            if permit.verdict is Verdict.DENY:
                ctx.touch(TaskState.REJECTED)
                ctx.final_output = f"rejected by governance: {permit.reason}"
                self.audit.append(
                    "task_rejected", task_id=ctx.task_id, tool=step.tool, reason=permit.reason
                )
                return

            if permit.verdict is Verdict.NEEDS_REVIEW:
                ctx.touch(TaskState.NEEDS_REVIEW)
                ctx.approval_id = permit.approval_id
                ctx.final_output = (
                    f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                )
                self.audit.append(
                    "task_needs_review",
                    task_id=ctx.task_id,
                    tool=step.tool,
                    approval_id=permit.approval_id,
                )
                return

            # ALLOW → execute this cleared step.
            ctx.touch(TaskState.EXECUTING)
            result = self.executor.execute(step)
            sr.executed = True
            sr.output = result.output
            self.audit.append(
                "step_executed", task_id=ctx.task_id, tool=step.tool, ok=result.ok
            )
            if not result.ok:
                ctx.error = f"step failed: {step.tool}: {result.output}"
                ctx.touch(TaskState.FAILED)
                self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
                return

    # --- C + A ---------------------------------------------------------------
    def _check_and_act(self, ctx: TaskContext) -> None:
        ctx.touch(TaskState.VALIDATING)
        ctx.final_output = self._synthesize(ctx)
        # Minimal completeness check; the full Validation Engine (objective
        # checklists, peer review) arrives in M6.
        if ctx.final_output.strip():
            ctx.touch(TaskState.COMPLETED)
            self.audit.append("task_completed", task_id=ctx.task_id, steps=len(ctx.executed_steps))
        else:
            ctx.error = "empty output"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

    @staticmethod
    def _synthesize(ctx: TaskContext) -> str:
        if not ctx.executed_steps:
            return ctx.prompt
        lines = [ctx.prompt, "", "## executed"]
        for i, sr in enumerate(ctx.executed_steps, 1):
            lines.append(f"{i}. {sr.step.tool} {sr.step.args} -> {sr.output}")
        return "\n".join(lines)


def replay_task(audit: AuditLog, task_id: str) -> list[dict]:
    """Reconstruct a task's event sequence from the shared audit chain.

    Returns the ordered events for ``task_id``. The chain itself is verified
    elsewhere; this just demonstrates that a task is fully replayable from the
    one tamper-evident log.
    """
    return [
        {"seq": r.seq, "event": r.event, **r.payload}
        for r in audit.records
        if r.payload.get("task_id") == task_id
    ]
