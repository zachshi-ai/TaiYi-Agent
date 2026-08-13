"""Standalone supervisor for one durable tool process.

The worker intentionally uses only the Python standard library. It outlives the
gateway process, writes heartbeats and a terminal result atomically, and owns
the child process group so cancellation and deadlines include grandchildren.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

JOB_SCHEMA_VERSION = "taiyi.job/v2"


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
        fields = raw[raw.rfind(")") + 2 :].split()
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


def _terminate_group(proc: subprocess.Popen, grace_seconds: float = 1.0) -> bool:
    """Terminate the owned process group and report whether SIGKILL was needed."""

    if proc.poll() is not None:
        return False
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=grace_seconds)
        return False
    except ProcessLookupError:
        return False
    except subprocess.TimeoutExpired:
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
        return True
    return False


def _terminate_lingering_group(pgid: int) -> bool:
    """Stop descendants that kept the job's stdout/stderr pipes open."""

    if os.name != "posix":
        return False
    escalated = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return escalated
        if sig == signal.SIGTERM:
            time.sleep(0.1)
        else:
            escalated = True
    return escalated


class _BoundedCapture:
    """Drain an unbounded stream into a bounded, independently checkable artifact.

    The complete byte stream is counted and hashed while only a head/tail sample
    is retained on disk. Keeping the drain active after the artifact limit is
    reached prevents both pipe backpressure and false idle-timeout attribution.
    """

    def __init__(self, path: Path, limit: int):
        self.path = path
        self.limit = max(256, int(limit))
        self.tail_limit = max(1, self.limit // 2)
        self.total_bytes = 0
        self.artifact_bytes = 0
        self.last_output_at: float | None = None
        self.truncated = False
        self.error: str | None = None
        self._tail = bytearray()
        self._hasher = hashlib.sha256()
        self._lock = threading.Lock()

    def drain(self, pipe) -> None:
        output = None
        try:
            try:
                output = self.path.open("wb", buffering=0)
            except OSError as exc:
                self._set_error(exc)
            while True:
                chunk = pipe.read(65_536)
                if not chunk:
                    break
                with self._lock:
                    self.total_bytes += len(chunk)
                    self.last_output_at = time.time()
                    self._hasher.update(chunk)
                    self._tail.extend(chunk)
                    if len(self._tail) > self.tail_limit:
                        del self._tail[: -self.tail_limit]
                    remaining = max(0, self.limit - self.artifact_bytes)
                    prefix = chunk[:remaining]
                if prefix and output is not None:
                    try:
                        output.write(prefix)
                        with self._lock:
                            self.artifact_bytes += len(prefix)
                    except OSError as exc:
                        self._set_error(exc)
                        output.close()
                        output = None
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
        except OSError as exc:
            self._set_error(exc)
        finally:
            if output is not None:
                output.close()
            pipe.close()
            self._finalize_artifact()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "bytes": self.total_bytes,
                "artifact_bytes": self.artifact_bytes,
                "digest": "sha256:" + self._hasher.copy().hexdigest(),
                "artifact_truncated": self.truncated,
                "last_output_at": self.last_output_at,
                "error": self.error,
            }

    def _finalize_artifact(self) -> None:
        with self._lock:
            total = self.total_bytes
            digest = "sha256:" + self._hasher.copy().hexdigest()
            tail = bytes(self._tail)
            current_error = self.error
        if current_error is not None or total <= self.limit:
            with self._lock:
                self.truncated = total > self.limit
            return
        omitted_hint = max(0, total - self.limit)
        marker = (
            f"\n[TAIYI OUTPUT TRUNCATED: at least {omitted_hint} bytes omitted; "
            f"full-stream {digest}]\n"
        ).encode("utf-8")
        tail_budget = min(len(tail), self.tail_limit)
        head_budget = max(0, self.limit - len(marker) - tail_budget)
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.bounded.tmp")
        try:
            with self.path.open("rb") as source:
                head = source.read(head_budget)
            with temp.open("wb") as output:
                output.write(head)
                output.write(marker)
                output.write(tail[-tail_budget:])
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
            with self._lock:
                self.artifact_bytes = self.path.stat().st_size
                self.truncated = True
        except OSError as exc:
            self._set_error(exc)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def _set_error(self, exc: OSError) -> None:
        with self._lock:
            if self.error is None:
                self.error = f"{type(exc).__name__}: {exc}"


