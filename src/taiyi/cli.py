"""Taiyi CLI — the first channel.

    taiyi run "commit my changes" [--scenario dev.git] [--base-dir ./state]
    taiyi serve [--host 127.0.0.1] [--port 8080] [--token SECRET ...]
"""
from __future__ import annotations

import argparse
import sys

from taiyi.gateway import AuthPolicy, GatewayApp, build_gateway
from taiyi.gateway.server import make_server


def _run(args) -> int:
    gw = build_gateway(base_dir=args.base_dir)
    ctx = gw.submit(args.prompt, scenario=args.scenario)
    print(f"state:    {ctx.state.value}")
    print(f"scenario: {ctx.scenario}")
    if ctx.plan and ctx.plan.skill_name:
        print(f"skill:    {ctx.plan.skill_name}")
    if ctx.approval_id:
        print(f"approval: {ctx.approval_id}")
    if ctx.final_output:
        print("---")
        print(ctx.final_output)
    return 0 if ctx.state.value in ("COMPLETED", "NEEDS_REVIEW") else 1


def _serve(args) -> int:
    app = GatewayApp(build_gateway(base_dir=args.base_dir), auth=AuthPolicy(tuple(args.token or ())))
    server = make_server(app, host=args.host, port=args.port)
    where = f"http://{args.host}:{args.port}"
    print(f"taiyi gateway listening on {where}  (auth {'on' if app.auth.enabled else 'off'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


def _mcp(args) -> int:
    from taiyi.mcp import MCPServer

    MCPServer(build_gateway(base_dir=args.base_dir)).serve_stdio()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="taiyi", description="Taiyi / The One — Agent OS")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a single task")
    run.add_argument("prompt")
    run.add_argument("--scenario", default=None)
    run.add_argument("--base-dir", default=None)
    run.set_defaults(func=_run)

    serve = sub.add_parser("serve", help="start the HTTP gateway")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--token", action="append", help="valid bearer token (repeatable)")
    serve.add_argument("--base-dir", default=None)
    serve.set_defaults(func=_serve)

    mcp = sub.add_parser("mcp", help="run the MCP server over stdio")
    mcp.add_argument("--base-dir", default=None)
    mcp.set_defaults(func=_mcp)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
