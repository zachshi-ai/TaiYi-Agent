"""Governance Engine behaviour.

The six scenarios from the Phase 0 feasibility report are reproduced here as
real assertions against the production engine and the rules-as-data set, plus
conflict-resolution and audit-trail coverage.
"""
from __future__ import annotations

import pytest

from taiyi.core.types import PermitRequest, Verdict
from taiyi.governance import GovernanceEngine


@pytest.fixture
def engine():
    return GovernanceEngine()


def permit(engine, tool, args=None, scenario="default", **kw):
    return engine.issue_permit(
        PermitRequest(tool=tool, args=args or [], scenario=scenario, task_id="t1", **kw)
    )


# --- Feasibility report cases 1-6 -------------------------------------------

def test_normal_git_commit_is_allowed(engine):
    r = permit(engine, "shell:git commit", ["-m", "fix tests"], scenario="dev.git")
    assert r.verdict is Verdict.ALLOW


def test_git_identity_override_is_denied(engine):
    # The founding incident: -c user.name=... must be blocked.
    r = permit(engine, "shell:git commit", ["-c", "user.name=OtherUser", "-m", "x"], scenario="dev.git")
    assert r.verdict is Verdict.DENY
    assert r.matched_rule_id == "authorship.git_identity.no_override"


def test_dangerous_rm_is_denied(engine):
    r = permit(engine, "shell:rm -rf", ["/"], scenario="default")
    assert r.verdict is Verdict.DENY
    assert r.matched_rule_id == "safety.recursive_delete.no_critical_path"


def test_git_push_needs_review_in_dev_scenario(engine):
    r = permit(engine, "shell:git push", ["origin", "main"], scenario="dev.git")
    assert r.verdict is Verdict.NEEDS_REVIEW
    assert r.approval_id is not None


def test_outbound_notify_needs_review_in_report_scenario(engine):
    allow = permit(engine, "sql:query", ["SELECT 1"], scenario="ops.report")
    assert allow.verdict is Verdict.ALLOW
    review = permit(engine, "notify:feishu", ["send", "ops-team", "report.pdf"], scenario="ops.report")
    assert review.verdict is Verdict.NEEDS_REVIEW


def test_large_refund_needs_review_small_does_not(engine):
    big = permit(engine, "tool:refund", ["refund", "amount=200"], scenario="customer_service.refund")
    assert big.verdict is Verdict.NEEDS_REVIEW
    small = permit(engine, "tool:refund", ["refund", "amount=50"], scenario="customer_service.refund")
    assert small.verdict is Verdict.ALLOW


# --- Scenario isolation ------------------------------------------------------

def test_scenario_rules_are_scoped(engine):
    # The same refund call outside the customer-service scenario is not gated.
    r = permit(engine, "tool:refund", ["refund", "amount=200"], scenario="dev.git")
    assert r.verdict is Verdict.ALLOW


# --- Conflict resolution: red line always beats scenario review --------------

def test_block_beats_review_fail_closed(engine):
    # A git push (would be NEEDS_REVIEW) that also overrides identity (DENY)
    # must come back DENY — the red line is a one-vote veto.
    r = permit(
        engine,
        "shell:git push",
        ["--author=Someone <x@y.z>", "origin", "main"],
        scenario="dev.git",
    )
    assert r.verdict is Verdict.DENY
    assert r.matched_rule_id == "authorship.git_identity.no_override"


# --- Audit trail -------------------------------------------------------------

def test_every_decision_is_audited(engine):
    permit(engine, "shell:git commit", ["-m", "x"], scenario="dev.git")
    permit(engine, "shell:rm -rf", ["/"])
    assert len(engine.audit) == 2
    assert all(rec.event == "permit_decision" for rec in engine.audit.records)
    ok, broken = engine.audit.verify()
    assert ok and broken is None
