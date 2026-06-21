"""Configuration & deployment layer (M15)."""
from __future__ import annotations

from taiyi.config import load_config
from taiyi.core.types import PermitRequest, Verdict
from taiyi.gateway import build_gateway, build_gateway_from_config
from taiyi.governance import GovernanceEngine
from taiyi.runtime import TaskState
from taiyi.scheduler import PlanStep

CUSTOM_RULE = """
id: workspace.wire_transfer.review
domain: business
severity: advisory
scenario: finance.wire
applies_to: ["tool:wire*"]
trigger: pre_execution
check:
  type: deterministic
  match: tool_only
on_fail:
  action: request_confirmation
  message: Wire transfers require human review.
precedence: 50
owner: finance
"""


def test_load_config_from_file(tmp_path):
    p = tmp_path / "taiyi.yaml"
    p.write_text(
        "base_dir: ./state\nport: 9000\nexecutor: sandbox\nauth_tokens: [abc, def]\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.port == 9000
    assert cfg.executor == "sandbox"
    assert cfg.auth_tokens == ("abc", "def")


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "taiyi.yaml"
    p.write_text("port: 9000\n", encoding="utf-8")
    monkeypatch.setenv("TAIYI_PORT", "7777")
    monkeypatch.setenv("TAIYI_AUTH_TOKENS", "tok1,tok2")
    cfg = load_config(p)
    assert cfg.port == 7777
    assert cfg.auth_tokens == ("tok1", "tok2")


# --- Custom rules merge with the built-ins -----------------------------------

def test_custom_rule_merges_with_builtins(tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "wire.yaml").write_text(CUSTOM_RULE, encoding="utf-8")

    gov = GovernanceEngine(extra_rules_dirs=[rules_dir])
    # The custom rule is enforced...
    wire = gov.issue_permit(PermitRequest(tool="tool:wire", scenario="finance.wire", task_id="t"))
    assert wire.verdict is Verdict.NEEDS_REVIEW
    # ...and the built-in red lines still are too.
    override = gov.issue_permit(
        PermitRequest(tool="shell:git commit", args=["-c", "user.name=Evil"], scenario="dev.git", task_id="t")
    )
    assert override.verdict is Verdict.DENY


# --- Gateway from config -----------------------------------------------------

def test_build_gateway_from_config_runs(tmp_path):
    p = tmp_path / "taiyi.yaml"
    p.write_text(f"base_dir: {tmp_path / 'state'}\nexecutor: mock\n", encoding="utf-8")
    gw = build_gateway_from_config(load_config(p))
    ctx = gw.submit("commit my changes")
    assert ctx.state is TaskState.COMPLETED


def test_gateway_honors_extra_rules_dir(tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "wire.yaml").write_text(CUSTOM_RULE, encoding="utf-8")
    gw = build_gateway(extra_rules_dirs=(str(rules_dir),))
    # Drive the custom rule straight through the runtime's scheduler/governance.
    permit = gw.runtime.scheduler.request_permit(
        PlanStep("tool:wire", ["amount=999"]), "finance.wire", task_id="t"
    )
    assert permit.verdict is Verdict.NEEDS_REVIEW
