"""Agent explainability pack (Phase 35): "why did you do that?"

One tool, agent.why, reads the agent's audit log — the same log the
agent core writes for every tool call (jarvis/agent/audit.py, stored
at <data_dir>/jarvis.db). Passwords/tokens are redacted at write time.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


def _audit_db() -> Path:
    override = os.environ.get("JARVIS_DATA")
    base = Path(override) if override else Path.home() / ".jarvis"
    return base / "jarvis.db"


def why_handler(args: dict) -> dict:
    """agent.why {tool?, limit?=10} — explain recent actions.

    Reads the audit log (newest first): who called what, with which
    args, what it returned, and its risk label. Filter by tool name
    substring. Honest 'no audit records yet' when the log is empty —
    the log only exists once the agent has actually run.
    """
    try:
        a = args if isinstance(args, dict) else {}
        tool_filter = str(a.get("tool") or "").strip()
        try:
            limit = int(a.get("limit", 10))
        except (TypeError, ValueError):
            return {"error": "agent.why: 'limit' must be an integer"}
        limit = max(1, min(100, limit))
        db = _audit_db()
        if not db.exists():
            return {"actions": [], "count": 0,
                    "read": ("No audit records yet — the audit log is "
                             "written as the agent acts. Nothing has acted "
                             "in this data dir.")}
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            if tool_filter:
                rows = conn.execute(
                    "SELECT ts, actor, tool, args_json, result_summary, risk"
                    " FROM audit_log WHERE tool LIKE ?"
                    " ORDER BY ts DESC LIMIT ?",
                    (f"%{tool_filter}%", limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ts, actor, tool, args_json, result_summary, risk"
                    " FROM audit_log ORDER BY ts DESC LIMIT ?",
                    (limit,)).fetchall()
        finally:
            conn.close()
        actions = [{
            "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
            "actor": r["actor"], "tool": r["tool"],
            "args": r["args_json"][:300],
            "result": (r["result_summary"] or "")[:300],
            "risk": r["risk"],
        } for r in rows]
        return {"actions": actions, "count": len(actions),
                "read": ("Newest first. 'actor' is who/what invoked the "
                         "tool; 'risk' is the policy label at call time. "
                         "This explains WHAT happened and with what — the "
                         "WHY lives in the 'why' fields of the plans and "
                         "proposals that authorized them.")}
    except Exception as exc:  # never raise
        return {"error": f"agent.why failed: {exc}"}


TOOL_DEFS = [
    {"name": "agent.why",
     "description": ("Explain recent actions: read the agent's audit log "
                     "(newest first) — who called what, with which args, "
                     "what it returned, risk label. Filter by tool name. "
                     "Honest 'no records yet' before the agent has acted."),
     "handler": why_handler, "risk": "low", "needs_network": False,
     "schema": {"tool?": "string", "limit?": "int"}},
]

RISK_TABLE_ADDITIONS = {
    "agent.why": ("low", False),
}


def register(reg) -> None:
    """Wire the agent pack into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(
            name=spec["name"], description=spec["description"],
            handler=spec["handler"], risk=spec["risk"],
            needs_network=spec["needs_network"], schema=spec["schema"]))
