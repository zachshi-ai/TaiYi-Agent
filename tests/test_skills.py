"""Skill engine + quality gates (Module 8)."""
from __future__ import annotations

import pytest

from taiyi.memory import MemoryEngine
from taiyi.skills import SkillError, SkillRegistry, parse_gate
from taiyi.skills.loader import load_skill

GOOD_GATE = """---
admission: [has a gate]
exit_criteria: [incident]
verification:
  - id: c1
    description: does the thing
side_effects: [creates a commit]
upgrade: [PR + approval]
---
notes
"""

INCOMPLETE_GATE = """---
admission: [has a gate]
verification:
  - id: c1
    description: does the thing
---
notes
"""

SKILL_MD = """---
name: demo_skill
category: managed
triggers: [demo]
---
# Demo
do things
"""


def _make_skill(tmp_path, gate_text: str | None):
    d = tmp_path / "demo_skill"
    d.mkdir()
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    if gate_text is not None:
        (d / "quality_gate.md").write_text(gate_text, encoding="utf-8")
    return d


# --- Gate parsing/validation -------------------------------------------------

def test_complete_gate_passes():
    assert parse_gate(GOOD_GATE).passes


def test_incomplete_gate_reports_problems():
    gate = parse_gate(INCOMPLETE_GATE)
    assert not gate.passes
    assert any("exit_criteria" in p for p in gate.problems())


# --- Loader: production eligibility ------------------------------------------

def test_skill_with_complete_gate_is_eligible(tmp_path):
    skill = load_skill(_make_skill(tmp_path, GOOD_GATE))
    assert skill.production_eligible


def test_skill_without_gate_is_not_eligible(tmp_path):
    skill = load_skill(_make_skill(tmp_path, None))
    assert not skill.production_eligible
    assert "missing quality_gate.md" in skill.gate_problems


def test_skill_with_incomplete_gate_is_not_eligible(tmp_path):
    skill = load_skill(_make_skill(tmp_path, INCOMPLETE_GATE))
    assert not skill.production_eligible


# --- Registry refuses the sandbox from production ----------------------------

def test_registry_refuses_sandbox_skill_from_production(tmp_path):
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text(
        SKILL_MD.replace("demo_skill", "good"), encoding="utf-8"
    )
    (tmp_path / "good" / "quality_gate.md").write_text(GOOD_GATE, encoding="utf-8")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text(
        SKILL_MD.replace("demo_skill", "bad"), encoding="utf-8"
    )  # no gate

    reg = SkillRegistry.load_dir(tmp_path)
    assert {s.name for s in reg.production_skills()} == {"good"}
    assert {s.name for s in reg.sandbox_skills()} == {"bad"}
    assert reg.get_production("good").name == "good"
    with pytest.raises(SkillError):
        reg.get_production("bad")


# --- The shipped catalog is all production-eligible --------------------------

def test_shipped_catalog_skills_all_pass_gates():
    reg = SkillRegistry.load_dir()
    names = {s.name for s in reg.all()}
    assert {"git_safe_commit", "weekly_report", "refund_request"} <= names
    assert reg.sandbox_skills() == []  # every shipped skill has a passing gate


def test_registry_indexes_into_memory():
    reg = SkillRegistry.load_dir()
    mem = MemoryEngine()
    n = reg.index_into(mem)
    assert n == len(reg.production_skills())
    assert "git_safe_commit" in mem.list_skills()
