"""An LLM-driven planner.

Implements the same ``Planner`` interface as ``KeywordPlanner``, so it drops into
``SchedulerEngine`` unchanged. The model proposes the tool calls; the planner only
transcribes them into an ``ExecutionPlan``. It deliberately does **not** execute
anything and has no path to a permit — every proposed step is still gated by
governance in the runtime loop. That is why a prompt-injected model cannot run a
red-line action: it can *ask*, but governance answers.

Swapping the offline provider for a live one changes where the tool calls come
from, not whether they are gated.
"""
from __future__ import annotations

from taiyi.llm.base import LLMMessage, LLMProvider
from taiyi.scheduler.planner import ExecutionPlan, PlanStep

_DEFAULT_SYSTEM = (
    "You are a planning component. Propose the tool calls needed to satisfy the "
    "user's request. You do not have authority to bypass any safety check."
)


class LLMPlanner:
    def __init__(self, provider: LLMProvider, *, system_prompt: str | None = None):
        self._provider = provider
        self._system = system_prompt or _DEFAULT_SYSTEM

    def plan(self, prompt: str, scenario: str) -> ExecutionPlan:
        return self.plan_with_context(prompt, scenario, context=None)

    def plan_with_context(
        self,
        prompt: str,
        scenario: str,
        *,
        context: str | None,
    ) -> ExecutionPlan:
        return self.plan_with_provider(
            prompt,
            scenario,
            context=context,
            provider=self._provider,
        )

    def plan_with_provider(
        self,
        prompt: str,
        scenario: str,
        *,
        context: str | None,
        provider: LLMProvider,
    ) -> ExecutionPlan:
        """Plan with a task-selected provider.

        ``SchedulerEngine`` only calls this seam when a Provider Router selected
        a concrete route. The planner still has no execution or permit authority.
        """
        messages = [
            LLMMessage("system", self._system),
            LLMMessage("system", f"scenario: {scenario}"),
        ]
        if context:
            messages.append(LLMMessage("system", context))
        messages.append(LLMMessage("user", prompt))
        resp = provider.complete(messages)
        steps = [PlanStep(tool=tc.tool, args=list(tc.args)) for tc in resp.tool_calls]
        return ExecutionPlan(
            skill_name=None,
            steps=steps,
            rationale=f"LLM ({resp.model}) proposed {len(steps)} tool call(s)",
            provider_model=resp.model,
            planner_output=(resp.text or "").strip() or None,
        )
