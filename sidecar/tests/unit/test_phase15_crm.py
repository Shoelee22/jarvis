"""Offline tests for the Phase 15 Relationship keeper pack (crm_pack).

Exercised against the REAL handlers with a tmp sqlite CRM DB
(JARVIS_CRM_DB env override).

Covers:
  - people.add / people.note / people.last_contact round-trip
  - silence nudge fires at 21 days without contact (seeded old timestamps)
  - no nudge when contact was recent (yesterday)
  - per-person nudge_after_days override
  - birthday nudge fires 7 days and 1 day before (dates computed from today)
  - ambiguous name returns candidates — never silently picks
  - people.search FTS over names, context, and notes
  - autopilot.propose is called when the pack is bound (fake registry
    double) and skipped honestly when unbound
  - risk table additions are all low risk
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.crm_pack as crm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setattr(crm, "_AGENT", None)
    yield tmp_path


def _add(name, context="", **kw):
    res = crm.people_add_handler({"name": name, "context": context, **kw})
    assert res.get("ok"), f"add failed: {res}"
    return res["id"]


def _seed_contact_days_ago(pid, days):
    """Direct DB write: pretend the last contact happened `days` ago."""
    conn = sqlite3.connect(os.environ["JARVIS_CRM_DB"])
    ts = int(time.time()) - int(days * 86400)
    conn.execute(
        "UPDATE people SET created_at=?, last_contact_ts=?,"
        " last_contact_note=? WHERE id=?",
        (ts, ts, "seeded old contact", pid))
    conn.execute("INSERT INTO notes(person_id, ts, note) VALUES(?,?,?)",
                 (pid, ts, "seeded old contact"))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Core habit: add / note / last_contact round-trip
# ---------------------------------------------------------------------------
def test_add_note_last_contact_roundtrip(tmpdb):
    pid = _add("Priya Nair", "old college friend, now a baker in Kochi")
    assert pid > 0

    note = crm.people_note_handler(
        {"name": "  priya NAIR ", "note": "called — catching up next week"})
    assert note.get("ok"), note
    assert note["name"] == "Priya Nair"
    assert "logged_at" in note

    lc = crm.people_last_contact_handler({"name": "Priya"})
    assert lc.get("ok"), lc
    assert lc["days_ago"] < 1.0
    assert lc["note"] == "called — catching up next week"
    assert len(lc["recent_notes"]) >= 1

    # Birthday parsed out of the free-text context.
    pid2 = _add("Arjun", "gym buddy, birthday 1990-05-12")
    res = crm.people_search_handler({"query": "Arjun"})
    assert res["hits"][0]["birthday"] == "1990-05-12"


def test_last_contact_never_logged(tmpdb):
    _add("Quiet Quietson", "met at a conference")
    lc = crm.people_last_contact_handler({"name": "quiet quietson"})
    assert lc.get("ok")
    assert lc["last_contact"] is None
    assert "no interaction ever logged" in lc["message"]


# ---------------------------------------------------------------------------
# Nudges: silence cadence
# ---------------------------------------------------------------------------
def test_silence_nudge_fires_at_21_days(tmpdb):
    pid = _add("Meera", "designer friend")
    _seed_contact_days_ago(pid, 21)
    out = crm.people_nudges_handler({})
    assert out.get("ok")
    kinds = [n["kind"] for n in out["nudges"]]
    assert "silence" in kinds
    n = next(n for n in out["nudges"] if n["kind"] == "silence")
    assert n["person"] == "Meera"
    assert n["days_since_contact"] >= 21
    assert "Reach out" in n["message"]


def test_silence_nudge_quiet_when_recent(tmpdb):
    pid = _add("Kabir", "neighbour")
    _seed_contact_days_ago(pid, 1)  # talked yesterday
    out = crm.people_nudges_handler({})
    assert out.get("ok")
    assert out["nudges"] == [], f"expected silence, got: {out['nudges']}"


def test_nudge_after_days_override(tmpdb):
    pid = _add("Frequent", "weekly standup buddy", nudge_after_days=5)
    _seed_contact_days_ago(pid, 6)
    out = crm.people_nudges_handler({})
    assert any(n["person"] == "Frequent" and n["kind"] == "silence"
               for n in out["nudges"])
    # ...and at 4 days of silence the same person is still quiet.
    _seed_contact_days_ago(pid, 4)
    out = crm.people_nudges_handler({})
    assert not any(n["person"] == "Frequent" and n["kind"] == "silence"
                   for n in out["nudges"])


# ---------------------------------------------------------------------------
# Nudges: birthdays
# ---------------------------------------------------------------------------
def test_birthday_nudge_7_days_before(tmpdb):
    in7 = date.today() + timedelta(days=7)
    bday = f"1990-{in7.month:02d}-{in7.day:02d}"
    _add("Birthday Gal", "friend", birthday=bday)
    out = crm.people_nudges_handler({})
    n = next((n for n in out["nudges"]
              if n["kind"] == "birthday" and n["person"] == "Birthday Gal"),
             None)
    assert n is not None, f"no birthday nudge in {out['nudges']}"
    assert n["days_until"] == 7
    assert "birthday" in n["message"]


def test_birthday_nudge_1_day_before(tmpdb):
    tmr = date.today() + timedelta(days=1)
    bday = f"1985-{tmr.month:02d}-{tmr.day:02d}"
    _add("Birthday Dude", "cousin", birthday=bday)
    out = crm.people_nudges_handler({})
    n = next((n for n in out["nudges"]
              if n["kind"] == "birthday" and n["person"] == "Birthday Dude"),
             None)
    assert n is not None, f"no birthday nudge in {out['nudges']}"
    assert n["days_until"] == 1


def test_no_birthday_nudge_when_far(tmpdb):
    far = date.today() + timedelta(days=30)
    _add("No Party Yet", "acquaintance",
         birthday=f"1999-{far.month:02d}-{far.day:02d}")
    out = crm.people_nudges_handler({})
    assert not any(n["kind"] == "birthday" and n["person"] == "No Party Yet"
                   for n in out["nudges"])


# ---------------------------------------------------------------------------
# Ambiguous names: never silently pick
# ---------------------------------------------------------------------------
def test_ambiguous_name_returns_candidates(tmpdb):
    _add("Rahul", "colleague at VelocitiQ")
    _add("Rahul", "gym buddy from Mansarovar")
    res = crm.people_note_handler({"name": "Rahul", "note": "hi"})
    assert res.get("ambiguous") or res.get("multiple_matches"), res
    assert "ok" not in res, "must never silently pick a candidate"
    assert len(res["candidates"]) == 2
    contexts = {c["context"] for c in res["candidates"]}
    assert "colleague at VelocitiQ" in contexts
    assert "gym buddy from Mansarovar" in contexts

    lc = crm.people_last_contact_handler({"name": "rahul"})
    assert lc.get("ambiguous") or lc.get("multiple_matches")


def test_unknown_name_honest(tmpdb):
    res = crm.people_note_handler({"name": "Nobody Here", "note": "x"})
    assert res.get("error")
    assert "add them first" in res["error"]


# ---------------------------------------------------------------------------
# Search: FTS over names, context, notes
# ---------------------------------------------------------------------------
def test_search_hits_notes_and_context(tmpdb):
    pid = _add("Sana Kapoor", "florist in Jaipur")
    crm.people_note_handler({"name": "Sana", "note": "ordered marigolds"})
    hit = crm.people_search_handler({"query": "marigolds"})
    assert hit["count"] >= 1
    assert hit["hits"][0]["name"] == "Sana Kapoor"
    hit = crm.people_search_handler({"query": "florist"})
    assert any(h["name"] == "Sana Kapoor" for h in hit["hits"])
    hit = crm.people_search_handler({"query": "kapoor"})
    assert any(h["name"] == "Sana Kapoor" for h in hit["hits"])
    # Not-valid-FTS5 syntax falls back to substring matching, not an error.
    hit = crm.people_search_handler({"query": "marigolds AND ("})
    assert hit.get("ok")


# ---------------------------------------------------------------------------
# autopilot.propose: called when bound, skipped honestly when unbound
# ---------------------------------------------------------------------------
class _FakeRegistry:
    def __init__(self):
        self.tools = {"autopilot.propose":
                      SimpleNamespace(risk="low")}
        self.calls = []

    def call(self, name, args, actor=None):
        self.calls.append((name, args, actor))
        assert name == "autopilot.propose"
        assert args.get("goal") and args.get("steps")
        return {"ok": True, "result": {"ok": True, "id": "ap_test_1"}}


class _FakeAgent:
    def __init__(self, registry):
        self.registry = registry


def test_propose_called_when_bound(tmpdb, monkeypatch):
    pid = _add("Old Friend", "school friend")
    _seed_contact_days_ago(pid, 25)
    reg = _FakeRegistry()
    monkeypatch.setattr(crm, "_AGENT", _FakeAgent(reg))
    out = crm.people_nudges_handler({})
    assert out.get("ok")
    assert out["nudges"], "expected a silence nudge"
    assert out["proposals"]["offered"] is True
    assert out["proposals"]["queued"] == len(out["nudges"])
    assert reg.calls, "autopilot.propose was never called"
    name, args, actor = reg.calls[0]
    assert "Old Friend" in args["goal"]
    # Propose contract: proposals queue steps, they never execute anything.
    assert args["steps"][0]["tool"] == "people.note"


def test_propose_skipped_honestly_when_unbound(tmpdb):
    pid = _add("Old Friend", "school friend")
    _seed_contact_days_ago(pid, 25)
    # _AGENT is None via the tmpdb fixture: no binding happened.
    assert crm._AGENT is None
    out = crm.people_nudges_handler({})
    assert out.get("ok")
    assert out["nudges"], "nudges must still be returned unbound"
    assert out["proposals"]["offered"] is False
    assert "not bound" in out["proposals"]["reason"]


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_risk_table_all_low():
    assert set(crm.RISK_TABLE_ADDITIONS) == \
        {"people.add", "people.note", "people.last_contact",
         "people.nudges", "people.search"}
    for name, (risk, net) in crm.RISK_TABLE_ADDITIONS.items():
        assert risk == "low" and net is False, name


def test_register_wires_five_tools():
    from jarvis.tools.base import Registry
    reg = Registry()
    crm.register(reg)
    for name in ("people.add", "people.note", "people.last_contact",
                 "people.nudges", "people.search"):
        assert name in reg.tools, name
