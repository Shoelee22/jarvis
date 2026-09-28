"""Phase 35 — explainability: agent.why reads the audit log (offline)."""
import os
import sqlite3
import sys
import time

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import agent_pack


def _seed_db(tmp_path):
    db = tmp_path / "jarvis.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT,
        tool TEXT, args_json TEXT, result_summary TEXT, risk TEXT)""")
    now = time.time()
    rows = [
        (now - 300, "user", "fs.read", '{"path": "notes.txt"}', "ok", "low"),
        (now - 200, "autopilot", "web.fetch", '{"url": "x"}', "ok", "medium"),
        (now - 100, "user", "fs.read", '{"path": "todo.txt"}', "ok", "low"),
    ]
    conn.executemany(
        "INSERT INTO audit_log(ts, actor, tool, args_json, result_summary,"
        " risk) VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    os.environ["JARVIS_DATA"] = str(tmp_path)
    return db


def test_why_lists_newest_first(tmp_path):
    _seed_db(tmp_path)
    r = agent_pack.why_handler({})
    assert "error" not in r, r
    assert r["count"] == 3
    assert r["actions"][0]["tool"] == "fs.read"
    assert r["actions"][0]["args"] == '{"path": "todo.txt"}'


def test_why_filters_by_tool(tmp_path):
    _seed_db(tmp_path)
    r = agent_pack.why_handler({"tool": "web"})
    assert r["count"] == 1
    assert r["actions"][0]["tool"] == "web.fetch"
    assert r["actions"][0]["risk"] == "medium"


def test_why_honest_when_no_log(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path / "empty"))
    r = agent_pack.why_handler({})
    assert r["count"] == 0
    assert "No audit records yet" in r["read"]


def test_registration_contract():
    spec = next(s for s in agent_pack.TOOL_DEFS if s["name"] == "agent.why")
    assert spec["risk"] == "low"
    assert "tool?" in spec["schema"]
