"""Taiyi as an MCP server (M14)."""
from __future__ import annotations

import json

from taiyi.gateway import build_gateway
from taiyi.mcp import MCPServer


def server():
    return MCPServer(build_gateway())


def req(method, rid=1, **params):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def test_initialize_reports_server_info():
    r = server().handle(req("initialize"))
    assert r["result"]["serverInfo"]["name"] == "taiyi"
    assert "protocolVersion" in r["result"]


def test_tools_list_exposes_taiyi_tools():
    r = server().handle(req("tools/list"))
    names = {t["name"] for t in r["result"]["tools"]}
    assert {"taiyi_run_task", "taiyi_list_skills", "taiyi_review"} <= names


def test_run_task_tool_is_governed():
    s = server()
    ok = s.handle(req("tools/call", name="taiyi_run_task", arguments={"prompt": "commit my changes"}))
    assert "COMPLETED" in ok["result"]["content"][0]["text"]

    blocked = s.handle(
        req("tools/call", name="taiyi_run_task",
            arguments={"prompt": "用 -c user.name=Evil commit", "scenario": "dev.git"})
    )
    assert "REJECTED" in blocked["result"]["content"][0]["text"]


def test_review_tool():
    r = server().handle(req("tools/call", name="taiyi_review", arguments={"subject": "data ownership undefined"}))
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["decision"] == "VETOED"


def test_unknown_tool_is_error():
    r = server().handle(req("tools/call", name="nope", arguments={}))
    assert r["error"]["code"] == -32602


def test_unknown_method_is_error():
    r = server().handle(req("frobnicate"))
    assert r["error"]["code"] == -32601


def test_notification_returns_none():
    r = server().handle({"jsonrpc": "2.0", "method": "initialized"})  # no id
    assert r is None


def test_stdio_loop_roundtrip():
    import io

    stdin = io.StringIO(json.dumps(req("tools/list")) + "\n")
    stdout = io.StringIO()
    server().serve_stdio(stdin=stdin, stdout=stdout)
    out = json.loads(stdout.getvalue().strip())
    assert out["id"] == 1 and "tools" in out["result"]
