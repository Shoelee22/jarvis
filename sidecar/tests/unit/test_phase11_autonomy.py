"""Offline tests for the Phase 11 Autonomy pack (budget, kill switch, run-until-done).

Uses a tmp sqlite DB via the JARVIS_AUTONOMY_DB env var; binds a REAL
jarvis.agent.core.Agent with a scripted FakeLLM backend so the chunked
sub-Agent loop is exercised end to end (not stubbed).
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.autonomy_pack as ap
from jarvis.agent import policy
from jarvis.agent.audit import AuditLog
from jarvis.agent.core import Agent, FakeLLM
from jarvis.tools.base import Registry, Tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db = tmp_path / "autonomy.db"
    monkeypatch.setenv("JARVIS_AUTONOMY_DB", str(db))
    # Drop the module's cached connection so the new path is picked up.
    old = ap._store_cache["conn"]
    ap._store_cache.update(path=None, conn=None)
    yield db
    if ap._store_cache["conn"] is not None:
        ap._store_cache["conn"].close()
        ap._store_cache.update(path=None, conn=None)
    monkeypatch.setattr(ap, "_AGENT", None)


def _ping_tool():
    return Tool(name="test.ping", description="low-risk test tool",
                schema={}, handler=lambda a: {"pong": True},
                risk="low", needs_network=False)


def _danger_tool():
    return Tool(name="test.danger", description="high-risk test tool",
                schema={}, handler=lambda a: {"boom": "executed"},
                risk="high", needs_network=False)


@pytest.fixture
def live_agent(tmpdb, monkeypatch, tmp_path):
    """A real Agent bound to the pack, with test tools in the policy table."""
    reg = Registry()
    reg.register(_ping_tool())
    reg.register(_danger_tool())
    monkeypatch.setitem(policy.RISK_TABLE, "test.ping", ("low", False))
    monkeypatch.setitem(policy.RISK_TABLE, "test.danger", ("high", False))
    audit = AuditLog(tmp_path / "audit.db")
    agent = Agent(FakeLLM(), reg, audit, memory=None, max_steps=20)
    ap.bind_agent(agent)
    return agent


class RecordingLLM(FakeLLM):
    """FakeLLM that records the messages of every generate() call."""

    def __init__(self, script):
        super().__init__(script=list(script))
        self.seen = []

    def generate(self, messages, tools):
        self.seen.append([m.get("content", "") for m in messages])
        return super().generate(messages, tools)


def _regcall(reg, name, args, actor="user", confirmed=False):
    return reg.call(name, args or {}, actor=actor, confirmed=confirmed)


@pytest.fixture
def full_registry(tmpdb, monkeypatch):
    reg = Registry()
    ap.register(reg)
    reg.register(_ping_tool())
    reg.register(_danger_tool())
    monkeypatch.setitem(policy.RISK_TABLE, "test.ping", ("low", False))
    monkeypatch.setitem(policy.RISK_TABLE, "test.danger", ("high", False))
    for tool_name, (risk, net) in ap.RISK_TABLE_ADDITIONS.items():
        monkeypatch.setitem(policy.RISK_TABLE, tool_name, (risk, net))
    return reg


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
def test_budget_set_and_validation(tmpdb):
    out = ap.budget_handler({"n": 25})
    assert out == {"budget": 25, "window": "60min"}
    assert ap._budget_per_hour() == 25
    assert "error" in ap.budget_handler({"n": 0})
    assert "error" in ap.budget_handler({"n": 1001})
    assert "error" in ap.budget_handler({"n": "lots"})
    # default when n omitted
    assert ap.budget_handler({}) == {"budget": 10, "window": "60min"}


def test_budget_exhaustion(tmpdb):
    ap.budget_handler({"n": 1})
    first = ap.spend(1, what="test")
    assert first == {"ok": True, "remaining": 0}
    second = ap.spend(1, what="test")
    assert second.get("reason") == "budget_exhausted"
    assert "error" in second
    # multi-unit spend against a too-small budget also fails
    ap.budget_handler({"n": 3})
    big = ap.spend(5, what="test")
    assert big.get("reason") == "budget_exhausted"


def test_spend_rolls_off_after_window(tmpdb):
    ap.budget_handler({"n": 1})
    assert ap.spend(1)["ok"] is True
    # Backdate the usage row so it falls outside the 60-minute window.
    old = int(time.time()) - 7200

    def _w(conn):
        conn.execute("UPDATE usage SET ts=?", (old,))

    ap._exec(_w)
    assert ap.spend(1)["ok"] is True


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------
def test_kill_switch_blocks_spend_and_clears(tmpdb):
    assert ap.is_killed() is False
    off = ap.off_handler({})
    assert off["killed"] is True
    assert "autonomy.on" in off["note"]
    assert ap.is_killed() is True
    spent = ap.spend(1, what="test")
    assert spent.get("reason") == "killed"
    on = ap.on_handler({})
    assert on["killed"] is False
    assert ap.is_killed() is False
    assert ap.spend(1, what="test")["ok"] is True


def test_kill_switch_checked_before_budget(tmpdb):
    # Budget fully available, but killed wins.
    ap.budget_handler({"n": 100})
    ap.off_handler({})
    assert ap.spend(1).get("reason") == "killed"
    ap.on_handler({})


def test_run_until_refuses_when_killed(tmpdb, live_agent):
    ap.off_handler({})
    out = ap.run_until_handler({"goal": "do a thing", "max_steps": 5,
                                "checkpoint_every": 2})
    assert out.get("reason") == "killed"
    assert "error" in out


def test_off_is_low_risk_and_on_is_high_risk(tmpdb, full_registry):
    # The whole point: policy must allow autonomy.off without confirmation.
    assert policy.PolicyEngine().decide("autonomy.off", {})["action"] == "allow"
    assert policy.PolicyEngine().decide("autonomy.on", {})["action"] == "confirm"
    # And through the real registry path (no confirmed flag needed for off).
    res = full_registry.call("autonomy.off", {}, actor="user")
    assert res["ok"] is True and res["result"]["killed"] is True
    res = full_registry.call("autonomy.on", {}, actor="user")
    assert res["ok"] is False and res.get("needs_confirmation") is True
    assert ap.is_killed() is True  # off took effect even though on was blocked


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def test_status_shape(tmpdb):
    ap.budget_handler({"n": 7})
    ap.spend(2, what="probe")
    st = ap.status_handler({})
    assert st["killed"] is False
    assert st["budget_per_hour"] == 7
    assert st["used_last_hour"] == 2
    assert st["remaining"] == 5
    assert st["autonomous_actions_today"] == 2
    assert isinstance(st["recent"], list) and len(st["recent"]) == 2
    assert st["recent"][0]["what"] == "probe"


# ---------------------------------------------------------------------------
# run_until chunking / checkpoints
# ---------------------------------------------------------------------------
def _script_turn(tool=None, text=""):
    if tool:
        return {"thought": "act", "tool_calls": [{"name": tool, "args": {}}],
                "text": ""}
    return {"thought": "done", "tool_calls": [], "text": text}


def test_run_until_checkpoints_and_continues(tmpdb, live_agent):
    llm = RecordingLLM([
        _script_turn("test.ping"), _script_turn("test.ping"),
        _script_turn("test.ping"), _script_turn("test.ping"),
        _script_turn("test.ping"), _script_turn(text="all done"),
    ])
    live_agent.llm = llm

    first = ap.run_until_handler({"goal": "ping things", "max_steps": 10,
                                  "checkpoint_every": 4})
    assert first["done"] is False
    assert first["checkpoint"] == 1
    assert first["steps_used"] == 4  # 4 tool calls consumed in the 4-step chunk
    run_id = first["run_id"]
    # Run state persisted.
    state = ap._load_run(run_id)
    assert state is not None and state["status"] == "checkpoint"
    assert state["goal"] == "ping things" and state["steps_used"] == 4
    # Budget was spent: 4 tool calls.
    assert ap._used_last_hour() == 4

    second = ap.run_continue_handler({"run_id": run_id, "checkpoint_every": 4})
    assert second["done"] is True
    assert second["reply"] == "all done"
    assert second["steps_used"] == 5
    # Rolling summary was carried into the second chunk's prompt.
    assert any("Goal: ping things" in "".join(seen) and
               "Progress so far" in "".join(seen)
               for seen in llm.seen[4:])
    assert ap._load_run(run_id)["status"] == "done"


def test_run_until_finishes_within_one_chunk(tmpdb, live_agent):
    live_agent.llm = FakeLLM([_script_turn(text="immediately done")])
    out = ap.run_until_handler({"goal": "trivial", "max_steps": 10,
                                "checkpoint_every": 5})
    assert out["done"] is True
    assert out["reply"] == "immediately done"
    assert out["steps_used"] == 0
    assert ap._load_run(out["run_id"])["status"] == "done"


def test_run_until_awaits_confirmation_then_resumes(tmpdb, live_agent):
    live_agent.llm = FakeLLM([
        _script_turn("test.danger"),                      # chunk 1: hits policy
        _script_turn("test.danger"),                      # chunk 2: retried, confirmed
        _script_turn(text="danger handled"),              # chunk 2: finishes
    ])
    first = ap.run_until_handler({"goal": "do danger", "max_steps": 10,
                                  "checkpoint_every": 2})
    assert first["done"] is False
    assert first["awaiting_confirmation"] == "test.danger"
    assert first["confirm_text"]  # the policy's confirm text
    run_id = first["run_id"]
    assert ap._load_run(run_id)["status"] == "awaiting_confirmation"

    # Without confirm=true the chunk pauses again on the same tool.
    again = ap.run_continue_handler({"run_id": run_id})
    assert again["done"] is False
    assert again["awaiting_confirmation"] == "test.danger"

    # With confirm=true the pre-confirmed tool executes and the run finishes.
    done = ap.run_continue_handler({"run_id": run_id, "confirm": True})
    assert done["done"] is True
    assert done["reply"] == "danger handled"


def test_run_until_no_agent_bound(tmpdb):
    ap.bind_agent(None)
    out = ap.run_until_handler({"goal": "x"})
    assert "error" in out and "bind_agent" in out["error"]


def test_run_until_budget_halt_mid_run(tmpdb, live_agent):
    ap.budget_handler({"n": 1})
    live_agent.llm = FakeLLM([
        _script_turn("test.ping"), _script_turn("test.ping"),
        _script_turn(text="never reached"),
    ])
    out = ap.run_until_handler({"goal": "ping twice", "max_steps": 10,
                                "checkpoint_every": 5})
    assert out["done"] is False
    assert out["reason"] == "budget_exhausted"
    assert "error" in out


def test_run_continue_unknown_and_finished(tmpdb, live_agent):
    assert "error" in ap.run_continue_handler({"run_id": "nope"})
    assert "error" in ap.run_continue_handler({})  # missing run_id
    live_agent.llm = FakeLLM([_script_turn(text="done")])
    out = ap.run_until_handler({"goal": "quick", "checkpoint_every": 5})
    assert out["done"] is True
    assert "already done" in ap.run_continue_handler(
        {"run_id": out["run_id"]})["error"]


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_tool_defs_and_risk_table(tmpdb):
    names = {t["name"] for t in ap.TOOL_DEFS}
    assert names == {"autonomy.budget", "autonomy.status", "autonomy.off",
                     "autonomy.on", "agent.run_until", "agent.run_continue"}
    assert set(ap.RISK_TABLE_ADDITIONS) == names
    risks = {t["name"]: t["risk"] for t in ap.TOOL_DEFS}
    assert risks["autonomy.off"] == "low"    # kill switch never blocked
    assert risks["autonomy.on"] == "high"    # re-enable needs confirmation
    assert risks["autonomy.budget"] == "medium"
    assert risks["agent.run_continue"] == "medium"
    for spec in ap.TOOL_DEFS:
        assert spec["handler"] and spec["description"] and "schema" in spec


def test_register_wires_six_tools(tmpdb):
    reg = Registry()
    ap.register(reg)
    for name in ("autonomy.budget", "autonomy.status", "autonomy.off",
                 "autonomy.on", "agent.run_until", "agent.run_continue"):
        assert name in reg.tools
