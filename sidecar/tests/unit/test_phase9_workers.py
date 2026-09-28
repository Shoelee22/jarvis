"""Offline tests for the Background Workers pack (Phase 9).

Uses a tmp sqlite DB via the JARVIS_WORKERS_DB env var; monkeypatches the
autonomy opt-in reader instead of writing real config files; uses a FakeAgent
with a real delegate() signature (dict -> dict, never raising).
"""
from __future__ import annotations

import os
import time

import pytest

import jarvis.tools.builtin.workers_pack as wp
from jarvis.tools.base import Registry


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WORKERS_DB", str(tmp_path / "workers.db"))
    return tmp_path


@pytest.fixture
def opt_in(monkeypatch):
    monkeypatch.setattr(wp, "_autonomy_opt_in", lambda: True)


@pytest.fixture
def opt_out(monkeypatch):
    monkeypatch.setattr(wp, "_autonomy_opt_in", lambda: False)


class FakeAgent:
    """Mimics Agent.delegate(role_name, task, max_steps) -> dict."""

    def __init__(self):
        self.calls = []

    def delegate(self, role_name, task, max_steps=8):
        self.calls.append({"role": role_name, "task": task,
                           "max_steps": max_steps})
        return {"ok": True, "role": role_name, "task": task,
                "reply": "Morning brief: 3 priority emails.", "done": True,
                "steps_used": 2, "timeline": []}


@pytest.fixture
def fake_agent(monkeypatch):
    fa = FakeAgent()
    monkeypatch.setattr(wp, "_AGENT", fa)
    yield fa
    monkeypatch.setattr(wp, "_AGENT", None)


def _spawned():
    return wp.spawn_handler({"name": "brief_me", "goal": "Check inbox, brief me.",
                             "schedule": "interval:3600", "role": "planner"})


# ---------------------------------------------------------------- opt-in gate
def test_spawn_blocked_without_opt_in(tmpdb, opt_out):
    res = _spawned()
    assert "error" in res
    assert "autonomy_opt_in" in res["error"]
    assert "tools_config.phase9.yaml" in res["error"]


def test_spawn_works_with_opt_in(tmpdb, opt_in):
    res = _spawned()
    assert res["ok"] is True
    assert res["name"] == "brief_me"
    assert res["parsed"] == {"kind": "interval", "seconds": 3600}
    listed = wp.list_handler({})["workers"]
    assert [w["name"] for w in listed] == ["brief_me"]
    assert listed[0]["status"] == "active"
    assert listed[0]["run_count"] == 0


# ------------------------------------------------------------- validation
def test_spawn_invalid_name(tmpdb, opt_in):
    res = wp.spawn_handler({"name": "Bad Name!", "goal": "g", "schedule": "interval:60"})
    assert "error" in res and "bad name" in res["error"]
    res = wp.spawn_handler({"name": "ab", "goal": "g", "schedule": "interval:60"})
    assert "error" in res  # too short


def test_spawn_unknown_role(tmpdb, opt_in):
    res = wp.spawn_handler({"name": "bad_role", "goal": "g",
                            "schedule": "interval:60", "role": "ninja"})
    assert "error" in res and "unknown role" in res["error"]


@pytest.mark.parametrize("bad", ["every day", "interval:abc", "interval:0",
                                 "interval:-5", "daily@25:00", "daily@8:3",
                                 "hourly", ""])
def test_spawn_invalid_schedule(tmpdb, opt_in, bad):
    res = wp.spawn_handler({"name": "sched_test", "goal": "g", "schedule": bad})
    assert "error" in res
    assert "interval:<seconds>" in res["error"] and "daily@HH:MM" in res["error"]


def test_spawn_valid_daily_schedule(tmpdb, opt_in):
    res = wp.spawn_handler({"name": "morning_brief", "goal": "Brief me.",
                            "schedule": "daily@08:30"})
    assert res["ok"] is True
    assert res["parsed"] == {"kind": "daily", "hour": 8, "minute": 30}


def test_spawn_duplicate_name(tmpdb, opt_in):
    assert _spawned()["ok"] is True
    res = _spawned()
    assert "error" in res and "already exists" in res["error"]


def test_spawn_default_role_planner(tmpdb, opt_in):
    res = wp.spawn_handler({"name": "no_role", "goal": "g", "schedule": "interval:10"})
    assert res["role"] == "planner"


# ------------------------------------------------------------------- tick
def test_tick_runs_due_worker(tmpdb, opt_in, fake_agent):
    _spawned()  # last_run None -> due immediately
    res = wp.tick_handler({})
    assert res["ticked"] == ["brief_me"]
    assert res["skipped"] == []
    assert fake_agent.calls and fake_agent.calls[0]["role"] == "planner"
    assert fake_agent.calls[0]["max_steps"] == 6

    listed = wp.list_handler({})["workers"]
    assert listed[0]["run_count"] == 1
    assert listed[0]["last_run"] is not None

    logs = wp.logs_handler({"name": "brief_me"})["logs"]
    assert len(logs) == 2  # spawn + run
    assert logs[0]["kind"] == "run"
    assert "Morning brief" in logs[0]["text"]