def _drain(pipe, capture: _BoundedCapture) -> None:
    """Drain a child pipe without giving the governed process artifact paths."""

    capture.drain(pipe)


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
    captures: list[_BoundedCapture] = []
    termination_reason: str | None = None
    termination_escalated = False
    owned_process_group_settled: bool | None = None

    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request.get("schema_version") != JOB_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported request schema: {request.get('schema_version')!r}"
            )
        interval = max(0.05, float(request.get("heartbeat_interval", 1.0)))
        hard_timeout = request.get("hard_timeout")
        idle_timeout = request.get("idle_timeout")
        artifact_limit = max(256, int(request.get("artifact_limit", 8_388_608)))
        worker_pid = os.getpid()
        worker_token = _process_token(worker_pid)
        _atomic_json(
            heartbeat_path,
            {
                "schema_version": JOB_SCHEMA_VERSION,
                "timestamp": time.time(),
                "worker_pid": worker_pid,
                "worker_process_token": worker_token,
                "child_pid": None,
                "child_process_token": None,
                "child_pgid": None,
                "stdout_bytes": 0,
                "stderr_bytes": 0,
                "stdout_artifact_bytes": 0,
                "stderr_artifact_bytes": 0,
                "last_output_at": last_output_at,
            },
        )
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
        captures = [
            _BoundedCapture(stdout_path, artifact_limit),
            _BoundedCapture(stderr_path, artifact_limit),
        ]
        drains = [
            threading.Thread(
                target=_drain, args=(proc.stdout, captures[0]), daemon=True
            ),
            threading.Thread(
                target=_drain, args=(proc.stderr, captures[1]), daemon=True
            ),
        ]
        for drain in drains:
            drain.start()
        child_token = _process_token(proc.pid)
        timeout_kind = None
        cancelled = False
        while True:
            now = time.time()
            stdout_progress = captures[0].snapshot()
            stderr_progress = captures[1].snapshot()
            new_stdout_bytes = int(stdout_progress["bytes"])
            new_stderr_bytes = int(stderr_progress["bytes"])
            if (new_stdout_bytes, new_stderr_bytes) != (stdout_bytes, stderr_bytes):
                observed_output_times = [
                    value
                    for value in (
                        stdout_progress["last_output_at"],
                        stderr_progress["last_output_at"],
                    )
                    if value is not None
                ]
                last_output_at = max(observed_output_times, default=now)
                stdout_bytes, stderr_bytes = new_stdout_bytes, new_stderr_bytes

            _atomic_json(
                heartbeat_path,
                {
                    "schema_version": JOB_SCHEMA_VERSION,
                    "timestamp": now,
                    "worker_pid": worker_pid,
                    "worker_process_token": worker_token,
                    "child_pid": proc.pid,
                    "child_process_token": child_token,
                    "child_pgid": proc.pid if os.name == "posix" else None,
                    "stdout_bytes": stdout_bytes,
                    "stderr_bytes": stderr_bytes,
                    "stdout_artifact_bytes": int(stdout_progress["artifact_bytes"]),
                    "stderr_artifact_bytes": int(stderr_progress["artifact_bytes"]),
                    "last_output_at": last_output_at,
                },
            )

            returncode = proc.poll()
            if returncode is not None:
                break
            if cancel_path.exists():
                cancelled = True
                termination_reason = "cancel"
                termination_escalated = _terminate_group(proc)
                returncode = proc.poll()
                break
            if hard_timeout is not None and now - started_at >= float(hard_timeout):
                timeout_kind = "hard"
                termination_reason = "hard_timeout"
                termination_escalated = _terminate_group(proc)
                returncode = proc.poll()
                break
            if idle_timeout is not None and now - last_output_at >= float(idle_timeout):
                timeout_kind = "idle"
                termination_reason = "idle_timeout"
                termination_escalated = _terminate_group(proc)
                returncode = proc.poll()
                break
            time.sleep(interval)
        for drain in drains:
            drain.join(timeout=0.5)
        lingering_descendants = any(drain.is_alive() for drain in drains)
        if lingering_descendants:
            termination_reason = termination_reason or "lingering_descendants"
            termination_escalated = (
                _terminate_lingering_group(proc.pid) or termination_escalated
            )
            for drain in drains:
                drain.join(timeout=2.0)
        owned_process_group_settled = not any(drain.is_alive() for drain in drains)
        if not owned_process_group_settled:
            raise RuntimeError(
                "job descendants kept output pipes open after termination"
            )

        finished_at = time.time()
        stdout_result = captures[0].snapshot()
        stderr_result = captures[1].snapshot()
        stdout_bytes = int(stdout_result["bytes"])
        stderr_bytes = int(stderr_result["bytes"])
        signal_number = (
            -returncode if returncode is not None and returncode < 0 else None
        )
        error = None
        capture_errors = [
            str(value)
            for value in (stdout_result["error"], stderr_result["error"])
            if value
        ]
        if capture_errors:
            status = "FAILED"
            failure_kind = "TOOL_OUTPUT_CAPTURE_ERROR"
            error = "; ".join(capture_errors)
        elif lingering_descendants:
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
        _atomic_json(
            result_path,
            {
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
                "stdout_artifact_bytes": int(stdout_result["artifact_bytes"]),
                "stderr_artifact_bytes": int(stderr_result["artifact_bytes"]),
                "stdout_digest": stdout_result["digest"],
                "stderr_digest": stderr_result["digest"],
                "stdout_artifact_truncated": bool(stdout_result["artifact_truncated"]),
                "stderr_artifact_truncated": bool(stderr_result["artifact_truncated"]),
                "termination_reason": termination_reason,
                "termination_escalated": termination_escalated,
                "owned_process_group_settled": owned_process_group_settled,
                "error": error,
            },
        )
        return 0
    except BaseException as exc:  # noqa: BLE001 - the parent needs a durable terminal record
        if proc is not None:
            termination_reason = termination_reason or "supervisor_error"
            termination_escalated = _terminate_group(proc) or termination_escalated
        for drain in drains:
            drain.join(timeout=2.0)
        owned_process_group_settled = not any(drain.is_alive() for drain in drains)
        stdout_result = captures[0].snapshot() if captures else {}
        stderr_result = captures[1].snapshot() if captures else {}
        failure_kind = (
            "PERMISSION_DENIED"
            if isinstance(exc, PermissionError)
            else "TOOL_STARTUP_ERROR"
        )
        _atomic_json(
            result_path,
            {
                "schema_version": JOB_SCHEMA_VERSION,
                "status": "FAILED",
                "failure_kind": failure_kind,
                "timeout_kind": None,
                "returncode": None,
                "signal": None,
                "started_at": started_at,
                "finished_at": time.time(),
                "last_output_at": last_output_at,
                "stdout_bytes": int(stdout_result.get("bytes", stdout_bytes)),
                "stderr_bytes": int(stderr_result.get("bytes", stderr_bytes)),
                "stdout_artifact_bytes": int(stdout_result.get("artifact_bytes", 0)),
                "stderr_artifact_bytes": int(stderr_result.get("artifact_bytes", 0)),
                "stdout_digest": stdout_result.get("digest"),
                "stderr_digest": stderr_result.get("digest"),
                "stdout_artifact_truncated": bool(
                    stdout_result.get("artifact_truncated", False)
                ),
                "stderr_artifact_truncated": bool(
                    stderr_result.get("artifact_truncated", False)
                ),
                "termination_reason": termination_reason,
                "termination_escalated": termination_escalated,
                "owned_process_group_settled": owned_process_group_settled,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return 0


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    return supervise(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
