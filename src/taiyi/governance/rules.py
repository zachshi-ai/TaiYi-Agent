"""The Rule model — a red line or scenario constraint expressed as data.

A rule is a versionable, reviewable, testable record (the YAML files under
`taiyi/rules/`). Keeping rules as data rather than prose buried in a prompt is
what makes them auditable via `git diff`, loadable without re-parsing a prompt,
and usable directly as test fixtures.

Module 1 implements only `check.type: deterministic`. Deterministic checks are
the highest-trust kind — a string/number comparison that is cheap and cannot be
forged. `external_tool` and `model_judge` check types are reserved for later
modules (Validation Engine) and are rejected by the loader for now.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from taiyi.core.types import Action, Domain, Severity, Trigger


class RuleError(ValueError):
    """Raised when a rule file is malformed. Fail fast at load time, not runtime."""


_NUMERIC_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


@dataclass(frozen=True)
class Rule:
    """One governance rule.

    Matching has two stages:
      1. *Applicability* — does this rule apply to the requested tool & scenario?
      2. *Firing* — given it applies, does the deterministic check trip?
    A rule that applies and fires contributes its `action` to the verdict.
    """

    id: str
    domain: Domain
    severity: Severity
    applies_to: tuple[str, ...]      # glob patterns matched against the tool id
    trigger: Trigger
    action: Action
    message: str
    precedence: int
    owner: str
    audit: bool = True
    scenario: str | None = None      # if set, only applies within this scenario
    # --- deterministic check spec ---
    match: str = "args_any"          # args_any | tool_only | numeric_threshold
    patterns: tuple[str, ...] = ()   # for args_any
    extract: str | None = None       # regex w/ one capture group, for numeric_threshold
    op: str | None = None            # one of _NUMERIC_OPS keys, for numeric_threshold
    value: float | None = None       # threshold, for numeric_threshold

    def applies(self, tool: str, scenario: str) -> bool:
        if self.scenario is not None and self.scenario != scenario:
            return False
        return any(fnmatch.fnmatch(tool, pat) for pat in self.applies_to)

    def fires(self, full_call: str) -> tuple[bool, str]:
        """Return (fired, evidence). Evidence explains *why* for the audit trail."""
        if self.match == "tool_only":
            return True, f"{self.id}: tool matched {self.applies_to}"

        if self.match == "args_any":
            for pat in self.patterns:
                if _glob_in(full_call, pat):
                    return True, f"{self.id}: call contains forbidden pattern {pat!r}"
            return False, ""

        if self.match == "numeric_threshold":
            assert self.extract and self.op and self.value is not None
            m = re.search(self.extract, full_call)
            if not m:
                return False, ""
            try:
                observed = float(m.group(1))
            except (TypeError, ValueError):
                return False, ""
            if _NUMERIC_OPS[self.op](observed, self.value):
                return True, f"{self.id}: {observed} {self.op} {self.value}"
            return False, ""

        raise RuleError(f"rule {self.id}: unknown match kind {self.match!r}")


def _glob_in(text: str, pattern: str) -> bool:
    """Substring match, or glob substring match when the pattern contains '*'."""
    if "*" in pattern:
        regex = re.escape(pattern).replace(r"\*", ".*")
        return re.search(regex, text) is not None
    return pattern in text
