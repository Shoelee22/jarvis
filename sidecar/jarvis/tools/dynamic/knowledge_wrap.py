"""Knowledge base: a real sqlite-FTS5 document store with full-text search.

Two tools:
  knowledge.add_text  {text, source?} -> {"id", "chars", "source"}
  knowledge.ask       {query, k?=5}   -> {"results": [{"source","snippet","rank"}]}

Storage: one sqlite file (default ``DATA_DIR/"knowledge.db"``, overridable via
the ``knowledge.db_path`` config section) with a ``docs`` table plus an
external-content FTS5 index (``docs_fts``) kept in sync manually on insert.

Handlers are dict->dict and never raise. FTS5 availability is probed at
connect time; if the Python build lacks the FTS5 module, handlers return an
honest error dict instead of crashing.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..base import Tool
from .providers import Provider
from .config import load_dynamic_config, section
from ...config import DATA_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'manual',
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_FTS_DDL = "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(text, content='docs', content_rowid='id')"

_SNIPPET_SQL = """
SELECT d.source AS source,
       snippet(docs_fts, 0, '<b>', '</b>', '\u2026', 20) AS snip,
       rank AS rnk
FROM docs_fts
JOIN docs AS d ON d.id = docs_fts.rowid
WHERE docs_fts MATCH ?
ORDER BY rnk
LIMIT ?
"""

# FTS5 query operators / syntax characters we strip before MATCH so that odd
# user queries can never raise a syntax error.
_FTS_SPECIAL_RE = re.compile(r'["*():^+\-]')
_WS_RE = re.compile(r"\s+")
# Bare boolean operators would be parsed as FTS5 operators; drop them so the
# remaining terms are matched with implicit AND.
_BOOLWORD_RE = re.compile(r"(?i)\b(and|or|not)\b")

# Common English stopwords: a natural-language question like "where does Jarvis
# run" should still find a doc containing "Jarvis". Remaining terms are joined
# with OR for recall; FTS5 rank orders the best matches first.
_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did will would shall should "
    "can could may might must have has had having i me my we our you your he she "
    "it its they them their this that these those what which who whom whose when "
    "where why how in on at to for of with by from as and or not no nor so than "
    "too very just about into over after before between during".split()
)


def _sanitize_match(query: str) -> str | None:
    """Make `query` safe for FTS5 MATCH. Returns None when nothing searchable."""
    if not isinstance(query, str):
        return None
    q = _FTS_SPECIAL_RE.sub(" ", query)
    q = _BOOLWORD_RE.sub(" ", q)
    q = _WS_RE.sub(" ", q).strip()
    terms = [t for t in q.split(" ") if t and t.lower() not in _STOPWORDS]
    if not terms:
        return None
    # Dedupe, keep order, join with OR.
    seen = set()
    uniq = [t for t in terms if not (t.lower() in seen or seen.add(t.lower()))]
    return " OR ".join(uniq)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeProvider(Provider):
    """sqlite-FTS5 knowledge base. expand() == 2."""

    namespace = "knowledge"

    def __init__(self, data_dir=None):
        super().__init__(data_dir)

    # -- storage ------------------------------------------------------
    def _db_path(self) -> Path:
        configured = section("knowledge", {}).get("db_path") or ""
        if configured:
            return Path(configured).expanduser()
        base = self.data_dir if self.data_dir is not None else DATA_DIR
        return Path(base) / "knowledge.db"

    def _connect(self) -> tuple[sqlite3.Connection | None, str | None]:
        """Open the DB and ensure schema. Returns (conn, error)."""
        path = self._db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path)
        except Exception as e:  # noqa: BLE001 - disk problems are handler errors
            return None, f"cannot open knowledge db: {type(e).__name__}: {e}"
        try:
            conn.executescript(_SCHEMA)
            try:
                conn.execute(_FTS_DDL)
            except sqlite3.OperationalError as e:
                conn.close()
                if "no such module" in str(e).lower():
                    return None, (
                        "SQLite FTS5 is not available in this Python build "
                        f"(sqlite {sqlite3.sqlite_version}); "
                        "knowledge search is disabled"
                    )
                return None, f"cannot create FTS5 index: {e}"
            conn.commit()
            return conn, None
        except Exception as e:  # noqa: BLE001
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return None, f"knowledge db init failed: {type(e).__name__}: {e}"

    # -- handlers -----------------------------------------------------
    def _handle_add_text(self, args: dict) -> dict:
        try:
            text = (args or {}).get("text", "")
            source = (args or {}).get("source", "manual") or "manual"
            if not isinstance(text, str) or not text.strip():
                return {"error": "text is required (non-empty string)"}
            if not isinstance(source, str):
                source = "manual"
            conn, err = self._connect()
            if err:
                return {"error": err}
            try:
                cur = conn.execute(
                    "INSERT INTO docs(source, text, created_at) VALUES (?,?,?)",
                    (source[:200], text, _utcnow()),
                )
                rowid = cur.lastrowid
                conn.execute(
                    "INSERT INTO docs_fts(rowid, text) VALUES (?, ?)",
                    (rowid, text),
                )
                conn.commit()
            finally:
                conn.close()
            return {"id": rowid, "chars": len(text), "source": source[:200]}
        except Exception as e:  # never raise
            return {"error": f"{type(e).__name__}: {e}"}

    def _handle_ask(self, args: dict) -> dict:
        try:
            a = args or {}
            query = a.get("query", "")
            try:
                k = int(a.get("k", 5))
            except (TypeError, ValueError):
                k = 5
            k = max(1, min(50, k))
            match = _sanitize_match(query)
            if match is None:
                # Odd/empty query: honest empty result, never a crash.
                return {"results": []}
            conn, err = self._connect()
            if err:
                return {"error": err}
            try:
                try:
                    rows = conn.execute(_SNIPPET_SQL, (match, k)).fetchall()
                except sqlite3.Error:
                    # Defensive: any FTS5 quirk -> honest empty, not a crash.
                    return {"results": []}
            finally:
                conn.close()
            return {
                "results": [
                    {"source": r[0], "snippet": r[1], "rank": r[2]} for r in rows
                ]
            }
        except Exception as e:  # never raise
            return {"error": f"{type(e).__name__}: {e}"}

    # -- Provider contract --------------------------------------------
    def expand(self) -> int:
        return 2

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        local = self._local(name)
        if local == "add_text":
            return Tool(
                name=name,
                description="Store a text document in the FTS5 knowledge base.",
                schema={"text": "string", "source": "string?"},
                handler=self._handle_add_text,
                risk="low",
            )
        if local == "ask":
            return Tool(
                name=name,
                description="Full-text search the knowledge base (FTS5 ranked snippets).",
                schema={"query": "string", "k": "int?"},
                handler=self._handle_ask,
                risk="low",
            )
        return None

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted("add_text"), self._dotted("ask")][: max(0, n)]


# Keep the config import referenced (contract check for sibling workers).
_ = load_dynamic_config
