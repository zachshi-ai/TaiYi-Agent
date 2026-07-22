"""Value Stream (H4): goal anchoring, contribution scoring, bottlenecks (M10)."""
from __future__ import annotations

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import SchedulerEngine
from taiyi.value_stream import (
    GoalAnchoringMode,
    ValueStreamEngine,
    load_streams,
)


def engine():
    return ValueStreamEngine()


# --- Mode B: preset ----------------------------------------------------------

def test_preset_anchors_default_stack():
    vse = engine()
    # dev.git default_stack is [task] only.
    git_goal = vse.anchor("commit", "dev.git", mode=GoalAnchoringMode.PRESET_DEFAULT)
    assert git_goal.task_layer is not None
    assert git_goal.tactical_layer is None
    # refund default_stack is [task, tactical, strategic].
    refund_goal = vse.anchor("refund", "customer_service.refund")
    assert refund_goal.tactical_layer is not None
    assert refund_goal.strategic_layer is not None
    assert refund_goal.anchoring_source == "preset"


# --- Mode A: infer + confirm -------------------------------------------------

def test_infer_then_confirm_locks_selected_layers():
    vse = engine()
    candidate = vse.infer_candidates("submit my feature branch", "dev.git")
    assert candidate.anchoring_source == "llm_inferred"
    assert candidate.task_layer.title.startswith("submit my feature")
    locked = vse.anchor(
        "submit my feature branch", "dev.git",
        mode=GoalAnchoringMode.AI_INFER_CONFIRM, selection=["task", "tactical"],
    )
    assert locked.tactical_layer is not None
    assert locked.strategic_layer is None
    assert locked.anchoring_source == "user_confirmed"


# --- Scoring + bottlenecks ---------------------------------------------------

def test_scoring_reflects_completion_and_efficiency():
    vse = engine()
    goal = vse.anchor("refund", "customer_service.refund")  # has tactical + strategic
    good = vse.score(goal, completed=True, n_steps=3)
    assert good.task_layer_completion == 1.0
    assert good.tactical_alignment > 0
    bloated = vse.score(goal, completed=True, n_steps=12, task_type="refund_request")
    assert bloated.wasted_steps  # too many steps flagged as waste


def test_bottleneck_report_aggregates():
    vse = engine()
    goal = vse.anchor("refund", "customer_service.refund")
    vse.score(goal, completed=True, n_steps=3, task_type="refund_request")
    vse.score(goal, completed=False, n_steps=20, task_type="refund_request")
    report = vse.bottlenecks()
    assert report["tasks_scored"] == 2
    assert report["worst_task_type"] == "refund_request"
    assert report["total_wasted_steps"] >= 1


def test_streams_file_loads():
    streams = load_streams()
    assert {"dev.git", "ops.report", "customer_service.refund", "default"} <= set(streams)


# --- Runtime integration -----------------------------------------------------

def test_runtime_anchors_and_scores():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    runtime = TaskRuntime(sched, audit_log=audit, value_stream=ValueStreamEngine())
    ctx = runtime.run("commit my changes", "dev.git")
    assert ctx.state is TaskState.SIMULATED
    assert ctx.goal is not None
    assert ctx.value_contribution is None  # mock work cannot claim delivered value
