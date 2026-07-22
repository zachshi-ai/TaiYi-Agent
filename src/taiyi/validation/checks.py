"""Deterministic checks and the checklist library.

Per the design (§5.2): **select, don't generate.** Acceptance criteria for a
given (task_type, scenario) are mostly known in advance, so we keep a library and
select + parameterize at runtime rather than inventing checks on the fly. New
checks are added deliberately (later, by the Loop Engineering module), not
synthesized per task.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass

from taiyi.policy import VerificationDepth
from taiyi.validation.types import COST_TIER, CheckKind, CheckResult, Outcome, ValidationContext

Predicate = Callable[[ValidationContext], tuple[bool, str]]


@dataclass(frozen=True)
class Check:
    id: str
    kind: CheckKind
    predicate: Predicate
    description: str = ""
    depth: VerificationDepth = VerificationDepth.STANDARD
    scope: str = "baseline"
    authority: str = "taiyi.validation"
    environment: str = "harness"
    configuration_digest: str = ""

    @property
    def tier(self) -> int:
        return COST_TIER[self.kind]

    def run(self, ctx: ValidationContext) -> CheckResult:
        ok, detail = self.predicate(ctx)
        return CheckResult(
            check_id=self.id,
            kind=self.kind,
            outcome=Outcome.PASS if ok else Outcome.FAIL,
            detail=detail,
            authority=self.authority,
            environment=self.environment,
            configuration_digest=self.configuration_digest,
        )


def deterministic(
    check_id: str,
    predicate: Predicate,
    *,
    description: str = "",
    depth: VerificationDepth = VerificationDepth.STANDARD,
    scope: str = "baseline",
    authority: str = "taiyi.validation",
    environment: str = "harness",
    configuration_digest: str = "",
) -> Check:
    return Check(
        check_id,
        CheckKind.DETERMINISTIC,
        predicate,
        description,
        depth,
        scope,
        authority,
        environment,
        configuration_digest,
    )


def external(
    check_id: str,
    predicate: Predicate,
    *,
    description: str,
    depth: VerificationDepth = VerificationDepth.STANDARD,
    scope: str = "objective",
    authority: str,
    environment: str,
    configuration_digest: str,
) -> Check:
    """Declare a read-only check backed by an authority outside the executor."""

    return Check(
        check_id,
        CheckKind.EXTERNAL,
        predicate,
        description,
        depth,
        scope,
        authority,
        environment,
        configuration_digest,
    )


# --- concrete predicates -----------------------------------------------------

def _executed_calls(ctx: ValidationContext) -> list[dict]:
    if ctx.executed_calls:
        return ctx.executed_calls
    return [{"tool": tool, "args": []} for tool in ctx.executed_tools]


def _called(ctx: ValidationContext, tool_prefix: str) -> bool:
    return any(
        str(call.get("tool", "")).startswith(tool_prefix)
        for call in _executed_calls(ctx)
    )


def _parameter_digest(task_type: str, parameters: Mapping[str, str]) -> str:
    payload = {"task_type": task_type, "parameters": dict(parameters)}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _non_empty(ctx: ValidationContext) -> tuple[bool, str]:
    ok = bool(ctx.final_output.strip())
    return ok, "output present" if ok else "output is empty"


def _no_surrender(ctx: ValidationContext) -> tuple[bool, str]:
    bad = ("我无法", "i cannot", "sorry, i can't", "sorry, i cannot")
    low = ctx.final_output.lower()
    hit = next((b for b in bad if b in low), None)
    return (hit is None), ("no surrender language" if hit is None else f"contains {hit!r}")


def _git_commit_executed(ctx: ValidationContext) -> tuple[bool, str]:
    ok = _called(ctx, "shell:git commit")
    return ok, "a git commit ran" if ok else "no git commit was executed"


def _git_push_executed(
    ctx: ValidationContext,
    *,
    remote: str,
    ref: str,
) -> tuple[bool, str]:
    for call in _executed_calls(ctx):
        if not str(call.get("tool", "")).startswith("shell:git push"):
            continue
        args = [str(arg) for arg in call.get("args", [])]
        if args[:2] == [remote, ref]:
            return True, f"git push ran for frozen target {remote} {ref}"
        return False, (
            f"git push target mismatch: expected {remote} {ref}, observed {args!r}"
        )
    return False, f"no git push was executed for frozen target {remote} {ref}"


def _report_has_query(
    ctx: ValidationContext,
    *,
    source: str,
    period: str,
) -> tuple[bool, str]:
    expected = (source.casefold(), f"week={period}".casefold())
    for call in _executed_calls(ctx):
        if not str(call.get("tool", "")).startswith("sql:"):
            continue
        statement = " ".join(str(arg) for arg in call.get("args", [])).casefold()
        if all(fragment in statement for fragment in expected):
            return True, f"report query used frozen source {source} and period {period}"
        return False, (
            f"report query mismatch: expected source={source!r}, period={period!r}"
        )
    return False, "report not backed by a query"


def _refund_executed(ctx: ValidationContext, *, amount: str) -> tuple[bool, str]:
    expected = f"amount={amount}"
    for call in _executed_calls(ctx):
        if not str(call.get("tool", "")).startswith("tool:refund"):
            continue
        args = [str(arg) for arg in call.get("args", [])]
        if expected in args:
            return True, f"refund action ran for frozen amount {amount}"
        return False, f"refund amount mismatch: expected {expected!r}, observed {args!r}"
    return False, f"no refund action was executed for frozen amount {amount}"


def _report_delivered(
    ctx: ValidationContext,
    *,
    recipient: str,
    artifact: str,
) -> tuple[bool, str]:
    expected = ["send", recipient, artifact]
    for call in _executed_calls(ctx):
        if not str(call.get("tool", "")).startswith("notify:"):
            continue
        args = [str(arg) for arg in call.get("args", [])]
        if args[:3] == expected:
            return True, f"report delivery ran for frozen recipient {recipient}"
        return False, (
            f"report delivery mismatch: expected {expected!r}, observed {args!r}"
        )
    return False, f"report was not delivered to frozen recipient {recipient}"


def _executions_have_outputs(ctx: ValidationContext) -> tuple[bool, str]:
    # Older/custom ValidationContext producers may not provide step outputs.
    # Runtime-owned contexts opt into the stronger evidence contract explicitly.
    if not ctx.extras.get("require_step_outputs"):
        return True, "step-output evidence not requested by this validation context"
    if not ctx.executed_tools:
        return True, "no tool execution required"
    ok = len(ctx.outputs) == len(ctx.executed_tools) and all(o.strip() for o in ctx.outputs)
    return ok, (
        "every executed step produced inspectable output"
        if ok
        else "one or more executed steps produced no inspectable output"
    )


# --- the library -------------------------------------------------------------

UNIVERSAL: list[Check] = [
    deterministic(
        "non_empty_output", _non_empty,
        description="The final deliverable is present.",
        depth=VerificationDepth.CRITICAL,
    ),
    deterministic(
        "no_surrender_language", _no_surrender,
        description="The result does not substitute refusal language for the requested outcome.",
        depth=VerificationDepth.STANDARD,
    ),
    deterministic(
        "executed_steps_have_outputs", _executions_have_outputs,
        description="Every executed action produced inspectable evidence.",
        depth=VerificationDepth.EXHAUSTIVE,
    ),
]

BY_SCENARIO: dict[str, list[Check]] = {}

BY_TASK_TYPE: dict[str, list[Check]] = {
    "git_safe_commit": [deterministic(
        "git_commit_executed", _git_commit_executed,
        description="A git commit was actually executed.",
        depth=VerificationDepth.CRITICAL,
        scope="objective",
    )],
}


def _parameterized_checks(
    task_type: str,
    parameters: Mapping[str, str],
) -> list[Check]:
    digest = _parameter_digest(task_type, parameters)
    if task_type == "git_push":
        remote = parameters.get("remote", "origin")
        ref = parameters.get("ref", "main")
        return [deterministic(
            "git_push_executed",
            lambda ctx: _git_push_executed(ctx, remote=remote, ref=ref),
            description=(
                f"A git push to the frozen target {remote} {ref} was executed after approval."
            ),
            depth=VerificationDepth.CRITICAL,
            scope="objective",
            configuration_digest=digest,
        )]
    if task_type == "refund_request":
        amount = parameters.get("amount", "100")
        return [deterministic(
            "refund_executed",
            lambda ctx: _refund_executed(ctx, amount=amount),
            description=f"A refund for the frozen amount {amount} was executed.",
            depth=VerificationDepth.CRITICAL,
            scope="objective",
            configuration_digest=digest,
        )]
    if task_type in {"weekly_report", "weekly_report_query"}:
        source = parameters.get("source", "sales_analytics")
        period = parameters.get("period", "last")
        checks = [deterministic(
            "report_has_query",
            lambda ctx: _report_has_query(ctx, source=source, period=period),
            description=(
                f"The report is backed by the frozen source {source} for period {period}."
            ),
            depth=VerificationDepth.CRITICAL,
            scope="objective",
            configuration_digest=digest,
        )]
        if task_type == "weekly_report":
            recipient = parameters.get("recipient", "ops-team")
            artifact = parameters.get("artifact", "weekly_report_v1.pdf")
            checks.append(deterministic(
                "report_delivered",
                lambda ctx: _report_delivered(
                    ctx,
                    recipient=recipient,
                    artifact=artifact,
                ),
                description=(
                    f"The frozen report artifact {artifact} was delivered to {recipient}."
                ),
                depth=VerificationDepth.CRITICAL,
                scope="objective",
                configuration_digest=digest,
            ))
        return checks
    return []


def select_checks(
    task_type: str,
    scenario: str,
    parameters: Mapping[str, str] | None = None,
) -> list[Check]:
    """Select the applicable checks for a (task_type, scenario), de-duplicated."""
    selected: list[Check] = []
    seen: set[str] = set()
    sources = (
        UNIVERSAL,
        BY_SCENARIO.get(scenario, []),
        BY_TASK_TYPE.get(task_type, []),
        _parameterized_checks(task_type, parameters or {}),
    )
    for source in sources:
        for check in source:
            if check.id not in seen:
                seen.add(check.id)
                selected.append(check)
    return selected
