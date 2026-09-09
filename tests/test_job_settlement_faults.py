"""Deterministic faults at the durable worker's terminal settlement boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from taiyi.runtime import _job_worker


def _request(tmp_path: Path, code: str, *, hard_timeout: float = 0.1) -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": _job_worker.JOB_SCHEMA_VERSION,
                "job_id": "j_" + "b" * 32,
                "operation_id": "settlement-fault",
                "argv": [sys.executable, "-c", code],
                "cwd": str(tmp_path),
                "hard_timeout": hard_timeout,
                "heartbeat_interval": 0.05,
            }
        ),
        encoding="utf-8",
    )
    return path


def _record_children(monkeypatch) -> list[subprocess.Popen]:
    children = []
    original_popen = subprocess.Popen

    def record_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        children.append(proc)
        return proc

    # Process-identity discovery is unrelated to these in-process supervisor
    # faults; avoid recording its auxiliary `ps` command on macOS.
    monkeypatch.setattr(_job_worker, "_process_token", lambda pid: f"test:{pid}")
    monkeypatch.setattr(_job_worker.subprocess, "Popen", record_popen)
    return children


def test_unfinished_output_finalizer_is_lost_and_retains_hard_timeout(tmp_path, monkeypatch):
    request_path = _request(tmp_path, "import time; time.sleep(30)")
    release = threading.Event()
    ready = {name: threading.Event() for name in ("stdout.log", "stderr.log")}
    drain_threads = []
    original_finalize = _job_worker._BoundedCapture._finalize_artifact
    original_join = threading.Thread.join

    def blocked_finalize(capture):
        drain_threads.append(threading.current_thread())
        ready[capture.path.name].set()
        if release.wait(timeout=5):
            original_finalize(capture)

    def expired_join(thread, timeout=None):
        if getattr(thread, "_target", None) is _job_worker._drain:
            # Force both join budgets to expire only after real EOF, keeping
            # finalization blocked until after result.json has been inspected.
            capture = thread._args[1]
            assert ready[capture.path.name].wait(timeout=5)
            return None
        return original_join(thread, timeout)

    monkeypatch.setattr(_job_worker._BoundedCapture, "_finalize_artifact", blocked_finalize)
    monkeypatch.setattr(threading.Thread, "join", expired_join)
    try:
        assert _job_worker.supervise(request_path) == 0
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

        assert len(drain_threads) == 2
        assert all(thread.is_alive() for thread in drain_threads)
        assert result["status"] == "LOST"
        assert result["failure_kind"] == "TOOL_LOST"
        assert result["timeout_kind"] == "hard"
        assert result["termination_reason"] == "hard_timeout"
        assert result["returncode"] is not None
        assert result["owned_process_group_settled"] is False
        assert "did not settle" in result["error"]
    finally:
        release.set()
        for thread in drain_threads:
            original_join(thread, timeout=5)
        assert all(not thread.is_alive() for thread in drain_threads)


def test_closed_output_pipes_cannot_settle_a_still_running_main_process(tmp_path, monkeypatch):
    request_path = _request(
        tmp_path,
        "import os,time; os.close(1); os.close(2); time.sleep(30)",
    )
    children = _record_children(monkeypatch)
    original_terminate = _job_worker._terminate_group
    original_finalize = _job_worker._BoundedCapture._finalize_artifact
    finalized = {name: threading.Event() for name in ("stdout.log", "stderr.log")}
    drain_threads = []

    def observe_finalization(capture):
        original_finalize(capture)
        drain_threads.append(threading.current_thread())
        finalized[capture.path.name].set()

    def ineffective_termination(proc, grace_seconds=1.0):
        # Model an OS cleanup that returns without actually stopping the child.
        # The child itself closes both FDs, so there is no output-boundary hint
        # that could rescue a settlement check which only inspects the drains.
        assert all(event.wait(timeout=5) for event in finalized.values())
        for thread in drain_threads:
            thread.join(timeout=5)
        assert all(not thread.is_alive() for thread in drain_threads)
        assert proc.poll() is None
        return False

    monkeypatch.setattr(_job_worker._BoundedCapture, "_finalize_artifact", observe_finalization)
    monkeypatch.setattr(_job_worker, "_terminate_group", ineffective_termination)
    try:
        assert _job_worker.supervise(request_path) == 0
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

        assert len(children) == 1
        assert children[0].poll() is None
        assert result["status"] == "LOST"
        assert result["failure_kind"] == "TOOL_LOST"
        assert result["timeout_kind"] == "hard"
        assert result["termination_reason"] == "hard_timeout"
        assert result["returncode"] is None
        assert result["signal"] is None
        assert result["owned_process_group_settled"] is False
    finally:
        for child in children:
            original_terminate(child)
            child.wait(timeout=5)


def test_post_spawn_heartbeat_write_error_is_lost_with_observed_exit_facts(tmp_path, monkeypatch):
    request_path = _request(
        tmp_path,
        "print('execution reached'); raise SystemExit(7)",
        hard_timeout=5,
    )
    children = _record_children(monkeypatch)
    original_atomic_json = _job_worker._atomic_json
    original_terminate = _job_worker._terminate_group
    injected = False

    def fail_running_heartbeat_once(path, payload):
        nonlocal injected
        if path.name == "heartbeat.json" and payload.get("child_pid") and not injected:
            injected = True
            # The process has genuinely started and exited; the late I/O fault
            # must retain its exit code rather than call this a startup failure.
            children[0].wait(timeout=5)
            raise OSError("injected heartbeat persistence failure")
        return original_atomic_json(path, payload)

    monkeypatch.setattr(_job_worker, "_atomic_json", fail_running_heartbeat_once)
    try:
        assert _job_worker.supervise(request_path) == 0
        result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))

        assert injected
        assert len(children) == 1
        assert result["status"] == "LOST"
        assert result["failure_kind"] == "TOOL_LOST"
        assert result["termination_reason"] == "supervisor_error"
        assert result["timeout_kind"] is None
        assert result["returncode"] == 7
        assert result["signal"] is None
        assert result["owned_process_group_settled"] is True
        assert result["stdout_bytes"] == len(b"execution reached\n")
        assert "injected heartbeat persistence failure" in result["error"]
    finally:
        for child in children:
            original_terminate(child)
            child.wait(timeout=5)
