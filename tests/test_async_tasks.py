"""Asynchronous task submission, progress polling, and durable cancellation."""
from __future__ import annotations

import json
import time

from taiyi.gateway import GatewayApp, build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import FailureKind, RunPhase, TaskState
from taiyi.tools import SandboxExecutor


def _wait_for(app, path, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, last = app.handle("GET", path, {}, "")
        assert status == 200
        if predicate(last):
            return last
        time.sleep(0.02)
    raise AssertionError(f"condition not reached; last response: {last}")


def test_async_task_returns_immediately_exposes_progress_and_cancels(tmp_path):
    workspace = tmp_path / "workspace"
    executor = SandboxExecutor(
        workspace,
        job_dir=tmp_path / "jobs",
        heartbeat_interval=0.05,
        hard_timeout=10,
    )
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall(
            "shell:python3",
            ["-c", "import time; print('started', flush=True); time.sleep(5)"],
        )]),
        LLMResponse(text="should not complete after cancellation"),
    ])
    app = GatewayApp(build_gateway(
        base_dir=tmp_path,
        mode="agent",
        executor=executor,
        provider=provider,
        validator=False,
    ))

    started = time.monotonic()
    status, accepted = app.handle(
        "POST",
        "/v1/tasks",
        {},
        json.dumps({"prompt": "run a long job", "async": True}),
    )
    elapsed = time.monotonic() - started

    assert status == 202
    assert elapsed < 0.5
    task_id = accepted["task_id"]
    running = _wait_for(
        app,
        accepted["status_url"],
        lambda item: item["phase"] == RunPhase.TOOL_RUNNING.value and item.get("job"),
    )
    assert running["settled"] is False
    assert running["job"]["status"] == "RUNNING"
    assert running["job"]["heartbeat_at"] is not None

    event_status, event_payload = app.handle("GET", accepted["events_url"], {}, "")
    assert event_status == 200
    assert any(event["event"] == "job_attached" for event in event_payload["events"])
    cursor_status, no_new_events = app.handle(
        "GET",
        f'{accepted["events_url"]}?after={event_payload["next_after"]}',
        {},
        "",
    )
    assert cursor_status == 200
    assert no_new_events["events"] == []

    cancel_status, cancelled = app.handle("POST", accepted["cancel_url"], {}, "")
    assert cancel_status == 202
    assert cancelled["cancelled"] is True
    assert cancelled["job"]["status"] == "CANCELLED"

    ambiguous = _wait_for(
        app,
        accepted["status_url"],
        lambda item: item["phase"] == RunPhase.WAITING_INPUT.value,
    )
    assert ambiguous["state"] == TaskState.NEEDS_INPUT.value
    assert ambiguous["failure_kind"] == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
    assert ambiguous["settled"] is False

    resolve_status, _ = app.handle(
        "POST",
        f"/v1/tasks/{task_id}/effects/resolve",
        {},
        json.dumps({
            "resolution": "abandon",
            "note": "operator cancelled the job and accepts an unknown partial effect",
        }),
    )
    assert resolve_status == 200
    settled = _wait_for(
        app,
        accepted["status_url"],
        lambda item: item["phase"] == RunPhase.SETTLED.value,
    )
    assert settled["state"] == TaskState.FAILED.value
    assert settled["failure_kind"] == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
    assert settled["settled"] is True


def test_explicit_async_endpoint_and_unknown_task_status(tmp_path):
    app = GatewayApp(build_gateway(base_dir=tmp_path, mode="workflow", validator=False))

    status, accepted = app.handle(
        "POST",
        "/v1/tasks/async",
        {},
        json.dumps({"prompt": "hello"}),
    )
    assert status == 202
    assert accepted["status_url"].endswith(accepted["task_id"])

    missing_status, missing = app.handle("GET", "/v1/tasks/not-found", {}, "")
    assert missing_status == 404
    assert "unknown task" in missing["error"]


def test_async_api_fails_closed_without_persistent_run_store():
    app = GatewayApp(build_gateway(mode="workflow", validator=False))

    status, payload = app.handle(
        "POST",
        "/v1/tasks/async",
        {},
        json.dumps({"prompt": "hello"}),
    )

    assert status == 409
    assert "persistent base_dir" in payload["error"]
