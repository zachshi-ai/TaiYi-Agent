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

import threading
import uuid
from pathlib import Path

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.approvals import ApprovalStore
from taiyi.iteration import IterationEngine
from taiyi.llm import LLMProvider, ProviderRouter, make_provider, make_provider_router
from taiyi.memory import MemoryEngine
from taiyi.multi_agent import ExpertCommittee
from taiyi.observability import Observability
from taiyi.policy import OperatingMode
from taiyi.agent import AgentRuntime
from taiyi.runtime import RunStore, TaskContext, TaskRuntime, TaskState
from taiyi.runtime.executor import Executor, RecoverableExecutor
from taiyi.scenarios import DEFAULT_SCENARIOS_DIR, ScenarioMatcher, ScenarioRegistry
from taiyi.scheduler import LLMPlanner, SchedulerEngine
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
        base_dir: str | None = None,
    ):
        self.runtime = runtime
        self.matcher = scenario_matcher
        self.skills = skills
        self.memory = memory
        self.obs = observability
        self.iteration = iteration
        self.committee = committee
        self.approvals = approvals
        # base_dir is where OODA-approved rules/skills land (rules/auto, skills/auto);
        # kept here so the review endpoints can resolve suggestions without re-deriving it.
        self.base_dir = base_dir
        self._task_lock = threading.RLock()
        self._task_threads: dict[str, threading.Thread] = {}
        self._task_results: dict[str, TaskContext] = {}
        self._task_errors: dict[str, BaseException] = {}

    def submit(
        self,
        prompt: str,
        *,
        scenario: str | None = None,
        user_id: str = "u1",
        session_id: str = "s1",
        operating_mode: str | OperatingMode | None = None,
        task_id: str | None = None,
    ) -> TaskContext:
        scenario = scenario or self.matcher.match(prompt)
        scenario_obj = self.matcher.registry.get(scenario)
        candidate = self.skills.match_candidate(prompt, scenario)
        skill = candidate if candidate is not None and candidate.production_eligible else None
        capability_error = None
        if candidate is not None and skill is None:
            capability_error = (
                f"matched Skill {candidate.name!r} is unavailable: "
                f"{'; '.join(candidate.production_problems)}"
            )
        return self.runtime.run(
            prompt,
            scenario,
            user_id=user_id,
            session_id=session_id,
            operating_mode=operating_mode,
            scenario_definition=scenario_obj.body if scenario_obj else None,
            skill_name=candidate.name if candidate else None,
            skill_instructions=skill.body if skill else None,
            capability_error=capability_error,
            task_id=task_id,
        )

    def submit_async(
        self,
        prompt: str,
        *,
        scenario: str | None = None,
        user_id: str = "u1",
        session_id: str = "s1",
        operating_mode: str | OperatingMode | None = None,
    ) -> str:
        """Accept a task immediately and advance it in a background thread."""

        if not self.runtime.run_store.persistent:
            raise RuntimeError("asynchronous tasks require a persistent base_dir")
        prefix = "a" if isinstance(self.runtime, AgentRuntime) else "t"
        task_id = f"{prefix}_{uuid.uuid4().hex}"

        def run_task() -> None:
            try:
                ctx = self.submit(
                    prompt,
                    scenario=scenario,
                    user_id=user_id,
                    session_id=session_id,
                    operating_mode=operating_mode,
                    task_id=task_id,
                )
            except BaseException as exc:  # a background failure must remain observable
                with self._task_lock:
                    self._task_errors[task_id] = exc
            else:
                with self._task_lock:
                    self._task_results[task_id] = ctx

        thread = threading.Thread(
            target=run_task,
            name=f"taiyi-task-{task_id}",
            daemon=True,
        )
        with self._task_lock:
            self._task_threads[task_id] = thread
        thread.start()
        return task_id

    def task_status(self, task_id: str) -> dict | None:
        """Read the authoritative persisted checkpoint for a submitted task."""

        checkpoint = self.runtime.run_store.load(task_id)
        if checkpoint is not None:
            context = checkpoint["context"]
            continuation = checkpoint.get("continuation") or {}
            status = {
                "task_id": task_id,
                "state": context.get("state"),
                "phase": context.get("phase"),
                "settled": context.get("phase") == "SETTLED",
                "attempt_id": context.get("attempt_id"),
                "checkpoint_revision": context.get("checkpoint_revision"),
                "scenario": context.get("scenario"),
                "operating_mode": context.get("operating_mode"),
                "execution_environment": context.get("execution_environment"),
                "executed_action_count": context.get("executed_action_count", 0),
                "approval_id": context.get("approval_id"),
                "final_output": context.get("final_output"),
                "error": context.get("error"),
                "failure_kind": context.get("failure_kind"),
                "validation_summary": context.get("validation_summary"),
                "provider_route": context.get("provider_route"),
                "contract": context.get("contract"),
                "evidence": context.get("evidence"),
                "steps": context.get("step_results", []),
                "updated_at": context.get("updated_at"),
                "continuation": {
                    key: continuation[key]
                    for key in ("kind", "operation_id", "job_id", "next_step", "next_step_index")
                    if key in continuation
                } or None,
            }
            job_id = continuation.get("job_id")
            executor = self.runtime.executor
            if job_id and hasattr(executor, "poll"):
                status["job"] = executor.poll(str(job_id)).to_dict()
            return status

        with self._task_lock:
            result = self._task_results.get(task_id)
            error = self._task_errors.get(task_id)
            thread = self._task_threads.get(task_id)
        if result is not None:
            return result.to_dict()
        if error is not None:
            return {
                "task_id": task_id,
                "state": TaskState.FAILED.value,
                "phase": "SETTLED",
                "settled": True,
                "failure_kind": "INTERNAL",
                "error": f"{type(error).__name__}: {error}",
            }
        if thread is not None:
            return {
                "task_id": task_id,
                "state": "QUEUED",
                "phase": "READY",
                "settled": False,
            }
        return None

    def task_events(self, task_id: str) -> tuple[dict, ...] | None:
        events = self.runtime.run_store.read_events(task_id)
        if events:
            return events
        with self._task_lock:
            known = task_id in self._task_threads
        if not known:
            known = self.runtime.run_store.load(task_id) is not None
        return () if known else None

    def cancel_task(self, task_id: str) -> dict:
        checkpoint = self.runtime.run_store.load(task_id)
        if checkpoint is None:
            raise KeyError(task_id)
        context = checkpoint["context"]
        if context.get("phase") == "SETTLED":
            return {"task_id": task_id, "cancelled": False, "reason": "task already settled"}
        continuation = checkpoint.get("continuation") or {}
        job_id = continuation.get("job_id")
        if not job_id:
            raise RuntimeError("task has no cancellable durable job attached")
        executor = self.runtime.executor
        if not isinstance(executor, RecoverableExecutor):
            raise RuntimeError("executor does not support durable cancellation")
        record = executor.cancel(str(job_id))
        return {
            "task_id": task_id,
            "cancelled": record.status.value == "CANCELLED",
            "job": record.to_dict(),
        }

    def resume(self, approval_id: str, *, approve: bool) -> TaskContext:
        """Resume a task suspended for human review.

        Delegates to the runtime (TaskRuntime or AgentRuntime both expose
        ``resume``). The gateway itself never executes and never grants
        clearance — it only forwards the human's decision.
        """
        return self.runtime.resume(approval_id, approve=approve)

    def resolve_review(self, suggestion_id: int, *, approve: bool):
        """Resolve an OODA suggestion: approve lands it in the auto dirs, reject drops it.

        This is the Act gate of the outer loop, exposed for human review. Approve
        writes the rule/skill YAML under base_dir's rules/auto (skills/auto), which
        governance loads read-only on the next start — the live set is never
        mutated at runtime. Returns the written path (approve) or None (reject).
        """
        if self.iteration is None:
            raise RuntimeError("iteration engine not configured")
        if not approve:
            self.iteration.reject(suggestion_id)
            return None
        if not self.base_dir:
            raise RuntimeError("no base_dir configured — cannot persist approved suggestions")
        from pathlib import Path
        base = Path(self.base_dir)
        return self.iteration.approve(
            suggestion_id, rules_dir=base / "rules", skills_dir=base / "skills"
        )


