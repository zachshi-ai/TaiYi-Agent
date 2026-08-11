"""Live LLM provider — the OpenAI-compatible adapter, plus the wiring factory.

This is a *real* adapter, not a skeleton: ``OpenAICompatProvider.complete()``
issues an HTTP POST to any OpenAI-compatible ``/chat/completions`` endpoint and
parses the response. One adapter covers Ollama (``http://localhost:11434/v1``,
no key), DeepSeek, 智谱, Moonshot, OpenAI, SiliconFlow, and any other service
that speaks the OpenAI chat protocol — the only difference between them is the
``base_url``/``model``/``api_key`` you configure.

Design notes:
* Uses ``httpx`` (pure Python) rather than the ``openai`` SDK, so there is no
  heavyweight dependency and the request/response shape is fully under our
  control. httpx is an opt-in ``[live]`` extra.
* The API key lives in the config file (gitignored) — never in git, never echoed
  back by the config endpoint. An empty key (local Ollama) simply omits the
  Authorization header.
* Tool calls: OpenAI function-calling is spotty across providers (Ollama models
  vary). So the adapter prefers the native ``tool_calls`` field when present, and
  otherwise parses a ``tool: <name> <args...>`` line from the model's text. The
  AgentRuntime's ReAct loop works with either — it just reads ``tool_calls``.
* Failures surface as exceptions the runtime catches into a FAILED task — the
  adapter never fabricates a response (that would break the governance invariant).
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any

from taiyi.llm.base import DEFAULT_LIVE_MODEL, LLMMessage, LLMProvider, LLMResponse, ToolCall
from taiyi.llm.errors import LLMErrorKind, LLMRequestError

# Matches the tool-call convention the system prompt teaches the model to emit:
#   tool: shell:git status
#   tool: notify:feishu --msg hello world
_TOOL_LINE = re.compile(r"^\s*tool:\s*(\S+)(?:\s+(.*))?$", re.MULTILINE)


def _messages_to_openai(messages: list[LLMMessage]) -> list[dict]:
    """taiyi LLMMessage -> OpenAI {role, content}.

    taiyi uses a ``system`` role for both the system prompt and the scenario
    injection; OpenAI accepts multiple system messages, so we pass them through.
    """
    return [{"role": m.role, "content": m.content} for m in messages]


def _parse_tool_calls_from_text(text: str) -> list[ToolCall]:
    """Fall back to text parsing when the provider returns no native tool_calls.

    The system prompt asks the model to emit ``tool: <name> <args...>`` on its own
    line when it wants to call a tool. We split args on whitespace (taiyi tool
    args are string tokens). Only the first such line is honored — the ReAct loop
    calls one tool per turn.
    """
    m = _TOOL_LINE.search(text)
    if not m:
        return []
    tool = m.group(1)
    rest = (m.group(2) or "").strip()
    args = rest.split() if rest else []
    return [ToolCall(tool=tool, args=args)]


class OpenAICompatProvider(LLMProvider):
    """Calls any OpenAI-compatible /chat/completions endpoint via httpx."""

    def __init__(
        self,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        *,
        name: str = "openai_compat",
        timeout: float | None = None,
        connect_timeout: float | None = None,
        first_token_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        hard_timeout: float | None = None,
        transport: Any = None,  # injected for tests (httpx MockTransport)
    ):
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_LIVE_MODEL
        self._api_key = api_key or None
        # ``timeout`` remains a compatibility shorthand for older callers. New
        # deployments configure each phase independently.
        def deadline(value: float | None, default: float) -> float:
            resolved = float(value if value is not None else timeout if timeout is not None else default)
            if resolved <= 0:
                raise ValueError("LLM phase deadlines must be positive")
            return resolved

        self._connect_timeout = deadline(connect_timeout, 10.0)
        self._first_token_timeout = deadline(first_token_timeout, 60.0)
        self._stream_idle_timeout = deadline(stream_idle_timeout, 30.0)
        self._hard_timeout = deadline(hard_timeout, 180.0)
        self._transport = transport  # None in production → real network

    def complete(
        self, messages: list[LLMMessage], *, tools: list[str] | None = None
    ) -> LLMResponse:
        coroutine = self._complete_async(messages, tools=tools)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        # The public provider seam is synchronous, but library callers may invoke
        # it from an event-loop thread. Run the request in an isolated loop rather
        # than crashing with "asyncio.run() cannot be called".
        result: list[LLMResponse] = []
        failure: list[BaseException] = []

        def run() -> None:
            try:
                result.append(asyncio.run(coroutine))
            except BaseException as exc:  # carried back to the calling thread
                failure.append(exc)

        worker = threading.Thread(target=run, name="taiyi-llm-request", daemon=True)
        worker.start()
        worker.join(self._hard_timeout + 1.0)
        if worker.is_alive():  # defensive: the coroutine itself also enforces this
            raise self._timeout_error(LLMErrorKind.LLM_HARD_TIMEOUT, "hard")
        if failure:
            raise failure[0]
        return result[0]

    async def _complete_async(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[str] | None,
    ) -> LLMResponse:
        import httpx  # local import: offline deployments never need httpx

        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body: dict = {
            "model": self._model,
            "messages": _messages_to_openai(messages),
            "temperature": 0,
            "stream": True,
        }
        if tools:
            body["messages"] = _with_tool_hint(body["messages"], tools)

        started = time.monotonic()
        hard_deadline = started + self._hard_timeout
        timeout = httpx.Timeout(
            connect=self._connect_timeout,
            read=None,  # phase deadlines below own response reads
            write=self._connect_timeout,
            pool=self._connect_timeout,
        )
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout) as client:
                stream = client.stream("POST", url, json=body, headers=headers)
                response = None
                try:
                    response = await self._wait(
                        stream.__aenter__(),
                        phase_timeout=self._first_token_timeout,
                        hard_deadline=hard_deadline,
                        phase_kind=LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT,
                        phase="first_token",
                    )
                    if response.status_code >= 400:
                        content = await self._wait(
                            response.aread(),
                            phase_timeout=self._first_token_timeout,
                            hard_deadline=hard_deadline,
                            phase_kind=LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT,
                            phase="error_body",
                        )
                        raise self._http_error(response.status_code, content, response.headers)

                    chunks: list[bytes] = []
                    iterator = response.aiter_bytes()
                    saw_body = False
                    first_deadline = started + self._first_token_timeout
                    while True:
                        phase = "stream_idle" if saw_body else "first_token"
                        phase_kind = (
                            LLMErrorKind.LLM_STREAM_IDLE_TIMEOUT
                            if saw_body
                            else LLMErrorKind.LLM_FIRST_TOKEN_TIMEOUT
                        )
                        phase_timeout = (
                            self._stream_idle_timeout
                            if saw_body
                            else max(0.0, first_deadline - time.monotonic())
                        )
                        try:
                            chunk = await self._wait(
                                iterator.__anext__(),
                                phase_timeout=phase_timeout,
                                hard_deadline=hard_deadline,
                                phase_kind=phase_kind,
                                phase=phase,
                            )
                        except StopAsyncIteration:
                            break
                        if chunk:
                            chunks.append(chunk)
                            saw_body = True
                    if not saw_body:
                        raise LLMRequestError(
                            LLMErrorKind.LLM_PROTOCOL_ERROR,
                            "model endpoint returned an empty response body",
                            retryable=False,
                            phase="decode",
                            provider=self.name,
                        )
                    return self._decode_body(b"".join(chunks), response.headers)
                finally:
                    if response is not None:
                        await stream.__aexit__(None, None, None)
        except LLMRequestError:
            raise
        except httpx.ConnectTimeout as exc:
            raise self._timeout_error(LLMErrorKind.LLM_CONNECT_TIMEOUT, "connect") from exc
        except httpx.TimeoutException as exc:
            raise self._timeout_error(LLMErrorKind.LLM_TRANSPORT_ERROR, "transport") from exc
        except httpx.TransportError as exc:
            raise LLMRequestError(
                LLMErrorKind.LLM_TRANSPORT_ERROR,
                f"model transport failed: {type(exc).__name__}",
                retryable=True,
                phase="transport",
                provider=self.name,
            ) from exc

    async def _wait(
        self,
        awaitable,
        *,
        phase_timeout: float,
        hard_deadline: float,
        phase_kind: LLMErrorKind,
        phase: str,
    ):
        hard_remaining = hard_deadline - time.monotonic()
        if hard_remaining <= 0:
            raise self._timeout_error(LLMErrorKind.LLM_HARD_TIMEOUT, "hard")
        allowed = min(max(0.0, phase_timeout), hard_remaining)
        try:
            return await asyncio.wait_for(awaitable, timeout=allowed)
        except asyncio.TimeoutError as exc:
            kind = (
                LLMErrorKind.LLM_HARD_TIMEOUT
                if hard_remaining <= max(0.0, phase_timeout)
                else phase_kind
            )
            raise self._timeout_error(kind, "hard" if kind is LLMErrorKind.LLM_HARD_TIMEOUT else phase) from exc

    def _timeout_error(self, kind: LLMErrorKind, phase: str) -> LLMRequestError:
        return LLMRequestError(
            kind,
            f"model request exceeded its {phase} deadline",
            retryable=True,
            phase=phase,
            provider=self.name,
        )

    def _http_error(self, status: int, content: bytes, headers) -> LLMRequestError:
        detail = content.decode("utf-8", errors="replace")[:300]
        text = detail.casefold()
        if status in {401, 403}:
            kind, retryable = LLMErrorKind.LLM_AUTH_ERROR, False
        elif status == 429:
            kind, retryable = LLMErrorKind.RATE_LIMIT, True
        elif status >= 500 or status in {408, 425}:
            kind, retryable = LLMErrorKind.LLM_SERVER_ERROR, True
        elif "context" in text and any(word in text for word in ("length", "window", "overflow")):
            kind, retryable = LLMErrorKind.CONTEXT_OVERFLOW, False
        else:
            kind, retryable = LLMErrorKind.LLM_PROTOCOL_ERROR, False
        return LLMRequestError(
            kind,
            f"model endpoint returned HTTP {status}: {detail}",
            retryable=retryable,
            phase="response_status",
            provider=self.name,
            status_code=status,
            retry_after=_parse_retry_after(headers.get("retry-after")),
        )

    def _decode_body(self, raw: bytes, headers) -> LLMResponse:
        text = raw.decode("utf-8", errors="replace")
        content_type = headers.get("content-type", "").casefold()
        try:
            if "text/event-stream" in content_type or any(
                line.lstrip().startswith("data:") for line in text.splitlines()
            ):
                return self._decode_sse(text)
            return self._to_response(json.loads(text), self._model)
        except LLMRequestError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMRequestError(
                LLMErrorKind.LLM_PROTOCOL_ERROR,
                f"model response could not be decoded: {type(exc).__name__}",
                retryable=False,
                phase="decode",
                provider=self.name,
            ) from exc

    def _decode_sse(self, body: str) -> LLMResponse:
        content: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}
        model = self._model
        saw_event = False
        for line in body.splitlines():
            if not line.lstrip().startswith("data:"):
                continue
            payload = line.lstrip()[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            data = json.loads(payload)
            if data.get("error"):
                raise LLMRequestError(
                    LLMErrorKind.LLM_PROTOCOL_ERROR,
                    "model stream returned an error event",
                    retryable=False,
                    phase="stream_decode",
                    provider=self.name,
                )
            saw_event = True
            model = data.get("model") or model
            choice = (data.get("choices") or [{}])[0]
            if choice.get("message") is not None:
                return self._to_response(data, model)
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(str(delta["content"]))
            for item in delta.get("tool_calls") or []:
                index = int(item.get("index", 0))
                current = tool_parts.setdefault(index, {"name": "", "arguments": ""})
                fn = item.get("function") or {}
                current["name"] += str(fn.get("name") or "")
                current["arguments"] += str(fn.get("arguments") or "")
        if not saw_event:
            raise LLMRequestError(
                LLMErrorKind.LLM_PROTOCOL_ERROR,
                "model stream contained no data events",
                retryable=False,
                phase="stream_decode",
                provider=self.name,
            )
        text = "".join(content)
        calls = [
            ToolCall(tool=parts["name"], args=_coerce_args(parts["arguments"]))
            for _, parts in sorted(tool_parts.items())
            if parts["name"]
        ]
        if not calls and text:
            calls = _parse_tool_calls_from_text(text)
        return LLMResponse(text=text, tool_calls=calls, model=model)

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key_set(self) -> bool:
        return bool(self._api_key)

    def with_model(self, model: str, *, route: str) -> "OpenAICompatProvider":
        """Clone endpoint/auth settings while selecting a route-specific model."""

        return OpenAICompatProvider(
            base_url=self._base_url,
            model=model,
            api_key=self._api_key,
            name=f"{self.name}:{route}",
            connect_timeout=self._connect_timeout,
            first_token_timeout=self._first_token_timeout,
            stream_idle_timeout=self._stream_idle_timeout,
            hard_timeout=self._hard_timeout,
            transport=self._transport,
        )

    @staticmethod
    def _to_response(data: dict, model: str) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        text = msg.get("content") or ""
        tool_calls: list[ToolCall] = []

        # Prefer native tool_calls when the provider returns them.
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            name = fn.get("name")
            if not name:
                continue
            raw_args = fn.get("arguments", "")
            args = _coerce_args(raw_args)
            tool_calls.append(ToolCall(tool=name, args=args))

        # Fall back to parsing a `tool:` line from the text.
        if not tool_calls and text:
            tool_calls = _parse_tool_calls_from_text(text)

        return LLMResponse(text=text, tool_calls=tool_calls, model=model)


def _coerce_args(raw: Any) -> list[str]:
    """Normalise OpenAI function arguments (a JSON string) to taiyi's list[str]."""
    if isinstance(raw, list):
        return [str(a) for a in raw]
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(a) for a in parsed]
            if isinstance(parsed, dict):
                # A single-object arg: flatten values to positional tokens.
                return [str(v) for v in parsed.values()]
            return [str(parsed)]
        except json.JSONDecodeError:
            return raw.split()
    return []


