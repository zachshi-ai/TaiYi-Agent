"""Skill market — discover and install skills, gated on the way in.

The market is a registry of skills (here, a local directory; Git-based
distribution is the deferred transport). The load-bearing rule: **a skill is only
installable into a workspace if it passes its quality gate.** An ungated or
incomplete skill is refused at the door, so the market cannot become the junkyard
the design warns about.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from taiyi.skills import SkillError, SkillRegistry


class SkillMarket:
    def __init__(self, registry_dir: str | Path):
        self.registry = SkillRegistry.load_dir(registry_dir)

    def list_available(self) -> list[str]:
        return sorted(s.name for s in self.registry.all())

    def search(self, query: str) -> list[str]:
        q = query.lower()
        return sorted(
            s.name
            for s in self.registry.all()
            if q in (s.name + " " + (s.applicability or "")).lower()
        )

    def install(self, name: str, workspace_dir: str | Path) -> Path:
        skill = self.registry.get(name)
        if skill is None:
            raise SkillError(f"unknown skill: {name}")
        if not skill.production_eligible:
            raise SkillError(
                f"refused: {name!r} does not pass its quality gate ({', '.join(skill.gate_problems)})"
            )
        if skill.path is None:
            raise SkillError(f"{name!r} has no source path")
        dst = Path(workspace_dir) / name
        shutil.copytree(skill.path, dst, dirs_exist_ok=True)
        return dst


__all__ = ["SkillMarket"]
