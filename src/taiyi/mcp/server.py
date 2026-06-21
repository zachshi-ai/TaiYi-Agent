"""Taiyi as an MCP server (JSON-RPC 2.0).

Implements the subset of the Model Context Protocol an agent client needs —
`initialize`, `tools/list`, `tools/call` — so Claude Code / Cursor / Codex can call
Taiyi's governed tools. `handle()` is transport-agnostic (dict in, dict out) and so
testable without stdio; `serve_stdio()` runs the newline-delimited JSON-RPC loop.
"""
from __future__ import annotations

import json
import sys

from taiyi import __version__
from taiyi.mcp.tools import build_tools

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    def __init__(self, gateway, *, name: str = "taiyi", version: str = __version__):
        self.gateway = gateway
        self.tools = build_tools(gateway)
        self.name = name
        self.version = version

    def handle(self, request: dict) -> dict | None:
        """Process one JSON-RPC request. Returns the response, or None for a notification."""
        if request.get("jsonrpc") != "2.0":
            return self._error(request.get("id"), -32600, "invalid request")
        rid = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if rid is None:  # notification — no response
            return None
        if method == "initialize":
            return self._ok(rid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        if method == "tools/list":
            return self._ok(rid, {
                "tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                    for t in self.tools.values()
                ]
            })
        if method == "tools/call":
            return self._call(rid, params)
        return self._error(rid, -32601, f"method not found: {method}")

    def _call(self, rid, params: dict) -> dict:
        name = params.get("name")
        tool = self.tools.get(name)
        if tool is None:
            return self._error(rid, -32602, f"unknown tool: {name}")
        try:
            text = tool.handler(params.get("arguments") or {})
        except Exception as e:  # noqa: BLE001 — report as a tool error, not a transport crash
            return self._ok(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        return self._ok(rid, {"content": [{"type": "text", "text": text}]})

    @staticmethod
    def _ok(rid, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _error(rid, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    def serve_stdio(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
