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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from taiyi.benchmark.schema import canonical_digest


CONTROLLED_MODEL_ID = "taiyi-controlled-model-v1"
CONTROLLED_MODEL_POLICY = "taiyi.controlled-openai-tool-policy/v1"
FAULT_NONE = "none"
FAULT_FIRST_TOKEN_STALL = "first_token_stall"
FAULT_STREAM_IDLE_STALL = "stream_idle_stall"
CONTROLLED_FAULTS = {
    FAULT_NONE,
    FAULT_FIRST_TOKEN_STALL,
    FAULT_STREAM_IDLE_STALL,
}


@dataclass(frozen=True)
class _ResponsePlan:
    request_index: int
    payload: bytes
    chunks: tuple[bytes, ...]
    delay_before_first: float = 0.0
    delay_after_first: float = 0.0


class ControlledModelServer:
    """Serve deterministic streamed Chat Completions on loopback only."""

    def __init__(
        self,
        *,
        fault: str = FAULT_NONE,
        fault_delay_seconds: float = 0.0,
    ):
        if fault not in CONTROLLED_FAULTS:
            raise ValueError(f"unsupported controlled-model fault: {fault}")
        if fault != FAULT_NONE and fault_delay_seconds <= 0:
            raise ValueError("fault_delay_seconds must be positive for a faulted server")
        self.fault = fault
        self.fault_delay_seconds = float(fault_delay_seconds)
        self._requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
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
        return controlled_model_policy_digest(
            fault=self.fault,
            fault_delay_seconds=self.fault_delay_seconds,
        )

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

    def request_summaries(
        self,
        *,
        since: int = 0,
        relative_to: float | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            values = []
            for item in self._requests[since:]:
                value = {
                    key: content
                    for key, content in item.items()
                    if key != "_accepted_monotonic"
                }
                if relative_to is not None:
                    value["request_started_after_seconds"] = max(
                        0.0,
                        float(item["_accepted_monotonic"]) - relative_to,
                    )
                values.append(value)
            return values

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def respond(self, body: dict[str, Any]) -> _ResponsePlan:
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        tools = body.get("tools") if isinstance(body.get("tools"), list) else []
        tool_names = _tool_names(tools)
        roles = [
            str(message.get("role", ""))
            for message in messages
            if isinstance(message, dict)
        ]
        summary: dict[str, Any] = {
            "request_digest": canonical_digest(body),
            "model": str(body.get("model") or ""),
            "stream": body.get("stream") is True,
            "message_count": len(messages),
            "roles": roles,
            "tool_names": tool_names,
            "injected_fault": self.fault,
            "response_started": False,
            "response_completed": False,
            "client_disconnected": False,
            "_accepted_monotonic": time.monotonic(),
        }
        with self._lock:
            request_index = len(self._requests)
            self._requests.append(summary)

        if "tool" in roles or _has_tool_observation(messages):
            payload = _sse_text("delivery prepared for independent verification")
        elif "write" in tool_names:
            payload = _sse_tool("write", {"path": "result.txt", "content": "verified"})
        else:
            payload = _sse_text("tool: file:write result.txt verified")

        chunks = (payload,)
        delay_before_first = 0.0
        delay_after_first = 0.0
        if self.fault == FAULT_FIRST_TOKEN_STALL:
            delay_before_first = self.fault_delay_seconds
        elif self.fault == FAULT_STREAM_IDLE_STALL:
            chunks = _split_after_first_event(payload)
            delay_after_first = self.fault_delay_seconds
        return _ResponsePlan(
            request_index=request_index,
            payload=payload,
            chunks=chunks,
            delay_before_first=delay_before_first,
            delay_after_first=delay_after_first,
        )

    def mark_delivery(
        self,
        request_index: int,
        *,
        started: bool = False,
        completed: bool = False,
        disconnected: bool = False,
    ) -> None:
        with self._lock:
            summary = self._requests[request_index]
            if started:
                summary["response_started"] = True
            if completed:
                summary["response_completed"] = True
            if disconnected:
                summary["client_disconnected"] = True


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
        plan = controller.respond(body)
        try:
            if plan.delay_before_first:
                time.sleep(plan.delay_before_first)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(plan.payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(plan.chunks[0])
            self.wfile.flush()
            controller.mark_delivery(plan.request_index, started=True)
            if plan.delay_after_first:
                time.sleep(plan.delay_after_first)
            for chunk in plan.chunks[1:]:
                self.wfile.write(chunk)
                self.wfile.flush()
            controller.mark_delivery(plan.request_index, completed=True)
        except (BrokenPipeError, ConnectionResetError, OSError):
            controller.mark_delivery(plan.request_index, disconnected=True)

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


def _split_after_first_event(payload: bytes) -> tuple[bytes, ...]:
    boundary = payload.find(b"\n\n")
    if boundary < 0 or boundary + 2 >= len(payload):
        return (payload,)
    return payload[:boundary + 2], payload[boundary + 2:]


def controlled_model_policy_digest(
    *,
    fault: str = FAULT_NONE,
    fault_delay_seconds: float = 0.0,
) -> str:
    policy: dict[str, Any] = {
        "policy": CONTROLLED_MODEL_POLICY,
        "model": CONTROLLED_MODEL_ID,
        "first_turn": "write exact result artifact through advertised tool",
        "after_tool": "return a fixed final answer",
    }
    if fault != FAULT_NONE:
        policy["fault"] = fault
        policy["fault_delay_seconds"] = float(fault_delay_seconds)
    return canonical_digest(policy)


__all__ = [
    "CONTROLLED_MODEL_ID",
    "CONTROLLED_MODEL_POLICY",
    "FAULT_FIRST_TOKEN_STALL",
    "FAULT_NONE",
    "FAULT_STREAM_IDLE_STALL",
    "ControlledModelServer",
    "controlled_model_policy_digest",
]
