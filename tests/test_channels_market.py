"""Channel adapter + Skill market (M14)."""
from __future__ import annotations

import pytest

from taiyi.channels import InProcessChannel
from taiyi.gateway import build_gateway
from taiyi.market import SkillMarket
from taiyi.skills import DEFAULT_SKILLS_DIR, SkillError
from taiyi.skills.loader import load_skill


# --- Channel adapter ---------------------------------------------------------

def test_in_process_channel_runs_a_task():
    channel = InProcessChannel(build_gateway())
    reply = channel.handle_text("commit my changes")
    assert reply.state == "COMPLETED"
    assert reply.task_id


def test_channel_reflects_governance():
    channel = InProcessChannel(build_gateway())
    reply = channel.handle_text("用 -c user.name=Evil commit", scenario="dev.git")
    assert reply.state == "REJECTED"


# --- Skill market ------------------------------------------------------------

def test_market_lists_and_installs_gated_skill(tmp_path):
    market = SkillMarket(DEFAULT_SKILLS_DIR)
    assert "git_safe_commit" in market.list_available()

    dst = market.install("git_safe_commit", tmp_path)
    installed = load_skill(dst)
    assert installed.production_eligible
    assert (dst / "quality_gate.md").exists()


def test_market_refuses_ungated_skill(tmp_path):
    # Build a registry containing a skill with no gate.
    registry = tmp_path / "registry"
    sk = registry / "ungated"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: ungated\ncategory: managed\n---\n# x\n", encoding="utf-8")

    market = SkillMarket(registry)
    with pytest.raises(SkillError):
        market.install("ungated", tmp_path / "workspace")
