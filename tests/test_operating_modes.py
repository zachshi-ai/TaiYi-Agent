"""Three-mode control plane: different policy, identical governance floor."""
from __future__ import annotations

import json

from taiyi.agent import AgentRuntime
from taiyi.cli import main
from taiyi.core.audit import AuditLog
from taiyi.gateway import GatewayApp, build_gateway
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMMessage, LLMResponse, ProviderRouter, ScriptedProvider, ToolCall
from taiyi.policy import OperatingMode, VerificationDepth, resolve_policy
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import ExecutionPlan, LLMPlanner, PlanStep, SchedulerEngine
from taiyi.validation import ModelJudge, ValidationEngine


def _scheduler(planner=None):
    audit = AuditLog()
    governance = GovernanceEngine(audit_log=audit)
    return audit, SchedulerEngine(LocalPermitClient(governance), planner=planner)


def test_profiles_encode_three_distinct_operating_strategies():
    quality = resolve_policy("quality")
    balanced = resolve_policy("balanced")
    efficiency = resolve_policy("efficiency")

    assert quality.verification_depth is VerificationDepth.EXHAUSTIVE
    assert balanced.verification_depth is VerificationDepth.STANDARD
    assert efficiency.verification_depth is VerificationDepth.CRITICAL
    assert quality.max_steps > balanced.max_steps > efficiency.max_steps
    assert quality.max_validation_rounds > balanced.max_validation_rounds > efficiency.max_validation_rounds
    assert quality.ask_strategy == "material_ambiguity"
    assert efficiency.ask_strategy == "blocking_only"
    assert quality.requires_independent_review is True
    assert balanced.requires_independent_review is False
    assert efficiency.requires_independent_review is False


def test_scenario_risk_can_only_tighten_an_efficiency_profile():
    low = resolve_policy("efficiency", scenario="default")
    high = resolve_policy("efficiency", scenario="customer_service.refund")
    assert low.verification_depth is VerificationDepth.CRITICAL
    assert high.verification_depth is VerificationDepth.STANDARD
    assert high.requested_mode is OperatingMode.EFFICIENCY


class CapturingProvider(ScriptedProvider):
    def __init__(self, responses):
        super().__init__(responses)
        self.seen: list[list[LLMMessage]] = []

    def complete(self, messages, *, tools=None):
        self.seen.append(list(messages))
        return super().complete(messages, tools=tools)


def test_agent_receives_policy_and_can_suspend_for_a_material_question():
    provider = CapturingProvider([LLMResponse(text="QUESTION: Which repository is the target?")])
    audit, scheduler = _scheduler()
    runtime = AgentRuntime(scheduler, audit, provider, validator=None)

    ctx = runtime.run("ship the change", operating_mode="quality")

    assert ctx.state is TaskState.NEEDS_INPUT
    assert ctx.operating_mode == "quality"
    assert ctx.contract is not None
    assert any("Operating mode: quality" in m.content for m in provider.seen[0])


def test_gateway_injects_matched_scenario_and_production_skill_into_agent_context():
    provider = CapturingProvider([LLMResponse(text="ready")])
    gateway = build_gateway(provider=provider, validator=False)

    ctx = gateway.submit("commit my changes", scenario="dev.git")

    assert ctx.state is TaskState.COMPLETED
    assert ctx.selected_skill == "git_safe_commit"
    contents = [m.content for m in provider.seen[0]]
    assert any("committer identity must match" in c for c in contents)
    assert any("Selected production-eligible skill (git_safe_commit)" in c for c in contents)
    assert any("git status" in c and "git commit" in c for c in contents)


def test_workflow_llm_planner_receives_trusted_context_separately_from_user_prompt():
    provider = CapturingProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["done"])])
    ])
    audit, scheduler = _scheduler(LLMPlanner(provider))
    runtime = TaskRuntime(scheduler, audit, validator=None)

    ctx = runtime.run(
        "do the task",
        scenario_definition="constraint: preserve ownership",
        skill_name="trusted_skill",
        skill_instructions="step: verify the artifact",
    )

    assert ctx.state is TaskState.SIMULATED
    messages = provider.seen[0]
    assert messages[-1].role == "user" and messages[-1].content == "do the task"
    assert any("preserve ownership" in m.content for m in messages if m.role == "system")
    assert any("verify the artifact" in m.content for m in messages if m.role == "system")
    assert any("Operating mode: balanced" in m.content for m in messages if m.role == "system")
    assert any("Task contract:" in m.content for m in messages if m.role == "system")


