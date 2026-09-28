"""Phase 39 — pattern noticing / proactive preparation (offline)."""
import os
import sqlite3
import sys
import time

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import proactive_pack, autopilot_pack


def _seed(tmp_path, rows):
    db = tmp_path / "jarvis.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT,
        tool TEXT, args_json TEXT, result_summary TEXT, risk TEXT)""")
    conn.executemany(
        "INSERT INTO audit_log(ts, actor, tool, args_json, result_summary,"
        " risk) VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    os.environ["JARVIS_DATA"] = str(tmp_path)
    os.environ["JARVIS_AUTOPILOT_DB"] = str(tmp_path / "ap.db")


def _rows(tool, n, days):
    now = time.time()
    # spread uses across `days` distinct days
    return [(now - d * 86400 - 3600, "user", tool, "{}", "ok", "low")
            for d in range(days) for _ in range(n // days)]


def test_patterns_found_and_suggested(tmp_path):
    _seed(tmp_path, _rows("inbox.triage", 6, 3)
          + _rows("fs.read", 2, 1))  # burst, not a pattern
    r = proactive_pack.patterns_handler({"days": 14, "min_repeats": 3})
    assert "error" not in r, r
    tools = [p["tool"] for p in r["patterns"]]
    assert "inbox.triage" in tools
    assert "fs.read" not in tools  # same-day burst ignored
    assert r["suggestions"] == 1
    props = autopilot_pack._list_proposals("pending")
    assert len(props) == 1
    assert "[proactive]" in props[0]["goal"]


def test_no_log_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path / "empty"))
    r = proactive_pack.patterns_handler({})
    assert r["patterns"] == [] and "No audit log yet" in r["read"]


def test_registration_contract():
    spec = next(s for s in proactive_pack.TOOL_DEFS
                if s["name"] == "proactive.patterns")
    assert spec["risk"] == "low" and spec["needs_network"] is False
    assert proactive_pack.RISK_TABLE_ADDITIONS["proactive.patterns"] == (
        "low", False)
