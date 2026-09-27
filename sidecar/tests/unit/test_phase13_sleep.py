"""Offline tests for the Phase 13 Sleep/Dream pack (sleep_pack).

Exercised against a REAL Registry with stub collaborators, a fake agent
bound via sleep_pack.bind_agent, a tmp sqlite sleep DB, and a tmp sqlite
audit DB seeded through the real AuditLog class.

Covers:
  - sleep.cycle composes all four steps against mocked collaborators
  - sleep.cycle NEVER invokes a medium/high-risk tool (spy registry)
  - sleep.cycle is idempotent per night (unless force=true)
  - sleep.dream forges a proposal from synthetic failure history (>=3)
  - sleep.dream is honest when there is nothing to dream about
  - sleep.review returns a stored summary / honest error for missing date
  - risk table additions are all low risk
"""
from __future__ import annotations

import json
import sys
import time

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.sleep_pack as sp
from jarvis.agent import policy
from jarvis.agent.audit import AuditLog
from jarvis.tools.base import Registry, Tool


@pytest.fixture(autouse=True)
def _risk_table(tmpdb, monkeypatch):
    """Mirror production wiring: every pack's RISK_TABLE_ADDITIONS applied.

    Without these, the real PolicyEngine default-denies the stub
    collaborators (unknown tool -> deny), which is also what would happen
    in production if the parent forgot the wiring.
    """
    from jarvis.tools.builtin import autopilot_pack, inbox_pack, mind_pack
    for additions in (sp.RISK_TABLE_ADDITIONS,
                      autopilot_pack.RISK_TABLE_ADDITIONS,
                      inbox_pack.RISK_TABLE_ADDITIONS,
                      mind_pack.RISK_TABLE_ADDITIONS):
        for k, v in additions.items():
            monkeypatch.setitem(policy.RISK_TABLE, k, v)
    # Test-only high-risk tool, to prove the sleep gate refuses it.
    monkeypatch.setitem(policy.RISK_TABLE, "test.nuke", ("high", False))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SLEEP_DB", str(tmp_path / "sleep.db"))
    monkeypatch.setenv("JARVIS_SLEEP_AUDIT_DB", str(tmp_path / "jarvis.db"))
    monkeypatch.setattr(sp, "_AGENT", None)
    yield tmp_path


def _stub(name, risk, calls, reply):
    def h(a):
        calls.append(name)
        return reply() if callable(reply) else reply

    return Tool(name=name, description=f"stub {name}", schema={},
                handler=h, risk=risk, needs_network=False)


class _FakeAgent:
    def __init__(self, registry):
        self.registry = registry


@pytest.fixture
def live(tmpdb):
    """REAL Registry + sleep_pack + stub collaborators, fake agent bound."""
    calls: list = []
    proposes: list = []
    reg = Registry()
    sp.register(reg)

    def _propose_reply(a):
        calls.append("autopilot.propose")
        proposes.append(a)
        return {"ok": True, "id": f"ap_{len(proposes)}",
                "goal": a.get("goal"), "steps": len(a.get("steps", [])),
                "status": "pending"}

    reg.register(Tool(name="autopilot.propose",
                      description="stub propose", schema={},
                      handler=_propose_reply, risk="low", needs_network=False))
    reg.register(_stub("memory.routines", "low", calls, {
        "proposals": [{
            "tool": "inbox.triage", "days_seen": 5, "window_days": 14,
            "tod_minutes": 480,
            "pattern": "'inbox.triage' used on 5 of the last 14 days at ~08:00",
            "suggestion": "Create a standing rule: run 'inbox.triage' daily at ~08:00.",
        }]}))
    reg.register(_stub("calendar.read", "low", calls, {
        "events": [{"id": 1, "title": "Dentist",
                    "start_iso": "2026-09-28T10:00:00",
                    "end_iso": "2026-09-28T11:00:00", "notes": None}],
        "count": 1}))
    reg.register(_stub("inbox.triage", "low", calls, {
        "messages": [{"id": "gmail:1", "subject_or_first_line": "Invoice due",
                      "from": "accounts@example.com", "urgency": 9,
                      "snippet": "pay by Friday"}],
        "channels": {"gmail": "ok (1)"}}))
    # Medium + high risk collaborators: the cycle must NEVER invoke these.
    reg.register(_stub("memory.consolidate", "medium", calls,
                       {"before": 10, "after": 8}))
    reg.register(_stub("test.nuke", "high", calls, {"boom": True}))
    sp.bind_agent(_FakeAgent(reg))
    return {"reg": reg, "calls": calls, "proposes": proposes}


