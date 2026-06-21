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
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
        self.records: list[AuditRecord] = []
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
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
        rec = AuditRecord(
            seq=len(self.records),
            ts=payload.pop("ts", None) or time.time(),
            event=event,
            payload=payload,
            prev_hash=self.head_hash,
        ).sealed()
        self.records.append(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False, separators=(",", ":")) + "\n")
        return rec

    def verify(self) -> tuple[bool, int | None]:
        """Re-walk the chain. Returns (ok, first_broken_seq_or_None)."""
        prev = GENESIS
        for rec in self.records:
            if rec.prev_hash != prev:
                return False, rec.seq
            if rec._digest() != rec.hash:
                return False, rec.seq
            prev = rec.hash
        return True, None

    def __len__(self) -> int:
        return len(self.records)
