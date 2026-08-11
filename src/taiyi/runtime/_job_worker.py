"""Standalone supervisor for one durable tool process.

The worker intentionally uses only the Python standard library. It outlives the
gateway process, writes heartbeats and a terminal result atomically, and owns
the child process group so cancellation and deadlines include grandchildren.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

JOB_SCHEMA_VERSION = "taiyi.job/v1"


def _atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _process_token(pid: int) -> str | None:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2:].split()
        return f"linux:{fields[19]}"
    except (OSError, IndexError):
        pass
    try:
        proc = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip()
    return f"ps:{value}" if proc.returncode == 0 and value else None


def _terminate_group(proc: subprocess.Popen, grace_seconds: float = 1.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=grace_seconds)
        return
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass
    if proc.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass


def _terminate_lingering_group(pgid: int) -> None:
    """Stop descendants that kept the job's stdout/stderr pipes open."""

    if os.name != "posix":
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        if sig == signal.SIGTERM:
            time.sleep(0.1)


def _drain(pipe, path: Path) -> None:
    """Drain a child pipe so the governed process never writes artifact paths."""

    try:
        with path.open("ab", buffering=0) as output:
            while True:
                chunk = pipe.read(65_536)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    finally:
        pipe.close()


def supervise(request_path: Path) -> int:
    job_dir = request_path.parent
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    heartbeat_path = job_dir / "heartbeat.json"
    result_path = job_dir / "result.json"
    cancel_path = job_dir / "cancel.request"
    started_at = time.time()
    last_output_at = started_at
    stdout_bytes = 0
    stderr_bytes = 0
    proc: subprocess.Popen | None = None
    drains: list[threading.Thread] = []

    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request.get("schema_version") != JOB_SCHEMA_VERSION:
            raise ValueError(f"unsupported request schema: {request.get('schema_version')!r}")
        interval = max(0.05, float(request.get("heartbeat_interval", 1.0)))
        hard_timeout = request.get("hard_timeout")
        idle_timeout = request.get("idle_timeout")
        worker_pid = os.getpid()
        worker_token = _process_token(worker_pid)
        _atomic_json(heartbeat_path, {
            "schema_version": JOB_SCHEMA_VERSION,
            "timestamp": time.time(),
            "worker_pid": worker_pid,
            "worker_process_token": worker_token,
            "child_pid": None,
            "child_process_token": None,
            "child_pgid": None,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "last_output_at": last_output_at,
        })
        proc = subprocess.Popen(
            [str(value) for value in request["argv"]],
            cwd=str(request["cwd"]),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        assert proc.stdout is not None and proc.stderr is not None
        drains = [
            threading.Thread(target=_drain, args=(proc.stdout, stdout_path), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, stderr_path), daemon=True),
        ]
        for drain in drains:
            drain.start()
        child_token = _process_token(proc.pid)
        timeout_kind = None
        cancelled = False
        while True:
            now = time.time()
            new_stdout_bytes = stdout_path.stat().st_size if stdout_path.exists() else 0
            new_stderr_bytes = stderr_path.stat().st_size if stderr_path.exists() else 0
            if (new_stdout_bytes, new_stderr_bytes) != (stdout_bytes, stderr_bytes):
                last_output_at = now
                stdout_bytes, stderr_bytes = new_stdout_bytes, new_stderr_bytes

            _atomic_json(heartbeat_path, {
                "schema_version": JOB_SCHEMA_VERSION,
                "timestamp": now,
                "worker_pid": worker_pid,
                "worker_process_token": worker_token,
                "child_pid": proc.pid,
                "child_process_token": child_token,
                "child_pgid": proc.pid if os.name == "posix" else None,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "last_output_at": last_output_at,
            })

            returncode = proc.poll()
            if returncode is not None:
                break
            if cancel_path.exists():
                cancelled = True
                _terminate_group(proc)
                returncode = proc.poll()
                break
            if hard_timeout is not None and now - started_at >= float(hard_timeout):
                timeout_kind = "hard"
                _terminate_group(proc)
                returncode = proc.poll()
                break
            if idle_timeout is not None and now - last_output_at >= float(idle_timeout):
                timeout_kind = "idle"
                _terminate_group(proc)
                returncode = proc.poll()
                break
            time.sleep(interval)
        for drain in drains:
            drain.join(timeout=0.5)
        lingering_descendants = any(drain.is_alive() for drain in drains)
        if lingering_descendants:
            _terminate_lingering_group(proc.pid)
            for drain in drains:
                drain.join(timeout=2.0)
        if any(drain.is_alive() for drain in drains):
            raise RuntimeError("job descendants kept output pipes open after termination")

        finished_at = time.time()
        stdout_bytes = stdout_path.stat().st_size if stdout_path.exists() else 0
        stderr_bytes = stderr_path.stat().st_size if stderr_path.exists() else 0
        signal_number = -returncode if returncode is not None and returncode < 0 else None
        error = None
        if lingering_descendants:
            status = "FAILED"
            failure_kind = "TOOL_LOST"
            error = "job descendants remained after the main process exited and were terminated"
        elif cancelled:
            status = "CANCELLED"
            failure_kind = "TOOL_CANCELLED"
        elif timeout_kind == "idle":
            status = "TIMED_OUT"
            failure_kind = "TOOL_IDLE_TIMEOUT"
        elif timeout_kind == "hard":
            status = "TIMED_OUT"
            failure_kind = "TOOL_HARD_TIMEOUT"
        elif returncode == 0:
            status = "SUCCEEDED"
            failure_kind = None
        elif signal_number is not None:
            status = "FAILED"
            failure_kind = "TOOL_SIGNAL"
        else:
            status = "FAILED"
            failure_kind = "TOOL_EXIT_NONZERO"
        _atomic_json(result_path, {
            "schema_version": JOB_SCHEMA_VERSION,
            "status": status,
            "failure_kind": failure_kind,
            "timeout_kind": timeout_kind,
            "returncode": returncode,
            "signal": signal_number,
            "started_at": started_at,
            "finished_at": finished_at,
            "last_output_at": last_output_at,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "error": error,
        })
        return 0
    except BaseException as exc:  # noqa: BLE001 - the parent needs a durable terminal record
        if proc is not None:
            _terminate_group(proc)
        for drain in drains:
            drain.join(timeout=2.0)
        failure_kind = "PERMISSION_DENIED" if isinstance(exc, PermissionError) else "TOOL_STARTUP_ERROR"
        _atomic_json(result_path, {
            "schema_version": JOB_SCHEMA_VERSION,
            "status": "FAILED",
            "failure_kind": failure_kind,
            "timeout_kind": None,
            "returncode": None,
            "signal": None,
            "started_at": started_at,
            "finished_at": time.time(),
            "last_output_at": last_output_at,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "error": f"{type(exc).__name__}: {exc}",
        })
        return 0


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    return supervise(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
