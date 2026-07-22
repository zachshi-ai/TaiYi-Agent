"""Gateway façade, request handling, OpenAI compat, auth/rate-limit, HTTP (M9)."""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from taiyi.gateway import AuthPolicy, GatewayApp, RateLimiter, build_gateway
from taiyi.gateway.server import make_server
from taiyi.runtime import TaskState


@pytest.fixture
def gateway():
    return build_gateway()


@pytest.fixture
def app(gateway):
    return GatewayApp(gateway)


# --- Façade: scenario matching + full stack ----------------------------------

def test_submit_matches_scenario_and_completes(gateway):
    ctx = gateway.submit("commit my changes")
    assert ctx.scenario == "dev.git"
    assert ctx.state is TaskState.SIMULATED


def test_submit_governance_still_applies(gateway):
    ctx = gateway.submit("用 -c user.name=Evil commit", scenario="dev.git")
    assert ctx.state is TaskState.REJECTED


# --- HTTP-agnostic handlers --------------------------------------------------

def test_healthz(app):
    assert app.handle("GET", "/healthz", {}, "") == (200, {"status": "ok"})


def test_tasks_endpoint(app):
    status, data = app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "commit my changes"}))
    assert status == 200
    assert data["state"] == "SIMULATED"
    assert data["scenario"] == "dev.git"
    assert data["task_id"]


def test_tasks_endpoint_rejects_identity_override(app):
    body = json.dumps({"prompt": "用 -c user.name=Evil commit", "scenario": "dev.git"})
    status, data = app.handle("POST", "/v1/tasks", {}, body)
    assert status == 200
    assert data["state"] == "REJECTED"


def test_tasks_missing_prompt(app):
    status, data = app.handle("POST", "/v1/tasks", {}, "{}")
    assert status == 400


def test_openai_compatible_chat(app):
    body = json.dumps({"model": "gpt-x", "messages": [{"role": "user", "content": "commit my changes"}]})
    status, data = app.handle("POST", "/v1/chat/completions", {}, body)
    assert status == 200
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"]
    assert data["taiyi"]["state"] == "SIMULATED"
    assert "simulation only" in data["choices"][0]["message"]["content"]


def test_invalid_json(app):
    status, _ = app.handle("POST", "/v1/tasks", {}, "{not json")
    assert status == 400


# --- Auth + rate limiting ----------------------------------------------------

def test_auth_required_when_tokens_configured(gateway):
    app = GatewayApp(gateway, auth=AuthPolicy(("secret",)))
    no_token = app.handle("POST", "/v1/tasks", {}, '{"prompt":"commit my changes"}')
    assert no_token[0] == 401
    ok = app.handle(
        "POST", "/v1/tasks", {"Authorization": "Bearer secret"}, '{"prompt":"commit my changes"}'
    )
    assert ok[0] == 200


def test_rate_limit(gateway):
    app = GatewayApp(gateway, rate_limiter=RateLimiter(max_per_window=1, window=60))
    first = app.handle("GET", "/v1/tasks", {}, "")  # counts against the limit
    second = app.handle("GET", "/v1/tasks", {}, "")
    assert first[0] != 429
    assert second[0] == 429


# --- Real HTTP round trip ----------------------------------------------------

def test_http_round_trip(app):
    server = make_server(app, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/tasks",
            data=json.dumps({"prompt": "commit my changes"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        assert data["state"] == "SIMULATED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
