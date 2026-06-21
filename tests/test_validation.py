"""Validation Engine + bounce-back correction loop (Module 6).

Covers selection-by-key, cheapest-first short-circuiting (a failed deterministic
check must not spend a model-judge call), isolated/version-tracked model judging
with calibration, and a validation failure bouncing the task back into PDCA until
it either succeeds or exhausts its rounds.
"""
from __future__ import annotations

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.llm.base import LLMResponse as _R  # noqa: F401  (clarity in helpers)
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import LLMPlanner, SchedulerEngine
from taiyi.validation import (
    ModelJudge,
    Outcome,
    ValidationContext,
    ValidationEngine,
    select_checks,
)


class CountingProvider:
    name = "counting"

    def __init__(self, text="PASS"):
        self.calls = 0
        self.text = text

    def complete(self, messages, *, tools=None):
        self.calls += 1
        return LLMResponse(text=self.text)


def vctx(scenario="dev.git", tools=("shell:git commit",), output="done"):
    return ValidationContext(
        prompt="x", scenario=scenario, task_type="generic",
        executed_tools=list(tools), final_output=output,
    )


# --- Selection ---------------------------------------------------------------

def test_select_checks_includes_universal_and_scenario():
    ids = {c.id for c in select_checks("generic", "dev.git")}
    assert {"non_empty_output", "no_surrender_language", "git_commit_executed"} <= ids


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


# --- Model judge: isolated, runs last, can fail ------------------------------

def test_model_judge_runs_only_after_cheaper_checks_and_can_fail():
    judge = ModelJudge(ScriptedProvider([LLMResponse(text="FAIL")]), "rubric")
    eng = ValidationEngine(model_judge=judge)
    res = eng.validate(vctx())  # deterministic checks pass, judge says FAIL
    assert res.outcome is Outcome.FAIL
    assert any(r.kind.value == "model_judge" for r in res.results)


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
    assert ctx.state is TaskState.COMPLETED
    assert ctx.round == 2


def test_validation_failure_exhausts_rounds_and_fails():
    provider = ScriptedProvider([LLMResponse(tool_calls=[ToolCall("shell:git status")])])
    runtime = _runtime(provider, validator=ValidationEngine(), max_rounds=1)
    ctx = runtime.run("commit my work", "dev.git")
    assert ctx.state is TaskState.FAILED
    assert "git_commit_executed" in (ctx.validation_summary or "")
