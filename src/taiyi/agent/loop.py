"""The iterative agent loop (reason → act → observe).

Unlike the plan-once `TaskRuntime`, this drives a step-by-step model: the LLM
proposes one tool call, governance gates it, it executes, the *result is fed back*
into the conversation, and the model decides the next step — until it answers with
no tool call (done) or the step budget runs out.

The invariant that matters is preserved exactly: **every proposed tool call goes
through `scheduler.request_permit` first.** A model cannot bypass a red line here
any more than a fixed plan can; the only difference is where the actions come from.

Offline-first: a `ScriptedProvider` drives this deterministically in tests. A live
LLM provider (the opt-in) drives it for real with the same control flow.
"""
from __future__ import annotations

import time
from contextlib import nullcontext

from taiyi.core.audit import AuditLog
from taiyi.core.types import Verdict
from taiyi.llm.base import LLMMessage, LLMProvider
from taiyi.runtime.context import StepResult, TaskContext
from taiyi.runtime.executor import Executor, MockExecutor
from taiyi.runtime.state import TaskState
from taiyi.scheduler import PlanStep, SchedulerEngine
from taiyi.validation import ValidationContext, ValidationEngine

DEFAULT_SYSTEM = (
    "You are Taiyi, a governed agent. Use tools to accomplish the task, one step "
    "at a time. Every tool call is checked by an independent governance layer that "
    "you cannot bypass. When the task is complete, reply with a final answer and no "
    "tool call."
)


