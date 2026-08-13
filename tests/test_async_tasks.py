"""Asynchronous task submission, progress polling, and durable cancellation."""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from taiyi.gateway import EventStream, GatewayApp, build_gateway
from taiyi.gateway.server import make_server
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.policy import resolve_policy
from taiyi.runtime import FailureKind, RunPhase, RunStore, TaskContext, TaskState
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
    original_poll = executor.poll
    first_status_poll = True

    def poll_after_publication(job_id):
        nonlocal first_status_poll
        if first_status_poll:
            first_status_poll = False
            raise FileNotFoundError(f"job record for {job_id} is not published yet")
        return original_poll(job_id)

    executor.poll = poll_after_publication
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


def test_event_long_poll_waits_for_a_new_durable_revision(tmp_path):
    store = RunStore(tmp_path)
    ctx = TaskContext(
        task_id="long-poll",
        prompt="hello",
        scenario="default",
        policy=resolve_policy("balanced"),
    )
    store.record(ctx, RunPhase.READY, "run_created")

    def publish_later():
        time.sleep(0.1)
        store.record(ctx, RunPhase.PLANNING, "phase_changed")

    writer = threading.Thread(target=publish_later)
    writer.start()

    started = time.monotonic()
    events, next_after, has_more = store.wait_for_events(
        ctx.task_id,
        after=1,
        timeout=2,
    )
    writer.join(timeout=2)

    assert [event["event"] for event in events] == ["phase_changed"]
    assert next_after == 2
    assert has_more is False
    assert time.monotonic() - started < 2


def test_event_long_poll_observes_a_different_run_store_process_view(tmp_path):
    writer = RunStore(tmp_path)
    reader = RunStore(tmp_path)
    ctx = TaskContext(
        task_id="cross-process-long-poll",
        prompt="hello",
        scenario="default",
        policy=resolve_policy("balanced"),
    )
    writer.record(ctx, RunPhase.READY, "run_created")

    def publish_later():
        time.sleep(0.1)
        writer.record(ctx, RunPhase.PLANNING, "phase_changed")

    thread = threading.Thread(target=publish_later)
    thread.start()
    started = time.monotonic()
    events, next_after, _ = reader.wait_for_events(
        ctx.task_id,
        after=1,
        timeout=2,
        cross_process_interval=0.05,
    )
    thread.join(timeout=2)

    assert [event["revision"] for event in events] == [2]
    assert next_after == 2
    assert time.monotonic() - started < 1


def test_event_wait_does_not_lose_change_between_read_and_condition_wait(tmp_path):
    store = RunStore(tmp_path)
    ctx = TaskContext(
        task_id="lost-wakeup-race",
        prompt="hello",
        scenario="default",
        policy=resolve_policy("balanced"),
    )
    store.record(ctx, RunPhase.READY, "run_created")
    original_page = store.event_page
    injected = False

    def event_page_with_racing_write(task_id, *, after=0, limit=200):
        nonlocal injected
        page = original_page(task_id, after=after, limit=limit)
        if not injected:
            injected = True
            store.record(ctx, RunPhase.PLANNING, "phase_changed")
        return page

    store.event_page = event_page_with_racing_write
    started = time.monotonic()
    events, next_after, _ = store.wait_for_events(
        ctx.task_id,
        after=1,
        timeout=0.5,
        cross_process_interval=1.0,
    )

    assert [event["event"] for event in events] == ["phase_changed"]
    assert next_after == 2
    assert time.monotonic() - started < 0.2


def test_sse_reconnect_uses_last_event_id_without_replaying_old_events(tmp_path):
    app = GatewayApp(build_gateway(base_dir=tmp_path, mode="workflow", validator=False))
    _, accepted = app.handle(
        "POST",
        "/v1/tasks/async",
        {},
        json.dumps({"prompt": "hello"}),
    )
    settled = _wait_for(
        app,
        accepted["status_url"],
        lambda item: item["phase"] == RunPhase.SETTLED.value,
    )
    assert settled["settled"] is True
    _, page = app.handle("GET", accepted["events_url"], {}, "")
    reconnect_after = page["events"][-2]["revision"]

    status, stream = app.handle(
        "GET",
        accepted["events_url"],
        {"Accept": "text/event-stream", "Last-Event-ID": str(reconnect_after)},
        "",
    )

    assert status == 200
    assert isinstance(stream, EventStream)
    payload = b"".join(stream.body).decode("utf-8")
    assert f"id: {reconnect_after}\n" not in payload
    assert "event: run_settled" in payload
    assert f'id: {page["events"][-1]["revision"]}' in payload

    final_revision = page["events"][-1]["revision"]
    started = time.monotonic()
    status, final_stream = app.handle(
        "GET",
        f'{accepted["events_url"]}?wait=30',
        {"Accept": "text/event-stream", "Last-Event-ID": str(final_revision)},
        "",
    )
    assert status == 200
    assert isinstance(final_stream, EventStream)
    assert b"".join(final_stream.body) == b""
    assert time.monotonic() - started < 0.2

    started = time.monotonic()
    status, final_page = app.handle(
        "GET",
        f'{accepted["events_url"]}?after={final_revision}&wait=30',
        {},
        "",
    )
    assert status == 200
    assert final_page["events"] == []
    assert time.monotonic() - started < 0.2


def test_real_http_sse_stream_has_reconnectable_event_ids(tmp_path):
    app = GatewayApp(build_gateway(base_dir=tmp_path, mode="workflow", validator=False))
    _, accepted = app.handle(
        "POST",
        "/v1/tasks/async",
        {},
        json.dumps({"prompt": "hello"}),
    )
    _wait_for(
        app,
        accepted["status_url"],
        lambda item: item["phase"] == RunPhase.SETTLED.value,
    )
    server = make_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f'http://127.0.0.1:{server.server_address[1]}{accepted["events_url"]}',
            headers={"Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            assert response.headers["Content-Type"].startswith("text/event-stream")
        assert "id: 1\n" in body
        assert "event: run_settled" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
