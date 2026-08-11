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
from contextlib import nullcontext

from taiyi.approvals import ApprovalStore, PendingApproval
from taiyi.core.audit import AuditLog
from taiyi.core.types import Verdict
from taiyi.iteration import IterationEngine
from taiyi.llm.router import ProviderRouter
from taiyi.memory import MemoryEngine
from taiyi.observability import Observability
from taiyi.policy import (
    CompletionAction,
    CompletionController,
    OperatingMode,
    resolve_policy,
)
from taiyi.runtime.context import StepResult, TaskContext
from taiyi.runtime.executor import ExecResult, Executor, MockExecutor, execute_step
from taiyi.runtime.persistence import (
    RunStore,
    continuation_steps,
    restore_context,
    workflow_continuation,
)
from taiyi.runtime.protocol import (
    CheckpointIncompatibleError,
    FailureKind,
    RunPhase,
    classify_exception,
)
from taiyi.runtime.quality import prepare_quality_contract
from taiyi.runtime.state import TaskState
from taiyi.scheduler import SchedulerEngine
from taiyi.validation import ValidationContext, ValidationEngine
from taiyi.value_stream import ValueStreamEngine


class TaskRuntime:
    def __init__(
        self,
        scheduler: SchedulerEngine,
        audit_log: AuditLog,
        executor: Executor | None = None,
        *,
        validator: ValidationEngine | None = None,
        memory: MemoryEngine | None = None,
        value_stream: ValueStreamEngine | None = None,
        observability: Observability | None = None,
        iteration: IterationEngine | None = None,
        approvals: ApprovalStore | None = None,
        committee=None,
        max_rounds: int | None = None,
        default_operating_mode: str | OperatingMode = OperatingMode.BALANCED,
        provider_router: ProviderRouter | None = None,
        run_store: RunStore | None = None,
    ):
        self.scheduler = scheduler
        self.audit = audit_log
        self.executor = executor or MockExecutor()
        self.validator = validator
        self.memory = memory
        self.value_stream = value_stream
        self.obs = observability
        self.iteration = iteration
        self.approvals = approvals
        self.committee = committee
        self.max_rounds = max(1, max_rounds) if max_rounds is not None else None
        self.default_operating_mode = OperatingMode.parse(default_operating_mode)
        self.provider_router = provider_router
        self.provider = provider_router.default_provider if provider_router else None
        self.completion = CompletionController()
        self.run_store = run_store or RunStore()

    def run(
        self,
        prompt: str,
        scenario: str = "default",
        *,
        user_id: str = "u1",
        session_id: str = "s1",
        operating_mode: str | OperatingMode | None = None,
        scenario_definition: str | None = None,
        skill_name: str | None = None,
        skill_instructions: str | None = None,
        capability_error: str | None = None,
    ) -> TaskContext:
        policy = resolve_policy(operating_mode or self.default_operating_mode, scenario=scenario)
        provider_selection = self.provider_router.select(policy) if self.provider_router else None
        contract, checklist = prepare_quality_contract(
            validator=self.validator,
            prompt=prompt,
            scenario=scenario,
            policy=policy,
            selected_skill=skill_name,
        )
        ctx = TaskContext(
            task_id=f"t_{int(time.time() * 1000)}_{len(self.audit)}",
            runtime_mode="workflow",
            prompt=prompt,
            scenario=scenario,
            user_id=user_id,
            session_id=session_id,
            operating_mode=policy.requested_mode.value,
            execution_environment=getattr(self.executor, "environment", "custom"),
            selected_skill=skill_name,
            scenario_definition=scenario_definition,
            skill_instructions=skill_instructions,
            policy=policy,
            provider_route=(provider_selection.to_dict() if provider_selection else None),
            contract=contract,
            validation_checklist=checklist,
        )
        start = time.time()
        self.audit.append(
            "task_start", task_id=ctx.task_id, prompt=prompt, scenario=scenario,
            mode="workflow", operating_mode=ctx.operating_mode, policy=policy.to_dict(),
            execution_environment=ctx.execution_environment,
            provider_route=ctx.provider_route,
            contract=contract.to_dict(),
        )
        self._record(ctx, RunPhase.READY, "run_created")
        if self.memory is not None:
            self.memory.add_message(session_id, "user", prompt)
        if self.value_stream is not None:
            ctx.goal = self.value_stream.anchor(prompt, scenario)  # L1: anchor goal

        capability_error = capability_error or contract.coverage_problem
        if capability_error:
            ctx.error = capability_error
            ctx.final_output = capability_error
            self._record(
                ctx,
                RunPhase.SETTLED,
                "run_settled",
                state=TaskState.CAPABILITY_UNAVAILABLE,
            )
            self.audit.append(
                "capability_unavailable",
                task_id=ctx.task_id,
                scenario=scenario,
                error=capability_error,
            )
            self._finish(ctx, start)
            return ctx

        trace = self.obs.tracer.start(ctx.task_id) if self.obs else None
        if self.obs is not None:
            self.obs.tasks_total.inc()

        try:
            with self._span(trace, "task"):
                self._record(ctx, RunPhase.PARSING, "phase_changed", state=TaskState.PARSING)
                self._execute_rounds(ctx, trace)
        except Exception as e:  # noqa: BLE001 — convert any failure into a terminal state
            self._fail(ctx, e)

        self._finish(ctx, start)
        return ctx

    def _execute_rounds(self, ctx: TaskContext, trace) -> None:
        assert ctx.policy is not None
        round_limit = self.max_rounds or ctx.policy.max_validation_rounds
        for rnd in range(1, round_limit + 1):
            ctx.round = rnd
            ctx.step_results = []
            with self._span(trace, "plan"):
                self._plan(ctx)
            if (
                ctx.plan is not None
                and not ctx.plan.steps
                and (ctx.plan.planner_output or "").upper().startswith("QUESTION:")
            ):
                ctx.final_output = ctx.plan.planner_output
                self._record(
                    ctx,
                    RunPhase.WAITING_INPUT,
                    "input_requested",
                    state=TaskState.NEEDS_INPUT,
                )
                self.audit.append(
                    "task_needs_input",
                    task_id=ctx.task_id,
                    operating_mode=ctx.operating_mode,
                    question=ctx.final_output,
                )
                return
            with self._span(trace, "do"):
                completed = self._do(ctx)
            if not completed:
                return  # DENY / NEEDS_REVIEW / failed execution: terminal state set

            ctx.final_output = (
                ctx.plan.planner_output
                if ctx.plan is not None and not ctx.plan.steps and ctx.plan.planner_output
                else self._synthesize(ctx)
            )
            with self._span(trace, "validate"):
                vr = self._validate(ctx)
            action = (
                CompletionAction.COMPLETE
                if vr is None
                else self.completion.assess(ctx.contract, ctx.evidence, vr)
            )
            if action is CompletionAction.COMPLETE:
                self._accept_completion(ctx, round_number=rnd)
                return

            if action is CompletionAction.NEEDS_HUMAN:
                ctx.validation_summary = vr.repair_feedback
                ctx.final_output = (
                    f"QUESTION: Please review this validation result: {vr.repair_feedback}"
                )
                self._record(
                    ctx,
                    RunPhase.WAITING_INPUT,
                    "input_requested",
                    state=TaskState.NEEDS_INPUT,
                )
                self.audit.append(
                    "validation_needs_human", task_id=ctx.task_id,
                    summary=ctx.validation_summary,
                )
                return

            # Validation failed → bounce back into PDCA.
            ctx.validation_attempts += 1
            ctx.validation_summary = vr.repair_feedback
            self.audit.append(
                "validation_failed", task_id=ctx.task_id, round=rnd, failed=vr.failed_checks
            )

        ctx.error = f"validation failed after {round_limit} round(s): {ctx.validation_summary}"
        ctx.failure_kind = FailureKind.EXTERNAL_FAILURE.value
        self._record(ctx, RunPhase.SETTLED, "run_settled", state=TaskState.FAILED)
        self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

    @staticmethod
    def _span(trace, name: str):
        return trace.span(name) if trace is not None else nullcontext()

    def _record(
        self,
        ctx: TaskContext,
        phase: RunPhase,
        event: str,
        *,
        state: TaskState | None = None,
        continuation: dict | None = None,
        **payload,
    ) -> None:
        if state is not None:
            ctx.touch(state)
        self.run_store.record(ctx, phase, event, continuation=continuation, **payload)

    def _fail(
        self,
        ctx: TaskContext,
        error: BaseException | str,
        *,
        kind: FailureKind | None = None,
    ) -> None:
        phase = ctx.phase
        ctx.error = (
            f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
        )
        resolved = kind or (
            FailureKind(ctx.failure_kind)
            if ctx.failure_kind
            else classify_exception(error, phase)
        )
        ctx.failure_kind = resolved.value
        self._record(
            ctx,
            RunPhase.SETTLED,
            "run_failed",
            state=TaskState.FAILED,
            failed_phase=phase.value,
            failure_kind=resolved.value,
        )
        self.audit.append(
            "task_failed",
            task_id=ctx.task_id,
            error=ctx.error,
            failed_phase=phase.value,
            failure_kind=resolved.value,
        )

    def _finish(self, ctx: TaskContext, start: float) -> None:
        if (
            self.memory is not None
            and ctx.final_output
            and ctx.state in (
                TaskState.COMPLETED,
                TaskState.SIMULATED,
                TaskState.NEEDS_INPUT,
                TaskState.CAPABILITY_UNAVAILABLE,
            )
        ):
            self.memory.add_message(ctx.session_id, "assistant", ctx.final_output)
        if self.iteration is not None:
            self.iteration.record(ctx)  # L5: feed the OODA outer loop
        if self.obs is None:
            return
        self.obs.task_state.inc(state=ctx.state.value)
        self.obs.task_duration.observe(time.time() - start)
        self.obs.logger.info(
            "task_finished",
            task_id=ctx.task_id,
            state=ctx.state.value,
            scenario=ctx.scenario,
            round=ctx.round,
        )

    # --- P -------------------------------------------------------------------
    def _plan(self, ctx: TaskContext) -> None:
        planning_phase = RunPhase.LLM_WAITING if self.provider_router else RunPhase.PLANNING
        self._record(ctx, planning_phase, "planning_started", state=TaskState.PLANNING)
        planning_prompt = ctx.prompt
        assert ctx.policy is not None
        trusted_parts = [ctx.policy.system_guidance]
        if ctx.contract is not None:
            trusted_parts.append(ctx.contract.prompt_block())
        if ctx.scenario_definition:
            trusted_parts.append("Trusted scenario definition:\n" + ctx.scenario_definition)
        if ctx.skill_instructions:
            trusted_parts.append(
                f"Selected production-eligible skill ({ctx.selected_skill or 'unnamed'}):\n"
                + ctx.skill_instructions
            )
        if ctx.validation_summary:
            planning_prompt += (
                "\n\nPrevious attempt failed these acceptance checks:\n"
                f"{ctx.validation_summary}\n"
                "Produce a corrected plan that addresses this evidence; do not repeat the same plan."
            )
        ctx.plan = self.scheduler.plan(
            planning_prompt,
            ctx.scenario,
            context="\n\n".join(trusted_parts),
            provider=(
                self.provider_router.select(ctx.policy).provider
                if self.provider_router is not None
                else None
            ),
        )
        if len(ctx.plan.steps) > ctx.policy.max_steps:
            self.audit.append(
                "plan_budget_exceeded",
                task_id=ctx.task_id,
                round=ctx.round,
                planned_steps=len(ctx.plan.steps),
                max_steps=ctx.policy.max_steps,
            )
            ctx.failure_kind = FailureKind.BUDGET_EXHAUSTED.value
            raise RuntimeError(
                f"planner proposed {len(ctx.plan.steps)} steps; "
                f"{ctx.operating_mode} mode permits at most {ctx.policy.max_steps}"
            )
        if ctx.provider_route is not None and ctx.plan.provider_model:
            ctx.provider_route["last_response_model"] = ctx.plan.provider_model
        self.audit.append(
            "plan_created",
            task_id=ctx.task_id,
            round=ctx.round,
            skill=ctx.plan.skill_name,
            steps=[s.tool for s in ctx.plan.steps],
            provider_route=ctx.provider_route,
        )

    # --- D --------------------------------------------------------------------
    def _do(self, ctx: TaskContext) -> bool:
        assert ctx.plan is not None
        return self._execute_steps(ctx, ctx.plan.steps, 0)

    def _execute_steps(self, ctx: TaskContext, steps: list, start: int) -> bool:
        """Gate + execute steps[start:]. Returns True iff every step executed.

        On NEEDS_REVIEW, if an approval store is configured, the suspended task is
        parked so it can be resumed; otherwise it simply suspends as before.
        """
        for i in range(start, len(steps)):
            step = steps[i]
            self._record(
                ctx,
                RunPhase.AWAITING_PERMIT,
                "permit_requested",
                state=TaskState.AWAITING_PERMIT,
                step_index=i,
                tool=step.tool,
            )
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
            if self.obs is not None:
                self.obs.governance_verdict.inc(verdict=permit.verdict.value)

            if permit.verdict is Verdict.DENY:
                ctx.final_output = f"rejected by governance: {permit.reason}"
                self._record(
                    ctx,
                    RunPhase.SETTLED,
                    "run_settled",
                    state=TaskState.REJECTED,
                    reason=permit.reason,
                )
                self.audit.append(
                    "task_rejected", task_id=ctx.task_id, tool=step.tool, reason=permit.reason
                )
                return False

            if permit.verdict is Verdict.NEEDS_REVIEW:
                ctx.approval_id = permit.approval_id
                ctx.final_output = (
                    f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                )
                self.audit.append(
                    "task_needs_review", task_id=ctx.task_id, tool=step.tool,
                    approval_id=permit.approval_id,
                )
                continuation = (
                    workflow_continuation(permit.approval_id, i, steps)
                    if permit.approval_id
                    else None
                )
                self._record(
                    ctx,
                    RunPhase.WAITING_APPROVAL,
                    "approval_requested",
                    state=TaskState.NEEDS_REVIEW,
                    continuation=continuation,
                    tool=step.tool,
                )
                if self.approvals is not None and permit.approval_id:
                    pending = PendingApproval(
                        approval_id=permit.approval_id, task_id=ctx.task_id, tool=step.tool,
                        reason=permit.reason, scenario=ctx.scenario, ctx=ctx,
                        held_index=i, steps=list(steps),
                    )
                    self.approvals.add(pending)
                return False

            # Governance allowed the step. Run the expert committee as a second,
            # one-way tightening gate before executing (it can escalate ALLOW →
            # NEEDS_REVIEW but never loosen a governance decision).
            permit = self._second_opinion(permit, step, ctx, i, steps)
            if permit.verdict is Verdict.NEEDS_REVIEW:
                sr.verdict = permit.verdict.value
                sr.reason = permit.reason
                ctx.approval_id = permit.approval_id
                ctx.final_output = (
                    f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                )
                self.audit.append(
                    "task_needs_review", task_id=ctx.task_id, tool=step.tool,
                    approval_id=permit.approval_id, source="committee",
                )
                continuation = (
                    workflow_continuation(permit.approval_id, i, steps)
                    if permit.approval_id
                    else None
                )
                self._record(
                    ctx,
                    RunPhase.WAITING_APPROVAL,
                    "approval_requested",
                    state=TaskState.NEEDS_REVIEW,
                    continuation=continuation,
                    tool=step.tool,
                )
                if self.approvals is not None and permit.approval_id:
                    pending = PendingApproval(
                        approval_id=permit.approval_id, task_id=ctx.task_id, tool=step.tool,
                        reason=permit.reason, scenario=ctx.scenario, ctx=ctx,
                        held_index=i, steps=list(steps),
                    )
                    self.approvals.add(pending)
                return False

            result = self._execute_tool(ctx, step, i)
            sr.executed = True
            sr.output = result.output
            ctx.executed_action_count += 1
            self._record(
                ctx,
                RunPhase.TOOL_RESULT,
                "tool_finished",
                state=TaskState.EXECUTING,
                step_index=i,
                tool=step.tool,
                ok=result.ok,
                operation_id=result.operation_id,
                job_id=result.job_id,
                exit_code=result.exit_code,
                signal=result.signal,
                failure_kind=result.failure_kind,
                timeout_kind=result.timeout_kind,
                stdout_artifact=result.stdout_artifact,
                stderr_artifact=result.stderr_artifact,
                output_truncated=result.output_truncated,
                duration_seconds=result.duration_seconds,
                error=result.error,
            )
            self.audit.append(
                "step_executed", task_id=ctx.task_id, tool=step.tool, ok=result.ok,
                operation_id=result.operation_id, job_id=result.job_id,
                exit_code=result.exit_code, signal=result.signal,
                failure_kind=result.failure_kind, timeout_kind=result.timeout_kind,
                duration_seconds=result.duration_seconds,
            )
            if not result.ok:
                self._fail(
                    ctx,
                    f"step failed: {step.tool}: {result.error or result.output}",
                    kind=self._result_failure_kind(result),
                )
                return False

        return True

    def _second_opinion(self, permit, step, ctx, held_index, steps):
        """Expert committee as a second gate on a governance ALLOW (one-way tighten).

        Only fires on an ALLOW. The committee can escalate to NEEDS_REVIEW; it
        never loosens a governance DENY/NEEDS_REVIEW. See
        ``taiyi.multi_agent.permit_review.reconsider_permit``.
        """
        if self.committee is None or not permit.allowed:
            return permit
        from taiyi.core.types import build_full_call
        from taiyi.multi_agent import reconsider_permit

        subject = build_full_call(step.tool, list(step.args))
        arb = self.committee.review(subject, {"scenario": ctx.scenario, "task_id": ctx.task_id})
        self.audit.append(
            "committee_review", task_id=ctx.task_id, tool=step.tool,
            decision=arb.decision.value, escalate=arb.escalate, conflict=arb.conflict,
        )
        approval_id = permit.approval_id
        if arb.decision.value != "APPROVED" and not approval_id:
            approval_id = f"c_{ctx.task_id}_{held_index}"
        return reconsider_permit(permit, arb, approval_id=approval_id)

    def resume(self, approval_id: str, *, approve: bool) -> TaskContext:
        """Resume (or reject) a task suspended for human review."""
        if self.approvals is None:
            raise RuntimeError("no approval store configured")
        pending = self.approvals.get(approval_id)
        if pending is None:
            raise KeyError(f"unknown approval: {approval_id}")
        ctx: TaskContext = pending.ctx
        self.approvals.remove(approval_id)

        if not approve:
            ctx.final_output = f"rejected by human reviewer (approval_id={approval_id})"
            self._record(
                ctx,
                RunPhase.SETTLED,
                "run_settled",
                state=TaskState.REJECTED,
                approval_id=approval_id,
            )
            self.audit.append("human_rejected", task_id=ctx.task_id, approval_id=approval_id)
            return ctx

        # Approved: re-check the held step against governance before executing.
        # A human override of the review does NOT bypass governance — it only
        # upgrades the NEEDS_REVIEW to a re-evaluation. If the rule set has
        # tightened while the task was suspended (the step is now a hard DENY,
        # not merely a review), the step is refused. This closes the one place
        # where an execute previously had no preceding permit.
        self.audit.append("human_approved", task_id=ctx.task_id, approval_id=approval_id)
        self._record(
            ctx,
            RunPhase.RECOVERING,
            "approval_resolved",
            state=TaskState.AWAITING_PERMIT,
            approval_id=approval_id,
        )
        held_step = pending.steps[pending.held_index]
        held_sr = ctx.step_results[-1]
        repermit = self.scheduler.request_permit(
            held_step, ctx.scenario, user_id=ctx.user_id, task_id=ctx.task_id
        )
        self.audit.append(
            "step_repermited", task_id=ctx.task_id, tool=held_step.tool,
            verdict=repermit.verdict.value, approved_by="human",
        )
        if repermit.verdict is Verdict.DENY:
            held_sr.verdict = "DENY(human-resubmit)"
            held_sr.reason = repermit.reason
            held_sr.matched_rule_id = repermit.matched_rule_id
            ctx.final_output = (
                f"human approved, but governance now denies {held_step.tool!r} "
                f"({repermit.reason}); step not executed"
            )
            self._record(
                ctx,
                RunPhase.SETTLED,
                "run_settled",
                state=TaskState.REJECTED,
                reason=repermit.reason,
            )
            self.audit.append("task_rejected", task_id=ctx.task_id, tool=held_step.tool,
                              reason=repermit.reason)
            return ctx

        result = self._execute_tool(
            ctx,
            held_step,
            pending.held_index,
            approved_by="human",
        )
        held_sr.verdict = "ALLOW(human)"
        held_sr.executed = True
        held_sr.output = result.output
        ctx.executed_action_count += 1
        self._record(
            ctx,
            RunPhase.TOOL_RESULT,
            "tool_finished",
            state=TaskState.EXECUTING,
            tool=held_step.tool,
            ok=result.ok,
            operation_id=result.operation_id,
            job_id=result.job_id,
            exit_code=result.exit_code,
            signal=result.signal,
            failure_kind=result.failure_kind,
            timeout_kind=result.timeout_kind,
            stdout_artifact=result.stdout_artifact,
            stderr_artifact=result.stderr_artifact,
            output_truncated=result.output_truncated,
            duration_seconds=result.duration_seconds,
            error=result.error,
        )
        self.audit.append("step_executed", task_id=ctx.task_id, tool=held_step.tool, ok=result.ok,
                          approved_by="human", operation_id=result.operation_id,
                          job_id=result.job_id, exit_code=result.exit_code,
                          signal=result.signal, failure_kind=result.failure_kind,
                          timeout_kind=result.timeout_kind,
                          duration_seconds=result.duration_seconds)
        if not result.ok:
            self._fail(
                ctx,
                f"step failed: {held_step.tool}: {result.error or result.output}",
                kind=self._result_failure_kind(result),
            )
            return ctx

        if not self._execute_steps(ctx, pending.steps, pending.held_index + 1):
            return ctx  # re-suspended / rejected / failed downstream

        ctx.final_output = self._synthesize(ctx)
        vr = self._validate(ctx)
        action = (
            CompletionAction.COMPLETE
            if vr is None
            else self.completion.assess(ctx.contract, ctx.evidence, vr)
        )
        if action is CompletionAction.COMPLETE:
            self._accept_completion(ctx)
        elif action is CompletionAction.NEEDS_HUMAN:
            ctx.validation_summary = vr.repair_feedback
            ctx.final_output = (
                f"QUESTION: Please review this validation result: {vr.repair_feedback}"
            )
            self._record(
                ctx,
                RunPhase.WAITING_INPUT,
                "input_requested",
                state=TaskState.NEEDS_INPUT,
            )
        else:
            ctx.validation_attempts += 1
            ctx.validation_summary = vr.repair_feedback
            ctx.error = f"validation failed after resume: {vr.repair_feedback}"
            ctx.failure_kind = FailureKind.EXTERNAL_FAILURE.value
            self._record(ctx, RunPhase.SETTLED, "run_settled", state=TaskState.FAILED)
        return ctx

    def _execute_tool(
        self,
        ctx: TaskContext,
        step,
        step_index: int,
        *,
        approved_by: str | None = None,
    ) -> ExecResult:
        operation_id = f"{ctx.task_id}:round:{ctx.round}:step:{step_index}"
        continuation = {
            "kind": "tool_operation",
            "operation_id": operation_id,
            "step_index": step_index,
            "tool": step.tool,
            "args": list(step.args),
        }
        if approved_by:
            continuation["approved_by"] = approved_by
        self._record(
            ctx,
            RunPhase.TOOL_RUNNING,
            "tool_started",
            state=TaskState.EXECUTING,
            continuation=continuation,
            operation_id=operation_id,
            step_index=step_index,
            tool=step.tool,
        )

        def attached(handle) -> None:
            attached_continuation = {**continuation, "job_id": handle.job_id}
            self._record(
                ctx,
                RunPhase.TOOL_RUNNING,
                "job_attached",
                state=TaskState.EXECUTING,
                continuation=attached_continuation,
                operation_id=operation_id,
                job_id=handle.job_id,
                step_index=step_index,
                tool=step.tool,
            )

        return execute_step(
            self.executor,
            step,
            operation_id=operation_id,
            on_started=attached,
        )

    @staticmethod
    def _result_failure_kind(result: ExecResult) -> FailureKind:
        if result.failure_kind:
            try:
                return FailureKind(result.failure_kind)
            except ValueError:
                pass
        return FailureKind.TOOL_EXIT_NONZERO

    def recover_pending(self) -> int:
        """Rehydrate workflow approvals persisted before a process restart."""

        if self.approvals is None:
            return 0
        recovered = 0
        for checkpoint in self.run_store.iter_checkpoints():
            snapshot = checkpoint.get("context") or {}
            if checkpoint.get("_load_error"):
                self.audit.append(
                    "run_recovery_failed",
                    task_id=snapshot.get("task_id"),
                    error=checkpoint["_load_error"],
                )
                continue
            continuation = checkpoint.get("continuation") or {}
            if snapshot.get("runtime_mode") != "workflow":
                continue
            if snapshot.get("phase") != RunPhase.WAITING_APPROVAL.value:
                continue
            if continuation.get("kind") != "workflow_approval":
                continue
            approval_id = str(continuation.get("approval_id", ""))
            if not approval_id or self.approvals.get(approval_id) is not None:
                continue
            try:
                ctx = restore_context(
                    snapshot,
                    validator=self.validator,
                    value_stream=self.value_stream,
                )
                steps = continuation_steps(continuation)
                held_index = int(continuation["held_index"])
                held = steps[held_index]
                if ctx.approval_id != approval_id:
                    raise CheckpointIncompatibleError("approval id differs from checkpoint context")
                if ctx.plan is None or ctx.plan.steps != steps:
                    raise CheckpointIncompatibleError("continuation plan differs from frozen plan")
                if ctx.step_results[held_index].step != held:
                    raise CheckpointIncompatibleError("held step differs from checkpoint context")
            except (
                CheckpointIncompatibleError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
                self.audit.append(
                    "run_recovery_failed",
                    task_id=snapshot.get("task_id"),
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            previous_attempt = ctx.attempt_id
            ctx.attempt_id += 1
            reason = (
                ctx.step_results[-1].reason
                if ctx.step_results
                else "restored pending approval"
            )
            self.approvals.add(PendingApproval(
                approval_id=approval_id,
                task_id=ctx.task_id,
                tool=held.tool,
                reason=reason,
                scenario=ctx.scenario,
                ctx=ctx,
                held_index=held_index,
                steps=steps,
            ))
            self._record(
                ctx,
                RunPhase.WAITING_APPROVAL,
                "run_recovered",
                state=TaskState.NEEDS_REVIEW,
                continuation=continuation,
                previous_attempt=previous_attempt,
            )
            recovered += 1
        return recovered

    # --- C -------------------------------------------------------------------
    def _validate(self, ctx: TaskContext):
        if self.validator is None:
            return None
        if ctx.validation_checklist is None:
            raise RuntimeError("validator configured without a frozen validation checklist")
        self._record(ctx, RunPhase.VALIDATING, "validation_started", state=TaskState.VALIDATING)
        vctx = ValidationContext(
            prompt=ctx.prompt,
            scenario=ctx.scenario,
            task_type=ctx.validation_checklist.task_type,
            executed_tools=[sr.step.tool for sr in ctx.executed_steps],
            executed_calls=[
                {"tool": sr.step.tool, "args": list(sr.step.args)}
                for sr in ctx.executed_steps
            ],
            outputs=[sr.output for sr in ctx.executed_steps if sr.output],
            final_output=ctx.final_output or "",
            extras={"require_step_outputs": True},
        )
        assert ctx.policy is not None
        result = self.validator.validate(
            vctx,
            checklist=ctx.validation_checklist,
        )
        ctx.evidence.record_validation(
            result,
            attempt=ctx.validation_attempts + 1,
            contract_id=ctx.contract.contract_id,
        )
        return result

    def _remember_completion(self, ctx: TaskContext) -> None:
        skill = ctx.selected_skill or (ctx.plan.skill_name if ctx.plan else None)
        if self.value_stream is not None and ctx.goal is not None:  # L4: score contribution
            ctx.value_contribution = self.value_stream.score(
                ctx.goal,
                completed=True,
                n_steps=len(ctx.executed_steps),
                task_type=skill or "generic",
            )
        if self.memory is None:
            return
        self.memory.remember(
            f"Completed [{skill}] via {len(ctx.executed_steps)} tool(s): {ctx.prompt}",
            tags=("task", ctx.scenario),
            source_task_id=ctx.task_id,
        )
        self.memory.observe_user(f"asked for: {ctx.prompt[:60]}")

    def _accept_completion(
        self,
        ctx: TaskContext,
        *,
        round_number: int | None = None,
    ) -> None:
        state = TaskState.successful(
            execution_environment=ctx.execution_environment,
            executed_actions=ctx.executed_action_count,
        )
        self._record(ctx, RunPhase.SETTLED, "run_settled", state=state)
        payload = {
            "task_id": ctx.task_id,
            "steps": len(ctx.executed_steps),
            "execution_environment": ctx.execution_environment,
        }
        if round_number is not None:
            payload["round"] = round_number
        self.audit.append(
            "task_simulated" if state is TaskState.SIMULATED else "task_completed",
            **payload,
        )
        if state is TaskState.COMPLETED:
            self._remember_completion(ctx)

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