def test_workflow_preserves_a_model_clarification_instead_of_completing_an_empty_plan():
    provider = ScriptedProvider([
        LLMResponse(text="QUESTION: Which repository should I change?", model="planner")
    ])
    audit, scheduler = _scheduler(LLMPlanner(provider))
    runtime = TaskRuntime(scheduler, audit, validator=None)

    ctx = runtime.run("ship the change", operating_mode="quality")

    assert ctx.state is TaskState.NEEDS_INPUT
    assert ctx.final_output == "QUESTION: Which repository should I change?"
    assert ctx.executed_steps == []


def test_agent_step_budget_is_selected_by_operating_mode():
    observed = {}
    for mode in OperatingMode:
        provider = ScriptedProvider([
            LLMResponse(tool_calls=[ToolCall("echo", ["work"])])
        ] * 20)
        audit, scheduler = _scheduler()
        runtime = AgentRuntime(scheduler, audit, provider, validator=None)
        ctx = runtime.run("keep working", operating_mode=mode)
        observed[mode.value] = len(ctx.executed_steps)
        assert ctx.state is TaskState.FAILED

    assert observed == {"quality": 16, "balanced": 8, "efficiency": 5}


def test_workflow_rejects_a_plan_that_exceeds_the_mode_step_budget():
    provider = ScriptedProvider([
        LLMResponse(
            tool_calls=[ToolCall("echo", [str(i)]) for i in range(6)],
            model="planner",
        )
    ])
    audit, scheduler = _scheduler(LLMPlanner(provider))
    runtime = TaskRuntime(scheduler, audit, validator=None)

    ctx = runtime.run("do six things", operating_mode="efficiency")

    assert ctx.state is TaskState.FAILED
    assert "efficiency mode permits at most 5" in (ctx.error or "")
    assert ctx.executed_steps == []
    assert any(r.event == "plan_budget_exceeded" for r in audit.records)


class AlwaysIncompletePlanner:
    def __init__(self):
        self.prompts: list[str] = []

    def plan(self, prompt, scenario):
        self.prompts.append(prompt)
        return ExecutionPlan(None, [PlanStep("shell:git status")], "intentionally incomplete")


def test_workflow_repair_budget_and_feedback_follow_the_mode():
    calls = {}
    for mode in OperatingMode:
        planner = AlwaysIncompletePlanner()
        audit, scheduler = _scheduler(planner)
        runtime = TaskRuntime(scheduler, audit, validator=ValidationEngine())
        ctx = runtime.run("commit my work", "dev.git", operating_mode=mode)
        calls[mode.value] = len(planner.prompts)
        assert ctx.state is TaskState.FAILED
        if len(planner.prompts) > 1:
            assert "Previous attempt failed these acceptance checks" in planner.prompts[1]
            assert "git_commit_executed" in planner.prompts[1]

    assert calls == {"quality": 3, "balanced": 2, "efficiency": 1}


def test_quality_records_deeper_acceptance_evidence_than_efficiency():
    quality = build_gateway().submit(
        "commit my changes", scenario="dev.git", operating_mode="quality"
    )
    efficiency = build_gateway().submit(
        "commit my changes", scenario="dev.git", operating_mode="efficiency"
    )

    assert quality.state is TaskState.SIMULATED
    assert efficiency.state is TaskState.SIMULATED
    assert quality.contract is not None and efficiency.contract is not None
    assert len(quality.contract.acceptance_criteria) > len(efficiency.contract.acceptance_criteria)
    assert not quality.contract.missing_evidence(quality.evidence)
    assert not efficiency.contract.missing_evidence(efficiency.evidence)


def test_quality_refuses_baseline_only_checks_before_any_action():
    ctx = build_gateway().submit("do an unknown task", operating_mode="quality")

    assert ctx.state is TaskState.CAPABILITY_UNAVAILABLE
    assert ctx.executed_steps == []
    assert ctx.contract.objective_evidence_required is True
    assert ctx.contract.objective_covered is False
    assert "no objective-specific acceptance checker" in (ctx.error or "")