def _seed_audit(db_path, rows):
    """rows: [(actor, tool, args, result_summary, risk)] — ts = now."""
    al = AuditLog(str(db_path))
    now = int(time.time())
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "INSERT INTO audit_log(ts,actor,tool,args_json,result_summary,risk)"
            " VALUES(?,?,?,?,?,?)",
            [(now, actor, tool, json.dumps(args), summary, risk)
             for actor, tool, args, summary, risk in rows])
        conn.commit()
    finally:
        conn.close()
    return al


# ---------------------------------------------------------------------------
# sleep.cycle
# ---------------------------------------------------------------------------
def test_cycle_composes_all_steps(live, tmpdb):
    _seed_audit(tmpdb / "jarvis.db", [
        ("agent", "weather.now", {"city": "Delhi"}, "ok", "low"),
        ("agent", "weather.now", {"city": "Delhi"}, "ok", "low"),
        ("agent", "inbox.triage", {"limit": 10}, "ok", "low"),
        ("agent", "calendar.read", {"days": 1}, "ValueError: bad date", "low"),
        ("agent", "mail.send", {"to": "x"}, "awaiting confirmation", "high"),
    ])
    out = sp.sleep_cycle_handler({})
    assert out.get("ok") is True, out
    assert out.get("stored") is True
    steps = out["steps"]

    # Step 1: consolidate NOT executed (medium risk) — queued instead.
    cons = steps["consolidate"]
    assert cons["executed"] is False
    assert cons["queued_proposal"]["queued"] is True
    assert "memory.consolidate" not in live["calls"]

    # Step 2: day summary reflects the seeded audit rows.
    assert steps["day_summary"]["tool_calls"] == 5
    assert steps["day_summary"]["failures"] == 1

    # Step 3: briefing queued via autopilot.propose.
    br = steps["briefing"]
    assert br["queued_proposal"]["queued"] is True
    assert br["calendar_ok"] is True and br["inbox_ok"] is True
    goals = [p["goal"] for p in live["proposes"]]
    assert any("Morning briefing" in g for g in goals)
    briefing_goal = next(g for g in goals if "Morning briefing" in g)
    assert "Dentist" in briefing_goal  # calendar event made the draft
    assert "Invoice due" in briefing_goal  # inbox item made the draft

    # Step 4: routine proposal queued.
    assert steps["routines"]["detected"] == 1
    assert any("routine" in g.lower() for g in goals)
    routine_steps = [p["steps"] for p in live["proposes"]
                     if "routine" in p["goal"].lower()][0]
    assert routine_steps[0]["tool"] == "workers.spawn"

    # Night persisted; review can read it back.
    rev = sp.sleep_review_handler({"date": out["date"]})
    assert "error" not in rev
    assert rev["summary"]["summary"]["totals"]["tool_calls"] == 5


def test_cycle_never_invokes_medium_or_high_risk_tools(live, tmpdb):
    _seed_audit(tmpdb / "jarvis.db", [
        ("agent", "weather.now", {}, "ok", "low"),
    ])
    out = sp.sleep_cycle_handler({})
    assert out.get("ok") is True, out
    reg = live["reg"]
    for name in live["calls"]:
        risk = reg.tools[name].risk
        assert risk == "low", f"sleep.cycle invoked {risk}-risk tool {name!r}"
    assert "memory.consolidate" not in live["calls"]
    assert "test.nuke" not in live["calls"]


def test_regcall_refuses_unknown_risk(live):
    # Unknown tool names fail closed: refused without invocation.
    res = sp._regcall("definitely.not_registered", {})
    assert res.get("skipped") is True
    assert "definitely.not_registered" not in live["calls"]


def test_cycle_idempotent_per_night(live, tmpdb):
    _seed_audit(tmpdb / "jarvis.db", [("agent", "weather.now", {}, "ok", "low")])
    first = sp.sleep_cycle_handler({})
    assert first.get("ok") is True
    second = sp.sleep_cycle_handler({})
    assert second.get("already_ran") is True
    assert second["date"] == first["date"]
    # force=true re-runs.
    third = sp.sleep_cycle_handler({"force": True})
    assert third.get("ok") is True


def test_cycle_survives_missing_collaborators(tmpdb):
    # Agent bound but NO collaborators registered: every step degrades
    # honestly instead of raising.
    reg = Registry()
    sp.register(reg)
    sp.bind_agent(_FakeAgent(reg))
    out = sp.sleep_cycle_handler({})
    assert out.get("ok") is True, out
    assert out["steps"]["consolidate"]["executed"] is False
    assert out["steps"]["day_summary"]["audit_available"] is False


