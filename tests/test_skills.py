"""Skill engine + quality gates (Module 8)."""
from __future__ import annotations

import json

import pytest

from taiyi.memory import MemoryEngine
from taiyi.skills import SkillError, SkillRegistry, parse_gate
from taiyi.skills.loader import load_skill
from taiyi.skills.verification import SkillGateRunner

GOOD_GATE = """---
admission: [has a gate]
exit_criteria: [incident]
verification:
  - id: c1
    description: does the thing
    purpose: skill_contract
    runner: declared_plan_workflow
    prompt: demo task
    scenario: default
    operating_mode: quality
    plan:
      - tool: echo
        args: [demo task]
    expect:
      state: SIMULATED
      executed_tools_contains: [echo]
  - id: c2
    description: repeats the declared procedure
    purpose: skill_contract
    runner: declared_plan_workflow
    prompt: demo task again
    scenario: default
    plan:
      - tool: echo
        args: [demo task again]
    expect:
      state: SIMULATED
      executed_tools_contains: [echo]
  - id: c3
    description: routes the demo request
    purpose: routing
    runner: keyword_workflow
    prompt: demo task
    scenario: default
    expect:
      state: SIMULATED
      executed_tools_contains: [echo]
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
1. `echo` — do things
"""


def _make_skill(tmp_path, gate_text: str | None):
    d = tmp_path / "demo_skill"
    d.mkdir()
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    if gate_text is not None:
        (d / "quality_gate.md").write_text(gate_text, encoding="utf-8")
    return d


def _write_passing_lock(skill_dir):
    skill = load_skill(skill_dir)
    report = SkillGateRunner().run(skill)
    assert report.passes, report.to_dict()
    report.write_lock(skill_dir)


def _load_verified(skill_dir):
    _write_passing_lock(skill_dir)
    skill = load_skill(skill_dir)
    skill.runtime_verification = SkillGateRunner().run(skill)
    return skill


# --- Gate parsing/validation -------------------------------------------------

def test_complete_gate_passes():
    assert parse_gate(GOOD_GATE).passes


def test_incomplete_gate_reports_problems():
    gate = parse_gate(INCOMPLETE_GATE)
    assert not gate.passes
    assert any("exit_criteria" in p for p in gate.problems())


def test_gate_rejects_scalar_sections_instead_of_splitting_them_into_characters():
    malformed = GOOD_GATE.replace("admission: [has a gate]", "admission: has a gate")

    with pytest.raises(ValueError, match="must be a YAML list"):
        parse_gate(malformed)


# --- Loader: production eligibility ------------------------------------------

def test_skill_needs_a_release_lock_and_current_runtime_rerun(tmp_path):
    skill_dir = _make_skill(tmp_path, GOOD_GATE)
    initial = load_skill(skill_dir)
    assert not initial.production_eligible
    assert any("missing quality_gate.lock.json" in p for p in initial.release_problems)

    skill = _load_verified(skill_dir)
    assert skill.production_eligible
    assert skill.verification_environment == "mock"
    assert not skill.live_ready


def test_skill_without_gate_is_not_eligible(tmp_path):
    skill = load_skill(_make_skill(tmp_path, None))
    assert not skill.production_eligible
    assert "missing quality_gate.md" in skill.gate_problems


def test_skill_with_incomplete_gate_is_not_eligible(tmp_path):
    skill = load_skill(_make_skill(tmp_path, INCOMPLETE_GATE))
    assert not skill.production_eligible


def test_release_lock_becomes_stale_when_skill_changes(tmp_path):
    skill_dir = _make_skill(tmp_path, GOOD_GATE)
    _write_passing_lock(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

    skill = load_skill(skill_dir)

    assert not skill.production_eligible
    assert any("lock is stale" in p for p in skill.attestation_problems)


def test_hand_edited_production_evidence_level_is_rejected(tmp_path):
    skill_dir = _make_skill(tmp_path, GOOD_GATE)
    _write_passing_lock(skill_dir)
    lock = skill_dir / "quality_gate.lock.json"
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["environment"] = "production"
    lock.write_text(json.dumps(payload), encoding="utf-8")

    skill = load_skill(skill_dir)

    assert not skill.release_eligible
    assert any("unsupported verification environment" in p for p in skill.attestation_problems)


def test_runner_fails_an_observable_expectation_mismatch(tmp_path):
    bad_expectation = GOOD_GATE.replace("state: SIMULATED", "state: REJECTED", 1)
    skill = load_skill(_make_skill(tmp_path, bad_expectation))

    report = SkillGateRunner().run(skill)

    assert not report.passes
    assert report.failed_case_ids == ["c1"]
    assert "expected 'REJECTED', observed 'SIMULATED'" in report.results[0].detail


def test_skill_contract_plan_must_be_declared_in_skill_body(tmp_path):
    skill_dir = _make_skill(tmp_path, GOOD_GATE)
    (skill_dir / "SKILL.md").write_text(
        SKILL_MD.replace("1. `echo` — do things", "do things without a declared tool"),
        encoding="utf-8",
    )

    report = SkillGateRunner().run(load_skill(skill_dir))

    assert not report.passes
    assert "absent from SKILL.md: echo" in report.results[0].detail


# --- Registry refuses the sandbox from production ----------------------------

def test_registry_refuses_sandbox_skill_from_production(tmp_path):
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text(
        SKILL_MD.replace("demo_skill", "good"), encoding="utf-8"
    )
    (tmp_path / "good" / "quality_gate.md").write_text(GOOD_GATE, encoding="utf-8")
    _write_passing_lock(tmp_path / "good")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text(
        SKILL_MD.replace("demo_skill", "bad"), encoding="utf-8"
    )  # no gate

    reg = SkillRegistry.load_dir(tmp_path)
    reg.verify_release_candidates()
    assert {s.name for s in reg.production_skills()} == {"good"}
    assert {s.name for s in reg.sandbox_skills()} == {"bad"}
    assert reg.get_production("good").name == "good"
    with pytest.raises(SkillError):
        reg.get_production("bad")


# --- The shipped catalog is all production-eligible --------------------------

def test_shipped_catalog_skills_all_pass_gates():
    reg = SkillRegistry.load_dir()
    reports = reg.verify_release_candidates()
    names = {s.name for s in reg.all()}
    assert {"git_safe_commit", "weekly_report", "refund_request"} <= names
    assert set(reports) >= {"git_safe_commit", "weekly_report", "refund_request"}
    assert all(r.passes for r in reports.values())
    assert reg.sandbox_skills() == []
    assert all(not s.live_ready for s in reg.all())  # mock evidence is intentionally not live certification


def test_registry_indexes_into_memory():
    reg = SkillRegistry.load_dir()
    reg.verify_release_candidates()
    mem = MemoryEngine()
    n = reg.index_into(mem)
    assert n == len(reg.production_skills())
    assert "git_safe_commit" in mem.list_skills()
