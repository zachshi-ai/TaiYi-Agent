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
from taiyi.approvals import ApprovalStore, PendingApproval
from taiyi.llm.base import LLMMessage, LLMProvider
from taiyi.llm.router import ProviderRouter
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
from taiyi.scheduler import PlanStep, SchedulerEngine
from taiyi.validation import ValidationContext, ValidationEngine

DEFAULT_SYSTEM = (
    "You are Taiyi, a governed agent. Use tools to accomplish the task, one step "
    "at a time. Every tool call is checked by an independent governance layer that "
    "you cannot bypass. When the task is complete, reply with a final answer and no "
    "tool call."
)


def _looks_like_narration(text: str) -> bool:
    """Heuristic: does this text read like the model *narrating* what it will do
    rather than delivering a final answer or a tool call?

    Catches the common failure where a real model says "I'll run echo hello for
    you" in prose instead of emitting `tool: shell:echo hello`. We nudge it back
    to tool use rather than misreading the narration as completion.
    """
    if not text:
        return False
    low = text.lower()
    narration_markers = (
        "i'll ", "i will ", "let me ", "i'll run", "i'll check", "i'll call",
        "i'm going to", "i am going to", "i can help", "i will help",
        "sure, ", "of course", "let's ", "first, i'll",
    )
    return any(m in low for m in narration_markers) and "tool:" not in low


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
        approvals: ApprovalStore | None = None,
        committee=None,
        max_steps: int | None = None,
        history_limit: int = 20,
        system_prompt: str | None = None,
        tool_names: list[str] | None = None,
        default_operating_mode: str | OperatingMode = OperatingMode.BALANCED,
        provider_router: ProviderRouter | None = None,
    ):
        self.scheduler = scheduler
        self.audit = audit_log
        self.provider = provider
        self.provider_router = provider_router or ProviderRouter(provider)
        self.executor = executor or MockExecutor()
        self.validator = validator
        self.memory = memory
        self.value_stream = value_stream
        self.obs = observability
        self.iteration = iteration
        self.approvals = approvals
        self.committee = committee
        self.max_steps = max(1, max_steps) if max_steps is not None else None
        self.history_limit = max(0, history_limit)
        self.default_operating_mode = OperatingMode.parse(default_operating_mode)
        self.completion = CompletionController()
        # Build the system prompt the model actually sees. The default prompt
        # alone is too vague for a real model — it must know the tool-call syntax
        # AND the exact tool ids (with prefixes) governance/executor expect, or it
        # will free-form answer and be misread as "done" (断裂点 1/3).
        from taiyi.tools.registry import tool_hint_block
        base_system = system_prompt or DEFAULT_SYSTEM
        self.system = base_system + "\n\n" + tool_hint_block()
        self.tool_names = tool_names

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
        provider_selection = self.provider_router.select(policy)
        contract, checklist = prepare_quality_contract(
            validator=self.validator,
            prompt=prompt,
            scenario=scenario,
            policy=policy,
            selected_skill=skill_name,
        )
        ctx = TaskContext(
            task_id=f"a_{int(time.time() * 1000)}_{len(self.audit)}",
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
            provider_route=provider_selection.to_dict(),
            contract=contract,
            validation_checklist=checklist,
        )
        start = time.time()
        self.audit.append(
            "task_start", task_id=ctx.task_id, prompt=prompt, scenario=scenario,
            mode="agent", operating_mode=ctx.operating_mode, policy=policy.to_dict(),
            execution_environment=ctx.execution_environment,
            provider_route=ctx.provider_route,
            contract=contract.to_dict(),
        )

        trace = self.obs.tracer.start(ctx.task_id) if self.obs else None
        if self.obs is not None:
            self.obs.tasks_total.inc()

        capability_error = capability_error or contract.coverage_problem
        if capability_error:
            if self.memory is not None:
                self.memory.add_message(session_id, "user", prompt)
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

        messages = [
            LLMMessage("system", self.system),
            LLMMessage("system", f"scenario: {scenario}"),
            LLMMessage("system", policy.system_guidance),
            LLMMessage("system", ctx.contract.prompt_block()),
        ]
        if scenario_definition:
            messages.append(LLMMessage(
                "system", "Trusted scenario definition and constraints:\n" + scenario_definition
            ))
        if skill_instructions:
            messages.append(LLMMessage(
                "system",
                f"Selected production-eligible skill ({skill_name or 'unnamed'}):\n"
                + skill_instructions,
            ))
        # Multi-turn context: replay recent prior turns for this session BEFORE
        # recording the current prompt, so the current prompt is not double-counted.
        # Only user/assistant turns are replayed — tool observations stay inside
        # their own task's ReAct loop and would only muddy a fresh task's context.
        if self.memory is not None and self.history_limit:
            for m in self.memory.get_messages(session_id, limit=self.history_limit):
                if m.get("role") in ("user", "assistant") and m.get("content"):
                    messages.append(LLMMessage(m["role"], m["content"]))
        # Now record the current user turn and append it to the model's context.
        if self.memory is not None:
            self.memory.add_message(session_id, "user", prompt)
        messages.append(LLMMessage("user", prompt))
        if self.value_stream is not None:
            ctx.goal = self.value_stream.anchor(prompt, scenario)
        try:
            with self._span(trace, "agent_task"):
                self._loop(ctx, messages, trace, provider_selection.provider)
        except Exception as e:  # noqa: BLE001
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

        self._finish(ctx, start)
        return ctx

    def _loop(
        self,
        ctx: TaskContext,
        messages: list[LLMMessage],
        trace,
        provider: LLMProvider,
    ) -> None:
        assert ctx.policy is not None
        step_limit = self.max_steps or ctx.policy.max_steps
        for step in range(1, step_limit + 1):
            ctx.round = step
            ctx.touch(TaskState.PLANNING)
            with self._span(trace, "think"):
                resp = provider.complete(messages, tools=self.tool_names)
            if ctx.provider_route is not None and resp.model:
                ctx.provider_route["last_response_model"] = resp.model

            # No tool call → the model believes it is done. Validate before accepting.
            if not resp.tool_calls:
                if (resp.text or "").strip().upper().startswith("QUESTION:"):
                    ctx.final_output = resp.text.strip()
                    ctx.touch(TaskState.NEEDS_INPUT)
                    self.audit.append(
                        "task_needs_input", task_id=ctx.task_id,
                        operating_mode=ctx.operating_mode, question=ctx.final_output,
                    )
                    return
                # Guard against the model answering in prose before doing any work
                # (a common failure: it narrates "I'll run X" instead of calling
                # the tool). If nothing has executed yet and the text looks like a
                # narration rather than a final answer, nudge it back to tool use
                # instead of misreading it as complete (断裂点 3).
                if not ctx.executed_steps and step == 1 and _looks_like_narration(resp.text):
                    messages.append(LLMMessage("user",
                        "You have not called any tool yet. To act, reply with a single "
                        "`tool: <id> <args>` line. If the task needs no tool and is truly "
                        "complete, reply with your final answer."))
                    continue
                ctx.final_output = resp.text or self._synthesize(ctx)
                vr = self._validate(ctx)
                action = (
                    CompletionAction.COMPLETE
                    if vr is None
                    else self.completion.assess(ctx.contract, ctx.evidence, vr)
                )
                if action is CompletionAction.COMPLETE:
                    self._accept_completion(ctx)
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
                ctx.validation_attempts += 1
                ctx.validation_summary = vr.repair_feedback
                self.audit.append("validation_failed", task_id=ctx.task_id, step=step, failed=vr.failed_checks)
                if ctx.validation_attempts >= ctx.policy.max_validation_rounds:
                    ctx.error = (
                        f"validation failed after {ctx.validation_attempts} attempt(s): "
                        f"{ctx.validation_summary}"
                    )
                    ctx.touch(TaskState.FAILED)
                    self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
                    return
                messages.append(LLMMessage(
                    "user", f"Validation failed with this evidence:\n{vr.repair_feedback}\n"
                    "Correct the failed acceptance criteria; do not repeat the same plan."
                ))
                continue

            call = resp.tool_calls[0]
            # Normalize the model's tool id to the canonical form the governance
            # rules and executor match on (e.g. "git status" -> "shell:git status").
            # Without this, a red-line rule keyed on "shell:git*" would miss a bare
            # "git" call, silently bypassing governance (断裂点 2).
            from taiyi.tools.registry import normalize_tool_name
            normalized = normalize_tool_name(call.tool)
            step_obj = PlanStep(tool=normalized, args=list(call.args))
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
                if self.approvals is not None and permit.approval_id:
                    # Park the live context AND the conversation so resume can
                    # continue the ReAct loop from exactly here. The held step
                    # is the last one in ctx.step_results (the one that needed
                    # review); held_index records its position.
                    self.approvals.add(PendingApproval(
                        approval_id=permit.approval_id, task_id=ctx.task_id,
                        tool=call.tool, reason=permit.reason, scenario=ctx.scenario,
                        ctx=ctx, held_index=len(ctx.step_results) - 1, steps=[],
                        messages=list(messages),
                    ))
                return

            # Governance allowed the step. Before executing, give the expert
            # committee a second-opinion review — a one-way tightening gate. The
            # committee can escalate ALLOW → NEEDS_REVIEW; it can never loosen a
            # governance DENY (reconsider_permit enforces this).
            permit = self._second_opinion(permit, step_obj, ctx)
            if permit.verdict is Verdict.NEEDS_REVIEW:
                # The committee escalated; update the step result and suspend.
                sr.verdict = permit.verdict.value
                sr.reason = permit.reason
                ctx.touch(TaskState.NEEDS_REVIEW)
                ctx.approval_id = permit.approval_id
                ctx.final_output = (
                    f"suspended for human review (approval_id={permit.approval_id}): {permit.reason}"
                )
                self.audit.append("task_needs_review", task_id=ctx.task_id, tool=call.tool,
                                   approval_id=permit.approval_id, source="committee")
                if self.approvals is not None and permit.approval_id:
                    self.approvals.add(PendingApproval(
                        approval_id=permit.approval_id, task_id=ctx.task_id,
                        tool=call.tool, reason=permit.reason, scenario=ctx.scenario,
                        ctx=ctx, held_index=len(ctx.step_results) - 1, steps=[],
                        messages=list(messages),
                    ))
                return

            ctx.touch(TaskState.EXECUTING)
            with self._span(trace, "act", tool=step_obj.tool):
                result = self.executor.execute(step_obj)
            sr.executed = True
            sr.output = result.output
            ctx.executed_action_count += 1
            self.audit.append("step_executed", task_id=ctx.task_id, tool=step_obj.tool, ok=result.ok)
            # Feed the observation back to the model. OpenAI/Ollama reject a bare
            # `role: "tool"` message (it requires a tool_call_id we don't carry),
            # so we replay it as a `user` turn — portable across every provider
            # (断裂点 4).
            messages.append(LLMMessage("assistant", f"tool_call: {step_obj.tool} {call.args}"))
            messages.append(LLMMessage("user", f"[tool result] {step_obj.tool}\n{result.output}"))
            if not result.ok:
                ctx.error = f"step failed: {step_obj.tool}: {result.output}"
                ctx.touch(TaskState.FAILED)
                self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
                return

        ctx.error = f"step budget ({step_limit}) exhausted"
        ctx.touch(TaskState.FAILED)
        self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

    def _second_opinion(self, permit, step_obj, ctx):
        """Run the expert committee as a second gate on a governance ALLOW.

        Only fires when governance allowed the step (a DENY/NEEDS_REVIEW is
        already at least as strict). The committee can only tighten — see
        ``taiyi.multi_agent.permit_review.reconsider_permit``. Returns the
        (possibly escalated) permit; records the arbitration in the audit log.
        """
        if self.committee is None or not permit.allowed:
            return permit
        from taiyi.core.types import build_full_call
        from taiyi.multi_agent import reconsider_permit

        subject = build_full_call(step_obj.tool, list(step_obj.args))
        arb = self.committee.review(subject, {"scenario": ctx.scenario, "task_id": ctx.task_id})
        self.audit.append(
            "committee_review", task_id=ctx.task_id, tool=step_obj.tool,
            decision=arb.decision.value, escalate=arb.escalate, conflict=arb.conflict,
        )
        # Generate an approval id for an escalation so the approval store can park it.
        approval_id = permit.approval_id
        if arb.decision.value != "APPROVED" and not approval_id:
            approval_id = f"c_{ctx.task_id}_{len(ctx.step_results)}"
        return reconsider_permit(permit, arb, approval_id=approval_id)

    def resume(self, approval_id: str, *, approve: bool) -> TaskContext:
        """Resume (or reject) an agent task suspended for human review.

        Mirrors TaskRuntime.resume's contract but for the ReAct loop: a human
        override of a NEEDS_REVIEW does NOT bypass governance. The held step is
        re-checked against governance before it is allowed to run — if the rule
        set has since turned it into a hard DENY, resume refuses. Only then does
        the loop continue feeding results back to the model.
        """
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
            self._finish(ctx, time.time())
            return ctx

        # Human approved — but governance gets the final word on the held step.
        held_sr = ctx.step_results[pending.held_index]
        held_step = held_sr.step
        self.audit.append("human_approved", task_id=ctx.task_id, approval_id=approval_id)
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
            self._finish(ctx, time.time())
            return ctx

        # Re-check passed (ALLOW or still NEEDS_REVIEW-but-human-overrode). Execute
        # the held step, feed its result back, and let the loop continue reasoning.
        ctx.touch(TaskState.EXECUTING)
        result = self.executor.execute(held_step)
        held_sr.verdict = "ALLOW(human)"
        held_sr.executed = True
        held_sr.output = result.output
        ctx.executed_action_count += 1
        self.audit.append("step_executed", task_id=ctx.task_id, tool=held_step.tool,
                          ok=result.ok, approved_by="human")

        if not result.ok:
            ctx.error = f"step failed: {held_step.tool}: {result.output}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)
            self._finish(ctx, time.time())
            return ctx

        # Rebuild the conversation: the suspended messages, plus the held call
        # and its freshly observed result, then continue the ReAct loop.
        messages: list[LLMMessage] = list(pending.messages or [])
        messages.append(LLMMessage("assistant", f"tool_call: {held_step.tool} {held_step.args}"))
        messages.append(LLMMessage("user", f"[tool result] {held_step.tool}\n{result.output}"))

        trace = self.obs.tracer.start(ctx.task_id) if self.obs else None
        assert ctx.policy is not None
        provider_selection = self.provider_router.select(ctx.policy)
        ctx.provider_route = provider_selection.to_dict()
        ctx.provider_route["resumed"] = True
        try:
            with self._span(trace, "agent_task"):
                self._loop(ctx, messages, trace, provider_selection.provider)
        except Exception as e:  # noqa: BLE001
            ctx.error = f"{type(e).__name__}: {e}"
            ctx.touch(TaskState.FAILED)
            self.audit.append("task_failed", task_id=ctx.task_id, error=ctx.error)

        self._finish(ctx, time.time())
        return ctx

    # --- shared helpers ------------------------------------------------------
    @staticmethod
    def _span(trace, name, **attrs):
        return trace.span(name, **attrs) if trace is not None else nullcontext()

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

    def _accept_completion(self, ctx: TaskContext) -> None:
        state = TaskState.successful(
            execution_environment=ctx.execution_environment,
            executed_actions=ctx.executed_action_count,
        )
        ctx.touch(state)
        self.audit.append(
            "task_simulated" if state is TaskState.SIMULATED else "task_completed",
            task_id=ctx.task_id,
            steps=len(ctx.executed_steps),
            execution_environment=ctx.execution_environment,
        )
        if state is TaskState.COMPLETED:
            self._remember(ctx)

    def _finish(self, ctx: TaskContext, start: float) -> None:
        # Record the assistant's final answer into the session so the next turn
        # in this session can replay it as conversation history (multi-turn).
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
            self.iteration.record(ctx)
        if self.obs is None:
            return
        self.obs.task_state.inc(state=ctx.state.value)
        self.obs.task_duration.observe(time.time() - start)
        self.obs.logger.info("agent_task_finished", task_id=ctx.task_id, state=ctx.state.value,
                             scenario=ctx.scenario, steps=len(ctx.executed_steps))
