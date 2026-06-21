"""Transport-agnostic request handling.

`GatewayApp.handle(method, path, headers, body)` returns `(status, dict)` and is
the same whether the request arrived over stdlib http.server, a test harness, or
(later) FastAPI. Keeping routing/auth here, separate from any HTTP framework, is
why the gateway is testable without binding a socket.
"""
from __future__ import annotations

import json

from taiyi.gateway.auth import AuthPolicy, RateLimiter
from taiyi.gateway.core import Gateway
from taiyi.gateway.openai_compat import last_user_message, to_openai_response
from taiyi.runtime import TaskContext


def task_summary(ctx: TaskContext) -> dict:
    return {
        "task_id": ctx.task_id,
        "state": ctx.state.value,
        "scenario": ctx.scenario,
        "skill": ctx.plan.skill_name if ctx.plan else None,
        "approval_id": ctx.approval_id,
        "final_output": ctx.final_output,
        "steps": [s.to_dict() for s in ctx.step_results],
    }


class GatewayApp:
    def __init__(
        self,
        gateway: Gateway,
        *,
        auth: AuthPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
    ):
        self.gateway = gateway
        self.auth = auth or AuthPolicy()
        self.rate = rate_limiter

    def handle(self, method: str, path: str, headers, body: str) -> tuple[int, dict]:
        if method == "GET" and path == "/healthz":
            return 200, {"status": "ok"}

        if not self.auth.authorize(headers):
            return 401, {"error": "unauthorized"}
        if self.rate is not None and not self.rate.allow(self._identity(headers)):
            return 429, {"error": "rate limited"}

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return 400, {"error": "invalid json"}

        if method == "POST" and path == "/v1/tasks":
            return self._tasks(payload)
        if method == "POST" and path == "/v1/chat/completions":
            return self._chat(payload)
        return 404, {"error": "not found"}

    # --- routes --------------------------------------------------------------
    def _tasks(self, payload: dict) -> tuple[int, dict]:
        prompt = payload.get("prompt")
        if not prompt:
            return 400, {"error": "missing prompt"}
        ctx = self.gateway.submit(
            prompt,
            scenario=payload.get("scenario"),
            user_id=payload.get("user_id", "u1"),
            session_id=payload.get("session_id", "s1"),
        )
        return 200, task_summary(ctx)

    def _chat(self, payload: dict) -> tuple[int, dict]:
        prompt = last_user_message(payload.get("messages", []))
        if not prompt:
            return 400, {"error": "no user message"}
        ctx = self.gateway.submit(prompt, scenario=payload.get("scenario"))
        return 200, to_openai_response(ctx, payload.get("model", "taiyi"))

    @staticmethod
    def _identity(headers) -> str:
        value = headers.get("Authorization") or headers.get("authorization") or ""
        return value[7:] if value.startswith("Bearer ") else "anonymous"
