"""Offline tests for the Autopilot approval-queue pack (Phase 11).

Uses a tmp sqlite DB via the JARVIS_AUTOPILOT_DB env var; stubs the bound
agent with a fake registry that records calls (never touches real tools);
defensive kill-switch and permissions-pack seams are monkeypatched.
Handlers are dict -> dict and never raise.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import jarvis.tools.builtin.autopilot_pack as ap
from jarvis.tools.base import Registry


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db = str(tmp_path / "autopilot.db")
    monkeypatch.setenv("JARVIS_AUTOPILOT_DB", db)
    return db


class FakeRegistry:
    """Records every call; mimics base.Registry.call signature."""

    def __init__(self, fail_tools=None):
        self.calls = []          # list of dicts: tool, args, actor, confirmed
        self.fail_tools = set(fail_tools or ())

    def call(self, name, args, actor="agent", audit=None, confirmed=False):
        self.calls.append({"tool": name, "args": args or {},
                           "actor": actor, "confirmed": confirmed})
        if name in self.fail_tools:
            return {"ok": False, "error": f"tool '{name}' boom"}
        return {"ok": True, "result": {"echo": name}}


class FakeAgent:
    def __init__(self, registry):
        self.registry = registry


@pytest.fixture
def fake_agent(monkeypatch):
    reg = FakeRegistry()
    monkeypatch.setattr(ap, "_AGENT", FakeAgent(reg))
    return reg


@pytest.fixture
def no_agent(monkeypatch):
    monkeypatch.setattr(ap, "_AGENT", None)


@pytest.fixture
def allow_all(monkeypatch):
    """permissions_pack stub that allows everything (the real pack is a
    sibling workstream and may not be installed yet)."""
    stub = type("PP", (), {"get_level": staticmethod(lambda tool: "allow")})()
    monkeypatch.setattr(ap, "permissions_pack", stub)
    return stub


def _propose(goal="Test the plan", steps=None):
    steps = steps if steps is not None else [
        {"tool": "notes.add", "args": {"text": "hello"}, "why": "record intent"},
        {"tool": "calc.eval", "args": {"expr": "2+2"}, "why": "verify math"},
    ]
    return ap.propose_handler({"goal": goal, "steps": steps})


# ------------------------------------------------------- propose validation
def test_propose_validates_steps(tmpdb, allow_all):
    r = ap.propose_handler({"goal": "g", "steps": []})
    assert "error" in r and "non-empty" in r["error"]

    r = ap.propose_handler({"goal": "g", "steps": [{"tool": "x"}]})
    assert "error" in r and "why" in r["error"]

    r = ap.propose_handler({"goal": "g", "steps": [{"tool": "", "args": {}, "why": "w"}]})
    assert "error" in r and "non-empty string" in r["error"]

    r = ap.propose_handler({"goal": "g", "steps": [{"tool": "x", "args": "nope", "why": "w"}]})
    assert "error" in r and "args" in r["error"]

    r = ap.propose_handler({"goal": "  ", "steps": [{"tool": "x", "args": {}, "why": "w"}]})
    assert "error" in r and "goal" in r["error"]


def test_propose_returns_id_and_never_executes(tmpdb, fake_agent):
    r = _propose()
    assert r.get("ok") is True
    assert r["id"].startswith("ap_")
    assert r["status"] == "pending"
    assert r["steps"] == 2
    # Proposing must never touch the registry.
    assert fake_agent.calls == []

    q = ap.queue_handler({})
    assert q["count"] == 1
    p = q["pending"][0]
    assert p["id"] == r["id"]
    assert [s["tool"] for s in p["steps"]] == ["notes.add", "calc.eval"]
    assert fake_agent.calls == []


# ------------------------------------------------- approve: order + gating
def test_approve_all_executes_steps_in_order(tmpdb, fake_agent, allow_all):
    r1 = _propose(goal="Plan one")
    r2 = _propose(goal="Plan two", steps=[
        {"tool": "reminders.add", "args": {"text": "x"}, "why": "remind"}])

    res = ap.approve_handler({"id": "all"})
    assert res.get("ok") is True
    assert len(res["approved"]) == 2

    calls = fake_agent.calls
    assert [c["tool"] for c in calls] == ["notes.add", "calc.eval", "reminders.add"]
    for c in calls:
        assert c["actor"] == "autopilot"
        assert c["confirmed"] is True

    for ran in res["approved"]:
        assert ran["status"] == "done"
        assert [s["outcome"] for s in ran["steps"]] == ["ok"] * len(ran["steps"])

    q = ap.queue_handler({})
    assert q["count"] == 0
    st = ap.status_handler({})
    assert st["counts"].get("done") == 2


def test_approve_single_id(tmpdb, fake_agent, allow_all):
    r1 = _propose(goal="Plan one")
    r2 = _propose(goal="Plan two")
    res = ap.approve_handler({"id": r1["id"]})
    assert res.get("ok") is True
    assert res["approved"][0]["id"] == r1["id"]
    assert len(fake_agent.calls) == 2  # only plan one's steps ran
    # Plan two is still pending.
    assert ap.queue_handler({})["count"] == 1


def test_approve_unknown_id_errors(tmpdb, fake_agent, allow_all):
    res = ap.approve_handler({"id": "ap_nonexistent"})
    assert "error" in res and "no such proposal" in res["error"]
    assert fake_agent.calls == []


def test_approve_without_bound_agent_is_honest(tmpdb, no_agent, allow_all):
    r = _propose()
    res = ap.approve_handler({"id": r["id"]})
    assert "error" in res
    assert "not wired to a live agent session" in res["error"]


def test_approve_kill_switch_blocks_everything(tmpdb, fake_agent, allow_all, monkeypatch):
    monkeypatch.setattr(ap, "_kill_switch", lambda: (True, ""))
    r = _propose()
    res = ap.approve_handler({"id": "all"})
    assert "error" in res
    assert "kill switch" in res["error"]
    assert fake_agent.calls == []
    assert ap.queue_handler({})["count"] == 1  # still pending, nothing ran


def test_failed_step_marks_proposal_partial(tmpdb, allow_all, monkeypatch):
    reg = FakeRegistry(fail_tools={"calc.eval"})
    monkeypatch.setattr(ap, "_AGENT", FakeAgent(reg))
    r = _propose()
    res = ap.approve_handler({"id": r["id"]})
    assert res.get("ok") is True
    ran = res["approved"][0]
    assert ran["status"] == "partial"
    assert [s["outcome"] for s in ran["steps"]] == ["ok", "failed"]
    st = ap.status_handler({})
    assert st["counts"].get("partial") == 1
    # Outcomes persisted with per-step detail.
    recent = st["recent"][0]
    assert [o["outcome"] for o in recent["step_outcomes"]] == ["ok", "failed"]


# ------------------------------------------------------- reject stops plan
def test_reject_stops_plan(tmpdb, fake_agent, allow_all):
    r = _propose()
    rej = ap.reject_handler({"id": r["id"], "reason": "user changed their mind"})
    assert rej.get("ok") is True and rej["status"] == "rejected"

    res = ap.approve_handler({"id": r["id"]})
    assert "error" in res and "can never be approved" in res["error"]
    assert fake_agent.calls == []

    # "all" must not pick up the rejected proposal either.
    res = ap.approve_handler({"id": "all"})
    assert res.get("ok") is True and res["approved"] == []
    assert fake_agent.calls == []


def test_reject_requires_reason_and_pending(tmpdb, allow_all):
    r = _propose()
    assert "error" in ap.reject_handler({"id": r["id"]})          # no reason
    assert "error" in ap.reject_handler({"id": "ap_nope", "reason": "x"})
    ap.reject_handler({"id": r["id"], "reason": "nope"})
    assert "error" in ap.reject_handler({"id": r["id"], "reason": "again"})


# ------------------------------------------- deny-level steps are skipped
def test_deny_level_step_is_skipped_not_executed(tmpdb, fake_agent, monkeypatch):
    stub = type("PP", (), {"get_level": staticmethod(
        lambda tool: "deny" if tool == "sms.send" else "allow")})()
    monkeypatch.setattr(ap, "permissions_pack", stub)

    r = _propose(steps=[
        {"tool": "notes.add", "args": {"text": "hi"}, "why": "record"},
        {"tool": "sms.send", "args": {"to": "1", "text": "x"}, "why": "notify"},
        {"tool": "calc.eval", "args": {"expr": "1+1"}, "why": "math"},
    ])
    res = ap.approve_handler({"id": r["id"]})
    assert res.get("ok") is True
    ran = res["approved"][0]
    assert ran["status"] == "partial"  # one step skipped
    assert [s["outcome"] for s in ran["steps"]] == ["ok", "skipped", "ok"]
    skipped = ran["steps"][1]
    assert skipped["detail"] == {"skipped": "denied by permissions grant"}
    # The denied tool was NEVER sent to the registry.
    assert "sms.send" not in [c["tool"] for c in fake_agent.calls]
    assert [c["tool"] for c in fake_agent.calls] == ["notes.add", "calc.eval"]


def test_permission_lookup_timeout_fails_closed(tmpdb, fake_agent, monkeypatch):
    """If permissions_pack.get_level hangs (the real pack deadlocks on its
    own _DB_LOCK), autopilot must skip the step, never hang, never execute."""
    import time as _t

    def _hang(tool):
        _t.sleep(30)
        return "allow"

    stub = type("PP", (), {"get_level": staticmethod(_hang)})()
    monkeypatch.setattr(ap, "permissions_pack", stub)
    monkeypatch.setattr(ap, "_PERMISSION_TIMEOUT_S", 0.05)

    r = _propose(steps=[
        {"tool": "notes.add", "args": {"text": "hi"}, "why": "record"},
        {"tool": "calc.eval", "args": {"expr": "1+1"}, "why": "math"},
    ])
    res = ap.approve_handler({"id": r["id"]})
    assert res.get("ok") is True
    ran = res["approved"][0]
    assert ran["status"] == "partial"
    assert [s["outcome"] for s in ran["steps"]] == ["skipped", "skipped"]
    assert "timed out" in ran["steps"][0]["detail"]["skipped"]
    # Nothing was executed while the permission system was unresponsive.
    assert fake_agent.calls == []


# ------------------------------------------------------------ persistence
def test_rows_survive_a_fresh_connection(tmpdb, allow_all):
    r = _propose()
    conn = sqlite3.connect(tmpdb)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (r["id"],)).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "pending"
    assert [s["tool"] for s in json.loads(row["steps"])] == ["notes.add", "calc.eval"]


# ------------------------------------------------------------- registration
def test_register_and_risk_table(tmpdb):
    reg = Registry()
    ap.register(reg)
    for spec in ap.TOOL_DEFS:
        assert spec["name"] in reg.tools
        assert reg.tools[spec["name"]].risk == spec["risk"]
    assert set(ap.RISK_TABLE_ADDITIONS) == {s["name"] for s in ap.TOOL_DEFS}
    for name, (risk, net) in ap.RISK_TABLE_ADDITIONS.items():
        assert risk in ("low", "medium", "high")
        assert net is False


def test_status_counts_and_recent(tmpdb, fake_agent, allow_all):
    r1 = _propose(goal="will run")
    r2 = _propose(goal="will reject")
    ap.reject_handler({"id": r2["id"], "reason": "no"})
    ap.approve_handler({"id": r1["id"]})
    st = ap.status_handler({})
    assert st["counts"] == {"done": 1, "rejected": 1}
    assert len(st["recent"]) == 2
    by_id = {p["id"]: p for p in st["recent"]}
    assert by_id[r1["id"]]["step_outcomes"][0]["outcome"] == "ok"
    assert "step_outcomes" not in by_id[r2["id"]]