def build_gateway(
    base_dir: str | Path | None = None,
    *,
    executor: Executor | None = None,
    provider: LLMProvider | None = None,
    provider_router: ProviderRouter | None = None,
    mode: str = "agent",
    operating_mode: str | OperatingMode = OperatingMode.BALANCED,
    validator: ValidationEngine | None = None,
    max_rounds: int | None = None,
    extra_rules_dirs: tuple[str, ...] = (),
    extra_scenarios_dirs: tuple[str, ...] = (),
    extra_skills_dirs: tuple[str, ...] = (),
) -> Gateway:
    base = Path(base_dir) if base_dir else None
    audit = AuditLog(base / "audit.jsonl") if base else AuditLog()
    run_store = RunStore(base)

    # OODA outer loop: trajectories + the human-review queue persist under base/.
    # Approved suggestions land in base/rules/auto and base/skills/auto, which we
    # add to the load dirs so a restart picks them up (read-only, as the loader
    # requires) — that is the Act step of the loop, made real.
    auto_rules_dir = str(base / "rules" / "auto") if base else None
    auto_skills_dir = str(base / "skills" / "auto") if base else None
    rules_dirs = list(extra_rules_dirs) + ([auto_rules_dir] if auto_rules_dir else [])
    skills_dirs = list(extra_skills_dirs) + ([auto_skills_dir] if auto_skills_dir else [])

    governance = GovernanceEngine(audit_log=audit, extra_rules_dirs=rules_dirs or None)
    active_provider = provider or (
        provider_router.default_provider if provider_router is not None else None
    )
    workflow_planner = (
        LLMPlanner(active_provider)
        if mode == "workflow" and active_provider is not None
        else None
    )
    scheduler = SchedulerEngine(LocalPermitClient(governance), planner=workflow_planner)
    memory = MemoryEngine(base)
    observability = Observability()
    iteration = IterationEngine(base)
    approvals = ApprovalStore()
    # validator=None means "no check phase" (useful for tests / pure permit-path
    # runs). A falsy-but-not-None default only kicks in when caller omits it.
    val = ValidationEngine() if validator is None else validator
    # Re-read the caller's intent: build_gateway's signature default is None, and
    # that means "give me the standard validator". To request NO validator, pass
    # validator=False explicitly. (None == default-validator; False == none.)
    if validator is False:
        val = None

    # The runtime shape is decided here: an agent runtime (ReAct, the default
    # "highest decision-maker") needs a provider; without one it cannot reason.
    # A workflow runtime (plan-once) needs no provider. When mode=agent but no
    # provider is supplied, we fall back to workflow so the system still runs
    # offline rather than failing to start.
    # The expert committee is shared between the runtime (as a second-opinion
    # gate on governance ALLOWs) and the Gateway (for the /v1/review endpoint).
    # One instance, two consumers — so the same expert matrix reviews both at
    # permit time and on demand.
    committee = ExpertCommittee()
    use_agent = mode == "agent" and (provider is not None or provider_router is not None)
    if use_agent:
        runtime = AgentRuntime(
            scheduler,
            audit_log=audit,
            provider=active_provider,
            provider_router=provider_router,
            executor=executor,
            validator=val,
            memory=memory,
            value_stream=ValueStreamEngine(),
            observability=observability,
            iteration=iteration,
            approvals=approvals,
            committee=committee,
            default_operating_mode=operating_mode,
            run_store=run_store,
        )
    else:
        workflow_router = provider_router or (
            ProviderRouter(active_provider) if active_provider is not None else None
        )
        runtime = TaskRuntime(
            scheduler,
            audit_log=audit,
            executor=executor,
            validator=val,
            memory=memory,
            value_stream=ValueStreamEngine(),
            observability=observability,
            iteration=iteration,
            approvals=approvals,
            committee=committee,
            max_rounds=max_rounds,
            default_operating_mode=operating_mode,
            provider_router=workflow_router,
            run_store=run_store,
        )

    # A task waiting for human approval is not settled. Rehydrate its frozen
    # context and continuation before accepting traffic so a process restart does
    # not silently discard the user's pending work.
    runtime.recover_pending()

    if extra_scenarios_dirs:
        scenarios = ScenarioRegistry.load_dirs([DEFAULT_SCENARIOS_DIR, *extra_scenarios_dirs])
    else:
        scenarios = ScenarioRegistry.load_dir()
    if skills_dirs:
        skills = SkillRegistry.load_dirs([DEFAULT_SKILLS_DIR, *skills_dirs])
    else:
        skills = SkillRegistry.load_dir()
    # A release lock proves what was run when the Skill was published; this
    # fresh, side-effect-free rerun proves it still behaves under *this* Taiyi
    # runtime. Only then may the Skill be indexed or matched into task context.
    skills.verify_release_candidates()
    skills.index_into(memory)

    return Gateway(
        runtime=runtime,
        scenario_matcher=ScenarioMatcher(scenarios),
        skills=skills,
        memory=memory,
        observability=observability,
        iteration=iteration,
        committee=committee,
        approvals=approvals,
        base_dir=str(base) if base else None,
    )


