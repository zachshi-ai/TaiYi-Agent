"""A deterministic OpenAI-compatible model endpoint for adapter comparison.

This fixture is intentionally not a model-quality benchmark.  It gives multiple
harnesses the same HTTP endpoint, model id, response policy, and request log so
their transport/tool/completion semantics can be compared without provider
variance or credentials.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from taiyi.benchmark.schema import canonical_digest


CONTROLLED_MODEL_ID = "taiyi-controlled-model-v1"
CONTROLLED_MODEL_POLICY = "taiyi.controlled-openai-tool-policy/v1"


class ControlledModelServer:
    """Serve deterministic streamed Chat Completions on loopback only."""

    def __init__(self):
        self._requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.controller = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="taiyi-controlled-model",
            daemon=True,
        )

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def policy_digest(self) -> str:
        return canonical_digest({
            "policy": CONTROLLED_MODEL_POLICY,
            "model": CONTROLLED_MODEL_ID,
            "first_turn": "write exact result artifact through advertised tool",
            "after_tool": "return a fixed final answer",
        })

    def start(self) -> "ControlledModelServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "ControlledModelServer":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def request_summaries(self, *, since: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._requests[since:]]

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def respond(self, body: dict[str, Any]) -> bytes:
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        tools = body.get("tools") if isinstance(body.get("tools"), list) else []
        tool_names = _tool_names(tools)
        roles = [
            str(message.get("role", ""))
            for message in messages
            if isinstance(message, dict)
        ]
        summary = {
            "request_digest": canonical_digest(body),
            "model": str(body.get("model") or ""),
            "stream": body.get("stream") is True,
            "message_count": len(messages),
            "roles": roles,
            "tool_names": tool_names,
        }
        with self._lock:
            self._requests.append(summary)

        if "tool" in roles or _has_tool_observation(messages):
            return _sse_text("delivery prepared for independent verification")
        if "write" in tool_names:
            return _sse_tool("write", {"path": "result.txt", "content": "verified"})
        return _sse_text("tool: file:write result.txt verified")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length <= 0 or length > 4 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400)
            return
        if not isinstance(body, dict):
            self.send_error(400)
            return
        controller: ControlledModelServer = self.server.controller  # type: ignore[attr-defined]
        payload = controller.respond(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def _tool_names(tools: list[Any]) -> list[str]:
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
        elif tool.get("name"):
            names.append(str(tool["name"]))
    return names


def _has_tool_observation(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and "[tool result]" in content.casefold():
            return True
        if isinstance(content, list) and any(
            isinstance(item, dict)
            and str(item.get("type", "")).casefold() in {"tool_result", "toolresult"}
            for item in content
        ):
            return True
    return False


def _sse_tool(name: str, arguments: dict[str, Any]) -> bytes:
    call_id = "call_taiyi_controlled_1"
    chunks = [
        {
            "id": "chatcmpl-taiyi-controlled",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": CONTROLLED_MODEL_ID,
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, separators=(",", ":")),
                        },
                    }],
                },
                "finish_reason": None,
            }],
        },
        {
            "id": "chatcmpl-taiyi-controlled",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": CONTROLLED_MODEL_ID,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    ]
    return _encode_sse(chunks)


def _sse_text(content: str) -> bytes:
    chunks = [
        {
            "id": "chatcmpl-taiyi-controlled",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": CONTROLLED_MODEL_ID,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": None,
            }],
        },
        {
            "id": "chatcmpl-taiyi-controlled",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": CONTROLLED_MODEL_ID,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    ]
    return _encode_sse(chunks)


def _encode_sse(chunks: list[dict[str, Any]]) -> bytes:
    lines = [
        "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
        for chunk in chunks
    ]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


__all__ = [
    "CONTROLLED_MODEL_ID",
    "CONTROLLED_MODEL_POLICY",
    "ControlledModelServer",
]