# ---------------------------------------------------------------------------
# sleep.dream
# ---------------------------------------------------------------------------
def _dream_live(tmpdb):
    calls: list = []
    proposes: list = []
    reg = Registry()
    sp.register(reg)

    def _propose_reply(a):
        calls.append("autopilot.propose")
        proposes.append(a)
        return {"ok": True, "id": f"ap_{len(proposes)}", "status": "pending"}

    reg.register(Tool(name="autopilot.propose", description="stub",
                      schema={}, handler=_propose_reply,
                      risk="low", needs_network=False))
    sp.bind_agent(_FakeAgent(reg))
    return calls, proposes


def test_dream_forges_proposal_from_repeated_failures(tmpdb):
    calls, proposes = _dream_live(tmpdb)
    rows = [("agent", "weather.now", {"city": "Xyz"},
             "ValueError: bad location 'Xyz'", "low")] * 4
    rows += [("agent", "news.headlines", {}, "ok", "low"),
             ("agent", "calendar.read", {}, "ValueError: once", "low")]
    _seed_audit(tmpdb / "jarvis.db", rows)
    out = sp.sleep_dream_handler({})
    assert "error" not in out, out
    assert out["patterns_found"] == 1
    dream = out["dreams"][0]
    assert dream["dreamed"] is True
    assert dream["pattern"]["tool"] == "weather.now"
    assert dream["pattern"]["count"] == 4
    spec = dream["spec"]
    assert spec["name"].startswith("dream_")
    assert dream["queued_proposal"]["queued"] is True
    # The queued proposal would forge the spec on morning approval.
    assert len(proposes) == 1
    step = proposes[0]["steps"][0]
    assert step["tool"] == "tools.forge"
    assert step["args"]["name"] == spec["name"]
    assert "3" in proposes[0]["goal"] or "4" in proposes[0]["goal"]


def test_dream_detects_thrash_without_failures(tmpdb):
    calls, proposes = _dream_live(tmpdb)
    rows = [("agent", "inbox.triage", {"limit": 10}, "ok", "low")] * 3
    _seed_audit(tmpdb / "jarvis.db", rows)
    out = sp.sleep_dream_handler({})
    assert out["patterns_found"] == 1
    assert out["dreams"][0]["pattern"]["kind"] == "repeat"
    assert out["dreams"][0]["dreamed"] is True


def test_dream_honest_when_nothing_found(tmpdb):
    calls, proposes = _dream_live(tmpdb)
    _seed_audit(tmpdb / "jarvis.db", [
        ("agent", "weather.now", {}, "ValueError: one-off", "low"),
        ("agent", "news.headlines", {}, "RuntimeError: one-off", "low"),
        ("agent", "calendar.read", {}, "ok", "low"),
    ])
    out = sp.sleep_dream_handler({})
    assert out["dreams"] == []
    assert out["patterns_found"] == 0
    assert "nothing" in out["message"].lower()
    assert proposes == []  # no fake dreams queued


def test_dream_honest_without_audit_db(tmpdb):
    _dream_live(tmpdb)
    (tmpdb / "jarvis.db").unlink(missing_ok=True)
    out = sp.sleep_dream_handler({})
    # Either an honest error or an empty honest result — never fabricated.
    assert ("error" in out) or (out.get("dreams") == [])


# ---------------------------------------------------------------------------
# sleep.review
# ---------------------------------------------------------------------------
def test_review_missing_date_is_honest(tmpdb):
    out = sp.sleep_review_handler({"date": "2020-01-01"})
    assert "error" in out
    assert "2020-01-01" in out["error"]


def test_review_rejects_bad_date(tmpdb):
    for bad in ("yesterday", "2026-13-99", "", "2026/09/27"):
        out = sp.sleep_review_handler({"date": bad})
        assert "error" in out, bad


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_risk_table_additions_all_low():
    assert sp.RISK_TABLE_ADDITIONS == {
        "sleep.cycle": ("low", False),
        "sleep.review": ("low", False),
        "sleep.dream": ("low", False),
    }
    names = [d["name"] for d in sp.TOOL_DEFS]
    assert names == ["sleep.cycle", "sleep.review", "sleep.dream"]
    assert all(d["risk"] == "low" for d in sp.TOOL_DEFS)


def test_register_wires_three_tools():
    reg = Registry()
    sp.register(reg)
    for name in ("sleep.cycle", "sleep.review", "sleep.dream"):
        assert name in reg.tools
        assert reg.tools[name].risk == "low"
