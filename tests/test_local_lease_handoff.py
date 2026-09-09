"""Deterministic same-process ownership handoffs through the real Gateway."""
from __future__ import annotations

import json
import threading
import time

import pytest

from taiyi.gateway import GatewayApp, build_gateway
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall
from taiyi.runtime import FailureKind, RunPhase, TaskState
from taiyi.tools import SandboxExecutor


def _wait_until(predicate, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("bounded wait expired")


def _status(app, path):
    status, body = app.handle("GET", path, {}, "")
    assert status == 200, body
    return body


def _gateway(tmp_path, runtime_mode):
    executor = SandboxExecutor(
        tmp_path / "workspace",
        job_dir=tmp_path / "jobs",
        heartbeat_interval=0.05,
        hard_timeout=10,
    )
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall(
            "shell:python3",
            ["-c", "import time; print('started', flush=True); time.sleep(5)"],
        )]),
        LLMResponse(text="must not complete a cancelled ambiguous effect"),
    ])
    app = GatewayApp(build_gateway(
        base_dir=tmp_path,
        mode=runtime_mode,
        executor=executor,
        provider=provider,
        validator=False,
        repository_index_enabled=False,
    ))
    return app, provider


def _start_parked_job(app):
    status, accepted = app.handle(
        "POST", "/v1/tasks/async", {}, json.dumps({"prompt": "run a cancellable job"})
    )
    assert status == 202, accepted
    task_id = accepted["task_id"]

    def child_has_started():
        current = _status(app, accepted["status_url"])
        job = current.get("job") or {}
        return (
            current["phase"] == RunPhase.TOOL_RUNNING.value
            and current.get("continuation", {}).get("parked") is True
            and job.get("status") == "RUNNING"
            and job.get("child_pid") is not None
        )

    _wait_until(child_has_started)
    _wait_until(lambda: task_id not in app.gateway._task_threads)
    # Drive the recovery scan explicitly so the race has a fixed schedule.
    # The durable child and Gateway HTTP handlers remain real.
    app.gateway.close()
    return accepted


def _cancel(app, accepted):
    status, body = app.handle("POST", accepted["cancel_url"], {}, "")
    assert status == 202, body
    assert body["job"]["status"] == "CANCELLED"


@pytest.mark.parametrize("runtime_mode", ["agent", "workflow"])
def test_old_recovery_cleanup_cannot_release_effect_resolver_lease(
    tmp_path, monkeypatch, runtime_mode
):
    app, provider = _gateway(tmp_path, runtime_mode)
    runtime = app.gateway.runtime
    store = runtime.run_store
    waiting_input_published = threading.Event()
    allow_old_cleanup = threading.Event()
    resolver_has_lease = threading.Event()
    allow_resolver = threading.Event()
    recovery_threads = []
    resolver_leases = []
    resolver_result = []
    resolver_errors = []
    original_finish = runtime._finish
    original_acquire = store.acquire_task_lease

    def finish_after_publication(ctx, start):
        if ctx.phase is RunPhase.WAITING_INPUT:
            recovery_threads.append(threading.current_thread())
            waiting_input_published.set()
            assert allow_old_cleanup.wait(5), "old recovery was not released"
        return original_finish(ctx, start)

    def acquire_then_pause(task_id, *, blocking=True):
        acquired = original_acquire(task_id, blocking=blocking)
        if acquired and threading.current_thread().name == "test-effect-resolver":
            resolver_leases.append(store.task_lease(task_id))
            resolver_has_lease.set()
            assert allow_resolver.wait(5), "effect resolver was not released"
        return acquired

    monkeypatch.setattr(runtime, "_finish", finish_after_publication)
    monkeypatch.setattr(store, "acquire_task_lease", acquire_then_pause)
    resolver_thread = None
    try:
        accepted = _start_parked_job(app)
        task_id = accepted["task_id"]
        _cancel(app, accepted)
        assert runtime.recover_pending() == 1
        assert waiting_input_published.wait(5)
        waiting = _status(app, accepted["status_url"])
        assert waiting["phase"] == RunPhase.WAITING_INPUT.value
        assert waiting["state"] == TaskState.NEEDS_INPUT.value
        assert waiting["failure_kind"] == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
        assert store.task_lease(task_id) is None

        def resolve_effect():
            try:
                resolver_result.append(app.handle(
                    "POST", f"/v1/tasks/{task_id}/effects/resolve", {},
                    json.dumps({
                        "resolution": "abandon",
                        "note": "cancelled; operator accepts the unknown partial effect",
                    }),
                ))
            except BaseException as exc:
                resolver_errors.append(exc)

        resolver_thread = threading.Thread(
            target=resolve_effect, name="test-effect-resolver", daemon=True
        )
        resolver_thread.start()
        assert resolver_has_lease.wait(5)
        resolver_lease = resolver_leases[0]
        assert resolver_lease is not None
        assert resolver_lease.token > waiting["write_fence"]["fencing_token"]

        allow_old_cleanup.set()
        recovery_threads[0].join(timeout=5)
        assert not recovery_threads[0].is_alive()
        # This is the race: A's finally runs after B acquired the same task in
        # the same RunStore. Both its local claim and shared row must survive.
        local = store.task_lease(task_id)
        shared = store.shared_task_lease(task_id)
        assert local is not None, "old recovery released the resolver's local lease"
        assert shared is not None, "old recovery released the resolver's shared lease"
        assert local.token == shared.token == resolver_lease.token
        assert local.owner_id == shared.owner_id == resolver_lease.owner_id

        allow_resolver.set()
        resolver_thread.join(timeout=5)
        assert not resolver_thread.is_alive()
        assert resolver_errors == []
        assert resolver_result[0][0] == 200, resolver_result
        settled = _status(app, accepted["status_url"])
        assert settled["phase"] == RunPhase.SETTLED.value
        assert settled["state"] == TaskState.FAILED.value
        assert settled["failure_kind"] == FailureKind.EFFECT_OUTCOME_UNKNOWN.value
        assert settled["settled"] is True
        events = store.read_events(task_id)
        names = [event["event"] for event in events]
        assert names.count("effect_resolution_received") == 1
        terminal = [event for event in events if event["phase"] == RunPhase.SETTLED.value]
        assert len(terminal) == 1
        assert terminal[0]["event"] == "run_failed"
        assert terminal[0]["state"] == TaskState.FAILED.value
        assert provider._i == 1
        assert task_id not in app.gateway._task_errors
        assert store.task_lease(task_id) is None
    finally:
        allow_old_cleanup.set()
        allow_resolver.set()
        for thread in recovery_threads:
            thread.join(timeout=5)
        if resolver_thread is not None:
            resolver_thread.join(timeout=5)
        app.gateway.close()
        store.close()


