"""Phase 31 — scheduled autonomy (offline).

Covers: schedule + tick fires once; double tick doesn't double-fire;
not-due doesn't fire; unschedule stops future fires; queued proposals
survive unschedule; invalid every_hours rejected; duplicate name
rejected; bad steps rejected; unknown unschedule errors; registration
contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import autopilot_pack


def _use_db(tmp_path):
    os.environ["JARVIS_AUTOPILOT_DB"] = str(tmp_path / "ap.db")
    autopilot_pack.bind_agent(None)


def _steps():
    return [{"tool": "inbox.triage", "args": {"limit": 10},
             "why": "refresh the inbox"}]


def test_schedule_and_tick_fires_once(tmp_path):
    _use_db(tmp_path)
    r = autopilot_pack.schedule_handler(
        {"name": "morning", "every_hours": 24, "goal": "triage inbox",
         "steps": _steps()})
    assert r.get("ok"), r
    t = autopilot_pack.tick_handler({})
    assert t["count"] == 1, t
    assert t["fired"][0]["schedule"] == "morning"
    q = autopilot_pack.queue_handler({})
    assert q["count"] == 1
    assert q["pending"][0]["goal"].startswith("[schedule:morning]")


def test_tick_is_idempotent(tmp_path):
    _use_db(tmp_path)
    autopilot_pack.schedule_handler(
        {"name": "hourly", "every_hours": 1, "goal": "check",
         "steps": _steps()})
    autopilot_pack.tick_handler({})
    t2 = autopilot_pack.tick_handler({})
    assert t2["count"] == 0
    assert autopilot_pack.queue_handler({})["count"] == 1


def test_not_due_does_not_fire(tmp_path):
    _use_db(tmp_path)
    autopilot_pack.schedule_handler(
        {"name": "weekly", "every_hours": 168, "goal": "review",
         "steps": _steps()})
    # push next_due into the future manually: tick right after scheduling
    # fired already, so a second tick must not fire
    autopilot_pack.tick_handler({})
    assert autopilot_pack.tick_handler({})["count"] == 0


def test_unschedule_stops_fires_keeps_proposals(tmp_path):
    _use_db(tmp_path)
    autopilot_pack.schedule_handler(
        {"name": "x", "every_hours": 1, "goal": "g", "steps": _steps()})
    autopilot_pack.tick_handler({})
    u = autopilot_pack.unschedule_handler({"name": "x"})
    assert u.get("ok"), u
    assert autopilot_pack.queue_handler({})["count"] == 1
    assert autopilot_pack.schedules_handler({})["schedules"] == []


def test_invalid_inputs(tmp_path):
    _use_db(tmp_path)
    assert "error" in autopilot_pack.schedule_handler(
        {"name": "a", "every_hours": 0.5, "goal": "g", "steps": _steps()})
    assert "error" in autopilot_pack.schedule_handler(
        {"name": "", "every_hours": 2, "goal": "g", "steps": _steps()})
    assert "error" in autopilot_pack.schedule_handler(
        {"name": "a", "every_hours": 2, "goal": "", "steps": _steps()})
    assert "error" in autopilot_pack.schedule_handler(
        {"name": "a", "every_hours": 2, "goal": "g", "steps": []})
    ok = autopilot_pack.schedule_handler(
        {"name": "a", "every_hours": 2, "goal": "g", "steps": _steps()})
    assert ok.get("ok")
    dup = autopilot_pack.schedule_handler(
        {"name": "a", "every_hours": 2, "goal": "g", "steps": _steps()})
    assert "error" in dup and "already exists" in dup["error"]
    assert "error" in autopilot_pack.unschedule_handler({"name": "nope"})


def test_schedules_lists_due(tmp_path):
    _use_db(tmp_path)
    autopilot_pack.schedule_handler(
        {"name": "due-now", "every_hours": 1, "goal": "g",
         "steps": _steps()})
    s = autopilot_pack.schedules_handler({})
    assert len(s["schedules"]) == 1
    assert s["schedules"][0]["due"] is True


def test_registration_contract():
    class _FakeReg:
        def __init__(self):
            self.tools = {}

        def register(self, tool):
            self.tools[tool.name] = tool

    reg = _FakeReg()
    autopilot_pack.register(reg)
    for n in ("autopilot.schedule", "autopilot.schedules",
              "autopilot.unschedule", "autopilot.tick"):
        assert n in reg.tools, n
        assert n in autopilot_pack.RISK_TABLE_ADDITIONS, n
