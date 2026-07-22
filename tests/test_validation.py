"""Validation Engine + bounce-back correction loop (Module 6).

Covers selection-by-key, cheapest-first short-circuiting (a failed deterministic
check must not spend a model-judge call), isolated/version-tracked model judging
with calibration, and a validation failure bouncing the task back into PDCA until
it either succeeds or exhausts its rounds.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.llm.base import LLMResponse as _R  # noqa: F401  (clarity in helpers)
from taiyi.policy import AcceptanceCriterion, EvidenceLedger, EvidenceRecord
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import LLMPlanner, SchedulerEngine
from taiyi.validation import (
    CheckKind,
    ModelJudge,
    Outcome,
    ValidationContext,
    ValidationEngine,
    deterministic,
    select_checks,
)
from taiyi.policy import VerificationDepth


class CountingProvider:
    name = "counting"

    def __init__(self, text="PASS"):
        self.calls = 0
        self.text = text
        self.last_messages = []

    def complete(self, messages, *, tools=None):
        self.calls += 1
        self.last_messages = list(messages)
        return LLMResponse(text=self.text)


def vctx(
    scenario="dev.git",
    tools=("shell:git commit",),
    output="done",
    task_type="git_safe_commit",
):
    return ValidationContext(
        prompt="x", scenario=scenario, task_type=task_type,
        executed_tools=list(tools), final_output=output,
    )


# --- Selection ---------------------------------------------------------------

def test_select_checks_includes_universal_and_task_type():
    ids = {c.id for c in select_checks("git_safe_commit", "dev.git")}
    assert {"non_empty_output", "no_surrender_language", "git_commit_executed"} <= ids

    generic_ids = {c.id for c in select_checks("generic", "dev.git")}
    assert "git_commit_executed" not in generic_ids

    refund_ids = {c.id for c in select_checks("refund_request", "customer_service.refund")}
    assert "refund_executed" in refund_ids


def test_task_contract_and_checklist_are_frozen_before_execution():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    runtime = TaskRuntime(
        SchedulerEngine(LocalPermitClient(gov)),
        audit_log=audit,
        validator=ValidationEngine(),
    )

    ctx = runtime.run("commit my work", "dev.git", operating_mode="quality")

    assert ctx.state is TaskState.SIMULATED
    assert isinstance(ctx.contract.acceptance_criteria, tuple)
    assert "git_commit_executed" in {
        c.criterion_id for c in ctx.contract.acceptance_criteria
    }
    task_start = next(r for r in audit.records if r.event == "task_start")
    assert task_start.payload["contract"]["contract_id"] == ctx.contract.contract_id
    assert task_start.payload["contract"]["checklist_id"] == ctx.contract.checklist_id
    assert task_start.payload["contract"]["immutable"] is True
    with pytest.raises(FrozenInstanceError):
        ctx.contract.objective = "weakened after execution"


def test_runtime_selects_the_checklist_once_then_reuses_it_for_repairs():
    selector_calls = []

    def selector(task_type, scenario):
        selector_calls.append((task_type, scenario))
        return [deterministic(
            "must_be_good",
            lambda ctx: (
                ctx.final_output == "good",
                "good output" if ctx.final_output == "good" else "output was not good",
            ),
            description="The direct answer must be the known-good artifact.",
            depth=VerificationDepth.CRITICAL,
        )]

    provider = ScriptedProvider([
        LLMResponse(text="bad", model="planner"),
        LLMResponse(text="good", model="planner"),
    ])
    runtime = _runtime(
        provider,
        validator=ValidationEngine(selector=selector),
        max_rounds=2,
    )

    ctx = runtime.run("produce the artifact", operating_mode="balanced")

    assert ctx.state is TaskState.COMPLETED
    assert ctx.round == 2
    assert selector_calls == [("generic", "default")]


def test_mock_action_in_an_earlier_repair_round_cannot_become_real_completion():
    def selector(task_type, scenario):
        return [deterministic(
            "must_be_good",
            lambda ctx: (ctx.final_output == "good", "checked final artifact"),
            description="The final artifact must be good.",
            depth=VerificationDepth.CRITICAL,
        )]

    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("echo", ["first attempt"])]),
        LLMResponse(text="good"),
    ])
    runtime = _runtime(
        provider,
        validator=ValidationEngine(selector=selector),
        max_rounds=2,
    )

    ctx = runtime.run("produce the artifact", operating_mode="balanced")

    assert ctx.state is TaskState.SIMULATED
    assert ctx.executed_action_count == 1
    assert ctx.executed_steps == []  # current artifact is text-only; task history still remembers the action
    assert [c.criterion_id for c in ctx.contract.acceptance_criteria] == ["must_be_good"]
    assert {r.contract_id for r in ctx.evidence.records} == {ctx.contract.contract_id}


def test_evidence_cannot_cross_contract_artifact_or_checker_boundaries():
    criterion = AcceptanceCriterion(
        "external:test",
        "An external authority verified the artifact.",
        evidence_kind=CheckKind.EXTERNAL.value,
    )
    ledger = EvidenceLedger(records=[EvidenceRecord(
        criterion_id="external:test",
        outcome="PASS",
        detail="claimed pass",
        source=CheckKind.DETERMINISTIC.value,
        attempt=1,
        subject_digest="sha256:old-artifact",
        contract_id="sha256:old-contract",
    )])

    assert not ledger.satisfies(
        criterion,
        subject_digest="sha256:new-artifact",
        contract_id="sha256:new-contract",
    )


# --- Cheapest-first short-circuit --------------------------------------------

def test_deterministic_failure_short_circuits_model_judge():
    counting = CountingProvider("PASS")
    eng = ValidationEngine(model_judge=ModelJudge(counting, "is it coherent?"))
    # dev.git with no git commit -> git_commit_executed fails before any judge call.
    res = eng.validate(vctx(tools=("shell:git status",)))
    assert not res.passed
    assert counting.calls == 0


def test_clean_output_passes():
    eng = ValidationEngine()
    assert eng.validate(vctx()).passed


def test_frozen_push_target_rejects_a_different_executed_ref():
    eng = ValidationEngine()
    checklist = eng.prepare(
        "git_push",
        "dev.git",
        parameters={"remote": "origin", "ref": "main"},
        depth=VerificationDepth.CRITICAL,
        run_model_judge=False,
    )
    ctx = ValidationContext(
        prompt="git push origin main",
        scenario="dev.git",
        task_type="git_push",
        executed_tools=["shell:git push"],
        executed_calls=[{
            "tool": "shell:git push",
            "args": ["origin", "feature/wrong"],
        }],
        final_output="done",
    )

    result = eng.validate(ctx, checklist=checklist)

    assert not result.passed
    failed = next(r for r in result.results if r.check_id == "git_push_executed")
    assert "target mismatch" in failed.detail


def test_frozen_refund_amount_rejects_a_different_transaction_amount():
    eng = ValidationEngine()
    checklist = eng.prepare(
        "refund_request",
        "customer_service.refund",
        parameters={"amount": "50"},
        depth=VerificationDepth.CRITICAL,
        run_model_judge=False,
    )
    ctx = ValidationContext(
        prompt="refund 50",
        scenario="customer_service.refund",
        task_type="refund_request",
        executed_tools=["tool:refund"],
        executed_calls=[{"tool": "tool:refund", "args": ["refund", "amount=500"]}],
        final_output="done",
    )

    result = eng.validate(ctx, checklist=checklist)

    assert not result.passed
    failed = next(r for r in result.results if r.check_id == "refund_executed")
    assert "amount mismatch" in failed.detail


def test_full_weekly_report_cannot_complete_without_frozen_delivery():
    eng = ValidationEngine()
    checklist = eng.prepare(
        "weekly_report",
        "ops.report",
        parameters={
            "source": "sales_analytics",
            "period": "last",
            "recipient": "ops-team",
            "artifact": "weekly_report_v1.pdf",
        },
        depth=VerificationDepth.CRITICAL,
        run_model_judge=False,
    )
    ctx = ValidationContext(
        prompt="generate last week's report",
        scenario="ops.report",
        task_type="weekly_report",
        executed_tools=["sql:query"],
        executed_calls=[{
            "tool": "sql:query",
            "args": ["SELECT * FROM sales_analytics WHERE week=last"],
        }],
        final_output="report data",
    )

    result = eng.validate(ctx, checklist=checklist)

    assert not result.passed
    assert "report_delivered" in result.failed_checks


# --- Model judge: isolated, runs last, can fail ------------------------------

def test_model_judge_runs_only_after_cheaper_checks_and_can_fail():
    provider = CountingProvider("FAIL")
    judge = ModelJudge(provider, "rubric")
    eng = ValidationEngine(model_judge=judge)
    res = eng.validate(vctx())  # deterministic checks pass, judge says FAIL
    assert res.outcome is Outcome.FAIL
    assert any(r.kind.value == "model_judge" for r in res.results)
    assert "Task objective:" in provider.last_messages[-1].content
    assert "Candidate output:\ndone" in provider.last_messages[-1].content


def test_model_judge_calibration_tracks_false_pass():
    provider = ScriptedProvider([
        LLMResponse(text="PASS"),   # truth PASS  -> correct
        LLMResponse(text="FAIL"),   # truth FAIL  -> correct
        LLMResponse(text="PASS"),   # truth FAIL  -> FALSE PASS
    ])
    judge = ModelJudge(provider, "rubric", rubric_version="v3")
    stats = judge.calibrate([
        (vctx(output="good"), Outcome.PASS),
        (vctx(output="bad1"), Outcome.FAIL),
        (vctx(output="bad2"), Outcome.FAIL),
    ])
    assert stats.calibration_cases == 3
    assert stats.false_pass == 1
    assert stats.false_block == 0


# --- Bounce-back into PDCA ---------------------------------------------------

def _runtime(provider, *, validator, max_rounds):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov), planner=LLMPlanner(provider))
    return TaskRuntime(sched, audit_log=audit, validator=validator, max_rounds=max_rounds)


def test_failed_validation_bounces_then_succeeds():
    # Round 1 proposes no commit (fails git_commit_executed); round 2 fixes it.
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),
        LLMResponse(tool_calls=[ToolCall("shell:git status"), ToolCall("shell:git commit", ["-m", "ok"])]),
    ])
    runtime = _runtime(provider, validator=ValidationEngine(), max_rounds=2)
    ctx = runtime.run("commit my work", "dev.git")
    assert ctx.state is TaskState.SIMULATED
    assert ctx.round == 2


def test_validation_failure_exhausts_rounds_and_fails():
    provider = ScriptedProvider([LLMResponse(tool_calls=[ToolCall("shell:git status")])])
    runtime = _runtime(provider, validator=ValidationEngine(), max_rounds=1)
    ctx = runtime.run("commit my work", "dev.git")
    assert ctx.state is TaskState.FAILED
    assert "git_commit_executed" in (ctx.validation_summary or "")
