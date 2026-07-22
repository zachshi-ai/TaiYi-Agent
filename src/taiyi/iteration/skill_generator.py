"""Skill auto-generation — sediment a repeated task shape into a gated Skill.

When the same skill-less task shape recurs, draft a Skill from its tool sequence.
The draft is category ``auto_generated`` and includes one seed verification case.
It deliberately does not meet the three-case release minimum: a human must add
boundary/failure coverage, review the procedure, promote its category, and run
the gate before it can enter the governed runtime catalog.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml


def _sig(tools: tuple[str, ...]) -> str:
    return hashlib.md5("|".join(tools).encode("utf-8")).hexdigest()[:6]


@dataclass
class SkillDraft:
    name: str
    scenario: str
    tools: tuple[str, ...]
    occurrences: int

    def skill_md(self) -> str:
        frontmatter = {
            "name": self.name,
            "category": "auto_generated",
            "risk": "low",
            "applicability": f"Auto-generated from {self.occurrences} repeats in {self.scenario}",
            "scenario": self.scenario,
        }
        steps = "\n".join(f"{i}. `{t}`" for i, t in enumerate(self.tools, 1))
        return (
            "---\n"
            + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + f"# {self.name}\n\n## Steps (observed)\n{steps}\n"
        )

    def gate_md(self) -> str:
        gate = {
            "admission": ["Auto-generated; pending human approval to promote to managed"],
            "exit_criteria": ["Success rate falls below the agreed baseline"],
            "verification": [
                {
                    "id": "runs_to_completion",
                    "description": "the recorded tool sequence passes in simulation",
                    "purpose": "skill_contract",
                    "runner": "declared_plan_workflow",
                    "prompt": f"Execute the observed workflow for {self.scenario}",
                    "scenario": self.scenario,
                    "operating_mode": "quality",
                    "plan": [{"tool": tool, "args": []} for tool in self.tools],
                    "expect": {
                        "state": "SIMULATED",
                        "selected_skill": self.name,
                        "executed_tools_contains": list(self.tools),
                    },
                }
            ],
            "side_effects": ["Inherits the side effects of the underlying tools"],
            "upgrade": ["Human approval required to promote to managed"],
        }
        return (
            "---\n"
            + yaml.safe_dump(gate, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + f"# {self.name} — Quality Gate (auto-generated)\n"
        )


def generate_skill_draft(
    scenario: str, tools: tuple[str, ...], occurrences: int, *, name: str | None = None
) -> SkillDraft:
    name = name or f"auto_{scenario.replace('.', '_')}_{_sig(tuple(tools))}"
    return SkillDraft(name=name, scenario=scenario, tools=tuple(tools), occurrences=occurrences)


def write_draft(draft: SkillDraft, skills_dir: str | Path) -> Path:
    """Write the draft into a (sandbox) skills directory."""
    d = Path(skills_dir) / draft.name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(draft.skill_md(), encoding="utf-8")
    (d / "quality_gate.md").write_text(draft.gate_md(), encoding="utf-8")
    return d
