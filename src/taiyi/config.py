"""Configuration for a self-operated Taiyi deployment.

One place to declare how an instance runs: persistence, network, auth, the
executor, validation rounds, and any custom rule/scenario/skill directories that
merge with the built-ins. Loaded from a YAML file and/or ``TAIYI_*`` environment
variables (env overrides the file), so the same image runs anywhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

import yaml

_LIST_FIELDS = {"auth_tokens", "rules_dirs", "scenarios_dirs", "skills_dirs"}


@dataclass
class TaiyiConfig:
    base_dir: str | None = None          # persistence root (audit/memory/markdown)
    host: str = "127.0.0.1"
    port: int = 8080
    auth_tokens: tuple[str, ...] = ()    # if non-empty, Bearer auth is required
    executor: str = "mock"               # mock | sandbox
    sandbox_dir: str | None = None       # working dir for the sandbox executor
    sandbox_backend: str = "local"       # local | sandbox_exec (macOS deny-all isolation)
    max_rounds: int = 1                  # PDCA correction rounds
    rules_dirs: tuple[str, ...] = ()     # extra rule dirs, merged with built-ins
    scenarios_dirs: tuple[str, ...] = ()
    skills_dirs: tuple[str, ...] = ()
    log_level: str = "info"
    # --- web UI ----------------------------------------------------------------
    static_dir: str | None = None       # directory of built web assets (web/dist); None disables UI
    config_path: str | None = None      # path the config was loaded from (for write-back via /v1/config)
    # --- runtime shape -------------------------------------------------------
    mode: str = "agent"                  # agent (ReAct, default) | workflow (plan-once)
    # --- LLM provider seam (opt-in; offline until a live adapter is wired) ---
    provider: str = "offline"            # offline | openai_compat | ollama
    model: str | None = None             # model id; None → provider default
    base_url: str | None = None          # OpenAI-compatible endpoint, e.g. http://localhost:11434/v1
    api_key: str | None = None           # the key value itself (empty for local Ollama)
    api_key_env: str | None = None       # alt: name of env var holding the key (overrides api_key)


# Fields the web UI is allowed to write back via PUT /v1/config. Anything else
# is read-only from the UI to avoid clobbering deployment-specific settings.
WRITABLE_FIELDS = {"provider", "model", "mode", "base_url", "api_key", "api_key_env",
                   "max_rounds", "executor", "host", "port", "log_level"}


def load_config(path: str | Path | None = None) -> TaiyiConfig:
    data: dict = {}
    if path:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config {path} must be a mapping")
        data = loaded

    valid = {f.name for f in fields(TaiyiConfig)}
    kwargs = {}
    for k, v in data.items():
        if k not in valid:
            continue
        kwargs[k] = tuple(v) if k in _LIST_FIELDS and v is not None else v

    cfg = _apply_env(TaiyiConfig(**kwargs))
    if path:
        cfg.config_path = str(path)
    return cfg


def save_config(path: str | Path, updates: dict) -> None:
    """Merge ``updates`` into the YAML config at ``path`` and write it back.

    Only ``WRITABLE_FIELDS`` are applied; unknown keys are ignored. Unknown keys
    and the file's own comments are preserved by round-tripping the mapping (yaml
    round-trip drops comments, but keeps unknown keys). The live runtime is NOT
    affected — a restart is required to load the new file (by design: governance
    and skill sets load once, read-only).
    """
    p = Path(path)
    data: dict = {}
    if p.is_file():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    for k, v in updates.items():
        if k in WRITABLE_FIELDS and v is not None:
            data[k] = list(v) if isinstance(v, tuple) else v
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _apply_env(cfg: TaiyiConfig) -> TaiyiConfig:
    env = os.environ
    over: dict = {}
    if env.get("TAIYI_BASE_DIR"):
        over["base_dir"] = env["TAIYI_BASE_DIR"]
    if env.get("TAIYI_HOST"):
        over["host"] = env["TAIYI_HOST"]
    if env.get("TAIYI_PORT"):
        over["port"] = int(env["TAIYI_PORT"])
    if env.get("TAIYI_EXECUTOR"):
        over["executor"] = env["TAIYI_EXECUTOR"]
    if env.get("TAIYI_SANDBOX_DIR"):
        over["sandbox_dir"] = env["TAIYI_SANDBOX_DIR"]
    if env.get("TAIYI_SANDBOX_BACKEND"):
        over["sandbox_backend"] = env["TAIYI_SANDBOX_BACKEND"]
    if env.get("TAIYI_MAX_ROUNDS"):
        over["max_rounds"] = int(env["TAIYI_MAX_ROUNDS"])
    if env.get("TAIYI_AUTH_TOKENS"):
        over["auth_tokens"] = tuple(t for t in env["TAIYI_AUTH_TOKENS"].split(",") if t)
    if env.get("TAIYI_MODE"):
        over["mode"] = env["TAIYI_MODE"]
    if env.get("TAIYI_PROVIDER"):
        over["provider"] = env["TAIYI_PROVIDER"]
    if env.get("TAIYI_MODEL"):
        over["model"] = env["TAIYI_MODEL"]
    if env.get("TAIYI_API_KEY_ENV"):
        over["api_key_env"] = env["TAIYI_API_KEY_ENV"]
    if env.get("TAIYI_BASE_URL"):
        over["base_url"] = env["TAIYI_BASE_URL"]
    if env.get("TAIYI_API_KEY"):
        over["api_key"] = env["TAIYI_API_KEY"]
    if env.get("TAIYI_STATIC_DIR"):
        over["static_dir"] = env["TAIYI_STATIC_DIR"]
    return replace(cfg, **over)