def test_efficiency_can_simulate_low_risk_generic_work_with_explicit_baseline_status():
    ctx = build_gateway().submit("do an unknown task", operating_mode="efficiency")

    assert ctx.state is TaskState.SIMULATED
    assert ctx.contract.objective_evidence_required is False
    assert ctx.contract.objective_covered is False
    assert ctx.contract.to_dict()["coverage"] == "baseline_only"


def test_efficiency_mode_never_weakens_governance():
    for mode in OperatingMode:
        provider = ScriptedProvider([
            LLMResponse(tool_calls=[
                ToolCall("shell:git commit", ["-c", "user.name=Evil", "-m", "x"])
            ])
        ])
        audit, scheduler = _scheduler()
        runtime = AgentRuntime(scheduler, audit, provider, validator=None)
        ctx = runtime.run("commit", "dev.git", operating_mode=mode)
        assert ctx.state is TaskState.REJECTED
        assert ctx.step_results[-1].matched_rule_id == "authorship.git_identity.no_override"


def test_gateway_accepts_per_task_mode_and_exposes_policy_contract_and_evidence():
    app = GatewayApp(build_gateway())
    status, data = app.handle(
        "POST",
        "/v1/tasks",
        {},
        json.dumps({"prompt": "do a small thing", "operating_mode": "efficiency"}),
    )
    assert status == 200
    assert data["operating_mode"] == "efficiency"
    assert data["execution_environment"] == "mock"
    assert data["policy"]["ask_strategy"] == "blocking_only"
    assert data["contract"]["operating_mode"] == "efficiency"
    assert data["contract"]["task_type"] == "generic"
    assert data["contract"]["task_parameters"] == {}
    assert data["contract"]["immutable"] is True
    assert data["contract"]["contract_id"].startswith("sha256:")
    assert data["contract"]["checklist_id"].startswith("sha256:")
    assert data["evidence"]["records"]
    assert all(
        r["contract_id"] == data["contract"]["contract_id"]
        and r["subject_digest"].startswith("sha256:")
        for r in data["evidence"]["records"]
    )

    bad_status, bad = app.handle(
        "POST", "/v1/tasks", {}, json.dumps({"prompt": "x", "operating_mode": "turbo"})
    )
    assert bad_status == 400
    assert "unknown operating mode" in bad["error"]


def test_gateway_exposes_the_concrete_provider_route_selected_for_the_mode():
    default = ScriptedProvider([LLMResponse(text="balanced", model="model-balanced")])
    default.name = "provider:default"
    default.model = "model-balanced"
    quality = ScriptedProvider([LLMResponse(text="quality", model="model-strong")])
    quality.name = "provider:quality"
    quality.model = "model-strong"
    router = ProviderRouter(default, strongest_capable=quality)
    app = GatewayApp(build_gateway(provider=default, provider_router=router, validator=False))

    status, data = app.handle(
        "POST",
        "/v1/tasks",
        {},
        json.dumps({"prompt": "answer", "operating_mode": "quality"}),
    )

    assert status == 200
    assert data["provider_route"] == {
        "requested_strategy": "strongest_capable",
        "route": "quality",
        "provider": "provider:quality",
        "model": "model-strong",
        "fallback": False,
        "last_response_model": "model-strong",
    }


def test_cli_accepts_a_per_task_operating_mode(capsys):
    code = main(["run", "do a small thing", "--operating-mode", "efficiency"])

    assert code == 0
    assert "mode:     efficiency" in capsys.readouterr().out


def test_quality_mode_surfaces_an_independent_judge_escalation_as_input_needed():
    judge = ModelJudge(
        ScriptedProvider([LLMResponse(text="NEEDS_HUMAN: visual quality is ambiguous")]),
        "Escalate genuinely ambiguous quality decisions.",
    )
    audit, scheduler = _scheduler()
    runtime = TaskRuntime(
        scheduler,
        audit,
        validator=ValidationEngine(model_judge=judge),
    )

    ctx = runtime.run("do a small thing", operating_mode="quality")

    assert ctx.state is TaskState.NEEDS_INPUT
    assert (ctx.final_output or "").startswith("QUESTION:")
    assert any(
        c.criterion_id.startswith("model_judge:quality@")
        for c in ctx.contract.acceptance_criteria
    )


