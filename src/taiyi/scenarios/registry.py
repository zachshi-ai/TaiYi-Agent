"""Scenario engineering — environments as data, decoupled from prompts.

A scenario is a set of environment constraints (industry / role / task type) that
governance, scheduling, and validation all read. Keeping scenarios as standalone
Markdown files (not a paragraph inside a prompt) is what lets them be versioned,
reused across tasks, and switched at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from taiyi.core.markdown import split_frontmatter

DEFAULT_SCENARIOS_DIR = Path(__file__).resolve().parent / "catalog"


@dataclass
class Scenario:
    name: str
    description: str = ""
    triggers: tuple[str, ...] = ()
    body: str = ""
    path: Path | None = None


class ScenarioRegistry:
    def __init__(self, scenarios: list[Scenario] | None = None):
        self._scenarios: dict[str, Scenario] = {s.name: s for s in (scenarios or [])}

    @classmethod
    def load_dir(cls, scenarios_dir: str | Path = DEFAULT_SCENARIOS_DIR) -> "ScenarioRegistry":
        scenarios_dir = Path(scenarios_dir)
        reg = cls()
        if scenarios_dir.exists():
            for md in sorted(scenarios_dir.glob("*.md")):
                meta, body = split_frontmatter(md.read_text(encoding="utf-8"))
                name = meta.get("name") or md.stem
                reg.add(
                    Scenario(
                        name=name,
                        description=meta.get("description", ""),
                        triggers=tuple(meta.get("triggers", []) or []),
                        body=body,
                        path=md,
                    )
                )
        return reg

    def add(self, scenario: Scenario) -> None:
        self._scenarios[scenario.name] = scenario

    def get(self, name: str) -> Scenario | None:
        return self._scenarios.get(name)

    def names(self) -> list[str]:
        return sorted(self._scenarios)

    def all(self) -> list[Scenario]:
        return list(self._scenarios.values())
