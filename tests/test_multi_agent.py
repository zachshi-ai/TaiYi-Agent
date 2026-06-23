"""Multi-agent review: expert matrix + arbitration (M13)."""
from __future__ import annotations

import json

from taiyi.gateway import GatewayApp, build_gateway
from taiyi.multi_agent import (
    Authority,
    Decision,
    ExpertCommittee,
    MarkerExpert,
    arbitrate,
    builtin_experts,
    reconsider_once,
)
from taiyi.multi_agent.experts import ExpertOpinion, OpinionVerdict


def committee():
    return ExpertCommittee()


# --- No veto → approved, advisories non-binding ------------------------------

def test_clean_subject_is_approved():
    r = committee().review("a straightforward contract with clear terms")
    assert r.decision is Decision.APPROVED
    assert not r.escalate


def test_advisory_only_does_not_block():
    r = committee().review("the wording in clause 3 is confusing")  # ux advises
    assert r.decision is Decision.APPROVED
    assert any("ux" in a for a in r.advisories)


# --- Red-line veto pauses + escalates ----------------------------------------

def test_security_veto_pauses_task():
    r = committee().review("contract draft: data ownership undefined; confusing wording")
    assert r.decision is Decision.VETOED
    assert r.escalate
    assert r.winning.domain == "security"
    # The UX advisory is still recorded alongside the veto.
    assert any("ux" in a for a in r.advisories)


# --- Cross-level precedence: highest veto wins --------------------------------

def test_higher_precedence_veto_wins():
    r = committee().review("over refund limit and data ownership undefined")
    # business (70) and security (100) both veto; security wins.
    assert r.decision is Decision.VETOED
    assert r.winning.domain == "security"


# --- Same-precedence hard conflict fails closed to human ---------------------

def test_same_precedence_conflict_needs_human():
    experts = [
        MarkerExpert("alpha", Authority.VETO, 80, veto_markers=("x",)),
        MarkerExpert("beta", Authority.VETO, 80, veto_markers=("y",)),
    ]
    result = arbitrate([e.review("contains x and y") for e in experts])
    assert result.decision is Decision.NEEDS_HUMAN
    assert result.conflict and result.escalate


# --- Reconsideration: one retry, then a system defect ------------------------

def test_reconsideration_resolves_when_amended():
    out = reconsider_once(
        committee(),
        "data ownership undefined in the contract",
        amend=lambda s: s.replace("data ownership undefined", "data ownership assigned to client"),
    )
    assert out.reconsidered
    assert not out.system_defect
    assert out.result.decision is Decision.APPROVED


def test_persistent_veto_becomes_system_defect():
    out = reconsider_once(
        committee(),
        "data ownership undefined",
        amend=lambda s: s,  # amendment does not fix it
    )
    assert out.reconsidered
    assert out.system_defect
    assert "L5 system defect" in out.result.notes


# --- Gateway /v1/review ------------------------------------------------------

def test_gateway_review_endpoint():
    app = GatewayApp(build_gateway())
    body = json.dumps({"subject": "data ownership undefined"})
    status, data = app.handle("POST", "/v1/review", {}, body)
    assert status == 200
    assert data["decision"] == "VETOED"
    assert data["winning"]["domain"] == "security"
