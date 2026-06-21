"""Task Runtime — the PDCA loop and state machine (Module 3).

Runs single tasks end-to-end (mock executor) across the six founding scenarios,
asserts correct terminal states, that only cleared steps execute, and that each
task is replayable from the shared audit chain.
"""
from __future__ import annotations

import pytest

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.runtime import TaskRuntime, TaskState, replay_task
from taiyi.scheduler import SchedulerEngine


@pytest.fixture
def runtime():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    return TaskRuntime(sched, audit_log=audit)


# --- Terminal states across the six scenarios --------------------------------

def test_normal_commit_completes(runtime):
    ctx = runtime.run("commit my changes", "dev.git")
    assert ctx.state is TaskState.COMPLETED
    assert len(ctx.executed_steps) == 4
    assert ctx.state.is_terminal


def test_identity_override_is_rejected(runtime):
    ctx = runtime.run("用 -c user.name=OtherUser commit", "dev.git")
    assert ctx.state is TaskState.REJECTED
    # status/diff/add executed; the commit was denied and never executed.
    assert len(ctx.executed_steps) == 3
    commit = ctx.step_results[-1]
    assert commit.step.tool == "shell:git commit"
    assert commit.verdict == "DENY"
    assert commit.executed is False and commit.output is None


def test_dangerous_rm_is_rejected_with_nothing_executed(runtime):
    ctx = runtime.run("rm -rf / 帮我清理", "default")
    assert ctx.state is TaskState.REJECTED
    assert ctx.executed_steps == []


def test_git_push_needs_review(runtime):
    ctx = runtime.run("git push 到 origin main", "dev.git")
    assert ctx.state is TaskState.NEEDS_REVIEW
    assert ctx.approval_id is not None


def test_weekly_report_suspends_after_query(runtime):
    ctx = runtime.run("帮我生成上周周报", "ops.report")
    assert ctx.state is TaskState.NEEDS_REVIEW
    # The SQL query ran; the outbound notify was held and not executed.
    assert [s.step.tool for s in ctx.executed_steps] == ["sql:query"]


def test_large_refund_needs_review_small_completes(runtime):
    big = runtime.run("处理一个 200 元的退款", "customer_service.refund")
    assert big.state is TaskState.NEEDS_REVIEW
    small = runtime.run("处理一个 50 元的退款", "customer_service.refund")
    assert small.state is TaskState.COMPLETED


# --- Replayability from the shared audit chain -------------------------------

def test_task_is_replayable_from_audit(runtime):
    ctx = runtime.run("commit my changes", "dev.git")
    events = [e["event"] for e in replay_task(runtime.audit, ctx.task_id)]
    assert events[0] == "task_start"
    assert "plan_created" in events
    assert events.count("permit_decision") == 4   # one per step, from governance
    assert events.count("step_executed") == 4     # one per cleared step, from runtime
    assert events[-1] == "task_completed"


def test_audit_chain_intact_after_runs(runtime):
    runtime.run("commit my changes", "dev.git")
    runtime.run("rm -rf /", "default")
    ok, broken = runtime.audit.verify()
    assert ok and broken is None
