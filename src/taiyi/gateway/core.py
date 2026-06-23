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
from taiyi.approvals import ApprovalStore
from taiyi.iteration import IterationEngine
from taiyi.memory import MemoryEngine
from taiyi.multi_agent import ExpertCommittee
from taiyi.observability import Observability
from taiyi.runtime import TaskContext, TaskRuntime
from taiyi.runtime.executor import Executor
from taiyi.scenarios import DEFAULT_SCENARIOS_DIR, ScenarioMatcher, ScenarioRegistry
from taiyi.scheduler import SchedulerEngine
from taiyi.skills import DEFAULT_SKILLS_DIR, SkillRegistry
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
        iteration: IterationEngine | None = None,
        committee: ExpertCommittee | None = None,
        approvals: ApprovalStore | None = None,
    ):
        self.runtime = runtime
        self.matcher = scenario_matcher
        self.skills = skills
        self.memory = memory
        self.obs = observability
        self.iteration = iteration
        self.committee = committee
        self.approvals = approvals

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
    extra_rules_dirs: tuple[str, ...] = (),
    extra_scenarios_dirs: tuple[str, ...] = (),
    extra_skills_dirs: tuple[str, ...] = (),
) -> Gateway:
    base = Path(base_dir) if base_dir else None
    audit = AuditLog(base / "audit.jsonl") if base else AuditLog()

    governance = GovernanceEngine(audit_log=audit, extra_rules_dirs=list(extra_rules_dirs) or None)
    scheduler = SchedulerEngine(LocalPermitClient(governance))
    memory = MemoryEngine(base)
    observability = Observability()
    iteration = IterationEngine()
    approvals = ApprovalStore()
    runtime = TaskRuntime(
        scheduler,
        audit_log=audit,
        executor=executor,
        validator=ValidationEngine(),
        memory=memory,
        value_stream=ValueStreamEngine(),
        observability=observability,
        iteration=iteration,
        approvals=approvals,
        max_rounds=max_rounds,
    )

    if extra_scenarios_dirs:
        scenarios = ScenarioRegistry.load_dirs([DEFAULT_SCENARIOS_DIR, *extra_scenarios_dirs])
    else:
        scenarios = ScenarioRegistry.load_dir()
    if extra_skills_dirs:
        skills = SkillRegistry.load_dirs([DEFAULT_SKILLS_DIR, *extra_skills_dirs])
    else:
        skills = SkillRegistry.load_dir()
    skills.index_into(memory)

    return Gateway(
        runtime=runtime,
        scenario_matcher=ScenarioMatcher(scenarios),
        skills=skills,
        memory=memory,
        observability=observability,
        iteration=iteration,
        committee=ExpertCommittee(),
        approvals=approvals,
    )


def build_gateway_from_config(config) -> Gateway:
    """Build a Gateway from a TaiyiConfig — the self-operated entry point."""
    executor = None
    if config.executor == "sandbox":
        from taiyi.tools import SandboxExecutor

        sandbox = config.sandbox_dir or (str(Path(config.base_dir or ".") / "sandbox"))
        executor = SandboxExecutor(sandbox)
    return build_gateway(
        base_dir=config.base_dir,
        executor=executor,
        max_rounds=config.max_rounds,
        extra_rules_dirs=tuple(config.rules_dirs),
        extra_scenarios_dirs=tuple(config.scenarios_dirs),
        extra_skills_dirs=tuple(config.skills_dirs),
    )
