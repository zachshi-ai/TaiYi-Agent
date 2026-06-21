"""The 5-layer memory engine (cross-cutting concern H1).

  L1 short-term  — session messages (recent conversational context).
  L2 skills      — an index of available skills (the full Skill engine is M8).
  L3 vector      — semantic retrieval over long-term memories (local embedder).
  L4 user model  — Honcho-style dialectical preferences (thesis→antithesis→synthesis).
  L5 full-text   — SQLite FTS5 history (falls back to LIKE where FTS5 is absent).

Markdown-first: long-term memories are also appended to a human-readable daily
log under ``<base>/memory/*.md``; SQLite is the index over that record. Everything
is stdlib (sqlite3) — zero ops, matching the single-machine MVP posture.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from taiyi.memory.embedding import Embedder, HashingEmbedder, cosine
from taiyi.memory.types import MemoryHit


class MemoryEngine:
    def __init__(self, base_dir: str | Path | None = None, *, embedder: Embedder | None = None):
        if base_dir is None:
            self.base: Path | None = None
            db_path = ":memory:"
        else:
            self.base = Path(base_dir)
            (self.base / "memory").mkdir(parents=True, exist_ok=True)
            db_path = str(self.base / "taiyi.db")

        # check_same_thread=False so the engine can serve a request handler that
        # runs in a server thread; the stdlib HTTPServer processes one request at
        # a time, so there is no concurrent-write hazard here.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.embedder = embedder or HashingEmbedder()
        self.fts = self._detect_fts5()
        self._init_schema()

    # --- setup ---------------------------------------------------------------
    def _detect_fts5(self) -> bool:
        try:
            self.conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
            self.conn.execute("DROP TABLE _fts_probe")
            return True
        except sqlite3.OperationalError:
            return False

    def _init_schema(self) -> None:
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY, content TEXT, tags TEXT,
                source_task_id TEXT, importance INTEGER, ts REAL);
            CREATE TABLE IF NOT EXISTS embeddings(
                memory_id INTEGER PRIMARY KEY, vector TEXT);
            CREATE TABLE IF NOT EXISTS user_model(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS skills(
                name TEXT PRIMARY KEY, summary TEXT, tags TEXT);
            """
        )
        if self.fts:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content)")
        c.commit()

    # --- L1: short-term session context --------------------------------------
    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages(session_id, role, content, ts) VALUES (?,?,?,?)",
            (session_id, role, content, time.time()),
        )
        self.conn.commit()

    def get_messages(self, session_id: str, limit: int | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content, ts FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        msgs = [dict(r) for r in rows]
        return msgs[-limit:] if limit else msgs

    def clear_session(self, session_id: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        self.conn.commit()

    # --- L2: skill index -----------------------------------------------------
    def register_skill(self, name: str, summary: str = "", tags: tuple[str, ...] = ()) -> None:
        self.conn.execute(
            "INSERT INTO skills(name, summary, tags) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET summary=excluded.summary, tags=excluded.tags",
            (name, summary, json.dumps(list(tags))),
        )
        self.conn.commit()

    def list_skills(self) -> list[str]:
        return [r["name"] for r in self.conn.execute("SELECT name FROM skills ORDER BY name")]

    def get_skill(self, name: str) -> dict | None:
        r = self.conn.execute("SELECT name, summary, tags FROM skills WHERE name=?", (name,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        return d

    # --- L5 write (also indexed by L3) ---------------------------------------
    def remember(
        self,
        content: str,
        *,
        tags: tuple[str, ...] = (),
        source_task_id: str | None = None,
        importance: int = 5,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO memories(content, tags, source_task_id, importance, ts) VALUES (?,?,?,?,?)",
            (content, json.dumps(list(tags)), source_task_id, importance, time.time()),
        )
        mid = cur.lastrowid
        self.conn.execute(
            "INSERT INTO embeddings(memory_id, vector) VALUES (?,?)",
            (mid, json.dumps(self.embedder.embed(content))),
        )
        if self.fts:
            self.conn.execute("INSERT INTO memories_fts(rowid, content) VALUES (?,?)", (mid, content))
        self.conn.commit()
        self._append_markdown(content, tags, source_task_id)
        return mid

    # --- L5: full-text search ------------------------------------------------
    def search_fulltext(self, query: str, limit: int = 5) -> list[MemoryHit]:
        import re

        tokens = re.findall(r"\w+", query.lower())
        if not tokens:
            return []
        if self.fts:
            match = " OR ".join(tokens)
            rows = self.conn.execute(
                "SELECT m.id AS id, m.content AS content "
                "FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                "WHERE memories_fts MATCH ? LIMIT ?",
                (match, limit),
            ).fetchall()
        else:
            like = "%" + "%".join(tokens) + "%"
            rows = self.conn.execute(
                "SELECT id, content FROM memories WHERE lower(content) LIKE ? LIMIT ?",
                (like, limit),
            ).fetchall()
        return [MemoryHit("L5", r["content"], str(r["id"]), 1.0) for r in rows]

    # --- L3: semantic search -------------------------------------------------
    def search_semantic(self, query: str, top_k: int = 5) -> list[MemoryHit]:
        qv = self.embedder.embed(query)
        rows = self.conn.execute(
            "SELECT m.id AS id, m.content AS content, e.vector AS vector "
            "FROM memories m JOIN embeddings e ON e.memory_id = m.id"
        ).fetchall()
        scored = [(cosine(qv, json.loads(r["vector"])), r) for r in rows]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [MemoryHit("L3", r["content"], str(r["id"]), s) for s, r in scored[:top_k] if s > 0]

    # --- L4: Honcho-style dialectical user model -----------------------------
    def observe_user(self, observation: str) -> str:
        row = self.conn.execute("SELECT value FROM user_model WHERE key='preferences'").fetchone()
        thesis = row["value"] if row else ""
        if not thesis:
            synthesis = observation
        elif observation.lower() in thesis.lower():
            synthesis = thesis  # already known
        else:
            synthesis = f"{thesis}\n- {observation}"
        self.conn.execute(
            "INSERT INTO user_model(key, value) VALUES ('preferences', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (synthesis,),
        )
        self.conn.commit()
        return synthesis

    def get_user_model(self) -> str:
        row = self.conn.execute("SELECT value FROM user_model WHERE key='preferences'").fetchone()
        return row["value"] if row else ""

    # --- helpers -------------------------------------------------------------
    def _append_markdown(self, content: str, tags: tuple[str, ...], source_task_id: str | None) -> None:
        if self.base is None:
            return
        path = self.base / "memory" / f"{time.strftime('%Y-%m-%d')}.md"
        meta = f" (task={source_task_id})" if source_task_id else ""
        tagstr = f" [{', '.join(tags)}]" if tags else ""
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {time.strftime('%H:%M:%S')}{meta}{tagstr}\n\n{content}\n")

    def close(self) -> None:
        self.conn.close()
