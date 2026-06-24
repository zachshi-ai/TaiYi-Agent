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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
