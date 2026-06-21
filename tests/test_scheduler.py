"""Scheduler + governance boundary (Module 2).

These tests assert the boundary semantics: nothing clears without an ALLOW, a
DENY halts the plan, and a NEEDS_REVIEW suspends it without dropping the steps
that were already cleared.
"""
from __future__ import annotations

import pytest

from taiyi.core.types import PermitRequest, PermitResponse, Verdict
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.scheduler import PlanStep, SchedulerEngine


@pytest.fixture
def scheduler():
    engine = GovernanceEngine()
    return SchedulerEngine(LocalPermitClient(engine))


def clear(scheduler, prompt, scenario):
    plan, clearance = scheduler.plan_and_clear(prompt, scenario, task_id="t1")
    return plan, clearance


# --- Routing ----------------------------------------------------------------

def test_push_routes_before_commit(scheduler):
    plan = scheduler.plan("git push 到 origin main", "dev.git")
    assert plan.skill_name == "git_safe_commit"
    assert [s.tool for s in plan.steps] == ["shell:git push"]


def test_commit_decomposes_into_atomic_steps(scheduler):
    plan = scheduler.plan("帮我把测试脚本 commit 一下", "dev.git")
    assert len(plan.steps) == 4
    assert plan.steps[-1].tool == "shell:git commit"


# --- Boundary: ALLOW path ----------------------------------------------------

def test_clean_commit_clears_every_step(scheduler):
    plan, c = clear(scheduler, "commit my changes", "dev.git")
    assert c.fully_cleared
    assert c.terminal_verdict is Verdict.ALLOW
    assert len(c.cleared_steps) == len(plan.steps)
    assert c.halted_step is None


# --- Boundary: DENY halts ----------------------------------------------------

def test_identity_override_denies_and_halts(scheduler):
    plan, c = clear(
        scheduler,
        "用 -c user.name=OtherUser -c user.email=other@example.com commit",
        "dev.git",
    )
    assert c.terminal_verdict is Verdict.DENY
    # First three steps (status/diff/add) cleared; commit halted.
    assert len(c.cleared_steps) == 3
    assert c.halted_step.tool == "shell:git commit"
    assert c.halted_response.matched_rule_id == "authorship.git_identity.no_override"


# --- Boundary: NEEDS_REVIEW suspends, preserving cleared steps ----------------

def test_weekly_report_suspends_at_outbound_but_keeps_query(scheduler):
    plan, c = clear(scheduler, "帮我生成上周周报", "ops.report")
    assert c.terminal_verdict is Verdict.NEEDS_REVIEW
    # The SQL query was cleared and is preserved; the outbound notify is halted.
    assert [s.tool for s in c.cleared_steps] == ["sql:query"]
    assert c.halted_step.tool == "notify:feishu"
    assert c.halted_response.approval_id is not None


def test_large_refund_needs_review(scheduler):
    _, c = clear(scheduler, "处理一个 200 元的退款", "customer_service.refund")
    assert c.terminal_verdict is Verdict.NEEDS_REVIEW


def test_small_refund_clears(scheduler):
    _, c = clear(scheduler, "处理一个 50 元的退款", "customer_service.refund")
    assert c.fully_cleared


# --- The scheduler cannot execute or self-grant ------------------------------

class RecordingClient:
    """A PermitClient that records every request and always allows."""

    def __init__(self):
        self.requests: list[PermitRequest] = []

    def issue_permit(self, req: PermitRequest) -> PermitResponse:
        self.requests.append(req)
        return PermitResponse(verdict=Verdict.ALLOW, reason="test allow")


def test_every_step_goes_through_the_permit_client():
    rec = RecordingClient()
    sched = SchedulerEngine(rec)
    plan, c = sched.plan_and_clear("commit my changes", "dev.git", task_id="t9")
    # One permit request per planned step — no step bypasses the boundary.
    assert len(rec.requests) == len(plan.steps)
    assert all(r.task_id == "t9" for r in rec.requests)


def test_scheduler_exposes_no_execution_capability():
    sched = SchedulerEngine(RecordingClient())
    for forbidden in ("execute", "run_tool", "shell", "run"):
        assert not hasattr(sched, forbidden)
