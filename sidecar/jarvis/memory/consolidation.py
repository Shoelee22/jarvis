"""Nightly consolidation ('dream'): dedupe, merge, decay. Produces a reviewable log."""
from __future__ import annotations
import time

from .store import _connect


def consolidate(db_path: str, decay_days: int = 90) -> dict:
    """Returns a dream log: {merged, expired, kept}."""
    log = {"merged": 0, "expired": 0, "kept": 0}
    now = int(time.time())
    with _connect(db_path) as c:
        # expire low-confidence old trivia
        cur = c.execute(
            "DELETE FROM memories WHERE confidence < 0.3 AND updated_at < ?",
            (now - decay_days * 86400,))
        log["expired"] = cur.rowcount
        # naive dedupe: exact-text duplicates keep the newest
        rows = c.execute("SELECT id, text, updated_at FROM memories ORDER BY updated_at").fetchall()
        seen: dict[str, str] = {}
        for r in rows:
            if r["text"] in seen:
                c.execute("DELETE FROM memories WHERE id=?", (r["id"],))
                log["merged"] += 1
            else:
                seen[r["text"]] = r["id"]
        log["kept"] = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    return log
