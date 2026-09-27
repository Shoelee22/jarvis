"""Offline tests for the Phase 18 schedules pack (stdlib cron math included)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.schedules_pack as sp
from jarvis.tools.base import Registry


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SCHEDULES_DB", str(tmp_path / "schedules.db"))
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path / "data"))
    yield tmp_path


# ---------------------------------------------------------------------------
# natural-language parsing
# ---------------------------------------------------------------------------

class TestParse:
    @pytest.mark.parametrize("text,cron", [
        ("every weekday at 9am", "0 9 * * 1-5"),
        ("every weekend at 10:30am", "30 10 * * 0,6"),
        ("every day at 6pm", "0 18 * * *"),
        ("daily at 9am", "0 9 * * *"),
        ("every 2 hours", "0 */2 * * *"),
        ("every 15 minutes", "*/15 * * * *"),
        ("hourly", "0 * * * *"),
        ("mondays and thursdays at 6pm", "0 18 * * 1,4"),
        ("every monday at 9:30am", "30 9 * * 1"),
        ("on the 1st of each month at 8am", "0 8 1 * *"),
        ("monthly on the 15th at 9am", "0 9 15 * *"),
    ])
    def test_parse_ok(self, text, cron):
        out = sp.parse_handler({"text": text})
        assert out.get("ok") is True, out
        assert out["cron"] == cron, (text, out["cron"])
        assert out["summary"]

    def test_parse_unparseable(self):
        out = sp.parse_handler({"text": "whenever I feel like it"})
        assert "error" in out

    def test_parse_empty(self):
        assert "error" in sp.parse_handler({"text": ""})

    def test_parse_bad_time(self):
        out = sp.parse_handler({"text": "every day at 25pm"})
        assert "error" in out


# ---------------------------------------------------------------------------
# cron next-run math
# ---------------------------------------------------------------------------

class TestCronMath:
    def test_next_daily(self):
        after = datetime(2026, 9, 27, 10, 0)
        nxt, err = sp._cron_next(after, "0 9 * * *")
        assert err is None
        assert nxt == datetime(2026, 9, 28, 9, 0)

    def test_next_weekly(self):
        # Sunday 2026-09-27 -> next Monday 9am
        after = datetime(2026, 9, 27, 10, 0)
        nxt, err = sp._cron_next(after, "0 9 * * 1")
        assert err is None
        assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 9, 28, 9)

    def test_next_interval(self):
        after = datetime(2026, 9, 27, 10, 7)
        nxt, err = sp._cron_next(after, "*/15 * * * *")
        assert err is None
        assert (nxt.hour, nxt.minute) == (10, 15)

    def test_next_bad_cron(self):
        nxt, err = sp._cron_next(datetime(2026, 9, 27), "not a cron")
        assert nxt is None and err

    def test_next_impossible(self):
        nxt, err = sp._cron_next(datetime(2026, 9, 27), "0 0 31 2 *")
        assert nxt is None and "never match" in err

    def test_prev(self):
        before = datetime(2026, 9, 27, 10, 0)
        prev, err = sp._cron_prev(before, "0 9 * * *")
        assert err is None
        assert (prev.day, prev.hour) == (27, 9)

    def test_next_handler(self):
        out = sp.next_handler({"cron": "0 9 * * *", "n": 3})
        assert out["ok"] is True
        assert len(out["next_runs"]) == 3
        # strictly increasing
        assert out["next_runs"] == sorted(out["next_runs"])

    def test_next_handler_bad(self):
        assert "error" in sp.next_handler({"cron": "junk"})


# ---------------------------------------------------------------------------
# quiet hours
# ---------------------------------------------------------------------------

class TestQuietHours:
    def test_set_and_get(self, tmpdb):
        out = sp.quiet_hours_handler({"start": "22:00", "end": "07:00"})
        assert out["ok"] is True
        got = sp.quiet_hours_handler({})
        assert got["quiet_start"] == "22:00"
        assert got["quiet_end"] == "07:00"

    def test_bad_format(self, tmpdb):
        out = sp.quiet_hours_handler({"start": "10pm", "end": "7am"})
        assert "error" in out

    def test_in_quiet_hours_wrap(self, tmpdb):
        sp.quiet_hours_handler({"start": "22:00", "end": "07:00"})
        night = datetime(2026, 9, 27, 23, 30)
        quiet, window = sp._in_quiet_hours(night)
        assert quiet is True and window == "22:00–07:00"
        day = datetime(2026, 9, 27, 12, 0)
        quiet, _ = sp._in_quiet_hours(day)
        assert quiet is False

    def test_due_respects_quiet(self, tmpdb):
        sp.quiet_hours_handler({"start": "00:00", "end": "23:59"})
        out = sp.due_handler({})
        assert out["ok"] is True
        assert out["quiet"] is True
        assert out["due"] == []


# ---------------------------------------------------------------------------
# run history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_record_and_history(self, tmpdb):
        sp.record_run_handler({"routine": "morning_brief", "status": "ok"})
        sp.record_run_handler({"routine": "morning_brief", "status": "failed",
                               "detail": "network down"})
        sp.record_run_handler({"routine": "morning_brief", "status": "ok"})
        out = sp.history_handler({"routine": "morning_brief"})
        assert out["ok"] is True
        assert len(out["runs"]) == 3
        assert out["stats"]["total_runs"] == 3
        assert out["stats"]["success_rate"] == pytest.approx(2 / 3, abs=1e-3)

    def test_history_empty(self, tmpdb):
        out = sp.history_handler({"routine": "never_ran"})
        assert out["runs"] == []
        assert out["stats"]["success_rate"] is None

    def test_record_bad_status(self, tmpdb):
        out = sp.record_run_handler({"routine": "x", "status": "maybe"})
        assert "error" in out


# ---------------------------------------------------------------------------
# routine creation via the automation engine
# ---------------------------------------------------------------------------

class TestCreateRoutine:
    def test_create_routine(self, tmpdb):
        out = sp.create_routine_handler({
            "name": "test_brief",
            "schedule": "every weekday at 9am",
            "steps": [{"tool": "calc.eval", "args": {"expression": "1+1"}}],
        })
        assert out.get("ok") is True, out
        assert out["cron"] == "0 9 * * 1-5"
        from jarvis.tools.dynamic.automation import AutomationEngine
        eng = AutomationEngine(data_dir=tmpdb / "data")
        got = eng.get("test_brief")
        assert got is not None
        assert got["cron"] == "0 9 * * 1-5"

    def test_create_routine_bad_schedule(self, tmpdb):
        out = sp.create_routine_handler({
            "name": "bad_sched", "schedule": "whenever",
            "steps": [{"tool": "calc.eval", "args": {"expression": "1"}}]})
        assert "error" in out

    def test_due_lists_routine(self, tmpdb):
        # routine scheduled every minute -> due inside a wide window
        from jarvis.tools.dynamic.automation import AutomationEngine
        eng = AutomationEngine(data_dir=tmpdb / "data")
        eng.create("every_min", trigger="cron", cron="* * * * *",
                   steps=[{"tool": "calc.eval", "args": {"expression": "1"}}])
        out = sp.due_handler({"window_minutes": 10})
        assert out["ok"] is True
        assert any(d["routine"] == "every_min" for d in out["due"])


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_all_seven(self):
        reg = Registry()
        sp.register(reg)
        for name in ("schedules.parse", "schedules.next", "schedules.quiet_hours",
                     "schedules.create_routine", "schedules.record_run",
                     "schedules.history", "schedules.due"):
            assert name in reg.tools, name
        assert reg.tools["schedules.create_routine"].risk == "medium"