def build_gateway_from_config(config) -> Gateway:
    """Build a Gateway from a TaiyiConfig — the self-operated entry point."""
    executor = None
    validator = None
    if config.executor == "sandbox":
        from taiyi.tools import SandboxExecutor
        from taiyi.validation import GitAuthority, GitHubAuthority, GitRemoteAuthority

        sandbox = config.sandbox_dir or (str(Path(config.base_dir or ".") / "sandbox"))
        job_dir = str(Path(config.base_dir) / "jobs") if config.base_dir else None
        executor = SandboxExecutor(
            sandbox,
            backend=config.sandbox_backend,
            hard_timeout=config.tool_hard_timeout,
            idle_timeout=config.tool_idle_timeout,
            heartbeat_interval=config.job_heartbeat_interval,
            job_dir=job_dir,
            output_limit=config.tool_output_limit,
        )
        authorities = []
        if config.external_git_validation:
            authorities.append(GitAuthority(sandbox))
        if config.external_git_remote_validation:
            authorities.append(GitRemoteAuthority(sandbox))
        if config.external_github_validation:
            if not config.github_expected_login:
                raise ValueError(
                    "github_expected_login is required when external_github_validation is enabled"
                )
            authorities.append(GitHubAuthority(sandbox, config.github_expected_login))
        if authorities:
            validator = ValidationEngine(
                external_authorities=tuple(authorities)
            )
    provider = make_provider(config)  # None when offline
    provider_router = make_provider_router(config, provider) if provider else None
    return build_gateway(
        base_dir=config.base_dir,
        executor=executor,
        provider=provider,
        provider_router=provider_router,
        validator=validator,
        mode=config.runtime_mode,
        operating_mode=config.operating_mode,
        max_rounds=config.max_rounds,
        extra_rules_dirs=tuple(config.rules_dirs),
        extra_scenarios_dirs=tuple(config.scenarios_dirs),
        extra_skills_dirs=tuple(config.skills_dirs),
    )
