"""The Gateway façade — the single entry point that wires the whole stack.

`build_gateway` assembles governance → scheduler → runtime, with memory, validation,
scenarios, and the gated skill catalog, all sharing one audit chain. `submit`
matches a scenario when none is given and runs the task. Channels (CLI, HTTP) sit
on top of this; they translate transport, they do not contain logic.

Defaults are safe and offline: the keyword planner and the side-effect-free mock
executor. Swap in the live LLM planner (M4) or the sandbox executor (M5) per
deployment without changing the gateway.
"""
from __future__ import annotations

from pathlib import Path

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.memory import MemoryEngine
from taiyi.observability import Observability
from taiyi.runtime import TaskContext, TaskRuntime
from taiyi.runtime.executor import Executor
from taiyi.scenarios import ScenarioMatcher, ScenarioRegistry
from taiyi.scheduler import SchedulerEngine
from taiyi.skills import SkillRegistry
from taiyi.validation import ValidationEngine
from taiyi.value_stream import ValueStreamEngine


class Gateway:
    def __init__(
        self,
        *,
        runtime: TaskRuntime,
        scenario_matcher: ScenarioMatcher,
        skills: SkillRegistry,
        memory: MemoryEngine,
        observability: Observability | None = None,
    ):
        self.runtime = runtime
        self.matcher = scenario_matcher
        self.skills = skills
        self.memory = memory
        self.obs = observability

    def submit(
        self,
        prompt: str,
        *,
        scenario: str | None = None,
        user_id: str = "u1",
        session_id: str = "s1",
    ) -> TaskContext:
        scenario = scenario or self.matcher.match(prompt)
        return self.runtime.run(prompt, scenario, user_id=user_id, session_id=session_id)


def build_gateway(
    base_dir: str | Path | None = None,
    *,
    executor: Executor | None = None,
    max_rounds: int = 1,
) -> Gateway:
    base = Path(base_dir) if base_dir else None
    audit = AuditLog(base / "audit.jsonl") if base else AuditLog()

    governance = GovernanceEngine(audit_log=audit)
    scheduler = SchedulerEngine(LocalPermitClient(governance))
    memory = MemoryEngine(base)
    observability = Observability()
    runtime = TaskRuntime(
        scheduler,
        audit_log=audit,
        executor=executor,
        validator=ValidationEngine(),
        memory=memory,
        value_stream=ValueStreamEngine(),
        observability=observability,
        max_rounds=max_rounds,
    )

    scenarios = ScenarioRegistry.load_dir()
    skills = SkillRegistry.load_dir()
    skills.index_into(memory)

    return Gateway(
        runtime=runtime,
        scenario_matcher=ScenarioMatcher(scenarios),
        skills=skills,
        memory=memory,
        observability=observability,
    )
