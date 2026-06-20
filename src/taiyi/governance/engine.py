"""The Governance Engine — issues execution permits.

Conflict resolution is fail-closed by construction:
  * Any firing `block` rule wins outright -> DENY. Red lines are a one-vote veto;
    no advisory or review can override a block.
  * Otherwise any firing `request_confirmation` rule -> NEEDS_REVIEW.
  * Otherwise -> ALLOW, carrying any `warn` advisories along for visibility.
Within a tier the highest `precedence` rule supplies the reason. Every decision
is written to the audit log with the evidence that produced it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from taiyi.core.audit import AuditLog
from taiyi.core.types import (
    Action,
    PermitRequest,
    PermitResponse,
    Verdict,
    build_full_call,
)
from taiyi.governance.loader import DEFAULT_RULES_DIR, load_rules
from taiyi.governance.rules import Rule


class GovernanceEngine:
    def __init__(
        self,
        rules_dir: str | Path = DEFAULT_RULES_DIR,
        audit_log: AuditLog | None = None,
    ):
        # Rules are stored as an immutable tuple; the engine exposes no mutators.
        self._rules: tuple[Rule, ...] = load_rules(rules_dir)
        self.audit = audit_log if audit_log is not None else AuditLog()

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    def issue_permit(self, req: PermitRequest) -> PermitResponse:
        full_call = build_full_call(req.tool, req.args)

        blocks: list[tuple[Rule, str]] = []
        reviews: list[tuple[Rule, str]] = []
        advisories: list[str] = []

        for rule in self._rules:
            # Module 1 gates at request time; later triggers belong to Validation.
            if rule.trigger.value != "pre_execution":
                continue
            if not rule.applies(req.tool, req.scenario):
                continue
            fired, evidence = rule.fires(full_call)
            if not fired:
                continue
            if rule.action is Action.BLOCK:
                blocks.append((rule, evidence))
            elif rule.action is Action.REQUEST_CONFIRMATION:
                reviews.append((rule, evidence))
            else:  # WARN
                advisories.append(f"[{rule.id}] {rule.message}")

        resp = self._decide(req, blocks, reviews, advisories)
        self._record(req, resp, full_call)
        return resp

    def _decide(self, req, blocks, reviews, advisories) -> PermitResponse:
        if blocks:
            rule, evidence = max(blocks, key=lambda re: re[0].precedence)
            return PermitResponse(
                verdict=Verdict.DENY,
                reason=f"red line: {rule.id}",
                evidence=f"{rule.message} | {evidence}",
                matched_rule_id=rule.id,
                precedence=rule.precedence,
                advisories=advisories,
            )
        if reviews:
            rule, evidence = max(reviews, key=lambda re: re[0].precedence)
            return PermitResponse(
                verdict=Verdict.NEEDS_REVIEW,
                reason=f"scenario constraint: {rule.id}",
                evidence=f"{rule.message} | {evidence}",
                matched_rule_id=rule.id,
                precedence=rule.precedence,
                advisories=advisories,
                approval_id=self._approval_id(req, rule.id),
            )
        return PermitResponse(
            verdict=Verdict.ALLOW,
            reason="no red line or scenario constraint fired",
            evidence="default allow",
            advisories=advisories,
        )

    def _record(self, req: PermitRequest, resp: PermitResponse, full_call: str) -> None:
        self.audit.append(
            "permit_decision",
            task_id=req.task_id,
            actor=req.actor,
            user_id=req.user_id,
            scenario=req.scenario,
            call=full_call,
            verdict=resp.verdict.value,
            matched_rule_id=resp.matched_rule_id,
            reason=resp.reason,
            evidence=resp.evidence,
        )

    @staticmethod
    def _approval_id(req: PermitRequest, rule_id: str) -> str:
        seed = f"{req.task_id}|{req.actor}|{req.tool}|{req.scenario}|{rule_id}"
        return "approval_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
