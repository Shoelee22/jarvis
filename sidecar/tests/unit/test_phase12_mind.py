"""Phase 12 self-learning memory pack tests: extraction, consolidation,
feedback, routine detection, and register() wiring."""
from __future__ import annotations

import sqlite3
import time

import pytest

import jarvis.tools.builtin.mind_pack as mp
from jarvis.memory.store import MemoryStore
from jarvis.tools.base import Registry


@pytest.fixture(autouse=True)
def _isolated_dbs(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MIND_DB", str(tmp_path / "mind.db"))
    monkeypatch.setenv("JARVIS_MIND_MEMORY_DB", str(tmp_path / "brain.db"))
    monkeypatch.setenv("JARVIS_MIND_AUDIT_DB", str(tmp_path / "audit.db"))


def _memdb():
    import os
    return os.environ["JARVIS_MIND_MEMORY_DB"]


# ------------------------------------------------------------- memory.learn
def test_learn_extracts_durable_facts():
    r = mp.learn_handler({
        "text": "My dog is named Bruno. I prefer concise answers. Call me Raj."})
    assert r["stored"] == 3
    texts = [f["text"] for f in r["facts"]]
    assert "User's dog is named Bruno" in texts
    assert "User prefers concise answers" in texts
    assert "User wants to be called Raj" in texts
    types = {f["type"] for f in r["facts"]}
    assert types == {"fact", "preference", "identity"}
    # persisted to the memory store
    hits = MemoryStore(_memdb()).search("dog", limit=5)
    assert any("Bruno" in h["text"] for h in hits)


def test_learn_ignores_chitchat():
    for turn in ["hello there!", "how are you?", "thanks!", "my day is good",
                 "what is my dog's name?",
                 "if my dog was named Max that would be cool",
                 "maybe I prefer tea, not sure"]:
        r = mp.learn_handler({"text": turn})
        assert r["stored"] == 0, turn
    store = MemoryStore(_memdb())
    rows = store.search("dog", limit=20)
    assert rows == []


def test_learn_dedupes_near_identical():
    r1 = mp.learn_handler({"text": "My gym is Iron Culture"})
    assert r1["stored"] == 1
    r2 = mp.learn_handler({"text": "My gym is Iron Culture."})
    assert r2["stored"] == 0
    assert r2["skipped"] and "duplicate" in r2["skipped"][0]


def test_learn_rejects_empty():
    assert "error" in mp.learn_handler({"text": "   "})
    assert "error" in mp.learn_handler({})


# ------------------------------------------------------ memory.consolidate
def _seed(text, mtype="fact", confidence=0.7, age_days=0):
    store = MemoryStore(_memdb())
    try:
        mid = store.remember(mtype, text, confidence=confidence, source="test")
        if age_days:
            conn = sqlite3.connect(_memdb())
            conn.execute("UPDATE memories SET created_at=?, updated_at=? WHERE id=?",
                         (int(time.time()) - age_days * 86400,
                          int(time.time()) - age_days * 86400, mid))
            conn.commit()
            conn.close()
        return mid
    finally:
        store.close()


def test_consolidate_merges_exact_dupes():
    _seed("User's gym is Iron Culture")
    _seed("User's gym is Iron Culture")
    r = mp.consolidate_handler({})
    assert r["before"] == 2 and r["after"] == 1
    assert len(r["merged_duplicates"]) == 1
    assert r["contradictions_resolved"] == []


def test_consolidate_resolves_contradiction_newer_wins():
    _seed("User's gym is Gold's Gym", age_days=5)
    time.sleep(0.05)
    _seed("User's gym is Iron Culture")
    r = mp.consolidate_handler({})
    assert r["before"] == 2 and r["after"] == 1
    assert len(r["contradictions_resolved"]) == 1
    res = r["contradictions_resolved"][0]
    assert res["kept"] == "User's gym is Iron Culture"
    assert res["superseded"] == "User's gym is Gold's Gym"
    # history note preserved
    conn = sqlite3.connect(_memdb())
    try:
        conn.row_factory = sqlite3.Row
        hist = [dict(x) for x in conn.execute(
            "SELECT type, text FROM memories").fetchall()]
    finally:
        conn.close()
    store = MemoryStore(_memdb())
    store.close()
    assert any(h["type"] == "memory_history" and "Gold's Gym" in h["text"]
               for h in hist)


def test_consolidate_drops_expired_and_stale():
    _seed("User's temp code is 1234", confidence=0.2, age_days=40)
    store = MemoryStore(_memdb())
    try:
        mid = store.remember("fact", "User's coupon is X", confidence=0.7)
        conn = sqlite3.connect(_memdb())
        conn.execute("UPDATE memories SET expires_at=? WHERE id=?",
                     (int(time.time()) - 10, mid))
        conn.commit()
        conn.close()
    finally:
        store.close()
    r = mp.consolidate_handler({})
    assert r["before"] == 2 and r["after"] == 0
    assert len(r["dropped_stale"]) == 2
    reasons = {d["reason"] for d in r["dropped_stale"]}
    assert reasons == {"expired", "low confidence + stale"}


# ---------------------------------------------------------- memory.feedback
def test_feedback_persists_and_is_retrievable():
    r = mp.feedback_handler({"about": "long status reports", "liked": False,
                             "detail": "too wordy, keep it short"})
    assert r["stored"] is True and r["liked"] is False
    assert "dislikes long status reports" in r["text"]
    hits = MemoryStore(_memdb()).search("status reports", limit=5)
    assert any("too wordy" in h["text"] for h in hits)


def test_feedback_liked_true():
    r = mp.feedback_handler({"about": "morning briefings", "liked": True})
    assert "likes morning briefings" in r["text"]


def test_feedback_validates_args():
    assert "error" in mp.feedback_handler({"about": "x"})  # missing liked
    assert "error" in mp.feedback_handler({"liked": True})  # missing about
    assert "error" in mp.feedback_handler({"about": "x", "liked": "yes"})


# ---------------------------------------------------------- memory.routines
def _seed_tool_use(tool: str, days_ago: list[int], hh: int = 9, mm: int = 5):
    """Seed the fallback usage log with tool uses at hh:mm local, N days ago."""
    import datetime as _dt
    conn = mp._mind_conn()
    try:
        for d in days_ago:
            dt = _dt.datetime.now().replace(hour=hh, minute=mm,
                                            second=0, microsecond=0)
            ts = int((dt - _dt.timedelta(days=d)).timestamp())
            conn.execute("INSERT INTO usage_log(ts,kind,detail) VALUES(?,?,?)",
                         (ts, "tool", tool))
        conn.commit()
    finally:
        conn.close()


def test_routines_detects_repeated_pattern():
    _seed_tool_use("briefing", [1, 2, 3, 4, 5])
    _seed_tool_use("notes.add", [1])  # one-off: no proposal
    r = mp.routines_handler({"days": 7, "min_days": 3})
    assert r["history_source"] == "usage_log"
    assert r["events_analyzed"] == 6
    assert r["created"] == 0  # proposal only: nothing was created
    tools = [p["tool"] for p in r["proposals"]]
    assert tools == ["briefing"]
    p = r["proposals"][0]
    assert p["days_seen"] == 5 and p["status"] == "proposed"
    assert "~09:05" in p["pattern"]
    assert "forwarding_note" in r
    # persisted as proposed, not scheduled anywhere
    conn = mp._mind_conn()
    try:
        rows = conn.execute(
            "SELECT tool, status FROM routine_proposals").fetchall()
    finally:
        conn.close()
    assert [(x[0], x[1]) for x in rows] == [("briefing", "proposed")]


def test_routines_no_pattern_no_proposal():
    _seed_tool_use("briefing", [1])
    r = mp.routines_handler({"days": 7, "min_days": 3})
    assert r["proposals"] == []


def test_routines_validates_args():
    assert "error" in mp.routines_handler({"days": 7, "min_days": 9})


# --------------------------------------------------------------- wiring
def test_register_and_risk_table():
    reg = Registry()
    mp.register(reg)
    assert {s["name"] for s in mp.TOOL_DEFS} == {
        "memory.learn", "memory.consolidate", "memory.feedback", "memory.routines"}
    for spec in mp.TOOL_DEFS:
        assert spec["name"] in reg.tools
        assert reg.tools[spec["name"]].risk == spec["risk"]
        assert reg.tools[spec["name"]].needs_network is False
        assert callable(reg.tools[spec["name"]].handler)
    assert set(mp.RISK_TABLE_ADDITIONS) == {s["name"] for s in mp.TOOL_DEFS}
    for name, (risk, net) in mp.RISK_TABLE_ADDITIONS.items():
        assert risk in ("low", "medium", "high")
        assert net is False
    # risk levels match the spec
    assert mp.RISK_TABLE_ADDITIONS["memory.consolidate"][0] == "medium"


def test_tool_defs_have_handlers_and_schemas():
    for spec in mp.TOOL_DEFS:
        assert spec["description"] and spec["handler"] and isinstance(spec["schema"], dict)
