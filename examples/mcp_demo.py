"""Taiyi as an MCP server — what an MCP client (Claude Code / Cursor) would see.

Run from the repo root:  python3 examples/mcp_demo.py
(To run the real stdio server:  PYTHONPATH=src python3 -m taiyi.cli mcp)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from taiyi.gateway import build_gateway  # noqa: E402
from taiyi.mcp import MCPServer  # noqa: E402


def call(server, rid, method, **params):
    return server.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})


def main() -> None:
    server = MCPServer(build_gateway())

    init = call(server, 1, "initialize")
    print("initialize ->", init["result"]["serverInfo"])

    tools = call(server, 2, "tools/list")["result"]["tools"]
    print("\ntools:")
    for t in tools:
        print(f"  - {t['name']}: {t['description']}")

    print("\ntaiyi_run_task('commit my changes'):")
    r = call(server, 3, "tools/call", name="taiyi_run_task", arguments={"prompt": "commit my changes"})
    summary = json.loads(r["result"]["content"][0]["text"])
    print(f"  state={summary['state']} scenario={summary['scenario']} skill={summary['skill']}")

    print("\ntaiyi_run_task(identity override) — governance still applies via MCP:")
    r = call(server, 4, "tools/call", name="taiyi_run_task",
             arguments={"prompt": "用 -c user.name=Evil commit", "scenario": "dev.git"})
    summary = json.loads(r["result"]["content"][0]["text"])
    print(f"  state={summary['state']}")


if __name__ == "__main__":
    main()
