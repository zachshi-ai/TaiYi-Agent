"""Taiyi CLI — the first channel.

    taiyi run "commit my changes" [--scenario dev.git] [--base-dir ./state]
    taiyi serve [--host 127.0.0.1] [--port 8080] [--token SECRET ...]
"""
from __future__ import annotations

import argparse
import sys

from taiyi.config import load_config
from taiyi.gateway import AuthPolicy, GatewayApp, build_gateway, build_gateway_from_config
from taiyi.gateway.server import make_server


def _gateway(args):
    if getattr(args, "config", None):
        return build_gateway_from_config(load_config(args.config))
    return build_gateway(base_dir=args.base_dir)


def _run(args) -> int:
    gw = _gateway(args)
    ctx = gw.submit(args.prompt, scenario=args.scenario)
    print(f"state:    {ctx.state.value}")
    print(f"scenario: {ctx.scenario}")
    if ctx.plan and ctx.plan.skill_name:
        print(f"skill:    {ctx.plan.skill_name}")
    executed = [s.step.tool for s in ctx.executed_steps]
    if executed:
        print(f"steps:    {' -> '.join(executed)}")
    if ctx.approval_id:
        print(f"approval: {ctx.approval_id}")
    if ctx.final_output:
        print("---")
        print(ctx.final_output)
    return 0 if ctx.state.value in ("COMPLETED", "NEEDS_REVIEW") else 1


