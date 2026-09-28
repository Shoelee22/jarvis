"""Offline tests for the Phase 18 meetings pack."""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.meetings_pack as mp
from jarvis.tools.base import Registry


@pytest.fixture(scope="module", autouse=True)
def _leave_no_module_trace():
    """Drop only our own pack's sys.modules entry on teardown (see
    test_phase17_projects.py for why the package attribute must stay)."""
    yield
    sys.modules.pop("jarvis.tools.builtin.meetings_pack", None)


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MEETINGS_DB", str(tmp_path / "meetings.db"))
    monkeypatch.setenv("JARVIS_LOOPS_DB", str(tmp_path / "loops.db"))
    yield tmp_path


NOTES = """\
Discussed the Q4 launch timeline.
Action: Priya to send the draft proposal by Friday.
TODO: book the venue for the offsite.
We should also review the budget next week.
- follow-up: send client the revised quote.
Random chatter about lunch.
"""


def _log(notes=NOTES, title="Sync"):
    return mp.log_handler({"title": title, "notes": notes,
                           "attendees": ["Priya", "Raj"]})


class TestLog:
    def test_log_ok(self, tmpdb):
        out = _log()
        assert out["ok"] is True
        assert out["meeting_id"].startswith("mtg_")
        assert out["actions_detected"] == 3  # action/todo/follow-up lines

    def test_log_defaults_date(self, tmpdb):
        out = _log()
        assert len(out["date"]) == 10  # YYYY-MM-DD

    def test_log_bad_date(self, tmpdb):
        out = mp.log_handler({"title": "x", "date": "yesterday",
                              "notes": "hello"})
        assert "error" in out

    def test_log_needs_notes(self, tmpdb):
        assert "error" in mp.log_handler({"title": "x", "notes": "  "})

    def test_list(self, tmpdb):
        _log(title="One")
        _log(title="Two")
        out = mp.list_handler({})
        assert out["ok"] is True
        assert {m["title"] for m in out["meetings"]} == {"One", "Two"}


class TestActions:
    def test_mine_without_register(self, tmpdb):
        mid = _log()["meeting_id"]
        out = mp.actions_handler({"meeting_id": mid})
        assert out["ok"] is True
        assert len(out["mined"]) == 3
        owners = [a["owner"] for a in out["mined"]]
        assert "Priya" in owners
        assert out["registered"] == []
        assert out["stored_actions"] == []

    def test_register_creates_loops(self, tmpdb):
        mid = _log()["meeting_id"]
        out = mp.actions_handler({"meeting_id": mid, "register": True})
        assert out["ok"] is True
        assert len(out["registered"]) == 3
        # loops.track validates the check tool against the live registry /
        # RISK_TABLE; meetings.open is in RISK_TABLE_ADDITIONS but the test
        # process table is only updated on build_registry(). Either outcome
        # is honest as long as it is reported, not fabricated.
        for r in out["registered"]:
            assert ("tracked" in r) and ("loop_id" in r)
        stored = mp.actions_handler({"meeting_id": mid})
        assert len(stored["stored_actions"]) == 3

    def test_register_idempotent(self, tmpdb):
        mid = _log()["meeting_id"]
        mp.actions_handler({"meeting_id": mid, "register": True})
        out = mp.actions_handler({"meeting_id": mid, "register": True})
        assert len(out["registered"]) == 0  # no duplicates

    def test_unknown_meeting(self, tmpdb):
        assert "error" in mp.actions_handler({"meeting_id": "mtg_nope"})

    def test_no_actions_in_notes(self, tmpdb):
        mid = _log(notes="Just chatted about the weather. Nice day.")["meeting_id"]
        out = mp.actions_handler({"meeting_id": mid, "register": True})
        assert out["mined"] == []
        assert out["registered"] == []


class TestOpen:
    def test_open_lists_untracked(self, tmpdb):
        mid = _log()["meeting_id"]
        # register=False -> actions stored? No: stored only on register.
        # Mine + register to store them.
        mp.actions_handler({"meeting_id": mid, "register": True})
        out = mp.open_handler({})
        assert out["ok"] is True
        assert out["count"] == 3
        entry = out["meetings_with_open_actions"][0]
        assert entry["meeting_id"] == mid
        for a in entry["open_actions"]:
            assert a["loop_status"] in ("open", "nudged", "unknown",
                                        "untracked", "missing")

    def test_open_empty(self, tmpdb):
        out = mp.open_handler({})
        assert out["meetings_with_open_actions"] == []


class TestRegistration:
    def test_register_all_four(self):
        reg = Registry()
        mp.register(reg)
        for name in ("meetings.log", "meetings.list", "meetings.actions",
                     "meetings.open"):
            assert name in reg.tools, name
            assert reg.tools[name].risk == "low"
