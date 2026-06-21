"""Deterministic checks and the checklist library.

Per the design (§5.2): **select, don't generate.** Acceptance criteria for a
given (task_type, scenario) are mostly known in advance, so we keep a library and
select + parameterize at runtime rather than inventing checks on the fly. New
checks are added deliberately (later, by the Loop Engineering module), not
synthesized per task.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from taiyi.validation.types import COST_TIER, CheckKind, CheckResult, Outcome, ValidationContext

Predicate = Callable[[ValidationContext], tuple[bool, str]]


@dataclass
class Check:
    id: str
    kind: CheckKind
    predicate: Predicate

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
        )


def deterministic(check_id: str, predicate: Predicate) -> Check:
    return Check(check_id, CheckKind.DETERMINISTIC, predicate)


# --- concrete predicates -----------------------------------------------------

def _non_empty(ctx: ValidationContext) -> tuple[bool, str]:
    ok = bool(ctx.final_output.strip())
    return ok, "output present" if ok else "output is empty"


def _no_surrender(ctx: ValidationContext) -> tuple[bool, str]:
    bad = ("我无法", "i cannot", "sorry, i can't", "sorry, i cannot")
    low = ctx.final_output.lower()
    hit = next((b for b in bad if b in low), None)
    return (hit is None), ("no surrender language" if hit is None else f"contains {hit!r}")


def _git_commit_executed(ctx: ValidationContext) -> tuple[bool, str]:
    ok = any(t.startswith("shell:git commit") for t in ctx.executed_tools)
    return ok, "a git commit ran" if ok else "no git commit was executed"


def _report_has_query(ctx: ValidationContext) -> tuple[bool, str]:
    ok = any(t.startswith("sql:") for t in ctx.executed_tools)
    return ok, "report backed by a query" if ok else "report not backed by a query"


# --- the library -------------------------------------------------------------

UNIVERSAL: list[Check] = [
    deterministic("non_empty_output", _non_empty),
    deterministic("no_surrender_language", _no_surrender),
]

BY_SCENARIO: dict[str, list[Check]] = {
    "dev.git": [deterministic("git_commit_executed", _git_commit_executed)],
    "ops.report": [deterministic("report_has_query", _report_has_query)],
}

BY_TASK_TYPE: dict[str, list[Check]] = {
    "git_safe_commit": [deterministic("git_commit_executed", _git_commit_executed)],
}


def select_checks(task_type: str, scenario: str) -> list[Check]:
    """Select the applicable checks for a (task_type, scenario), de-duplicated."""
    selected: list[Check] = []
    seen: set[str] = set()
    for source in (UNIVERSAL, BY_SCENARIO.get(scenario, []), BY_TASK_TYPE.get(task_type, [])):
        for check in source:
            if check.id not in seen:
                seen.add(check.id)
                selected.append(check)
    return selected
