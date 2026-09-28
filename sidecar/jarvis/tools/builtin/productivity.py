"""Reminders, timers, notes — local, persistent, SQLite-backed."""
from __future__ import annotations
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with self._c() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS reminders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, cron TEXT,
                once_at INTEGER, done INTEGER DEFAULT 0, created INTEGER)""")
            c.execute("""CREATE TABLE IF NOT EXISTS notes(
                id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created INTEGER)""")

    def _c(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # reminders
    def add_reminder(self, args: dict) -> dict:
        with self._c() as c:
            cur = c.execute(
                "INSERT INTO reminders(text,cron,once_at,created) VALUES(?,?,?,?)",
                (args["text"], args.get("cron"), args.get("once_at"), int(time.time())))
        return {"id": cur.lastrowid, "text": args["text"]}

    def list_reminders(self, args: dict) -> dict:
        with self._c() as c:
            rows = c.execute(
                "SELECT id,text,cron,once_at FROM reminders WHERE done=0 ORDER BY id").fetchall()
        return {"reminders": [dict(r) for r in rows]}

    # notes
    def add_note(self, args: dict) -> dict:
        with self._c() as c:
            cur = c.execute("INSERT INTO notes(text,created) VALUES(?,?)",
                            (args["text"], int(time.time())))
        return {"id": cur.lastrowid}

    def search_notes(self, args: dict) -> dict:
        q = f"%{args['query']}%"
        with self._c() as c:
            rows = c.execute("SELECT id,text FROM notes WHERE text LIKE ? LIMIT 20", (q,)).fetchall()
        return {"notes": [dict(r) for r in rows]}


def set_timer(args: dict) -> dict:
    return {"timer_set_for_s": args["seconds"], "label": args.get("label", ""),
            "fires_at": int(time.time()) + args["seconds"]}
