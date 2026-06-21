"""Loading a single Skill (SKILL.md + quality_gate.md)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from taiyi.core.markdown import split_frontmatter
from taiyi.skills.quality_gate import QualityGate, parse_gate


@dataclass
class Skill:
    name: str
    category: str                       # bundled | managed | workspace | auto_generated
    body: str
    risk: str | None = None
    applicability: str | None = None
    triggers: tuple[str, ...] = ()
    scenario: str | None = None
    gate: QualityGate | None = None
    gate_problems: list[str] = field(default_factory=list)
    path: Path | None = None

    @property
    def production_eligible(self) -> bool:
        """True only with a present, complete, passing quality gate."""
        return self.gate is not None and not self.gate_problems


def load_skill(skill_dir: str | Path) -> Skill:
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"no SKILL.md in {skill_dir}")
    meta, body = split_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = meta.get("name") or skill_dir.name

    gate_path = skill_dir / "quality_gate.md"
    gate: QualityGate | None = None
    problems: list[str]
    if gate_path.exists():
        try:
            gate = parse_gate(gate_path.read_text(encoding="utf-8"))
            problems = gate.problems()
        except Exception as e:  # noqa: BLE001 — a malformed gate is a (reported) problem, not a crash
            problems = [f"unparseable quality_gate.md: {e}"]
    else:
        problems = ["missing quality_gate.md"]

    return Skill(
        name=name,
        category=meta.get("category", "sandbox"),
        body=body,
        risk=meta.get("risk"),
        applicability=meta.get("applicability"),
        triggers=tuple(meta.get("triggers", []) or []),
        scenario=meta.get("scenario"),
        gate=gate,
        gate_problems=problems,
        path=skill_dir,
    )
