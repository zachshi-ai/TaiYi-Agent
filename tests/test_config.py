"""Configuration & deployment layer (M15)."""
from __future__ import annotations

from taiyi.config import TaiyiConfig, load_config
from taiyi.core.types import PermitRequest, Verdict
from taiyi.gateway import build_gateway, build_gateway_from_config
from taiyi.governance import GovernanceEngine
from taiyi.llm import make_provider, make_provider_router
from taiyi.policy import resolve_policy
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


def test_runtime_shape_and_operating_strategy_are_separate_with_legacy_alias(tmp_path):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("mode: workflow\noperating_mode: quality\n", encoding="utf-8")
    cfg = load_config(legacy)
    assert cfg.runtime_mode == "workflow"
    assert cfg.operating_mode == "quality"


def test_operating_mode_env_override(tmp_path, monkeypatch):
    p = tmp_path / "taiyi.yaml"
    p.write_text("runtime_mode: workflow\noperating_mode: balanced\n", encoding="utf-8")
    monkeypatch.setenv("TAIYI_RUNTIME_MODE", "agent")
    monkeypatch.setenv("TAIYI_OPERATING_MODE", "efficiency")
    monkeypatch.setenv("TAIYI_QUALITY_MODEL", "strong-model")
    monkeypatch.setenv("TAIYI_EFFICIENCY_MODEL", "fast-model")
    monkeypatch.setenv("TAIYI_EXTERNAL_GIT_VALIDATION", "false")
    monkeypatch.setenv("TAIYI_EXTERNAL_GIT_REMOTE_VALIDATION", "true")
    monkeypatch.setenv("TAIYI_EXTERNAL_GITHUB_VALIDATION", "true")
    monkeypatch.setenv("TAIYI_GITHUB_EXPECTED_LOGIN", "zachshi-ai")
    monkeypatch.setenv("TAIYI_TOOL_HARD_TIMEOUT", "900")
    monkeypatch.setenv("TAIYI_TOOL_IDLE_TIMEOUT", "45")
    monkeypatch.setenv("TAIYI_JOB_HEARTBEAT_INTERVAL", "0.5")
    monkeypatch.setenv("TAIYI_TOOL_OUTPUT_LIMIT", "8192")
    monkeypatch.setenv("TAIYI_TOOL_ARTIFACT_LIMIT", "1048576")
    monkeypatch.setenv("TAIYI_LLM_CONNECT_TIMEOUT", "5")
    monkeypatch.setenv("TAIYI_LLM_FIRST_TOKEN_TIMEOUT", "40")
    monkeypatch.setenv("TAIYI_LLM_STREAM_IDLE_TIMEOUT", "20")
    monkeypatch.setenv("TAIYI_LLM_HARD_TIMEOUT", "120")
    monkeypatch.setenv("TAIYI_CONTEXT_WINDOW_TOKENS", "64000")
    monkeypatch.setenv("TAIYI_CONTEXT_RESPONSE_RESERVE_TOKENS", "8000")
    monkeypatch.setenv("TAIYI_CONTEXT_TOOL_RESULT_MAX_TOKENS", "2000")
    monkeypatch.setenv("TAIYI_REPOSITORY_INDEX_ENABLED", "false")
    monkeypatch.setenv("TAIYI_REPOSITORY_INDEX_MAX_FILES", "1234")
    monkeypatch.setenv("TAIYI_REPOSITORY_FILE_MAX_BYTES", "65536")
    cfg = load_config(p)
    assert cfg.runtime_mode == "agent"
    assert cfg.operating_mode == "efficiency"
    assert cfg.quality_model == "strong-model"
    assert cfg.efficiency_model == "fast-model"
    assert cfg.external_git_validation is False
    assert cfg.external_git_remote_validation is True
    assert cfg.external_github_validation is True
    assert cfg.github_expected_login == "zachshi-ai"
    assert cfg.tool_hard_timeout == 900
    assert cfg.tool_idle_timeout == 45
    assert cfg.job_heartbeat_interval == 0.5
    assert cfg.tool_output_limit == 8192
    assert cfg.tool_artifact_limit == 1048576
    assert cfg.llm_connect_timeout == 5
    assert cfg.llm_first_token_timeout == 40
    assert cfg.llm_stream_idle_timeout == 20
    assert cfg.llm_hard_timeout == 120
    assert cfg.context_window_tokens == 64000
    assert cfg.context_response_reserve_tokens == 8000
    assert cfg.context_tool_result_max_tokens == 2000
    assert cfg.repository_index_enabled is False
    assert cfg.repository_index_max_files == 1234
    assert cfg.repository_file_max_bytes == 65536


def test_configured_mode_models_build_explicit_routes_with_default_fallback():
    cfg = TaiyiConfig(
        provider="openai_compat",
        base_url="https://models.example/v1",
        model="default-model",
        quality_model="strong-model",
        efficiency_model="fast-model",
    )
    provider = make_provider(cfg)
    router = make_provider_router(cfg, provider)

    quality = router.select(resolve_policy("quality"))
    balanced = router.select(resolve_policy("balanced"))
    efficiency = router.select(resolve_policy("efficiency"))

    assert quality.model == "strong-model" and not quality.fallback
    assert balanced.model == "default-model" and balanced.fallback
    assert efficiency.model == "fast-model" and not efficiency.fallback


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
    assert ctx.state is TaskState.SIMULATED


def test_sandbox_job_settings_are_wired_from_config(tmp_path):
    cfg = TaiyiConfig(
        base_dir=str(tmp_path / "state"),
        executor="sandbox",
        tool_hard_timeout=321,
        tool_idle_timeout=17,
        job_heartbeat_interval=0.25,
        tool_output_limit=4096,
        tool_artifact_limit=65536,
        context_window_tokens=64_000,
        context_response_reserve_tokens=8_000,
        context_tool_result_max_tokens=2_000,
        repository_index_max_files=1234,
        repository_file_max_bytes=65_536,
    )
    gw = build_gateway_from_config(cfg)
    executor = gw.runtime.executor

    assert executor.hard_timeout == 321
    assert executor.idle_timeout == 17
    assert executor.heartbeat_interval == 0.25
    assert executor.output_limit == 4096
    assert executor.artifact_limit == 65536
    assert executor.jobs.root == (tmp_path / "state" / "jobs").resolve()
    context_engine = gw.runtime.context_engine
    assert context_engine.context_window_tokens == 64_000
    assert context_engine.response_reserve_tokens == 8_000
    assert context_engine.tool_result_max_tokens == 2_000
    assert context_engine.repository.max_files == 1234
    assert context_engine.repository.max_file_bytes == 65_536


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
