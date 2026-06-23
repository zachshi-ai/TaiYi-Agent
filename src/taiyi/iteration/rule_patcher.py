"""Rule-patch suggestions — turn a recurring failure into a permanent check.

The Decide/Act of the OODA loop, *human-gated*: the loop only ever *suggests* a
rule (as data, in the M1 schema). A human approves it, which writes the YAML into
the rules directory; governance then loads it read-only on its next start. Nothing
auto-mutates the live rule set — the loop "only adds, never silently."
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from taiyi.iteration.trajectory import TrajectoryStore


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s).strip("_")


@dataclass
class RulePatchSuggestion:
    rule_id: str
    scenario: str
    tool: str
    occurrences: int
    rationale: str

    def to_rule_dict(self) -> dict:
        return {
            "id": self.rule_id,
            "domain": "safety",
            "severity": "advisory",
            "scenario": self.scenario,
            "applies_to": [self.tool],
            "trigger": "pre_execution",
            "check": {"type": "deterministic", "match": "tool_only"},
            "on_fail": {"action": "request_confirmation", "message": self.rationale},
            "precedence": 40,
            "owner": "loop-engineering",
            "audit": True,
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_rule_dict(), sort_keys=False, allow_unicode=True)


def suggest_rules(store: TrajectoryStore, *, threshold: int = 3) -> list[RulePatchSuggestion]:
    suggestions = []
    for (scenario, tool), n in store.failure_tool_classes().items():
        if n >= threshold:
            suggestions.append(
                RulePatchSuggestion(
                    rule_id=f"loop.{_slug(scenario)}.{_slug(tool)}.review",
                    scenario=scenario,
                    tool=tool,
                    occurrences=n,
                    rationale=(
                        f"Auto-suggested by Loop Engineering: {tool!r} ended badly {n} times "
                        f"in scenario {scenario!r}; require human review before running."
                    ),
                )
            )
    return suggestions


def approve(suggestion: RulePatchSuggestion, rules_dir: str | Path) -> Path:
    """Human-approved: persist the suggestion as a loadable rule file."""
    out_dir = Path(rules_dir) / "auto"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(suggestion.rule_id)}.yaml"
    path.write_text(suggestion.to_yaml(), encoding="utf-8")
    return path
