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
        # config_path is set by the CLI when the gateway was built from a config
        # file; PUT /v1/config writes back to it. None means "no config file to
        # write back to" (e.g. built from defaults/flags).
        self.config_path: str | None = None

    def handle(self, method: str, path: str, headers, body):
        if method == "GET" and path == "/healthz":
            return 200, {"status": "ok"}
        if method == "GET" and path == "/metrics":
            if self.gateway.obs is None:
                return 404, {"error": "metrics not enabled"}
            return 200, self.gateway.obs.render_metrics()  # text/plain payload
        if method == "GET" and path == "/v1/metrics.json":
            return self._metrics_json()

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
        # OODA Act gate: list pending rule/skill suggestions and resolve them.
        if method == "GET" and path == "/v1/review/pending":
            return self._list_review()
        if method == "POST" and path.startswith("/v1/review/"):
            return self._resolve_review(path, payload)
        # --- read-only browse endpoints for the web UI -------------------------
        if method == "GET" and path == "/v1/sessions":
            return self._list_sessions()
        if method == "GET" and path.startswith("/v1/sessions/") and path.endswith("/messages"):
            return self._session_messages(path)
        if method == "GET" and path == "/v1/memories":
            return self._list_memories()
        if method == "GET" and path == "/v1/skills":
            return self._list_skills()
        if method == "GET" and path == "/v1/trajectories":
            return self._list_trajectories()
        if method == "GET" and path.startswith("/v1/trajectories/"):
            return self._get_trajectory(path)
        if method == "GET" and path == "/v1/report":
            return self._report()
        if method == "GET" and path == "/v1/config":
            return self._get_config()
        if method == "PUT" and path == "/v1/config":
            return self._put_config(payload)
        if method == "POST" and path == "/v1/config/test":
            return self._test_config(payload)
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
        # Pass session_id through so multi-turn OpenAI-compatible clients can keep
        # context across requests (the runtime reads history by session_id).
        ctx = self.gateway.submit(
            prompt,
            scenario=payload.get("scenario"),
            session_id=payload.get("session_id", "s1"),
        )
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

    def _list_review(self) -> tuple[int, dict]:
        if self.gateway.iteration is None:
            return 404, {"error": "iteration not enabled"}
        return 200, {"pending": [s.summary() for s in self.gateway.iteration.list_pending()]}

    def _resolve_review(self, path: str, payload: dict) -> tuple[int, dict]:
        if self.gateway.iteration is None:
            return 404, {"error": "iteration not enabled"}
        # path is /v1/review/{id}/approve | /v1/review/{id}/reject
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "v1" or parts[1] != "review":
            return 404, {"error": "not found"}
        try:
            suggestion_id = int(parts[2])
        except ValueError:
            return 400, {"error": "suggestion id must be an integer"}
        action = parts[3]
        if action not in ("approve", "reject"):
            return 400, {"error": "action must be approve or reject"}
        try:
            path_written = self.gateway.resolve_review(suggestion_id, approve=(action == "approve"))
        except KeyError:
            return 404, {"error": f"unknown suggestion: {suggestion_id}"}
        except RuntimeError as e:
            return 409, {"error": str(e)}
        return 200, {"suggestion_id": suggestion_id, "action": action,
                     "written_to": str(path_written) if path_written else None}

    # --- browse / config endpoints for the web UI ---------------------------
    def _list_sessions(self) -> tuple[int, dict]:
        if self.gateway.memory is None:
            return 404, {"error": "memory not enabled"}
        return 200, {"sessions": self.gateway.memory.list_sessions()}

    def _session_messages(self, path: str) -> tuple[int, dict]:
        if self.gateway.memory is None:
            return 404, {"error": "memory not enabled"}
        # path is /v1/sessions/{id}/messages
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "v1" or parts[1] != "sessions" or parts[3] != "messages":
            return 404, {"error": "not found"}
        session_id = parts[2]
        return 200, {"session_id": session_id,
                     "messages": self.gateway.memory.get_messages(session_id)}

    def _list_memories(self) -> tuple[int, dict]:
        if self.gateway.memory is None:
            return 404, {"error": "memory not enabled"}
        return 200, {"memories": self.gateway.memory.list_memories()}

    def _list_skills(self) -> tuple[int, dict]:
        if self.gateway.memory is None:
            return 404, {"error": "memory not enabled"}
        return 200, {"skills": self.gateway.memory.list_skills_full()}

    def _list_trajectories(self) -> tuple[int, dict]:
        if self.gateway.iteration is None:
            return 404, {"error": "iteration not enabled"}
        return 200, {"trajectories": self.gateway.iteration.list_trajectories()}

    def _get_trajectory(self, path: str) -> tuple[int, dict]:
        if self.gateway.iteration is None:
            return 404, {"error": "iteration not enabled"}
        # path is /v1/trajectories/{task_id}
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "v1" or parts[1] != "trajectories":
            return 404, {"error": "not found"}
        task_id = parts[2]
        rec = self.gateway.iteration.get_trajectory(task_id)
        if rec is None:
            return 404, {"error": f"unknown task: {task_id}"}
        return 200, rec

    def _report(self) -> tuple[int, dict]:
        if self.gateway.iteration is None:
            return 404, {"error": "iteration not enabled"}
        return 200, self.gateway.iteration.report()

    def _metrics_json(self) -> tuple[int, dict]:
        if self.gateway.obs is None:
            return 404, {"error": "metrics not enabled"}
        # Render the Prometheus text and parse the simple lines into JSON for
        # the web UI (avoids needing a Prometheus parser client-side).
        return 200, self.gateway.obs.metrics.as_json()

    def _get_config(self) -> tuple[int, dict]:
        """Read-only view of the running config. Never echoes the API key value —
        only whether one is set, so the UI can show a placeholder without leaking."""
        from taiyi.config import WRITABLE_FIELDS

        prov = getattr(self.gateway.runtime, "provider", None)
        provider_name = getattr(prov, "name", None) if prov else "offline"
        has_key = bool(getattr(prov, "_api_key", None))
        return 200, {
            "config_path": self.config_path,
            "mode": "agent" if type(self.gateway.runtime).__name__ == "AgentRuntime" else "workflow",
            "provider": provider_name,
            "model": getattr(prov, "_model", None) if prov else None,
            "base_url": getattr(prov, "_base_url", None) if prov else None,
            "api_key_set": has_key,
            "base_dir": self.gateway.base_dir,
            "writable_fields": sorted(WRITABLE_FIELDS),
            "restart_required_after_write": True,
        }

    def _put_config(self, payload: dict) -> tuple[int, dict]:
        """Write selected fields back to the config file. Does NOT hot-swap the
        runtime — a restart is required to load the new config (by design: the
        governance and skill sets load once, read-only)."""
        if not self.config_path:
            return 409, {"error": "no config file path known (gateway not built from a config file)"}
        from taiyi.config import WRITABLE_FIELDS, save_config
        updates = {k: v for k, v in payload.items() if k in WRITABLE_FIELDS}
        if not updates:
            return 400, {"error": f"no writable fields; allowed: {sorted(WRITABLE_FIELDS)}"}
        # Treat an empty-string api_key as "clear it" → write null.
        if "api_key" in updates and updates["api_key"] == "":
            updates["api_key"] = None
        save_config(self.config_path, updates)
        return 200, {"restart_required": True, "applied": sorted(updates),
                     "config_path": self.config_path}

    def _test_config(self, payload: dict) -> tuple[int, dict]:
        """Probe a candidate LLM config without restarting.

        Builds a throwaway OpenAICompatProvider from the supplied fields and sends
        a one-token 'ping'. Returns ok/err so the UI can confirm a config works
        before committing it. Never persists anything.
        """
        from taiyi.llm import OpenAICompatProvider
        base_url = payload.get("base_url")
        provider = (payload.get("provider") or "").lower()
        if provider == "offline":
            return 200, {"ok": True, "note": "offline provider needs no connection"}
        if not base_url:
            return 400, {"error": "base_url required to test a live provider"}
        try:
            prov = OpenAICompatProvider(
                base_url=base_url,
                model=payload.get("model"),
                api_key=payload.get("api_key"),
                timeout=10.0,  # fail fast — a UI probe should not hang on a bad endpoint
            )
            from taiyi.llm.base import LLMMessage

            resp = prov.complete([LLMMessage("user", "ping")])
            return 200, {"ok": True, "model": resp.model, "reply": (resp.text or "")[:120]}
        except Exception as e:  # noqa: BLE001 — surface the connection error to the UI
            return 200, {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _identity(headers) -> str:
        value = headers.get("Authorization") or headers.get("authorization") or ""
        return value[7:] if value.startswith("Bearer ") else "anonymous"
