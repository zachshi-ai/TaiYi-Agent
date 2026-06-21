"""The Skill registry — separates production-eligible skills from the sandbox.

Skills with a passing quality gate are production-eligible; everything else is
held in the sandbox and refused from the production path. ``get_production`` is the
guarded accessor the runtime would use; a sandbox skill is never returned from it.
"""
from __future__ import annotations

from pathlib import Path

from taiyi.skills.loader import Skill, load_skill

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent / "catalog"


class SkillError(KeyError):
    pass


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills: dict[str, Skill] = {s.name: s for s in (skills or [])}

    @classmethod
    def load_dir(cls, skills_dir: str | Path = DEFAULT_SKILLS_DIR) -> "SkillRegistry":
        skills_dir = Path(skills_dir)
        reg = cls()
        if skills_dir.exists():
            for sub in sorted(skills_dir.iterdir()):
                if sub.is_dir() and (sub / "SKILL.md").exists():
                    reg.add(load_skill(sub))
        return reg

    @classmethod
    def load_dirs(cls, dirs: list[str | Path]) -> "SkillRegistry":
        """Merge skills from several directories; later dirs override by name."""
        reg = cls()
        for d in dirs:
            for skill in cls.load_dir(d).all():
                reg.add(skill)
        return reg

    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def production_skills(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.production_eligible]

    def sandbox_skills(self) -> list[Skill]:
        return [s for s in self._skills.values() if not s.production_eligible]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_production(self, name: str) -> Skill:
        """Return a skill only if it is production-eligible; refuse otherwise."""
        skill = self._skills.get(name)
        if skill is None:
            raise SkillError(f"unknown skill: {name}")
        if not skill.production_eligible:
            raise SkillError(
                f"skill {name!r} is not production-eligible: {', '.join(skill.gate_problems)}"
            )
        return skill

    def index_into(self, memory) -> int:
        """Register production skills into a MemoryEngine's L2 index. Returns count."""
        n = 0
        for s in self.production_skills():
            memory.register_skill(s.name, s.applicability or "", s.triggers)
            n += 1
        return n
