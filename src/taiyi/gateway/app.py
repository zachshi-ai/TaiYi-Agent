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
        "goal": ctx.goal.to_dict() if ctx.goal else None,
        "value_contribution": ctx.value_contribution.to_dict() if ctx.value_contribution else None,
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

    def handle(self, method: str, path: str, headers, body: str):
        if method == "GET" and path == "/healthz":
            return 200, {"status": "ok"}
        if method == "GET" and path == "/metrics":
            if self.gateway.obs is None:
                return 404, {"error": "metrics not enabled"}
            return 200, self.gateway.obs.render_metrics()  # text/plain payload

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
        if method == "POST" and path == "/v1/review":
            return self._review(payload)
        if method == "GET" and path == "/v1/approvals":
            return self._list_approvals()
        if method == "POST" and path == "/v1/approvals/resolve":
            return self._resolve_approval(payload)
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

    def _review(self, payload: dict) -> tuple[int, dict]:
        if self.gateway.committee is None:
            return 404, {"error": "multi-agent review not enabled"}
        subject = payload.get("subject")
        if not subject:
            return 400, {"error": "missing subject"}
        result = self.gateway.committee.review(subject, payload.get("context") or {})
        return 200, result.to_dict()

    def _list_approvals(self) -> tuple[int, dict]:
        if self.gateway.approvals is None:
            return 404, {"error": "approvals not enabled"}
        return 200, {"pending": [p.summary() for p in self.gateway.approvals.list()]}

    def _resolve_approval(self, payload: dict) -> tuple[int, dict]:
        if self.gateway.approvals is None:
            return 404, {"error": "approvals not enabled"}
        approval_id = payload.get("approval_id")
        decision = payload.get("decision")
        if not approval_id or decision not in ("approve", "reject"):
            return 400, {"error": "need approval_id and decision=approve|reject"}
        try:
            ctx = self.gateway.runtime.resume(approval_id, approve=(decision == "approve"))
        except KeyError:
            return 404, {"error": f"unknown approval: {approval_id}"}
        return 200, task_summary(ctx)

    @staticmethod
    def _identity(headers) -> str:
        value = headers.get("Authorization") or headers.get("authorization") or ""
        return value[7:] if value.startswith("Bearer ") else "anonymous"
