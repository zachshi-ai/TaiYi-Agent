"""Iteration / OODA (L5) — closing the loop to maturity L4 (M12)."""
from __future__ import annotations

from taiyi.core.audit import AuditLog
from taiyi.core.types import PermitRequest, Verdict
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.iteration import (
    IterationEngine,
    RegressionSet,
    TaskRecord,
    TrajectoryStore,
    approve,
    generate_skill_draft,
    suggest_rules,
    write_draft,
)
from taiyi.llm import LLMResponse, ScriptedProvider
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import SchedulerEngine
from taiyi.skills.loader import load_skill
from taiyi.validation import ModelJudge, Outcome


# --- A recurring failure becomes a permanent check ---------------------------

def test_recurring_failure_suggests_rule_that_governance_then_enforces(tmp_path):
    store = TrajectoryStore()
    for i in range(3):
        store.add(TaskRecord(f"t{i}", "ops.x", None, "FAILED", "do risky thing",
                             ("tool:risky",), fail_reason="boom"))

    suggestions = suggest_rules(store, threshold=3)
    assert len(suggestions) == 1
    assert suggestions[0].tool == "tool:risky"

    # Human approves → the suggestion becomes a loadable rule.
    approve(suggestions[0], tmp_path)
    gov = GovernanceEngine(rules_dir=tmp_path)
    verdict = gov.issue_permit(
        PermitRequest(tool="tool:risky", args=[], scenario="ops.x", task_id="t")
    ).verdict
    assert verdict is Verdict.NEEDS_REVIEW  # the new permanent check fires


def test_below_threshold_no_suggestion():
    store = TrajectoryStore()
    store.add(TaskRecord("t0", "ops.x", None, "FAILED", "x", ("tool:risky",)))
    assert suggest_rules(store, threshold=3) == []


# --- Skill auto-generation stays a draft until humans add sufficient coverage -

def test_generated_skill_requires_human_coverage_and_promotion(tmp_path):
    draft = generate_skill_draft("dev.git", ("shell:git status", "shell:git commit"), 5)
    skill_dir = write_draft(draft, tmp_path)
    skill = load_skill(skill_dir)
    assert skill.category == "auto_generated"
    assert skill.gate is not None and not skill.gate.passes
    assert skill.gate.verification[0]["expect"]["state"] == "SIMULATED"
    assert any("at least 3 executable cases" in p for p in skill.gate_problems)
    assert not skill.production_eligible
    assert any("not a production tier" in p for p in skill.production_problems)


def test_repeat_candidates_become_skill_drafts():
    eng = IterationEngine()
    for i in range(3):
        eng.store.add(TaskRecord(f"t{i}", "research.x", None, "COMPLETED", "dig",
                                 ("http:get", "file:write")))
    drafts = eng.propose_skills(min_repeats=3)
    assert len(drafts) == 1
    assert drafts[0].tools == ("http:get", "file:write")


# --- Validator regression set ------------------------------------------------

def test_regression_set_calibrates_judge():
    rs = RegressionSet()
    rs.add("a clean result", Outcome.PASS)
    rs.add("a broken result", Outcome.FAIL)
    judge = ModelJudge(ScriptedProvider([LLMResponse(text="PASS"), LLMResponse(text="FAIL")]), "rubric")
    stats = rs.evaluate(judge)
    assert stats.calibration_cases == 2
    assert stats.false_pass == 0 and stats.false_block == 0


# --- Runtime integration -----------------------------------------------------

def test_runtime_feeds_iteration_engine():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    iteration = IterationEngine()
    runtime = TaskRuntime(sched, audit_log=audit, iteration=iteration)

    assert runtime.run("commit my changes", "dev.git").state is TaskState.SIMULATED
    assert runtime.run("用 -c user.name=Evil commit", "dev.git").state is TaskState.REJECTED

    report = iteration.report()
    assert report["tasks"] == 2
    assert report["failures"] == 1            # the rejected task
    assert report["regression_cases"] == 0    # mock simulation is not labelled as a real PASS