@pytest.mark.parametrize("runtime_mode", ["agent", "workflow"])
def test_recovery_scan_rechecks_checkpoint_after_acquiring_lease(
    tmp_path, monkeypatch, runtime_mode
):
    app, provider = _gateway(tmp_path, runtime_mode)
    runtime = app.gateway.runtime
    store = runtime.run_store
    scan_captured = threading.Event()
    release_scan = threading.Event()
    scan_results = []
    scan_errors = []
    original_iter = store.iter_checkpoints

    def pause_stale_scan():
        checkpoints = original_iter()
        if threading.current_thread().name == "test-stale-recovery-scan":
            assert len(checkpoints) == 1
            assert checkpoints[0]["context"]["phase"] == RunPhase.TOOL_RUNNING.value
            scan_captured.set()
            assert release_scan.wait(5), "stale checkpoint scan was not released"
        return checkpoints

    def scan():
        try:
            scan_results.append(runtime.recover_pending())
        except BaseException as exc:
            scan_errors.append(exc)

    scanner = None
    try:
        accepted = _start_parked_job(app)
        task_id = accepted["task_id"]
        monkeypatch.setattr(store, "iter_checkpoints", pause_stale_scan)
        scanner = threading.Thread(target=scan, name="test-stale-recovery-scan", daemon=True)
        scanner.start()
        assert scan_captured.wait(5)

        _cancel(app, accepted)
        assert runtime.recover_pending() == 1
        _wait_until(lambda: (
            _status(app, accepted["status_url"])["phase"] == RunPhase.WAITING_INPUT.value
            and task_id not in runtime._recovery_threads
        ))
        checkpoint_before = store.load(task_id)
        events_before = store.read_events(task_id)
        assert store.task_lease(task_id) is None

        # The scan still holds TOOL_RUNNING, but can acquire only after the real
        # recovery has published WAITING_INPUT and released its ownership.
        release_scan.set()
        scanner.join(timeout=5)
        assert not scanner.is_alive()
        assert scan_errors == []
        assert scan_results == [0], "a stale tool checkpoint was scheduled again"
        assert store.load(task_id) == checkpoint_before
        assert store.read_events(task_id) == events_before
        assert provider._i == 1
        assert task_id not in runtime._recovery_threads
        assert store.task_lease(task_id) is None
    finally:
        release_scan.set()
        if scanner is not None:
            scanner.join(timeout=5)
        app.gateway.close()
        for thread in tuple(runtime._recovery_threads.values()):
            thread.join(timeout=5)
        store.close()