def _with_tool_hint(messages: list[dict], tools: list[str]) -> list[dict]:
    """Append a system note describing available tools and the call syntax.

    Kept as a separate message so the model's own system/scenario context is
    untouched. This is what makes the adapter portable: a model that supports
    native function-calling ignores the hint and returns tool_calls; one that
    doesn't follows the hint and emits a `tool:` line we parse.
    """
    hint = (
        "When you want to call a tool, reply with a single line in the form "
        "`tool: <name> <arg1> <arg2> ...` and nothing else. Available tools: "
        + ", ".join(tools)
        + ". If you have the answer (no tool needed), reply with the answer text."
    )
    return messages + [{"role": "system", "content": hint}]


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After seconds or an HTTP date into a non-negative delay."""

    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def make_provider(cfg) -> LLMProvider | None:
    """Construct the LLM provider selected by ``cfg``.

    Returns ``None`` for ``offline`` (the default) — the gateway then falls back
    to the keyword planner and offline providers, so the whole agent loop still
    runs with zero tokens and zero network.

    For ``openai_compat`` / ``ollama`` it returns a live ``OpenAICompatProvider``
    wired to ``cfg.base_url`` / ``cfg.model`` / ``cfg.api_key`` (or the env var
    named by ``cfg.api_key_env``).

    A live provider that is misconfigured (missing base_url) or whose transport
    is unavailable (httpx not installed) DEGRADES to offline with a warning,
    rather than crashing the gateway at startup — a bad LLM config should never
    make the whole agent unstartable. The offline fallback still serves the web
    UI and the governance/audit machinery.
    """
    provider = (getattr(cfg, "provider", "offline") or "offline").lower()
    if provider == "offline":
        return None

    if provider in ("openai_compat", "ollama"):
        base_url = getattr(cfg, "base_url", None)
        if not base_url:
            _warn(f"provider={provider!r} but base_url is unset — degrading to offline. "
                  f"Set base_url (e.g. http://localhost:11434/v1) in taiyi.yaml and restart.")
            return None
        # Verify the transport is importable NOW (not lazily at first call), so a
        # missing [live] extra surfaces at startup with a clear message instead of
        # a bare ModuleNotFoundError deep in a task.
        try:
            import httpx  # noqa: F401
        except ImportError:
            _warn(f"provider={provider!r} needs httpx — run `pip install -e '.[live]'` "
                  f"and restart. Degrading to offline for now.")
            return None
        model = getattr(cfg, "model", None)
        # api_key_env (a variable name) takes precedence over api_key (a value),
        # so a deployment can avoid storing the key in the file.
        import os

        key = getattr(cfg, "api_key", None)
        env_name = getattr(cfg, "api_key_env", None)
        if env_name:
            key = os.environ.get(env_name) or key
        return OpenAICompatProvider(
            base_url=base_url, model=model, api_key=key,
            name=f"live:{provider}",
            connect_timeout=float(getattr(cfg, "llm_connect_timeout", 10.0)),
            first_token_timeout=float(getattr(cfg, "llm_first_token_timeout", 60.0)),
            stream_idle_timeout=float(getattr(cfg, "llm_stream_idle_timeout", 30.0)),
            hard_timeout=float(getattr(cfg, "llm_hard_timeout", 180.0)),
        )

    # Unknown provider — degrade with a warning rather than crash.
    _warn(f"unknown provider {provider!r} — use offline | openai_compat | ollama. "
          f"Degrading to offline.")
    return None


def make_provider_router(cfg, default: LLMProvider | None = None):
    """Build the three-strategy router from one OpenAI-compatible endpoint.

    ``model`` remains the universal fallback. Optional quality/balanced/
    efficiency model ids create explicit routes on the same endpoint. Programmatic
    users can construct :class:`ProviderRouter` directly for cross-provider pools.
    """

    from taiyi.llm.router import ProviderRouter

    default = default or make_provider(cfg)
    if default is None:
        return None

    routes = {}
    if isinstance(default, OpenAICompatProvider):
        for field, strategy, route in (
            ("quality_model", "strongest_capable", "quality"),
            ("balanced_model", "adaptive", "balanced"),
            ("efficiency_model", "fastest_capable", "efficiency"),
        ):
            model = getattr(cfg, field, None)
            if model:
                routes[strategy] = default.with_model(str(model), route=route)
    return ProviderRouter(default, **routes)


def _warn(msg: str) -> None:
    import sys

    print(f"[taiyi] WARNING: {msg}", file=sys.stderr)


__all__ = ["OpenAICompatProvider", "make_provider", "make_provider_router"]
