"""Audit log: every tool call, file touch and send is recorded."""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path


class AuditLog:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, actor TEXT,
                tool TEXT, args_json TEXT, result_summary TEXT, risk TEXT)""")

    def record(self, actor: str, tool: str, args: dict, result_summary: str, risk: str):
        safe_args = {k: (v if k not in ("password", "token") else "***") for k, v in args.items()}
        with self._connect() as c:
            c.execute(
                "INSERT INTO audit_log(ts,actor,tool,args_json,result_summary,risk)"
                " VALUES(?,?,?,?,?,?)",
                (int(time.time()), actor, tool, json.dumps(safe_args),
                 result_summary[:500], risk))

    def today(self, limit: int = 100) -> list[dict]:
        day_start = int(time.time()) - 86400
        with self._connect() as c:
            rows = c.execute(
                "SELECT ts,actor,tool,args_json,result_summary,risk FROM audit_log"
                " WHERE ts>? ORDER BY ts DESC LIMIT ?", (day_start, limit)).fetchall()
        return [dict(r) for r in rows]
