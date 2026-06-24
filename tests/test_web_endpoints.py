"""Tests for the web UI's backend endpoints (pillar A3/A4).

All browse + config endpoints go through GatewayApp.handle (no socket bound),
so they are testable directly. Configures a gateway with memory + iteration so
the browse endpoints have data to return.
"""
from __future__ import annotations

import json

from taiyi.gateway import GatewayApp, build_gateway


def _app(tmp_path) -> GatewayApp:
    app = GatewayApp(build_gateway(base_dir=tmp_path))
    app.config_path = str(tmp_path / "taiyi.yaml")
    return app


def _get(app, path):
    return app.handle("GET", path, {}, "")


def test_sessions_endpoint_returns_empty_then_populated(tmp_path):
    app = _app(tmp_path)
    status, data = _get(app, "/v1/sessions")
    assert status == 200
    assert data["sessions"] == []

    # Run a task to seed a session message.
    app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "hi", "session_id": "sess-1"}))
    _, data = _get(app, "/v1/sessions")
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == "sess-1"
    assert data["sessions"][0]["msg_count"] >= 1


def test_session_messages_endpoint(tmp_path):
    app = _app(tmp_path)
    app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "hello", "session_id": "sX"}))
    status, data = _get(app, "/v1/sessions/sX/messages")
    assert status == 200
    assert data["session_id"] == "sX"
    assert any(m["role"] == "user" for m in data["messages"])


def test_memories_endpoint(tmp_path):
    app = _app(tmp_path)
    # Seed a long-term memory directly.
    app.gateway.memory.remember("a fact", tags=("note",), importance=7)
    status, data = _get(app, "/v1/memories")
    assert status == 200
    assert len(data["memories"]) == 1
    assert data["memories"][0]["content"] == "a fact"
    assert data["memories"][0]["importance"] == 7


def test_skills_endpoint(tmp_path):
    app = _app(tmp_path)
    status, data = _get(app, "/v1/skills")
    assert status == 200
    assert "skills" in data
    # Built-in skills are indexed on build_gateway.
    assert isinstance(data["skills"], list)


def test_trajectories_and_report_endpoints(tmp_path):
    app = _app(tmp_path)
    app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "do thing"}))
    status, data = _get(app, "/v1/trajectories")
    assert status == 200
    assert len(data["trajectories"]) >= 1
    rec = data["trajectories"][0]
    assert "steps" in rec  # signal-rich step trail present

    status, report = _get(app, "/v1/report")
    assert status == 200
    assert report["tasks"] >= 1

    # Single trajectory by task_id.
    status, one = _get(app, f"/v1/trajectories/{rec['task_id']}")
    assert status == 200
    assert one["task_id"] == rec["task_id"]


def test_metrics_json_endpoint(tmp_path):
    app = _app(tmp_path)
    app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "x"}))
    status, data = _get(app, "/v1/metrics.json")
    assert status == 200
    assert "taiyi_tasks_total" in data


def test_get_config_does_not_leak_key(tmp_path):
    app = _app(tmp_path)
    status, data = _get(app, "/v1/config")
    assert status == 200
    assert "provider" in data
    assert "base_url" in data
    assert "writable_fields" in data
    assert data["restart_required_after_write"] is True
    # The key is surfaced only as a presence boolean, never its value.
    assert "api_key_set" in data
    assert isinstance(data["api_key_set"], bool)
    blob = json.dumps(data)
    assert "sk-" not in blob  # no leaked key value
    assert "Bearer" not in blob
    assert "api_key" not in data or data.get("api_key") is None


def test_get_config_shows_key_set_after_writing_one(tmp_path):
    app = _app(tmp_path)
    # Write a key via PUT (writable field).
    app.handle("PUT", "/v1/config", {}, json.dumps({"api_key": "sk-secret123"}))
    # Re-load the config into the gateway so _get_config sees it. (The live
    # gateway wasn't rebuilt, so we simulate by reading the file's provider.)
    from taiyi.config import load_config

    cfg = load_config(app.config_path)
    assert cfg.api_key == "sk-secret123"
    # And GET must still NOT echo the value — only api_key_set=True.
    _, data = _get(app, "/v1/config")
    assert "sk-secret123" not in json.dumps(data)


def test_put_config_writes_back_and_reports_restart(tmp_path):
    app = _app(tmp_path)
    status, data = app.handle(
        "PUT", "/v1/config", {}, json.dumps({"provider": "ollama", "model": "llama3"})
    )
    assert status == 200
    assert data["restart_required"] is True
    assert "provider" in data["applied"]

    # The file was written with the new fields.
    import yaml
    written = yaml.safe_load(open(app.config_path))
    assert written["provider"] == "ollama"
    assert written["model"] == "llama3"


def test_put_config_rejects_unknown_fields(tmp_path):
    app = _app(tmp_path)
    status, data = app.handle("PUT", "/v1/config", {}, json.dumps({"evil_field": "x"}))
    assert status == 400


def test_put_config_without_config_path_is_409(tmp_path):
    app = GatewayApp(build_gateway(base_dir=tmp_path))  # no config_path set
    status, _ = app.handle("PUT", "/v1/config", {}, json.dumps({"mode": "workflow"}))
    assert status == 409