def test_balanced_independent_review_is_triggered_by_high_risk_only():
    high_judge_provider = ScriptedProvider([
        LLMResponse(text="PASS: independently reviewed")
    ])
    high_judge = ModelJudge(high_judge_provider, "Review consequential work.")
    high_audit, high_scheduler = _scheduler()
    high_runtime = TaskRuntime(
        high_scheduler,
        high_audit,
        validator=ValidationEngine(model_judge=high_judge),
    )

    high = high_runtime.run(
        "do a small thing",
        scenario="customer_service.refund",
        operating_mode="balanced",
    )

    assert high.state is TaskState.SIMULATED
    assert high.policy.requires_independent_review is True
    assert any(r.source == "model_judge" for r in high.evidence.records)

    low_judge_provider = ScriptedProvider([])
    low_judge = ModelJudge(low_judge_provider, "Should not be called for low risk.")
    low_audit, low_scheduler = _scheduler()
    low_runtime = TaskRuntime(
        low_scheduler,
        low_audit,
        validator=ValidationEngine(model_judge=low_judge),
    )

    low = low_runtime.run("do a small thing", operating_mode="balanced")

    assert low.state is TaskState.SIMULATED
    assert low.policy.requires_independent_review is False


def test_no_mode_can_fall_through_to_planner_when_matched_skill_is_unverified(tmp_path):
    override = tmp_path / "skills" / "git_safe_commit"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text(
        "---\n"
        "name: git_safe_commit\n"
        "category: managed\n"
        "triggers: [commit, git]\n"
        "scenario: dev.git\n"
        "---\n"
        "# Unverified override\n",
        encoding="utf-8",
    )
    gateway = build_gateway(extra_skills_dirs=(str(tmp_path / "skills"),))

    for mode in OperatingMode:
        ctx = gateway.submit("commit my changes", scenario="dev.git", operating_mode=mode)
        assert ctx.state is TaskState.CAPABILITY_UNAVAILABLE
        assert ctx.selected_skill == "git_safe_commit"
        assert ctx.executed_steps == []
        assert "missing quality_gate.md" in (ctx.error or "")


def test_agent_modes_select_real_providers_and_record_fallback_evidence():
    default = ScriptedProvider([LLMResponse(text="balanced", model="model-balanced")])
    default.name = "provider:default"
    default.model = "model-balanced"
    quality = ScriptedProvider([LLMResponse(text="quality", model="model-strong")])
    quality.name = "provider:quality"
    quality.model = "model-strong"
    efficiency = ScriptedProvider([LLMResponse(text="efficiency", model="model-fast")])
    efficiency.name = "provider:efficiency"
    efficiency.model = "model-fast"
    router = ProviderRouter(
        default,
        strongest_capable=quality,
        fastest_capable=efficiency,
    )
    audit, scheduler = _scheduler()
    runtime = AgentRuntime(
        scheduler,
        audit,
        default,
        provider_router=router,
        validator=None,
    )

    quality_ctx = runtime.run("answer", operating_mode="quality")
    balanced_ctx = runtime.run("answer", operating_mode="balanced")
    efficiency_ctx = runtime.run("answer", operating_mode="efficiency")

    assert quality_ctx.final_output == "quality"
    assert quality_ctx.provider_route["model"] == "model-strong"
    assert quality_ctx.provider_route["fallback"] is False
    assert balanced_ctx.final_output == "balanced"
    assert balanced_ctx.provider_route["model"] == "model-balanced"
    assert balanced_ctx.provider_route["fallback"] is True
    assert efficiency_ctx.final_output == "efficiency"
    assert efficiency_ctx.provider_route["model"] == "model-fast"
    assert efficiency_ctx.provider_route["fallback"] is False


def test_workflow_mode_uses_the_same_provider_router_for_planning():
    default = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["balanced"])], model="model-balanced")
    ])
    default.name = "provider:default"
    default.model = "model-balanced"
    quality = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["quality"])], model="model-strong")
    ])
    quality.name = "provider:quality"
    quality.model = "model-strong"
    router = ProviderRouter(default, strongest_capable=quality)
    gateway = build_gateway(
        provider=default,
        provider_router=router,
        mode="workflow",
        validator=False,
    )

    ctx = gateway.submit("answer", operating_mode="quality")

    assert ctx.state is TaskState.SIMULATED
    assert ctx.executed_steps[0].output == "[mock] echo ['quality']"
    assert ctx.provider_route == {
        "requested_strategy": "strongest_capable",
        "route": "quality",
        "provider": "provider:quality",
        "model": "model-strong",
        "fallback": False,
        "last_response_model": "model-strong",
    }
