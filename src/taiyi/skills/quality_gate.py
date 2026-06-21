"""Skill quality gates.

A Skill is procedural knowledge — "how to do X" with a clear contract and a
verifiable quality baseline. The design's rule is blunt: *a skill library without
quality gates is a junkyard.* So a Skill may enter the production path only if it
carries a complete `quality_gate.md` declaring how it is admitted, retired,
verified, what it touches, and how it is changed.

Module 8 enforces the gate's **presence and completeness**. Actually *running* a
gate's verification cases requires executing the skill and belongs to the Loop
Engineering module (M12); here a gate "passes" when it is well-formed and declares
at least one verification case.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from taiyi.core.markdown import split_frontmatter

# Every production gate must declare these.
REQUIRED_SECTIONS = ("admission", "exit_criteria", "verification", "side_effects", "upgrade")


class GateError(ValueError):
    pass


@dataclass
class QualityGate:
    admission: list[str] = field(default_factory=list)
    exit_criteria: list[str] = field(default_factory=list)
    verification: list[dict] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    upgrade: list[str] = field(default_factory=list)
    body: str = ""

    def problems(self) -> list[str]:
        """Return reasons this gate is not production-ready (empty == passes)."""
        issues: list[str] = []
        for section in REQUIRED_SECTIONS:
            value = getattr(self, section)
            if not value:
                issues.append(f"missing or empty section: {section}")
        for i, case in enumerate(self.verification):
            if not isinstance(case, dict) or "id" not in case or "description" not in case:
                issues.append(f"verification[{i}] needs an 'id' and a 'description'")
        return issues

    @property
    def passes(self) -> bool:
        return not self.problems()


def parse_gate(text: str) -> QualityGate:
    meta, body = split_frontmatter(text)
    if not meta:
        raise GateError("quality_gate.md has no YAML frontmatter")
    return QualityGate(
        admission=list(meta.get("admission", []) or []),
        exit_criteria=list(meta.get("exit_criteria", []) or []),
        verification=list(meta.get("verification", []) or []),
        side_effects=list(meta.get("side_effects", []) or []),
        upgrade=list(meta.get("upgrade", []) or []),
        body=body,
    )
