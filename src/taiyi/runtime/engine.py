"""TaskRuntime — the PDCA main loop for a single task.

  P (Plan)  — load the scenario, ask the scheduler for a plan.
  D (Do)    — for each step: ask governance for a permit; if cleared, execute it;
              a DENY rejects the task, a NEEDS_REVIEW suspends it (keeping the
              steps already done), a failed execution fails it.
  C (Check) — run the Validation Engine (independent of the executor).
  A (Act)   — PASS → COMPLETED and archive; FAIL → bounce back into the loop for
              another round, up to ``max_rounds``.

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
from taiyi.memory import MemoryEngine
from taiyi.scheduler import SchedulerEngine
from taiyi.validation import ValidationContext, ValidationEngine


class TaskRuntime:
    def __init__(
        self,
        scheduler: SchedulerEngine,
        audit_log: AuditLog,
        executor: Executor | None = None,
        *,
        validator: ValidationEngine | None = None,
        memory: MemoryEngine | None = None,
        max_rounds: int = 1,
    ):
        self.scheduler = scheduler
        self.audit = audit_log
        self.executor = executor or MockExecutor()
        self.validator = validator
        self.memory = memory
        self.max_rounds = max(1, max_rounds)

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
        if self.memory is not None:
            self.memory.add_message(session_id, "user", prompt)

        try:
            ctx.touch(TaskState.PARSING)
            for rnd in range(1, self.max_rounds + 1):
                ctx.round = rnd
                ctx.step_results = []
                self._plan(ctx)
                if not self._do(ctx):
                    return ctx  # DENY / NEEDS_REVIEW / failed execution: terminal

                ctx.final_output = self._synthesize(ctx)
                vr = self._validate(ctx)
                if vr is None or vr.passed:
                    ctx.touch(TaskState.COMPLETED)
                    self.audit.append(
                        "task_completed", task_id=ctx.task_id, round=rnd,
                        steps=len(ctx.executed_steps),
                    )
                    self._remember_completion(ctx)
                    return ctx

                # Validation failed → bounce back into PDCA.
                ctx.validation_summary = vr.summary
                self.audit.append(
                    "validation_failed", task_id=ctx.task_id, round=rnd,
                    failed=vr.failed_checks,
                )

            ctx.error = f"validation failed after {self.max_rounds} round(s): {ctx.validation_summary}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
            return ctx
        except Exception as e:  # noqa: BLE001 — convert any failure into a terminal state
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
            return ctx

    # --- P -------------------------------------------------------------------
    def _plan(self, ctx: TaskContext) -> None:
        ctx.touch(TaskState.PLANNING)
        ctx.plan = self.scheduler.plan(ctx.prompt, ctx.scenario)
        self.audit.append(
            "plan_created",
            task_id=ctx.task_id,
            round=ctx.round,
            skill=ctx.plan.skill_name,
            steps=[s.tool for s in ctx.plan.steps],
        )

    # --- D --------------------------------------------------------------------
    def _do(self, ctx: TaskContext) -> bool:
        """Run the plan step by step. Returns True iff every step executed."""
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
                return False

            if permit.verdict is Verdict.NEEDS_REVIEW:
                ctx.touch(TaskState.NEEDS_REVIEW)
                ctx.approval_id = permit.approval_id
                ctx.final_output = (
                    f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                )
                self.audit.append(
                    "task_needs_review", task_id=ctx.task_id, tool=step.tool,
                    approval_id=permit.approval_id,
                )
                return False

            ctx.touch(TaskState.EXECUTING)
            result = self.executor.execute(step)
            sr.executed = True
            sr.output = result.output
            self.audit.append("step_executed", task_id=ctx.task_id, tool=step.tool, ok=result.ok)
            if not result.ok:
                ctx.error = f"step failed: {step.tool}: {result.output}"
                ctx.touch(TaskState.FAILED)
                self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
                return False

        return True

    # --- C -------------------------------------------------------------------
    def _validate(self, ctx: TaskContext):
        if self.validator is None:
            return None
        ctx.touch(TaskState.VALIDATING)
        vctx = ValidationContext(
            prompt=ctx.prompt,
            scenario=ctx.scenario,
            task_type=(ctx.plan.skill_name or "generic") if ctx.plan else "generic",
            executed_tools=[sr.step.tool for sr in ctx.executed_steps],
            outputs=[sr.output for sr in ctx.executed_steps if sr.output],
            final_output=ctx.final_output or "",
        )
        return self.validator.validate(vctx)

    def _remember_completion(self, ctx: TaskContext) -> None:
        if self.memory is None:
            return
        skill = ctx.plan.skill_name if ctx.plan else None
        self.memory.remember(
            f"Completed [{skill}] via {len(ctx.executed_steps)} tool(s): {ctx.prompt}",
            tags=("task", ctx.scenario),
            source_task_id=ctx.task_id,
        )
        self.memory.observe_user(f"asked for: {ctx.prompt[:60]}")

    @staticmethod
    def _synthesize(ctx: TaskContext) -> str:
        if not ctx.executed_steps:
            return ctx.prompt
        lines = [ctx.prompt, "", "## executed"]
        for i, sr in enumerate(ctx.executed_steps, 1):
            lines.append(f"{i}. {sr.step.tool} {sr.step.args} -> {sr.output}")
        return "\n".join(lines)


def replay_task(audit: AuditLog, task_id: str) -> list[dict]:
    """Reconstruct a task's event sequence from the shared audit chain."""
    return [
        {"seq": r.seq, "event": r.event, **r.payload}
        for r in audit.records
        if r.payload.get("task_id") == task_id
    ]
