"""Read-only rule loader.

Rules are loaded once from disk and handed to the engine as an immutable tuple.
The scheduler has no API to mutate them; updating a rule means editing a YAML
file (a reviewable git change) and reloading governance — by design, never a
runtime call from the decision-maker. That is the "rules load read-only"
constraint from the architecture, made concrete.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from taiyi.core.types import Action, Domain, Severity, Trigger
from taiyi.governance.rules import Rule, RuleError

DEFAULT_RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

_REQUIRED = {"id", "domain", "severity", "applies_to", "trigger", "check", "on_fail", "precedence", "owner"}
_ALLOWED_CHECK_TYPES = {"deterministic"}  # Module 1 scope


def _enum(enum_cls, raw, rule_id, field_name):
    try:
        return enum_cls(raw)
    except ValueError:
        valid = [e.value for e in enum_cls]
        raise RuleError(f"rule {rule_id}: invalid {field_name} {raw!r}; expected one of {valid}")


def _build_rule(doc: dict, source: str) -> Rule:
    rule_id = doc.get("id", f"<{source}>")
    missing = _REQUIRED - doc.keys()
    if missing:
        raise RuleError(f"rule {rule_id}: missing required fields {sorted(missing)}")

    check = doc["check"]
    if not isinstance(check, dict) or "type" not in check:
        raise RuleError(f"rule {rule_id}: 'check' must be a mapping with a 'type'")
    if check["type"] not in _ALLOWED_CHECK_TYPES:
        raise RuleError(
            f"rule {rule_id}: check.type {check['type']!r} not supported in Module 1 "
            f"(deterministic only)"
        )

    on_fail = doc["on_fail"]
    if not isinstance(on_fail, dict) or "action" not in on_fail:
        raise RuleError(f"rule {rule_id}: 'on_fail' must be a mapping with an 'action'")

    applies_to = doc["applies_to"]
    if isinstance(applies_to, str):
        applies_to = [applies_to]
    if not applies_to:
        raise RuleError(f"rule {rule_id}: 'applies_to' must list at least one tool pattern")

    match_kind = check.get("match", "args_any")
    patterns = tuple(check.get("patterns", []) or [])
    if match_kind == "args_any" and not patterns:
        raise RuleError(f"rule {rule_id}: match 'args_any' requires non-empty 'patterns'")
    if match_kind == "numeric_threshold":
        for key in ("extract", "op", "value"):
            if check.get(key) is None:
                raise RuleError(f"rule {rule_id}: match 'numeric_threshold' requires '{key}'")

    return Rule(
        id=rule_id,
        domain=_enum(Domain, doc["domain"], rule_id, "domain"),
        severity=_enum(Severity, doc["severity"], rule_id, "severity"),
        applies_to=tuple(applies_to),
        trigger=_enum(Trigger, doc["trigger"], rule_id, "trigger"),
        action=_enum(Action, on_fail["action"], rule_id, "on_fail.action"),
        message=on_fail.get("message", ""),
        precedence=int(doc["precedence"]),
        owner=doc["owner"],
        audit=bool(doc.get("audit", True)),
        scenario=doc.get("scenario"),
        match=match_kind,
        patterns=patterns,
        extract=check.get("extract"),
        op=check.get("op"),
        value=None if check.get("value") is None else float(check["value"]),
    )


def _load_dir(rules_dir: Path) -> list[Rule]:
    """Load+validate every rule in a directory; tolerant of a missing/empty dir."""
    if not rules_dir.exists():
        return []
    rules: list[Rule] = []
    seen: dict[str, str] = {}
    for path in sorted(rules_dir.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if doc is None:
                continue
            if not isinstance(doc, dict):
                raise RuleError(f"{path}: each YAML document must be a mapping")
            rule = _build_rule(doc, source=path.name)
            if rule.id in seen:
                raise RuleError(
                    f"duplicate rule id {rule.id!r} in {path.name} (already in {seen[rule.id]})"
                )
            seen[rule.id] = path.name
            rules.append(rule)
    return rules


def load_rules(rules_dir: str | Path = DEFAULT_RULES_DIR) -> tuple[Rule, ...]:
    """Load and validate every *.yaml rule under ``rules_dir`` (recursively).

    Raises RuleError on the first malformed file or duplicate id — governance
    refuses to start with an ambiguous rule set rather than guess.
    """
    rules_dir = Path(rules_dir)
    if not rules_dir.exists():
        raise RuleError(f"rules directory not found: {rules_dir}")

    rules = _load_dir(rules_dir)
    if not rules:
        raise RuleError(f"no rules found under {rules_dir}")
    return tuple(rules)


def load_rule_set(dirs: list[str | Path]) -> tuple[Rule, ...]:
    """Merge rules from several directories. Later directories override earlier
    ones by rule id, so an operator can drop a custom rule next to the built-ins
    (and intentionally override a built-in by reusing its id)."""
    merged: dict[str, Rule] = {}
    for d in dirs:
        for rule in _load_dir(Path(d)):
            merged[rule.id] = rule
    if not merged:
        raise RuleError(f"no rules found in any of: {dirs}")
    return tuple(merged.values())