def _serve(args) -> int:
    from pathlib import Path

    if getattr(args, "config", None):
        cfg = load_config(args.config)
        gw = build_gateway_from_config(cfg)
        app = GatewayApp(gw, auth=AuthPolicy(tuple(cfg.auth_tokens)))
        app.config_path = cfg.config_path
        # Command-line flags override the config file's host/port/static_dir so a
        # user can run `taiyi serve --config x.yaml --port 9000` without editing
        # the file. host defaults to the config's value if no flag is given.
        host = args.host if args.host is not None else cfg.host
        port = args.port if args.port is not None else cfg.port
        static_dir = args.static_dir or cfg.static_dir
    else:
        app = GatewayApp(build_gateway(base_dir=args.base_dir), auth=AuthPolicy(tuple(args.token or ())))
        host = args.host or "127.0.0.1"
        port = args.port or 8080
        static_dir = args.static_dir
    # Default to the bundled web UI build if nothing specified and it exists.
    if not static_dir:
        bundled = Path(__file__).resolve().parents[2] / "web" / "dist"
        if bundled.is_dir():
            static_dir = str(bundled)
    server = make_server(app, host=host, port=port, static_dir=static_dir)
    where = f"http://{host}:{port}"
    ui = f"  web UI: {where}/" if static_dir else ""
    print(f"taiyi gateway listening on {where}{ui}  (auth {'on' if app.auth.enabled else 'off'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


def _mcp(args) -> int:
    from taiyi.mcp import MCPServer

    MCPServer(_gateway(args)).serve_stdio()
    return 0


def _review(args) -> int:
    """OODA Act gate: list / approve / reject auto-suggested rules & skills."""
    gw = _gateway(args)
    if gw.iteration is None:
        print("iteration engine not configured")
        return 1

    if args.action == "list":
        pending = gw.iteration.list_pending()
        if not pending:
            print("no pending suggestions")
            return 0
        for p in pending:
            s = p.summary()
            print(f"[{p.id}] {p.kind:6} {s.get('rule_id') or s.get('name')}")
            if p.kind == "rule":
                print(f"      scenario={s.get('scenario')} tool={s.get('tool')} "
                      f"occurrences={s.get('occurrences')}")
                print(f"      {s.get('rationale')}")
            else:
                print(f"      scenario={s.get('scenario')} tools={s.get('tools')} "
                      f"occurrences={s.get('occurrences')}")
        return 0

    # approve / reject <id>
    try:
        if args.action == "approve":
            path = gw.resolve_review(args.id, approve=True)
            print(f"approved #{args.id} → written to {path}")
            print("(restart taiyi for governance/skills to load the new rule/skill)")
        else:
            gw.resolve_review(args.id, approve=False)
            print(f"rejected #{args.id}")
    except KeyError:
        print(f"unknown suggestion: #{args.id}")
        return 1
    except RuntimeError as e:
        print(f"cannot approve: {e}")
        return 1
    return 0


def _prompt(question: str, default: str = "", *, choices=None) -> str:
    """Ask on stdin with a default shown in brackets. Empty input keeps default."""
    hint = f" [{default}]" if default else ""
    while True:
        raw = input(f"{question}{hint}: ").strip()
        val = raw or default
        if choices and val not in choices:
            print(f"  choose one of: {', '.join(choices)}")
            continue
        return val


def _render_config(cfg: dict) -> str:
    """Render a commented taiyi.yaml from the collected answers."""
    def y(v):
        if v is None:
            return "null"
        return v

    return f"""\
# Taiyi / The One — generated by `taiyi init`. Edit freely.
# Every value can also be set via TAIYI_* env vars (env overrides the file).
# Run with:  taiyi serve --config taiyi.yaml

base_dir: {cfg['base_dir']}          # persistence root: audit log, SQLite memory, markdown
host: {cfg['host']}
port: {cfg['port']}

# Bearer auth. Empty list = open (local use). Add tokens to require auth.
auth_tokens: {cfg['auth_tokens']}

# Execution backend:  mock (side-effect-free) | sandbox (real, governed + isolated)
executor: {cfg['executor']}
sandbox_dir: {cfg['sandbox_dir']}
sandbox_backend: {cfg['sandbox_backend']}   # local | sandbox_exec (macOS deny-all)

max_rounds: {cfg['max_rounds']}
mode: {cfg['mode']}                # agent (ReAct, default) | workflow (plan-once)

# LLM provider. offline = deterministic, zero tokens/network. For a real model,
# set provider + base_url + model (+ api_key). Requires:  pip install "taiyi[live] @ ..."
provider: {cfg['provider']}
base_url: {y(cfg['base_url'])}
model: {y(cfg['model'])}
api_key: {y(cfg['api_key'])}
api_key_env: {y(cfg['api_key_env'])}

# Custom rules/scenarios/skills, merged with the built-ins (same id/name overrides).
rules_dirs: []
scenarios_dirs: []
skills_dirs: []

log_level: {cfg['log_level']}
"""


def _init(args) -> int:
    """Interactively generate a taiyi.yaml (or write defaults non-interactively)."""
    from pathlib import Path

    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"{out} already exists — pass --force to overwrite.")
        return 1

    # Defaults (also used verbatim in non-interactive mode).
    cfg = {
        "base_dir": "./state", "host": "127.0.0.1", "port": 8080,
        "auth_tokens": "[]", "executor": "mock", "sandbox_dir": "./state/sandbox",
        "sandbox_backend": "local", "max_rounds": 1, "mode": "agent",
        "provider": "offline", "base_url": None, "model": None,
        "api_key": None, "api_key_env": None, "log_level": "info",
    }

    interactive = not args.yes and sys.stdin.isatty()
    if interactive:
        print("Taiyi setup — press Enter to accept the default in [brackets].\n")
        provider = _prompt("LLM provider (offline/ollama/openai_compat)", "offline",
                           choices=["offline", "ollama", "openai_compat"])
        cfg["provider"] = provider
        if provider == "ollama":
            cfg["base_url"] = _prompt("Ollama base_url", "http://localhost:11434/v1")
            cfg["model"] = _prompt("model (as you `ollama pull`ed)", "qwen2.5:7b")
        elif provider == "openai_compat":
            cfg["base_url"] = _prompt("base_url", "https://api.deepseek.com/v1")
            cfg["model"] = _prompt("model", "deepseek-chat")
            key = _prompt("api_key (blank to skip / set later)", "")
            cfg["api_key"] = key or None
        cfg["executor"] = _prompt("executor (mock/sandbox)", "mock",
                                  choices=["mock", "sandbox"])
        if cfg["executor"] == "sandbox" and sys.platform == "darwin":
            cfg["sandbox_backend"] = _prompt(
                "sandbox_backend (local/sandbox_exec)", "sandbox_exec",
                choices=["local", "sandbox_exec"])
        token = _prompt("bearer auth token (blank = open, local only)", "")
        if token:
            cfg["auth_tokens"] = f'["{token}"]'
        print()

    out.write_text(_render_config(cfg), encoding="utf-8")
    print(f"wrote {out}")
    if cfg["provider"] != "offline":
        print("live model configured — make sure httpx is installed:")
        print('  pip install "taiyi[live] @ git+https://github.com/zachshi-ai/TaiYi-Agent.git"')
    print(f"start it:  taiyi serve --config {out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="taiyi", description="Taiyi / The One — Agent OS")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a single task")
    run.add_argument("prompt")
    run.add_argument("--scenario", default=None)
    run.add_argument("--base-dir", default=None)
    run.add_argument("--config", default=None, help="path to taiyi.yaml")
    run.set_defaults(func=_run)

    serve = sub.add_parser("serve", help="start the HTTP gateway")
    serve.add_argument("--host", default=None, help="bind host (default: config or 127.0.0.1)")
    serve.add_argument("--port", type=int, default=None, help="bind port (default: config or 8080)")
    serve.add_argument("--token", action="append", help="valid bearer token (repeatable)")
    serve.add_argument("--base-dir", default=None)
    serve.add_argument("--config", default=None, help="path to taiyi.yaml")
    serve.add_argument("--static-dir", default=None, help="directory of built web assets (default: web/dist)")
    serve.set_defaults(func=_serve)

    mcp = sub.add_parser("mcp", help="run the MCP server over stdio")
    mcp.add_argument("--base-dir", default=None)
    mcp.add_argument("--config", default=None, help="path to taiyi.yaml")
    mcp.set_defaults(func=_mcp)

    review = sub.add_parser("review", help="review OODA-suggested rules & skills")
    review.add_argument("action", choices=["list", "approve", "reject"])
    review.add_argument("id", nargs="?", type=int, help="suggestion id (for approve/reject)")
    review.add_argument("--base-dir", default=None)
    review.add_argument("--config", default=None, help="path to taiyi.yaml")
    review.set_defaults(func=_review)

    init = sub.add_parser("init", help="interactively generate a taiyi.yaml")
    init.add_argument("-o", "--output", default="taiyi.yaml", help="path to write (default: taiyi.yaml)")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.add_argument("--yes", "-y", action="store_true", help="non-interactive: write defaults")
    init.set_defaults(func=_init)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
