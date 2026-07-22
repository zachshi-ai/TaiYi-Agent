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
from taiyi.runtime.executor import Executor, MockExecutor
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
        if self.memory is not None:
            self.memory.add_message(session_id, "user", prompt)
        if self.value_stream is not None:
            ctx.goal = self.value_stream.anchor(prompt, scenario)  # L1: anchor goal

        capability_error = capability_error or contract.coverage_problem
        if capability_error:
            ctx.error = capability_error
            ctx.final_output = capability_error
            ctx.touch(TaskState.CAPABILITY_UNAVAILABLE)
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
                ctx.touch(TaskState.PARSING)
                self._execute_rounds(ctx, trace)
        except Exception as e:  # noqa: BLE001 — convert any failure into a terminal state
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

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
                ctx.touch(TaskState.NEEDS_INPUT)
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
                ctx.touch(TaskState.NEEDS_INPUT)
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
        ctx.touch(TaskState.FAILED)
        self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

    @staticmethod
    def _span(trace, name: str):
        return trace.span(name) if trace is not None else nullcontext()

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
        ctx.touch(TaskState.PLANNING)
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
            if self.obs is not None:
                self.obs.governance_verdict.inc(verdict=permit.verdict.value)

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
                if self.approvals is not None and permit.approval_id:
                    self.approvals.add(PendingApproval(
                        approval_id=permit.approval_id, task_id=ctx.task_id, tool=step.tool,
                        reason=permit.reason, scenario=ctx.scenario, ctx=ctx,
                        held_index=i, steps=list(steps),
                    ))
                return False

            # Governance allowed the step. Run the expert committee as a second,
            # one-way tightening gate before executing (it can escalate ALLOW →
            # NEEDS_REVIEW but never loosen a governance decision).
            permit = self._second_opinion(permit, step, ctx, i, steps)
            if permit.verdict is Verdict.NEEDS_REVIEW:
                sr.verdict = permit.verdict.value
                sr.reason = permit.reason
                ctx.touch(TaskState.NEEDS_REVIEW)
                ctx.approval_id = permit.approval_id
                ctx.final_output = (
                    f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                )
                self.audit.append(
                    "task_needs_review", task_id=ctx.task_id, tool=step.tool,
                    approval_id=permit.approval_id, source="committee",
                )
                if self.approvals is not None and permit.approval_id:
                    self.approvals.add(PendingApproval(
                        approval_id=permit.approval_id, task_id=ctx.task_id, tool=step.tool,
                        reason=permit.reason, scenario=ctx.scenario, ctx=ctx,
                        held_index=i, steps=list(steps),
                    ))
                return False

            ctx.touch(TaskState.EXECUTING)
            result = self.executor.execute(step)
            sr.executed = True
            sr.output = result.output
            ctx.executed_action_count += 1
            self.audit.append("step_executed", task_id=ctx.task_id, tool=step.tool, ok=result.ok)
            if not result.ok:
                ctx.error = f"step failed: {step.tool}: {result.output}"
                ctx.touch(TaskState.FAILED)
                self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
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
            ctx.touch(TaskState.REJECTED)
            ctx.final_output = f"rejected by human reviewer (approval_id={approval_id})"
            self.audit.append("human_rejected", task_id=ctx.task_id, approval_id=approval_id)
            return ctx

        # Approved: re-check the held step against governance before executing.
        # A human override of the review does NOT bypass governance — it only
        # upgrades the NEEDS_REVIEW to a re-evaluation. If the rule set has
        # tightened while the task was suspended (the step is now a hard DENY,
        # not merely a review), the step is refused. This closes the one place
        # where an execute previously had no preceding permit.
        self.audit.append("human_approved", task_id=ctx.task_id, approval_id=approval_id)
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
            ctx.touch(TaskState.REJECTED)
            ctx.final_output = (
                f"human approved, but governance now denies {held_step.tool!r} "
                f"({repermit.reason}); step not executed"
            )
            self.audit.append("task_rejected", task_id=ctx.task_id, tool=held_step.tool,
                              reason=repermit.reason)
            return ctx

        ctx.touch(TaskState.EXECUTING)
        result = self.executor.execute(held_step)
        held_sr.verdict = "ALLOW(human)"
        held_sr.executed = True
        held_sr.output = result.output
        ctx.executed_action_count += 1
        self.audit.append("step_executed", task_id=ctx.task_id, tool=held_step.tool, ok=result.ok,
                          approved_by="human")
        if not result.ok:
            ctx.error = f"step failed: {held_step.tool}: {result.output}"
            ctx.touch(TaskState.FAILED)
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
            ctx.touch(TaskState.NEEDS_INPUT)
        else:
            ctx.validation_attempts += 1
            ctx.validation_summary = vr.repair_feedback
            ctx.error = f"validation failed after resume: {vr.repair_feedback}"
            ctx.touch(TaskState.FAILED)
        return ctx

    # --- C -------------------------------------------------------------------
    def _validate(self, ctx: TaskContext):
        if self.validator is None:
            return None
        if ctx.validation_checklist is None:
            raise RuntimeError("validator configured without a frozen validation checklist")
        ctx.touch(TaskState.VALIDATING)
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
        ctx.touch(state)
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