class AgentRuntime:
    def __init__(
        self,
        scheduler: SchedulerEngine,
        audit_log: AuditLog,
        provider: LLMProvider,
        executor: Executor | None = None,
        *,
        validator: ValidationEngine | None = None,
        memory=None,
        value_stream=None,
        observability=None,
        iteration=None,
        max_steps: int = 8,
        system_prompt: str | None = None,
        tool_names: list[str] | None = None,
    ):
        self.scheduler = scheduler
        self.audit = audit_log
        self.provider = provider
        self.executor = executor or MockExecutor()
        self.validator = validator
        self.memory = memory
        self.value_stream = value_stream
        self.obs = observability
        self.iteration = iteration
        self.max_steps = max(1, max_steps)
        self.system = system_prompt or DEFAULT_SYSTEM
        self.tool_names = tool_names

    def run(
        self, prompt: str, scenario: str = "default", *, user_id: str = "u1", session_id: str = "s1"
    ) -> TaskContext:
        ctx = TaskContext(
            task_id=f"a_{int(time.time() * 1000)}_{len(self.audit)}",
            prompt=prompt,
            scenario=scenario,
            user_id=user_id,
            session_id=session_id,
        )
        start = time.time()
        self.audit.append("task_start", task_id=ctx.task_id, prompt=prompt, scenario=scenario, mode="agent")
        if self.memory is not None:
            self.memory.add_message(session_id, "user", prompt)
        if self.value_stream is not None:
            ctx.goal = self.value_stream.anchor(prompt, scenario)

        trace = self.obs.tracer.start(ctx.task_id) if self.obs else None
        if self.obs is not None:
            self.obs.tasks_total.inc()

        messages = [
            LLMMessage("system", self.system),
            LLMMessage("system", f"scenario: {scenario}"),
            LLMMessage("user", prompt),
        ]
        try:
            with self._span(trace, "agent_task"):
                self._loop(ctx, messages, trace)
        except Exception as e:  # noqa: BLE001
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

        self._finish(ctx, start)
        return ctx

    def _loop(self, ctx: TaskContext, messages: list[LLMMessage], trace) -> None:
        for step in range(1, self.max_steps + 1):
            ctx.round = step
            ctx.touch(TaskState.PLANNING)
            with self._span(trace, "think"):
                resp = self.provider.complete(messages, tools=self.tool_names)

            # No tool call → the model believes it is done. Validate before accepting.
            if not resp.tool_calls:
                ctx.final_output = resp.text or self._synthesize(ctx)
                vr = self._validate(ctx)
                if vr is None or vr.passed:
                    ctx.touch(TaskState.COMPLETED)
                    self.audit.append("task_completed", task_id=ctx.task_id, steps=len(ctx.executed_steps))
                    self._remember(ctx)
                    return
                ctx.validation_summary = vr.summary
                self.audit.append("validation_failed", task_id=ctx.task_id, step=step, failed=vr.failed_checks)
                messages.append(LLMMessage("user", f"Validation failed: {vr.summary}. Please correct it."))
                continue

            call = resp.tool_calls[0]
            step_obj = PlanStep(tool=call.tool, args=list(call.args))
            ctx.touch(TaskState.AWAITING_PERMIT)
            permit = self.scheduler.request_permit(
                step_obj, ctx.scenario, user_id=ctx.user_id, task_id=ctx.task_id
            )
            if self.obs is not None:
                self.obs.governance_verdict.inc(verdict=permit.verdict.value)
            sr = StepResult(step=step_obj, verdict=permit.verdict.value, reason=permit.reason,
                            matched_rule_id=permit.matched_rule_id)
            ctx.step_results.append(sr)

            if permit.verdict is Verdict.DENY:
                ctx.touch(TaskState.REJECTED)
                ctx.final_output = f"rejected by governance: {permit.reason}"
                self.audit.append("task_rejected", task_id=ctx.task_id, tool=call.tool, reason=permit.reason)
                return
            if permit.verdict is Verdict.NEEDS_REVIEW:
                ctx.touch(TaskState.NEEDS_REVIEW)
                ctx.approval_id = permit.approval_id
                ctx.final_output = f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                self.audit.append("task_needs_review", task_id=ctx.task_id, tool=call.tool, approval_id=permit.approval_id)
                return

            ctx.touch(TaskState.EXECUTING)
            with self._span(trace, "act", tool=call.tool):
                result = self.executor.execute(step_obj)
            sr.executed = True
            sr.output = result.output
            self.audit.append("step_executed", task_id=ctx.task_id, tool=call.tool, ok=result.ok)
            messages.append(LLMMessage("assistant", f"tool_call: {call.tool} {call.args}"))
            messages.append(LLMMessage("tool", result.output))
            if not result.ok:
                ctx.error = f"step failed: {call.tool}: {result.output}"
                ctx.touch(TaskState.FAILED)
                self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
                return

        ctx.error = f"step budget ({self.max_steps}) exhausted"
        ctx.touch(TaskState.FAILED)
        self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

    # --- shared helpers ------------------------------------------------------
    @staticmethod
    def _span(trace, name, **attrs):
        return trace.span(name, **attrs) if trace is not None else nullcontext()

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

    @staticmethod
    def _synthesize(ctx: TaskContext) -> str:
        if not ctx.executed_steps:
            return ctx.prompt
        lines = [ctx.prompt, "", "## executed"]
        for i, sr in enumerate(ctx.executed_steps, 1):
            lines.append(f"{i}. {sr.step.tool} {sr.step.args} -> {sr.output}")
        return "\n".join(lines)

    def _remember(self, ctx: TaskContext) -> None:
        if self.value_stream is not None and ctx.goal is not None:
            ctx.value_contribution = self.value_stream.score(
                ctx.goal, completed=True, n_steps=len(ctx.executed_steps), task_type="agent"
            )
        if self.memory is not None:
            self.memory.remember(
                f"Agent completed via {len(ctx.executed_steps)} step(s): {ctx.prompt}",
                tags=("agent", ctx.scenario), source_task_id=ctx.task_id,
            )

    def _finish(self, ctx: TaskContext, start: float) -> None:
        if self.iteration is not None:
            self.iteration.record(ctx)
        if self.obs is None:
            return
        self.obs.task_state.inc(state=ctx.state.value)
        self.obs.task_duration.observe(time.time() - start)
        self.obs.logger.info("agent_task_finished", task_id=ctx.task_id, state=ctx.state.value,
                             scenario=ctx.scenario, steps=len(ctx.executed_steps))
