"""Monotonic fenced leases for shared durable runtime ownership.

The lease row answers who may act now. The counter answers which owner is newer.
Keeping those facts separate means releasing a lease never resets its fencing
token, so a delayed writer from an older owner can always be rejected.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator

LEASE_SCHEMA_VERSION = "taiyi.fenced-lease/v1"


class FencedLeaseLost(RuntimeError):
    """The caller's lease was replaced, expired, or explicitly released."""


@dataclass(frozen=True)
class FencedLease:
    namespace: str
    key: str
    owner_id: str
    token: int
    expires_at: float

    def to_dict(self) -> dict:
        return {
            "schema_version": LEASE_SCHEMA_VERSION,
            "namespace": self.namespace,
            "key": self.key,
            "owner_id": self.owner_id,
            "fencing_token": self.token,
            "expires_at": self.expires_at,
        }


class FencedLeaseStore:
    """SQLite reference backend for renewable, monotonically fenced leases.

    SQLite gives local and shared-disk deployments one transactional authority.
    The API deliberately does not expose SQLite concepts so a production
    deployment can replace this backend with PostgreSQL/etcd without changing
    Runtime ownership semantics.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        lease_seconds: float = 30.0,
        owner_id: str | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lease_seconds = max(0.1, float(lease_seconds))
        self.owner_id = owner_id or f"owner_{uuid.uuid4().hex}"
        self.clock = clock
        self._initialize()

    def acquire(
        self,
        namespace: str,
        key: str,
        *,
        owner_id: str | None = None,
    ) -> FencedLease | None:
        owner = owner_id or self.owner_id
        with self._transaction() as connection:
            now = self._now(connection)
            row = connection.execute(
                "SELECT owner_id, fencing_token, expires_at FROM leases "
                "WHERE namespace = ? AND lease_key = ?",
                (namespace, key),
            ).fetchone()
            if row is not None and float(row[2]) > now and str(row[0]) != owner:
                return None
            if row is not None and float(row[2]) > now and str(row[0]) == owner:
                token = int(row[1])
            else:
                counter = connection.execute(
                    "SELECT last_token FROM lease_counters "
                    "WHERE namespace = ? AND lease_key = ?",
                    (namespace, key),
                ).fetchone()
                token = max(
                    int(counter[0]) if counter is not None else 0,
                    int(row[1]) if row is not None else 0,
                ) + 1
                connection.execute(
                    "INSERT INTO lease_counters(namespace, lease_key, last_token) "
                    "VALUES (?, ?, ?) ON CONFLICT(namespace, lease_key) DO UPDATE "
                    "SET last_token = excluded.last_token",
                    (namespace, key, token),
                )
            expires_at = now + self.lease_seconds
            connection.execute(
                "INSERT INTO leases(namespace, lease_key, owner_id, fencing_token, "
                "expires_at, renewed_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(namespace, lease_key) DO UPDATE SET "
                "owner_id = excluded.owner_id, fencing_token = excluded.fencing_token, "
                "expires_at = excluded.expires_at, renewed_at = excluded.renewed_at",
                (namespace, key, owner, token, expires_at, now),
            )
        return FencedLease(namespace, key, owner, token, expires_at)

    def renew(self, lease: FencedLease) -> FencedLease:
        with self._transaction() as connection:
            now = self._now(connection)
            self._require_current(connection, lease, now)
            expires_at = now + self.lease_seconds
            connection.execute(
                "UPDATE leases SET expires_at = ?, renewed_at = ? WHERE "
                "namespace = ? AND lease_key = ? AND owner_id = ? "
                "AND fencing_token = ?",
                (
                    expires_at,
                    now,
                    lease.namespace,
                    lease.key,
                    lease.owner_id,
                    lease.token,
                ),
            )
        return replace(lease, expires_at=expires_at)

    @contextmanager
    def guard(self, lease: FencedLease) -> Iterator[FencedLease]:
        """Fence one authoritative write while ownership cannot be replaced."""

        with self._transaction() as connection:
            now = self._now(connection)
            self._require_current(connection, lease, now)
            refreshed = replace(lease, expires_at=now + self.lease_seconds)
            connection.execute(
                "UPDATE leases SET expires_at = ?, renewed_at = ? WHERE "
                "namespace = ? AND lease_key = ? AND owner_id = ? "
                "AND fencing_token = ?",
                (
                    refreshed.expires_at,
                    now,
                    lease.namespace,
                    lease.key,
                    lease.owner_id,
                    lease.token,
                ),
            )
            yield refreshed

    def release(self, lease: FencedLease) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM leases WHERE namespace = ? AND lease_key = ? "
                "AND owner_id = ? AND fencing_token = ?",
                (lease.namespace, lease.key, lease.owner_id, lease.token),
            )
            return cursor.rowcount == 1

    def current(self, namespace: str, key: str) -> FencedLease | None:
        with self._connect() as connection:
            now = self._now(connection)
            row = connection.execute(
                "SELECT owner_id, fencing_token, expires_at FROM leases "
                "WHERE namespace = ? AND lease_key = ?",
                (namespace, key),
            ).fetchone()
        if row is None or float(row[2]) <= now:
            return None
        return FencedLease(namespace, key, str(row[0]), int(row[1]), float(row[2]))

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lease_counters ("
                "namespace TEXT NOT NULL, lease_key TEXT NOT NULL, "
                "last_token INTEGER NOT NULL, PRIMARY KEY(namespace, lease_key))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS leases ("
                "namespace TEXT NOT NULL, lease_key TEXT NOT NULL, "
                "owner_id TEXT NOT NULL, fencing_token INTEGER NOT NULL, "
                "expires_at REAL NOT NULL, renewed_at REAL NOT NULL, "
                "PRIMARY KEY(namespace, lease_key))"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _now(self, connection: sqlite3.Connection) -> float:
        if self.clock is not None:
            return float(self.clock())
        # One database clock is authoritative across owners. Host wall-clock
        # skew therefore cannot make two Gateways disagree about expiry.
        row = connection.execute(
            "SELECT (julianday('now') - 2440587.5) * 86400.0"
        ).fetchone()
        assert row is not None
        return float(row[0])

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _require_current(
        connection: sqlite3.Connection,
        lease: FencedLease,
        now: float,
    ) -> None:
        row = connection.execute(
            "SELECT owner_id, fencing_token, expires_at FROM leases "
            "WHERE namespace = ? AND lease_key = ?",
            (lease.namespace, lease.key),
        ).fetchone()
        if (
            row is None
            or str(row[0]) != lease.owner_id
            or int(row[1]) != lease.token
            or float(row[2]) <= now
        ):
            raise FencedLeaseLost(
                f"lease {lease.namespace}/{lease.key} token {lease.token} is stale"
            )


__all__ = [
    "FencedLease",
    "FencedLeaseLost",
    "FencedLeaseStore",
    "LEASE_SCHEMA_VERSION",
]
