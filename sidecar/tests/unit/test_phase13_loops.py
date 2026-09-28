"""Offline tests for the Phase 13 Prediction + Open Loops pack.

predict.next is exercised against SYNTHETIC history (no audit DB needed):
seeded 9am briefings rank top at 9am; empty history yields low confidences
and an honest thin-signal note. loops.* run against a REAL Registry with
stub check tools and a fake bound agent; the autopilot propose call is
spied via monkeypatch (lazy import path inside _queue_nudge).
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.loops_pack as lp
import jarvis.tools.builtin.autopilot_pack as autopilot_pack
from jarvis.agent import policy
from jarvis.tools.base import Registry, Tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _leave_no_module_trace():
    """Sibling-workstream isolation.

    test_phase13_persona fakes sys.modules["jarvis.tools.builtin.loops_pack"]
    to test persona.followup's lazy registration path. That fake only takes
    effect when the REAL module was never imported: a lazy
    ``from . import loops_pack`` resolves via the parent package's attribute
    first, shadowing a sys.modules-only fake (verified). Since pytest imports
    this file first alphabetically, remove our import trace on teardown so
    their fake technique works regardless of file ordering.
    """
    yield
    sys.modules.pop("jarvis.tools.builtin.loops_pack", None)
    pkg = sys.modules.get("jarvis.tools.builtin")
    if pkg is not None and getattr(pkg, "loops_pack", None) is lp:
        try:
            delattr(pkg, "loops_pack")
        except AttributeError:
            pass


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db = tmp_path / "loops.db"
    monkeypatch.setenv("JARVIS_LOOPS_DB", str(db))
    monkeypatch.setattr(lp, "_AGENT", None)
    yield db


def _at(hour: int, minute: int, days_ago: int) -> int:
    dt = datetime.now().replace(hour=hour, minute=minute, second=0,
                                microsecond=0) - timedelta(days=days_ago)
    return int(dt.timestamp())


def _briefing_history() -> list[dict]:
    """10 days of 09:05 briefings + scattered weather checks + self-tools."""
    hist = []
    for d in range(1, 11):
        hist.append({"tool": "morning.briefing", "ts": _at(9, 5, d)})
    for d, (h, m) in [(1, (14, 20)), (2, (22, 10)), (3, (3, 0)), (5, (18, 45))]:
        hist.append({"tool": "weather.now", "ts": _at(h, m, d)})
    # The machinery's own calls must never be predicted.
    hist.append({"tool": "loops.check", "ts": _at(9, 1, 1)})
    hist.append({"tool": "predict.next", "ts": _at(9, 2, 1)})
    return hist


def _check_tool(calls, resolved: bool):
    def h(a):
        calls.append(("test.inbox_check", dict(a)))
        return {"resolved": resolved, "detail": "synthetic check result"}

    return Tool(name="test.inbox_check",
                description="low-risk stub check tool",
                schema={}, handler=h, risk="low", needs_network=False)


def _danger_tool(calls):
    def h(a):
        calls.append(("test.danger", dict(a)))
        return {"boom": True}

    return Tool(name="test.danger", description="medium-risk stub",
                schema={}, handler=h, risk="medium", needs_network=False)


@pytest.fixture
def live(tmpdb, monkeypatch):
    """REAL Registry + loops_pack + stub tools, fake agent bound."""
    calls: list = []
    reg = Registry()
    lp.register(reg)
    reg.register(_check_tool(calls, resolved=False))
    reg.register(_danger_tool(calls))
    for tool_name, (risk, net) in lp.RISK_TABLE_ADDITIONS.items():
        monkeypatch.setitem(policy.RISK_TABLE, tool_name, (risk, net))
    monkeypatch.setitem(policy.RISK_TABLE, "test.inbox_check", ("low", False))
    monkeypatch.setitem(policy.RISK_TABLE, "test.danger", ("medium", False))
    agent = types.SimpleNamespace(registry=reg)
    lp.bind_agent(agent)
    return reg, calls, agent


def _track(reg, promise="client replied to quote", days_overdue=1):
    deadline = (datetime.now() - timedelta(days=days_overdue)).isoformat(
        timespec="seconds")
    return reg.call("loops.track",
                    {"promise": promise, "deadline": deadline,
                     "check": {"tool": "test.inbox_check",
                               "args": {"filter": "from:client"}}},
                    actor="agent")


# ---------------------------------------------------------------------------
# predict.next
# ---------------------------------------------------------------------------
def test_predict_next_ranks_repeated_9am_briefing_top():
    out = lp.predict_next_handler(
        {"hour": 9, "dow": 2, "history": _briefing_history(),
         "window_days": 14, "n": 5})
    assert "error" not in out
    preds = out["predictions"]
    assert preds, "expected predictions from seeded history"
    assert preds[0]["tool"] == "morning.briefing"
    assert preds[0]["confidence"] > 0.5
    tools = [p["tool"] for p in preds]
    assert "loops.check" not in tools and "predict.next" not in tools
    assert all(0.0 <= p["confidence"] <= 1.0 for p in preds)


def test_predict_next_thin_signal_is_honest():
    out = lp.predict_next_handler({"hour": 9, "dow": 2, "history": []})
    assert "error" not in out
    assert out["predictions"] == []
    assert out["signal"] == "thin"
    assert "thin" in out["note"].lower() or "no " in out["note"].lower()

    sparse = [{"tool": "weather.now", "ts": _at(14, 20, 1)},
              {"tool": "weather.now", "ts": _at(15, 5, 2)}]
    out2 = lp.predict_next_handler({"hour": 9, "dow": 2, "history": sparse})
    assert out2["signal"] == "thin"
    assert all(p["confidence"] < 0.5 for p in out2["predictions"])


def test_predict_next_rejects_bad_args():
    assert "error" in lp.predict_next_handler({"hour": 25})
    assert "error" in lp.predict_next_handler({"history": "nope"})
    assert "error" in lp.predict_next_handler(
        {"history": [{"tool": "x"}]})


# ---------------------------------------------------------------------------
# loops.track -> loops.check -> nudge proposal (spied)
# ---------------------------------------------------------------------------
def test_loops_track_check_fires_nudge_when_overdue_unresolved(
        live, monkeypatch):
    reg, calls, _agent = live
    proposed: list = []

    def spy(payload):
        proposed.append(payload)
        return {"ok": True, "id": "ap_test123", "goal": payload["goal"],
                "steps": len(payload["steps"]), "status": "pending"}

    monkeypatch.setattr(autopilot_pack, "propose_handler", spy)

    tracked = _track(reg)
    assert tracked["ok"] is True
    lid = tracked["result"]["id"]

    # run_check() is the module-level sweeper the workers tick calls.
    out = lp.run_check()
    assert "error" not in out
    assert out["checked"] == 1
    outcome = out["outcomes"][0]
    assert outcome["outcome"] == "nudge_proposed"
    assert outcome["detail"]["proposal_id"] == "ap_test123"
    assert len(proposed) == 1
    assert "client replied to quote" in proposed[0]["goal"]
    assert "follow-up" in proposed[0]["goal"]

    # The check tool ran with its declared args; nothing was auto-sent.
    assert calls and calls[0][0] == "test.inbox_check"
    assert calls[0][1] == {"filter": "from:client"}

    # Second tick: no duplicate nudge.
    out2 = lp.run_check()
    assert out2["outcomes"][0]["outcome"] == "nudge_already_queued"
    assert len(proposed) == 1


def test_loops_check_marks_resolved_quietly(live, monkeypatch):
    reg, calls, _agent = live
    proposed: list = []
    monkeypatch.setattr(autopilot_pack, "propose_handler",
                        lambda payload: proposed.append(payload) or
                        {"ok": True, "id": "ap_x"})
    # Rebind the check stub to report resolved=True this time.
    reg.tools["test.inbox_check"].handler = (
        lambda a: {"resolved": True, "detail": "client replied"})

    tracked = _track(reg, promise="invoice paid")
    lid = tracked["result"]["id"]
    out = lp.run_check()
    outcome = out["outcomes"][0]
    assert outcome["outcome"] == "resolved_quietly"
    assert proposed == [], "resolved loops must not queue a nudge"

    done = reg.call("loops.done", {"id": lid}, actor="agent")
    assert done["result"]["status"] == "done"
    assert "already closed" in done["result"]["note"]


def test_loops_check_skips_not_yet_due(live):
    reg, _calls, _agent = live
    future = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
    tracked = reg.call("loops.track",
                       {"promise": "quarterly review",
                        "deadline": future,
                        "check": {"tool": "test.inbox_check", "args": {}}},
                       actor="agent")
    assert tracked["ok"] is True
    out = lp.run_check()
    assert out["outcomes"][0]["outcome"] == "waiting"


def test_loops_track_refuses_medium_risk_check_tool(live):
    reg, _calls, _agent = live
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    out = reg.call("loops.track",
                   {"promise": "something risky",
                    "deadline": future,
                    "check": {"tool": "test.danger", "args": {}}},
                   actor="agent")
    assert out["ok"] is True  # handler ran; the refusal is in the result
    assert "error" in out["result"]
    assert "medium-risk" in out["result"]["error"]
    assert "refused" in out["result"]["error"]


def test_loops_track_refuses_unknown_check_tool(live):
    reg, _calls, _agent = live
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    out = reg.call("loops.track",
                   {"promise": "mystery check",
                    "deadline": future,
                    "check": {"tool": "nope.missing", "args": {}}},
                   actor="agent")
    assert "error" in out["result"]
    assert "unknown tool" in out["result"]["error"]


def test_loops_track_rejects_bad_deadline(live):
    reg, _calls, _agent = live
    out = reg.call("loops.track",
                   {"promise": "x", "deadline": "not-a-date",
                    "check": {"tool": "test.inbox_check", "args": {}}},
                   actor="agent")
    assert "error" in out["result"]


def test_loops_done_unknown_id_is_honest_error(live):
    reg, _calls, _agent = live
    out = reg.call("loops.done", {"id": "loop_nope"}, actor="agent")
    assert out["ok"] is True
    assert "error" in out["result"]
    assert "no such loop" in out["result"]["error"]


def test_loops_check_handler_wraps_run_check(live):
    reg, _calls, _agent = live
    _track(reg)
    out = reg.call("loops.check", {}, actor="scheduler")
    assert out["ok"] is True
    assert out["result"]["checked"] == 1


# ---------------------------------------------------------------------------
# predict.prepare
# ---------------------------------------------------------------------------
def test_predict_prepare_warms_readonly_steps(live):
    reg, calls, _agent = live
    out = reg.call("predict.prepare",
                   {"prediction": "morning.briefing",
                    "steps": [{"tool": "test.inbox_check",
                               "args": {"filter": "unread"}}]},
                   actor="agent")
    res = out["result"]
    assert "error" not in res
    assert res["prepared_count"] == 1
    assert res["prepared"][0]["ok"] is True
    assert calls and calls[0][0] == "test.inbox_check"


def test_predict_prepare_refuses_medium_risk_step(live):
    reg, calls, _agent = live
    n_before = len(calls)
    out = reg.call("predict.prepare",
                   {"prediction": "morning.briefing",
                    "steps": [{"tool": "test.danger", "args": {}}]},
                   actor="agent")
    res = out["result"]
    assert "error" in res
    assert "medium-risk" in res["error"]
    assert len(calls) == n_before, "fail closed: nothing executed"


def test_predict_prepare_skips_unregistered_tool(live):
    reg, _calls, _agent = live
    out = reg.call("predict.prepare",
                   {"prediction": "morning.briefing",
                    "steps": [{"tool": "nope.not_a_tool", "args": {}}]},
                   actor="agent")
    res = out["result"]
    assert "error" not in res
    assert res["prepared"] == []
    assert any("nope.not_a_tool" in s for s in res["skipped"])


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_registration_contract():
    expected = {"predict.next", "predict.prepare", "loops.track",
                "loops.check", "loops.done"}
    assert {s["name"] for s in lp.TOOL_DEFS} == expected
    assert set(lp.RISK_TABLE_ADDITIONS) == expected
    for name, (risk, net) in lp.RISK_TABLE_ADDITIONS.items():
        assert risk == "low" and net is False
    reg = Registry()
    lp.register(reg)
    assert expected <= set(reg.tools)
