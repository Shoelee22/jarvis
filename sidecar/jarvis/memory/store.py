"""Memory store: encrypted SQLite (SQLCipher when available) + vector index."""
from __future__ import annotations
import sqlite3
import time
import uuid
from pathlib import Path

from .vectors import VectorIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id TEXT PRIMARY KEY, type TEXT, text TEXT, confidence REAL,
  created_at INTEGER, updated_at INTEGER, expires_at INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS episodes(
  id TEXT PRIMARY KEY, date TEXT, summary TEXT, created_at INTEGER);
CREATE TABLE IF NOT EXISTS people(
  id TEXT PRIMARY KEY, name TEXT, relation TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS training_data(
  id TEXT PRIMARY KEY, kind TEXT, prompt TEXT, response TEXT,
  source TEXT, pii_flags TEXT, created_at INTEGER);
"""


def _connect(db_path: str):
    try:
        import sqlcipher3  # type: ignore
        conn = sqlcipher3.connect(db_path)
    except ImportError:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


class MemoryStore:
    def __init__(self, db_path: str | Path, vector_dir: str | Path | None = None):
        self.db_path = str(db_path)
        if vector_dir is None:
            if self.db_path == ":memory:":
                import tempfile
                vector_dir = tempfile.mkdtemp(prefix="jarvis-vec-")
            else:
                vector_dir = str(db_path) + ".vectors"
        self.vectors = VectorIndex(vector_dir)
        # :memory: databases are per-connection, so hold one shared connection.
        self._mem_conn = _connect(":memory:") if self.db_path == ":memory:" else None
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        if self._mem_conn is not None:
            return self._mem_conn
        return _connect(self.db_path)

    def remember(self, type_: str, text: str, confidence: float = 1.0,
                 source: str = "user", expires_at: int | None = None) -> str:
        mid = uuid.uuid4().hex[:12]
        now = int(time.time())
        with self._c() as c:
            c.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?)",
                      (mid, type_, text, confidence, now, now, expires_at, source))
        self.vectors.add(mid, text)
        return mid

    def search(self, query: str, limit: int = 5) -> list[dict]:
        ids = self.vectors.search(query, limit * 2)
        if not ids:
            # fallback: keyword LIKE
            with self._c() as c:
                rows = c.execute(
                    "SELECT * FROM memories WHERE text LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (f"%{query}%", limit)).fetchall()
            return [dict(r) for r in rows]
        with self._c() as c:
            rows = []
            for mid in ids[:limit]:
                r = c.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
                if r:
                    rows.append(dict(r))
        return rows

    def forget(self, query: str) -> int:
        hits = self.search(query, limit=20)
        n = 0
        with self._c() as c:
            for h in hits:
                c.execute("DELETE FROM memories WHERE id=?", (h["id"],))
                self.vectors.remove(h["id"])
                n += 1
        return n

    def log_correction(self, prompt: str, bad_response: str, correction: str):
        """Corrections become training signal (FR-8.1)."""
        with self._c() as c:
            c.execute("INSERT INTO training_data VALUES(?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex[:12], "correction", prompt,
                       f"BAD: {bad_response}\nGOOD: {correction}",
                       "conversation", "", int(time.time())))

    def add_episode(self, summary: str):
        with self._c() as c:
            c.execute("INSERT INTO episodes VALUES(?,?,?,?)",
                      (uuid.uuid4().hex[:12],
                       time.strftime("%Y-%m-%d"), summary, int(time.time())))

    def close(self):
        self.vectors.close()
