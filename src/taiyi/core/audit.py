"""Tamper-evident audit log.

The design names "no immutable audit log" as a reliability anti-pattern. A plain
log file can be edited after the fact; a hash-chained log cannot be edited
*silently*. Each record commits to the one before it, so altering or deleting any
record breaks the chain from that point on — and `verify()` will say where.

This is deliberately dependency-free and append-only. It is not a replacement for
write-once storage at the infra level, but it makes in-band tampering detectable.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses process-local serialization
    fcntl = None

_AUDIT_PROCESS_LOCK = threading.RLock()

GENESIS = "0" * 64


@dataclass
class AuditRecord:
    seq: int
    ts: float
    event: str
    payload: dict
    prev_hash: str
    hash: str = ""

    def _digest(self) -> str:
        body = {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def sealed(self) -> "AuditRecord":
        self.hash = self._digest()
        return self


class AuditLog:
    """Append-only, hash-chained event log.

    Pass ``path`` to also persist as JSONL (one record per line). Without a path
    the log lives only in memory (useful for tests and short-lived workers).
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self.records: list[AuditRecord] = []
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        self.records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            self.records.append(AuditRecord(**d))

    @property
    def head_hash(self) -> str:
        return self.records[-1].hash if self.records else GENESIS

    def append(self, event: str, **payload) -> AuditRecord:
        with self._lock:
            if self.path is None:
                rec = self._next_record(event, payload)
                self.records.append(rec)
                return rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._locked_path():
                if self.path.exists():
                    self._load()
                else:
                    self.records = []
                rec = self._next_record(event, payload)
                self.records.append(rec)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            asdict(rec),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ) + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    directory = os.open(self.path.parent, os.O_RDONLY)
                except OSError:
                    pass
                else:
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                return rec

    def verify(self) -> tuple[bool, int | None]:
        """Re-walk the chain. Returns (ok, first_broken_seq_or_None)."""
        with self._lock:
            if self.path is not None:
                with self._locked_path():
                    if self.path.exists():
                        self._load()
            prev = GENESIS
            for rec in self.records:
                if rec.prev_hash != prev:
                    return False, rec.seq
                if rec._digest() != rec.hash:
                    return False, rec.seq
                prev = rec.hash
            return True, None

    def _next_record(self, event: str, payload: dict) -> AuditRecord:
        values = dict(payload)
        return AuditRecord(
            seq=len(self.records),
            ts=values.pop("ts", None) or time.time(),
            event=event,
            payload=values,
            prev_hash=self.head_hash,
        ).sealed()

    @contextmanager
    def _locked_path(self):
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with _AUDIT_PROCESS_LOCK:
            with lock_path.open("a+b") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def __len__(self) -> int:
        return len(self.records)