def test_tick_second_immediate_tick_not_due(tmpdb, opt_in, fake_agent):
    _spawned()
    assert wp.tick_handler({})["ticked"] == ["brief_me"]
    res = wp.tick_handler({})
    assert res["ticked"] == []
    assert res["skipped"] == ["brief_me"]
    assert res["skipped_reasons"]["brief_me"] == "not due"
    assert len(fake_agent.calls) == 1  # no second delegate call


def test_tick_without_agent_bound_logs_skip(tmpdb, opt_in):
    # _AGENT is None by default -> honest skip, no crash
    _spawned()
    res = wp.tick_handler({})
    assert res["ticked"] == []
    assert res["skipped"] == ["brief_me"]
    assert res["skipped_reasons"]["brief_me"] == "agent not bound"
    logs = wp.logs_handler({"name": "brief_me"})["logs"]
    assert logs[0]["kind"] == "skip"
    assert "agent not bound" in logs[0]["text"]


def test_tick_honors_due_after_fudged_last_run(tmpdb, opt_in, fake_agent):
    _spawned()
    wp.tick_handler({})
    # fudge last_run 2 hours back -> interval:3600 is due again
    conn = wp._get_store()
    with wp._DB_LOCK:
        conn.execute("UPDATE workers SET last_run=? WHERE name=?",
                     (int(time.time()) - 7200, "brief_me"))
        conn.commit()
    res = wp.tick_handler({})
    assert res["ticked"] == ["brief_me"]
    assert wp.list_handler({})["workers"][0]["run_count"] == 2


# ------------------------------------------------------------ pause/resume
def test_pause_skips_tick_resume_restores(tmpdb, opt_in, fake_agent):
    _spawned()
    assert wp.pause_handler({"name": "brief_me"})["status"] == "paused"
    res = wp.tick_handler({})
    assert res["ticked"] == [] and res["skipped"] == []
    assert fake_agent.calls == []

    assert wp.resume_handler({"name": "brief_me"})["status"] == "active"
    # still not due (interval) -> fudge last_run back so it IS due
    conn = wp._get_store()
    with wp._DB_LOCK:
        conn.execute("UPDATE workers SET last_run=? WHERE name=?",
                     (int(time.time()) - 7200, "brief_me"))
        conn.commit()
    assert wp.tick_handler({})["ticked"] == ["brief_me"]


def test_pause_unknown_worker(tmpdb, opt_in):
    assert "error" in wp.pause_handler({"name": "nope"})
    assert "error" in wp.resume_handler({"name": "nope"})


# ------------------------------------------------------------------- logs
def test_logs_newest_first_and_capped(tmpdb, opt_in):
    _spawned()
    logs = wp.logs_handler({"name": "brief_me"})
    assert logs["name"] == "brief_me"
    assert logs["logs"][0]["kind"] == "spawn"
    limited = wp.logs_handler({"name": "brief_me", "n": 1})
    assert len(limited["logs"]) == 1
    assert "error" in wp.logs_handler({"name": "ghost"})


# ------------------------------------------------------------------- kill
def test_kill_removes_worker_and_logs(tmpdb, opt_in):
    _spawned()
    assert wp.kill_handler({"name": "brief_me"})["killed"] is True
    assert wp.list_handler({})["workers"] == []
    assert "error" in wp.logs_handler({"name": "brief_me"})  # logs gone too
    assert "error" in wp.kill_handler({"name": "brief_me"})   # idempotent-ish


# ------------------------------------------------------------ registration
def test_register_and_risk_table():
    reg = Registry()
    wp.register(reg)
    names = sorted(reg.tools.keys())
    expected = ["workers.kill", "workers.list", "workers.logs",
                "workers.pause", "workers.resume", "workers.spawn", "workers.tick"]
    assert names == expected
    for n in names:
        assert reg.tools[n].risk == wp.RISK_TABLE_ADDITIONS[n][0]
    assert wp.RISK_TABLE_ADDITIONS["workers.spawn"] == ("high", False)
    assert wp.RISK_TABLE_ADDITIONS["workers.tick"] == ("low", False)
    assert wp.RISK_TABLE_ADDITIONS["workers.kill"] == ("high", False)
    assert wp.RISK_TABLE_ADDITIONS["workers.pause"] == ("medium", False)


def test_bind_agent_roundtrip():
    fa = FakeAgent()
    wp.bind_agent(fa)
    assert wp._AGENT is fa
    wp.bind_agent(None)
    assert wp._AGENT is None
