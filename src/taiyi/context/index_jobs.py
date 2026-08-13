"""Durable, idempotent repository-index jobs built on TaiYi's supervisor."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from taiyi.context.repository import RepositoryIndexProgress, RepositoryIndexResult
from taiyi.runtime.jobs import JobHandle, JobRecord, JobStatus, JobStore

try:
    import fcntl
except ImportError:  # pragma: no cover - process-local fallback on Windows
    fcntl = None

INDEX_JOB_REQUEST_SCHEMA = "taiyi.repository-index-request/v1"
INDEX_JOB_RECEIPT_SCHEMA = "taiyi.repository-index-receipt/v1"


class RepositoryIndexJobError(RuntimeError):
    def __init__(self, message: str, *, failure_kind: str):
        self.failure_kind = failure_kind
        super().__init__(message)


class RepositoryIndexParked(RuntimeError):
    """Internal control signal: durable work continues without a task thread."""

    def __init__(self, handle: JobHandle):
        self.handle = handle
        super().__init__(f"repository index job {handle.job_id} is parked")


class RepositoryIndexJobManager:
    """Start once per index generation and reattach after gateway restart."""

    def __init__(
        self,
        root: str | Path,
        *,
        heartbeat_interval: float = 0.25,
        poll_interval: float = 0.05,
        worker_progress_delay: float = 0.0,
        consumer_lease_seconds: float = 30.0,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs = JobStore(self.root / "jobs")
        self.heartbeat_interval = max(0.05, float(heartbeat_interval))
        self.poll_interval = max(0.01, float(poll_interval))
        # Deterministic fault/parking tests use this; production leaves it zero.
        self.worker_progress_delay = max(0.0, float(worker_progress_delay))
        self.consumer_lease_seconds = max(1.0, float(consumer_lease_seconds))
        self._generation_lock = threading.RLock()
        self._request_lock = threading.RLock()

    def current_generation(self, repository_id: str) -> int:
        with self._locked_generations():
            value = self._read_json(self._generation_path(repository_id)) or {}
            return max(0, int(value.get("generation", 0) or 0))

    def claim_generation(self, repository_id: str) -> int:
        """Share live work, but give a fresh task a generation after settlement.

        A recovering task persists its generation in the task checkpoint and
        therefore does not call this method. A new task calls it exactly once:
        concurrent callers coalesce onto a non-terminal generation, while the
        first caller after settlement advances the repository generation.
        """

        with self._locked_generations():
            path = self._generation_path(repository_id)
            value = self._read_json(path) or {}
            generation = max(0, int(value.get("generation", 0) or 0))
            operation_id = self.operation_id(repository_id, generation)
            existing = self.jobs.find_by_operation(operation_id)
            if existing is not None:
                existing = self.jobs.poll(existing.job_id)
                if existing.status.terminal:
                    generation += 1
                    self._write_generation(path, repository_id, generation)
            return generation

    def advance_generation(self, repository_id: str) -> int:
        with self._locked_generations():
            path = self._generation_path(repository_id)
            value = self._read_json(path) or {}
            generation = max(0, int(value.get("generation", 0) or 0)) + 1
            self._write_generation(path, repository_id, generation)
            return generation

    @staticmethod
    def operation_id(repository_id: str, generation: int) -> str:
        return f"repository:{repository_id}:generation:{max(0, int(generation))}"

    def run(
        self,
        *,
        repository_root: str | Path,
        db_path: str | Path,
        max_files: int,
        max_file_bytes: int,
        chunk_lines: int,
        operation_id: str,
        consumer_id: str,
        park: bool = False,
        attached: Callable[[JobHandle], None] | None = None,
        progress: Callable[[RepositoryIndexProgress], None] | None = None,
    ) -> RepositoryIndexResult:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        request_path = self.root / "requests" / f"{digest}.json"
        progress_path = self.root / "progress" / f"{digest}.json"
        receipt_path = self.root / "receipts" / f"{digest}.json"
        request = {
            "schema_version": INDEX_JOB_REQUEST_SCHEMA,
            "operation_id": operation_id,
            "repository_root": str(Path(repository_root).resolve()),
            "db_path": str(Path(db_path).resolve()),
            "max_files": int(max_files),
            "max_file_bytes": int(max_file_bytes),
            "chunk_lines": int(chunk_lines),
            "progress_path": str(progress_path),
            "receipt_path": str(receipt_path),
            "worker_progress_delay": self.worker_progress_delay,
        }
        self._write_request_once(request_path, request)
        worker = Path(__file__).with_name("_index_worker.py")
        handle = self.jobs.start(
            [sys.executable, str(worker), str(request_path)],
            cwd=repository_root,
            env=self._worker_environment(),
            operation_id=operation_id,
            tool="internal:repository-index",
            hard_timeout=None,
            idle_timeout=None,
            artifact_limit=262_144,
            heartbeat_interval=self.heartbeat_interval,
        )
        attachment_path = self._attachment_path(handle.job_id, consumer_id)
        cancellation_path = self._cancellation_path(handle.job_id, consumer_id)
        self._write_attachment(handle.job_id, consumer_id, attached_at=time.time())
        parked = False
        try:
            if attached is not None:
                attached(handle)

            last_progress = None
            next_lease_renewal = 0.0
            while True:
                if cancellation_path.exists():
                    raise RepositoryIndexJobError(
                        "repository index subscription was cancelled",
                        failure_kind="REPOSITORY_INDEX_CANCELLED",
                    )
                now = time.time()
                if now >= next_lease_renewal:
                    self.renew_consumer(handle.job_id, consumer_id)
                    next_lease_renewal = now + self.consumer_lease_seconds / 3
                current = self._read_json(progress_path)
                if current is not None and current != last_progress:
                    self._require_progress(current, operation_id)
                    last_progress = current
                    if progress is not None:
                        progress(RepositoryIndexProgress(**current["progress"]))
                record = self.jobs.poll(handle.job_id)
                if record.status.terminal:
                    return self._settle(record, receipt_path, operation_id)
                if park:
                    parked = True
                    raise RepositoryIndexParked(handle)
                time.sleep(self.poll_interval)
        finally:
            if not parked:
                attachment_path.unlink(missing_ok=True)

    def poll(self, job_id: str) -> JobRecord:
        return self.jobs.poll(job_id)

    def cancel(self, job_id: str) -> JobRecord:
        return self.jobs.cancel(job_id)

    def cancel_consumer(self, job_id: str, consumer_id: str) -> tuple[JobRecord, bool]:
        """Cancel one task subscription without breaking coalesced readers."""

        cancellation_path = self._cancellation_path(job_id, consumer_id)
        self._atomic_json(cancellation_path, {
            "job_id": job_id,
            "consumer_id": consumer_id,
            "cancelled_at": time.time(),
        })
        attachment_dir = self.root / "attachments" / job_id
        other_consumers = False
        for path in attachment_dir.glob("*.json"):
            value = self._read_json(path) or {}
            if self._attachment_expired(value):
                path.unlink(missing_ok=True)
                continue
            if value.get("consumer_id") != consumer_id:
                other_consumers = True
                break
        if other_consumers:
            return self.jobs.poll(job_id), True
        return self.jobs.cancel(job_id), False

    def renew_consumer(self, job_id: str, consumer_id: str) -> None:
        path = self._attachment_path(job_id, consumer_id)
        existing = self._read_json(path) or {}
        now = time.time()
        try:
            remaining = float(existing.get("lease_expires_at", 0.0)) - now
        except (TypeError, ValueError):
            remaining = 0.0
        if remaining > self.consumer_lease_seconds * 2 / 3:
            return
        self._write_attachment(
            job_id,
            consumer_id,
            attached_at=float(existing.get("attached_at", now)),
        )

    def consumer_cancelled(self, job_id: str, consumer_id: str) -> bool:
        return self._cancellation_path(job_id, consumer_id).exists()

    def ready_for_resume(self, job_id: str, consumer_id: str) -> bool:
        if self.consumer_cancelled(job_id, consumer_id):
            return True
        return self.jobs.poll(job_id).status.terminal

    def prune_expired_consumers(self, job_id: str) -> int:
        removed = 0
        for path in (self.root / "attachments" / job_id).glob("*.json"):
            if self._attachment_expired(self._read_json(path) or {}):
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _settle(
        self,
        record: JobRecord,
        receipt_path: Path,
        operation_id: str,
    ) -> RepositoryIndexResult:
        if record.status is JobStatus.SUCCEEDED:
            receipt = self._read_json(receipt_path)
            if (
                receipt is None
                or receipt.get("schema_version") != INDEX_JOB_RECEIPT_SCHEMA
                or receipt.get("operation_id") != operation_id
                or not isinstance(receipt.get("result"), dict)
            ):
                raise RepositoryIndexJobError(
                    "repository index worker succeeded without a valid receipt",
                    failure_kind="REPOSITORY_INDEX_FAILED",
                )
            known = RepositoryIndexResult.__dataclass_fields__
            return RepositoryIndexResult(**{
                key: value for key, value in receipt["result"].items() if key in known
            })
        failure_kind = {
            JobStatus.CANCELLED: "REPOSITORY_INDEX_CANCELLED",
            JobStatus.LOST: "REPOSITORY_INDEX_LOST",
        }.get(record.status, "REPOSITORY_INDEX_FAILED")
        raise RepositoryIndexJobError(
            record.error or f"repository index job settled as {record.status.value}",
            failure_kind=failure_kind,
        )

    def _write_request_once(self, path: Path, request: dict) -> None:
        with self._locked_file(self._request_lock, ".requests.lock"):
            existing = self._read_json(path)
            if existing is not None:
                if existing != request:
                    raise RuntimeError(
                        "repository index operation is already bound to different work"
                    )
                return
            self._atomic_json(path, request)

    def _generation_path(self, repository_id: str) -> Path:
        digest = hashlib.sha256(repository_id.encode("utf-8")).hexdigest()
        return self.root / "generations" / f"{digest}.json"

    def _attachment_path(self, job_id: str, consumer_id: str) -> Path:
        digest = hashlib.sha256(consumer_id.encode("utf-8")).hexdigest()
        return self.root / "attachments" / job_id / f"{digest}.json"

    def _cancellation_path(self, job_id: str, consumer_id: str) -> Path:
        digest = hashlib.sha256(consumer_id.encode("utf-8")).hexdigest()
        return self.root / "cancellations" / job_id / f"{digest}.json"

    def _write_attachment(
        self,
        job_id: str,
        consumer_id: str,
        *,
        attached_at: float,
    ) -> None:
        now = time.time()
        self._atomic_json(self._attachment_path(job_id, consumer_id), {
            "job_id": job_id,
            "consumer_id": consumer_id,
            "attached_at": attached_at,
            "renewed_at": now,
            "lease_expires_at": now + self.consumer_lease_seconds,
        })

    @staticmethod
    def _attachment_expired(value: dict) -> bool:
        try:
            return float(value.get("lease_expires_at", 0.0)) <= time.time()
        except (TypeError, ValueError):
            return True

    def _write_generation(
        self, path: Path, repository_id: str, generation: int
    ) -> None:
        self._atomic_json(path, {
            "repository_id": repository_id,
            "generation": generation,
            "updated_at": time.time(),
        })

    @contextmanager
    def _locked_generations(self):
        with self._locked_file(self._generation_lock, ".generations.lock"):
            yield

    @contextmanager
    def _locked_file(self, local_lock, name):
        with local_lock:
            lock_path = self.root / name
            with lock_path.open("a+b") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _worker_environment() -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", ""),
            "PYTHONUNBUFFERED": "1",
        }

    @staticmethod
    def _require_progress(value: dict, operation_id: str) -> None:
        if (
            value.get("schema_version") != INDEX_JOB_RECEIPT_SCHEMA
            or value.get("operation_id") != operation_id
            or not isinstance(value.get("progress"), dict)
        ):
            raise RepositoryIndexJobError(
                "repository index progress receipt is incompatible",
                failure_kind="REPOSITORY_INDEX_FAILED",
            )

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict):
            raise RepositoryIndexJobError(
                f"repository index artifact is not an object: {path.name}",
                failure_kind="REPOSITORY_INDEX_FAILED",
            )
        return value

    @staticmethod
    def _atomic_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)


__all__ = [
    "RepositoryIndexJobError",
    "RepositoryIndexJobManager",
    "RepositoryIndexParked",
]
