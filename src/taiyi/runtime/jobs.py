"""Persistent, restart-observable subprocess jobs for long-running tools."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows keeps the in-process lock only
    fcntl = None

JOB_SCHEMA_VERSION = "taiyi.job/v2"
JOB_NOTIFICATION_SCHEMA = "taiyi.job-notification/v1"
_SUPPORTED_JOB_SCHEMAS = {"taiyi.job/v1", JOB_SCHEMA_VERSION}


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    LOST = "LOST"

    @property
    def terminal(self) -> bool:
        return self in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMED_OUT,
            JobStatus.CANCELLED,
            JobStatus.LOST,
        }


@dataclass(frozen=True)
class JobHandle:
    job_id: str
    operation_id: str


@dataclass
class JobRecord:
    job_id: str
    operation_id: str
    tool: str
    argv_digest: str
    cwd: str
    environment_digest: str = ""
    schema_version: str = JOB_SCHEMA_VERSION
    status: JobStatus = JobStatus.PENDING
    worker_pid: int | None = None
    worker_process_token: str | None = None
    child_pid: int | None = None
    child_process_token: str | None = None
    child_pgid: int | None = None
    hard_timeout: float | None = None
    idle_timeout: float | None = None
    artifact_limit: int = 8_388_608
    heartbeat_interval: float = 1.0
    created_at: float = 0.0
    started_at: float | None = None
    heartbeat_at: float | None = None
    last_output_at: float | None = None
    finished_at: float | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_artifact_bytes: int = 0
    stderr_artifact_bytes: int = 0
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    stdout_artifact_truncated: bool = False
    stderr_artifact_truncated: bool = False
    returncode: int | None = None
    signal: int | None = None
    termination_reason: str | None = None
    termination_escalated: bool = False
    owned_process_group_settled: bool | None = None
    failure_kind: str | None = None
    timeout_kind: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        version = data.get("schema_version", JOB_SCHEMA_VERSION)
        if version not in _SUPPORTED_JOB_SCHEMAS:
            raise ValueError(f"unsupported job schema: {version!r}")
        known = cls.__dataclass_fields__
        migrated = {key: value for key, value in data.items() if key in known}
        migrated["schema_version"] = JOB_SCHEMA_VERSION
        migrated["status"] = JobStatus(data["status"])
        return cls(**migrated)


class JobStore:
    """Start, observe, cancel, and reattach durable supervisor jobs."""

    def __init__(
        self,
        root: str | Path,
        *,
        notification_path: str | Path | None = None,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.notification_path = (
            Path(notification_path).resolve()
            if notification_path is not None
            else self.root / "notifications.jsonl"
        )
        self.notification_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()

    def start(
        self,
        argv: Iterable[str],
        *,
        cwd: str | Path,
        env: dict[str, str],
        operation_id: str,
        tool: str,
        hard_timeout: float | None,
        idle_timeout: float | None,
        artifact_limit: int = 8_388_608,
        heartbeat_interval: float = 1.0,
    ) -> JobHandle:
        """Start once per operation id; repeated calls attach to the same job."""

        argv_list = [str(value) for value in argv]
        argv_digest = self._argv_digest(argv_list)
        environment_digest = self._environment_digest(env)
        resolved_cwd = str(Path(cwd).resolve())
        with self._lock, self._operation_lock():
            existing = self.find_by_operation(operation_id)
            if existing is not None:
                if (
                    existing.argv_digest != argv_digest
                    or existing.environment_digest != environment_digest
                    or existing.cwd != resolved_cwd
                    or existing.tool != tool
                ):
                    raise RuntimeError(
                        f"operation id {operation_id!r} is already bound to different work"
                    )
                return JobHandle(existing.job_id, existing.operation_id)

            job_id = f"j_{uuid.uuid4().hex}"
            job_dir = self._job_dir(job_id)
            job_dir.mkdir(mode=0o700)
            created_at = time.time()
            record = JobRecord(
                job_id=job_id,
                operation_id=operation_id,
                tool=tool,
                argv_digest=argv_digest,
                cwd=resolved_cwd,
                environment_digest=environment_digest,
                hard_timeout=hard_timeout,
                idle_timeout=idle_timeout,
                artifact_limit=max(256, int(artifact_limit)),
                heartbeat_interval=max(0.05, heartbeat_interval),
                created_at=created_at,
            )
            self._save(record)
            # Claim the operation before spawning. A crash can now leave a
            # visible LOST/PENDING job, but can never launch an unindexed side
            # effect that a retry starts a second time.
            self._write_operation_index(record)
            request = {
                "schema_version": JOB_SCHEMA_VERSION,
                "job_id": record.job_id,
                "operation_id": record.operation_id,
                "argv": argv_list,
                "cwd": record.cwd,
                "hard_timeout": hard_timeout,
                "idle_timeout": idle_timeout,
                "artifact_limit": record.artifact_limit,
                "heartbeat_interval": record.heartbeat_interval,
                "notification_path": str(self.notification_path),
            }
            request_path = job_dir / "request.json"
            self._atomic_json(request_path, request)
            request_path.chmod(0o600)
            worker_path = Path(__file__).with_name("_job_worker.py")
            try:
                worker_log = (job_dir / "worker.log").open("ab", buffering=0)
                try:
                    proc = subprocess.Popen(
                        [sys.executable, str(worker_path), str(request_path)],
                        cwd=record.cwd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=worker_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                finally:
                    worker_log.close()
            except (OSError, ValueError) as exc:
                record.status = JobStatus.FAILED
                record.failure_kind = (
                    "PERMISSION_DENIED" if isinstance(exc, PermissionError) else "TOOL_STARTUP_ERROR"
                )
                record.error = f"{type(exc).__name__}: {exc}"
                record.finished_at = time.time()
                self._save(record)
                self.append_notification(
                    event="job_terminal",
                    job_id=record.job_id,
                    operation_id=record.operation_id,
                    status=record.status.value,
                    failure_kind=record.failure_kind,
                )
                return JobHandle(job_id, operation_id)

            record.status = JobStatus.RUNNING
            record.worker_pid = proc.pid
            record.worker_process_token = self._process_token(proc.pid)
            record.started_at = time.time()
            record.heartbeat_at = record.started_at
            record.last_output_at = record.started_at
            self._save(record)
            return JobHandle(job_id, operation_id)

    def poll(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self.load(job_id)
            if record.status.terminal:
                return record
            job_dir = self._job_dir(job_id)
            result = self._read_json(job_dir / "result.json")
            if result is not None:
                self._require_schema(result, "result")
                record.status = JobStatus(result["status"])
                record.failure_kind = result.get("failure_kind")
                record.timeout_kind = result.get("timeout_kind")
                record.returncode = result.get("returncode")
                record.signal = result.get("signal")
                record.started_at = result.get("started_at", record.started_at)
                record.finished_at = result.get("finished_at", time.time())
                record.last_output_at = result.get("last_output_at", record.last_output_at)
                record.stdout_bytes = int(result.get("stdout_bytes", 0))
                record.stderr_bytes = int(result.get("stderr_bytes", 0))
                record.stdout_artifact_bytes = int(result.get("stdout_artifact_bytes", 0))
                record.stderr_artifact_bytes = int(result.get("stderr_artifact_bytes", 0))
                record.stdout_digest = result.get("stdout_digest")
                record.stderr_digest = result.get("stderr_digest")
                record.stdout_artifact_truncated = bool(
                    result.get("stdout_artifact_truncated", False)
                )
                record.stderr_artifact_truncated = bool(
                    result.get("stderr_artifact_truncated", False)
                )
                record.termination_reason = result.get("termination_reason")
                record.termination_escalated = bool(
                    result.get("termination_escalated", False)
                )
                record.owned_process_group_settled = result.get(
                    "owned_process_group_settled"
                )
                record.error = result.get("error")
                self._save(record)
                return record

            heartbeat = self._read_json(job_dir / "heartbeat.json")
            if heartbeat is not None:
                self._require_schema(heartbeat, "heartbeat")
                record.heartbeat_at = heartbeat.get("timestamp")
                record.last_output_at = heartbeat.get("last_output_at")
                record.worker_pid = heartbeat.get("worker_pid", record.worker_pid)
                record.worker_process_token = heartbeat.get(
                    "worker_process_token", record.worker_process_token
                )
                record.child_pid = heartbeat.get("child_pid")
                record.child_process_token = heartbeat.get("child_process_token")
                record.child_pgid = heartbeat.get("child_pgid")
                record.stdout_bytes = int(heartbeat.get("stdout_bytes", 0))
                record.stderr_bytes = int(heartbeat.get("stderr_bytes", 0))
                record.stdout_artifact_bytes = int(
                    heartbeat.get("stdout_artifact_bytes", 0)
                )
                record.stderr_artifact_bytes = int(
                    heartbeat.get("stderr_artifact_bytes", 0)
                )

            startup_grace = max(5.0, record.heartbeat_interval * 5)
            if (
                record.worker_pid is None
                and heartbeat is None
                and time.time() - record.created_at <= startup_grace
            ):
                self._save(record)
                return record
            worker_alive = self._process_matches(record.worker_pid, record.worker_process_token)
            stale_after = max(10.0, record.heartbeat_interval * 5)
            heartbeat_stale = (
                record.heartbeat_at is not None
                and time.time() - record.heartbeat_at > stale_after
            )
            supervisor_lost = False
            if not worker_alive or heartbeat_stale:
                if heartbeat_stale:
                    escalated, settled = self._terminate_processes(record)
                    reason = "durable job supervisor heartbeat became stale"
                else:
                    # The supervisor owns cancellation in the normal path. If it
                    # vanished first, clean up its independently-sessioned child.
                    escalated, settled = self._terminate_processes(record)
                    reason = "durable job supervisor exited without a terminal result"
                record.termination_reason = "supervisor_lost"
                record.termination_escalated = escalated
                record.owned_process_group_settled = settled
                self._settle_without_worker(record, JobStatus.LOST, "TOOL_LOST", reason)
                supervisor_lost = True
            self._save(record)
            if supervisor_lost:
                self.append_notification(
                    event="job_terminal",
                    job_id=record.job_id,
                    operation_id=record.operation_id,
                    status=record.status.value,
                    failure_kind=record.failure_kind,
                )
            return record

    def wait(self, job_id: str, *, poll_interval: float = 0.05) -> JobRecord:
        while True:
            record = self.poll(job_id)
            if record.status.terminal:
                return record
            time.sleep(max(0.01, poll_interval))

    def cancel(self, job_id: str, *, timeout: float = 5.0) -> JobRecord:
        record = self.poll(job_id)
        if record.status.terminal:
            return record
        self._atomic_json(self._job_dir(job_id) / "cancel.request", {"requested_at": time.time()})
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            record = self.poll(job_id)
            if record.status.terminal:
                return record
            time.sleep(0.05)

        # A healthy supervisor acknowledges cancel itself. This is the bounded
        # fail-safe for a wedged supervisor; process tokens prevent PID-reuse kills.
        escalated, settled = self._terminate_processes(record)
        record.termination_reason = "cancel_failsafe"
        record.termination_escalated = escalated
        record.owned_process_group_settled = settled
        self._settle_without_worker(
            record,
            JobStatus.CANCELLED,
            "TOOL_CANCELLED",
            "supervisor did not acknowledge cancellation before the deadline",
        )
        self._save(record)
        self.append_notification(
            event="job_terminal",
            job_id=record.job_id,
            operation_id=record.operation_id,
            status=record.status.value,
            failure_kind=record.failure_kind,
        )
        return record

    def append_notification(
        self,
        *,
        event: str,
        job_id: str,
        operation_id: str,
        status: str | None = None,
        failure_kind: str | None = None,
        consumer_id: str | None = None,
    ) -> None:
        """Append one fsync'd wake hint; JobRecord remains authoritative."""

        payload = {
            "schema_version": JOB_NOTIFICATION_SCHEMA,
            "notification_id": f"n_{uuid.uuid4().hex}",
            "timestamp": time.time(),
            "event": str(event),
            "job_id": str(job_id),
            "operation_id": str(operation_id),
            "status": status,
            "failure_kind": failure_kind,
            "consumer_id": consumer_id,
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            descriptor = os.open(
                self.notification_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            # The JobRecord/result is authoritative. Wake delivery failure must
            # never rewrite or mask the actual terminal job outcome.
            return

    def read_notifications(
        self,
        after_offset: int = 0,
    ) -> tuple[tuple[dict[str, Any], ...], int]:
        """Read complete append-only wake records after a durable byte cursor."""

        try:
            size = self.notification_path.stat().st_size
        except FileNotFoundError:
            return (), 0
        offset = max(0, min(int(after_offset), size))
        with self.notification_path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read()
        complete_length = raw.rfind(b"\n") + 1
        if complete_length <= 0:
            return (), offset
        records = []
        for line in raw[:complete_length].splitlines():
            try:
                value = json.loads(line)
                if value.get("schema_version") != JOB_NOTIFICATION_SCHEMA:
                    raise ValueError("unsupported durable job notification schema")
            except (json.JSONDecodeError, ValueError) as exc:
                # Wake hints are not authoritative. Advance past corruption and
                # let the Gateway audit it, then use JobRecord lease fallback.
                value = {
                    "schema_version": JOB_NOTIFICATION_SCHEMA,
                    "event": "notification_invalid",
                    "error": f"{type(exc).__name__}: {exc}",
                    "raw_digest": "sha256:" + hashlib.sha256(line).hexdigest(),
                }
            records.append(value)
        return tuple(records), offset + complete_length

    def recover(self) -> list[JobRecord]:
        """Refresh every persisted job and return the authoritative records."""

        records: list[JobRecord] = []
        for path in sorted(self.root.glob("j_*/job.json")):
            records.append(self.poll(path.parent.name))
        return records

    def find_by_operation(self, operation_id: str) -> JobRecord | None:
        index_path = self._operation_index_path(operation_id)
        index = self._read_json(index_path)
        if not index:
            return None
        try:
            record = self.load(str(index["job_id"]))
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise RuntimeError(
                f"operation index for {operation_id!r} is incomplete; refusing a duplicate start"
            ) from exc
        if record.operation_id != operation_id:
            raise RuntimeError(f"operation index mismatch for {operation_id!r}")
        return record

    def load(self, job_id: str) -> JobRecord:
        data = self._read_json(self._job_dir(job_id) / "job.json")
        if data is None:
            raise FileNotFoundError(f"unknown job: {job_id}")
        return JobRecord.from_dict(data)

    def output_paths(self, job_id: str) -> tuple[Path, Path]:
        job_dir = self._job_dir(job_id)
        return job_dir / "stdout.log", job_dir / "stderr.log"

    def output_tail(self, job_id: str, *, max_bytes: int = 16_384) -> tuple[str, bool]:
        stdout_path, stderr_path = self.output_paths(job_id)
        budget = max(256, max_bytes)
        stdout, stdout_cut = self._tail(stdout_path, budget // 2)
        stderr, stderr_cut = self._tail(stderr_path, budget - len(stdout.encode("utf-8")))
        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append("[stderr]\n" + stderr)
        output = "\n".join(parts).strip()
        raw = output.encode("utf-8")
        combined_cut = len(raw) > budget
        if combined_cut:
            marker = b"[truncated]\n"
            raw = marker + raw[-(budget - len(marker)):]
            output = raw.decode("utf-8", errors="replace")
        return output, stdout_cut or stderr_cut or combined_cut

    def _save(self, record: JobRecord) -> None:
        self._atomic_json(self._job_dir(record.job_id) / "job.json", record.to_dict())

    def _write_operation_index(self, record: JobRecord) -> None:
        path = self._operation_index_path(record.operation_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._atomic_json(path, {
            "schema_version": JOB_SCHEMA_VERSION,
            "operation_id": record.operation_id,
            "job_id": record.job_id,
        })

    def _operation_index_path(self, operation_id: str) -> Path:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self.root / "operations" / f"{digest}.json"

    def _job_dir(self, job_id: str) -> Path:
        if (
            len(job_id) != 34
            or not job_id.startswith("j_")
            or any(char not in "0123456789abcdef" for char in job_id[2:])
        ):
            raise ValueError(f"invalid job id: {job_id!r}")
        return self.root / job_id

    @contextmanager
    def _operation_lock(self):
        """Serialize operation claims across gateway processes on POSIX."""

        lock_path = self.root / ".operations.lock"
        with lock_path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _settle_without_worker(
        self,
        record: JobRecord,
        status: JobStatus,
        failure_kind: str,
        error: str,
    ) -> None:
        record.status = status
        record.failure_kind = failure_kind
        record.error = error
        record.finished_at = time.time()

    def _terminate_processes(
        self, record: JobRecord, grace_seconds: float = 0.5
    ) -> tuple[bool, bool]:
        targets = [
            (record.child_pid, record.child_process_token, True),
            (record.worker_pid, record.worker_process_token, True),
        ]
        escalated = False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            signalled = False
            for pid, token, process_group in targets:
                # Never signal an unverified pid: a restarted machine can reuse it.
                if pid is None or token is None or not self._process_matches(pid, token):
                    continue
                try:
                    if process_group and os.name == "posix":
                        os.killpg(pid, sig)
                    else:
                        os.kill(pid, sig)
                    signalled = True
                except ProcessLookupError:
                    pass
            if sig == signal.SIGKILL and signalled:
                escalated = True
            if sig == signal.SIGTERM:
                deadline = time.monotonic() + grace_seconds
                while time.monotonic() < deadline:
                    if not any(
                        pid is not None
                        and token is not None
                        and self._process_matches(pid, token)
                        for pid, token, _ in targets
                    ):
                        return escalated, True
                    time.sleep(0.05)
        settled = not any(
            pid is not None
            and token is not None
            and self._process_matches(pid, token)
            for pid, token, _ in targets
        )
        return escalated, settled

    @staticmethod
    def _argv_digest(argv: list[str]) -> str:
        raw = json.dumps(argv, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _environment_digest(env: dict[str, str]) -> str:
        raw = json.dumps(sorted(env.items()), ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_schema(payload: dict[str, Any], artifact: str) -> None:
        version = payload.get("schema_version")
        if version not in _SUPPORTED_JOB_SCHEMAS:
            raise ValueError(f"unsupported {artifact} schema: {version!r}")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            JobStore._fsync_directory(path.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @staticmethod
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

    @classmethod
    def _process_matches(cls, pid: int | None, token: str | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        current = cls._process_token(pid)
        return token is None or current == token

    @staticmethod
    def _tail(path: Path, max_bytes: int) -> tuple[str, bool]:
        if not path.exists() or max_bytes <= 0:
            return "", False
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        return (("[truncated]\n" + text) if size > max_bytes else text), size > max_bytes
