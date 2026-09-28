"""Offline tests for the Phase 13 Persona pack (workstream D).

No network, no real inbox: weather/mail sources are stubbed by monkeypatching
the module-level _fetch_weather / _fetch_unread helpers. DB isolation via
JARVIS_PERSONA_DB pointing at a tmp file.
"""
from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.persona_pack as pp
from jarvis.agent import policy
from jarvis.tools.base import Registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db = tmp_path / "persona.db"
    monkeypatch.setenv("JARVIS_PERSONA_DB", str(db))
    monkeypatch.setattr(pp, "_AGENT", None)
    yield db


@pytest.fixture
def reg(tmpdb, monkeypatch):
    r = Registry()
    pp.register(r)
    for tool_name, (risk, net) in pp.RISK_TABLE_ADDITIONS.items():
        monkeypatch.setitem(policy.RISK_TABLE, tool_name, (risk, net))
    return r


def _call(reg, name, args):
    out = reg.call(name, args or {}, actor="agent")
    assert out.get("ok"), f"{name} failed: {out}"
    return out["result"]


# ---------------------------------------------------------------------------
# register() wiring
# ---------------------------------------------------------------------------
def test_register_wires_all_tools_and_risk_table(reg):
    expected = {"persona.note", "persona.notes", "persona.greet",
                "persona.followup", "persona.mood", "persona.tone"}
    assert expected <= set(reg.tools)
    assert expected <= set(pp.RISK_TABLE_ADDITIONS)
    for name in expected:
        assert reg.tools[name].risk == "low"
        assert reg.tools[name].needs_network is False
        risk, net = pp.RISK_TABLE_ADDITIONS[name]
        assert (risk, net) == ("low", False)


# ---------------------------------------------------------------------------
# persona.note / persona.notes
# ---------------------------------------------------------------------------
def test_note_persists_and_notes_lists_it(reg):
    out = _call(reg, "persona.note", {"observation": "loves concise answers"})
    assert out["recorded"] is True
    assert out["id"]
    listed = _call(reg, "persona.notes", {})
    assert listed["count"] == 1
    assert listed["notes"][0]["note"] == "loves concise answers"
    assert listed["kind"] == "taste, not fact"

    _call(reg, "persona.note", {"observation": "prefers data over adjectives"})
    listed = _call(reg, "persona.notes", {})
    assert listed["count"] == 2


def test_note_rejects_empty_and_oversized(reg):
    bad = _call(reg, "persona.note", {"observation": "   "})
    assert "error" in bad
    bad = _call(reg, "persona.note", {"observation": "x" * 600})
    assert "error" in bad
    assert _call(reg, "persona.notes", {})["count"] == 0


# ---------------------------------------------------------------------------
# persona.greet — composed live, graceful degradation
# ---------------------------------------------------------------------------
def test_greet_with_live_sources(reg, monkeypatch):
    monkeypatch.setattr(pp, "_fetch_weather",
                        lambda lat, lon: {"temperature_c": 28,
                                          "conditions": "clear sky",
                                          "source": "weather.now"})
    monkeypatch.setattr(pp, "_fetch_unread",
                        lambda: {"count": 3, "source": "inbox.triage"})
    _call(reg, "persona.note", {"observation": "prefers data over adjectives"})

    out = _call(reg, "persona.greet",
                {"latitude": 28.61, "longitude": 77.21})
    g = out["greeting"]
    assert "sir" in g
    assert "28" in g and "clear sky" in g          # weather composed in
    assert "3 unread" in g                        # mail composed in
    assert "data over adjectives" in g             # taste note composed in
    sources = {c["source"] for c in out["components"]}
    assert {"clock", "weather.now", "inbox.triage",
            "persona.note"} <= sources
    assert out["omitted"] == []


def test_greet_graceful_when_sources_fail(reg, monkeypatch):
    monkeypatch.setattr(pp, "_fetch_weather", lambda lat, lon: None)
    monkeypatch.setattr(pp, "_fetch_unread", lambda: None)
    # No persona note recorded either.
    out = _call(reg, "persona.greet",
                {"latitude": 28.61, "longitude": 77.21})
    g = out["greeting"]
    assert "sir" in g                       # still a greeting
    assert "°C" not in g and "unread" not in g   # nothing invented
    sources = {c["source"] for c in out["components"]}
    assert sources == {"clock"}
    assert any("weather.now" in o for o in out["omitted"])
    assert any("inbox.triage" in o for o in out["omitted"])


def test_greet_without_coordinates_omits_weather(reg, monkeypatch):
    calls = []
    monkeypatch.setattr(pp, "_fetch_weather",
                        lambda lat, lon: calls.append((lat, lon)) or None)
    monkeypatch.setattr(pp, "_fetch_unread", lambda: None)
    out = _call(reg, "persona.greet", {})
    assert calls == []                      # weather never attempted
    assert "sir" in out["greeting"]
    assert any("no coordinates" in o for o in out["omitted"])


# ---------------------------------------------------------------------------
# persona.followup — extraction + honest date parsing
# ---------------------------------------------------------------------------
def _install_fake_loops(monkeypatch, spy):
    fake = types.ModuleType("jarvis.tools.builtin.loops_pack")
    fake.register_loop = spy
    monkeypatch.setitem(sys.modules,
                        "jarvis.tools.builtin.loops_pack", fake)


def _ensure_no_loops(monkeypatch):
    monkeypatch.delitem(sys.modules,
                        "jarvis.tools.builtin.loops_pack",
                        raising=False)


def test_followup_extracts_flight_friday_and_registers(reg, monkeypatch):
    registered = []

    def spy(what, when_iso, text):
        registered.append((what, when_iso, text))
        return {"loop_id": "abc123"}

    _install_fake_loops(monkeypatch, spy)
    out = _call(reg, "persona.followup", {"text": "my flight Friday"})
    assert out["detected"] is True
    assert out["registered"] is True
    assert out["via"] == "loops_pack.register_loop"
    assert len(registered) == 1
    what, when_iso, text = registered[0]
    assert "flight" in what.lower()
    assert text == "my flight Friday"
    # The date really is a Friday.
    d = date.fromisoformat(when_iso)
    assert d.weekday() == 4  # Monday=0
    today = date.today()
    expected = today + timedelta(days=(4 - today.weekday()) % 7)
    assert when_iso == expected.isoformat()


def test_followup_tomorrow_and_in_n_days(reg, monkeypatch):
    _ensure_no_loops(monkeypatch)  # missing loops_pack -> suggestion path
    today = date.today()
    out = _call(reg, "persona.followup", {"text": "call mom tomorrow"})
    assert out["registered"] is False
    assert out["candidate"]["what"] == "call mom"
    assert out["candidate"]["when"] == (today + timedelta(days=1)).isoformat()

    out = _call(reg, "persona.followup",
                {"text": "remind me to water the plants in 3 days"})
    assert out["candidate"]["what"] == "water the plants"
    assert out["candidate"]["when"] == (today + timedelta(days=3)).isoformat()


def test_followup_next_vs_this_weekday(reg, monkeypatch):
    _ensure_no_loops(monkeypatch)
    today = date.today()
    out = _call(reg, "persona.followup", {"text": "dentist next Monday"})
    delta = (0 - today.weekday()) % 7 or 7
    assert out["candidate"]["when"] == (today + timedelta(days=delta + 7)).isoformat()
    out = _call(reg, "persona.followup", {"text": "dentist this Monday"})
    delta = (0 - today.weekday()) % 7
    assert out["candidate"]["when"] == (today + timedelta(days=delta)).isoformat()


def test_followup_ambiguous_soon_returns_unregistered_candidate(reg, monkeypatch):
    _ensure_no_loops(monkeypatch)
    out = _call(reg, "persona.followup", {"text": "call mom soon"})
    assert out["detected"] is True
    assert out["registered"] is False
    assert out["candidate"]["when"] is None
    assert "ambiguous" in out["reason"].lower()


def test_followup_no_date_nothing_registered(reg, monkeypatch):
    registered = []
    _install_fake_loops(monkeypatch,
                        lambda w, i, t: registered.append((w, i, t)))
    out = _call(reg, "persona.followup", {"text": "the weather is nice"})
    assert out["detected"] is False
    assert out["registered"] is False
    assert registered == []

    out = _call(reg, "persona.followup",
                {"text": "when is my flight?"})  # a question, not a commitment
    assert out["detected"] is False


def test_followup_missing_loops_pack_returns_suggestion(reg, monkeypatch):
    _ensure_no_loops(monkeypatch)
    out = _call(reg, "persona.followup", {"text": "my flight Friday"})
    assert out["detected"] is True
    assert out["registered"] is False
    assert out["candidate"]["what"] == "my flight"
    assert "loops_pack" in out["reason"]


# ---------------------------------------------------------------------------
# persona.mood / persona.tone — calibration, never performed emotion
# ---------------------------------------------------------------------------
def test_tone_defaults_when_no_mood_notes(reg):
    out = _call(reg, "persona.tone", {})
    assert out["brevity"] == "normal"
    assert out["warmth"] == "warm"
    assert out["notes_considered"] == 0


def test_tone_shifts_terse_after_rushed_notes(reg):
    for note in ("user seems rushed", "he is in a hurry today",
                 "user asked for a quick answer, no time"):
        out = _call(reg, "persona.mood", {"note": note})
        assert out["recorded"] is True
    tone = _call(reg, "persona.tone", {})
    assert tone["brevity"] == "terse"
    assert tone["notes_considered"] == 3
    assert "short" in tone["guidance"].lower()
    assert "never performed emotion" in tone["guidance"]


def test_tone_verbose_and_dry_signals(reg):
    _call(reg, "persona.mood", {"note": "user wants a detailed walkthrough"})
    _call(reg, "persona.mood", {"note": "explain thoroughly, in depth please"})
    tone = _call(reg, "persona.tone", {})
    assert tone["brevity"] == "verbose"
    _call(reg, "persona.mood", {"note": "keep it strictly business, formal tone"})
    _call(reg, "persona.mood", {"note": "no jokes, professional please"})
    tone = _call(reg, "persona.tone", {})
    assert tone["warmth"] == "dry"


def test_mood_notes_accumulate(reg):
    _call(reg, "persona.mood", {"note": "user is joking around"})
    out = _call(reg, "persona.mood", {"note": "user seems cheerful"})
    assert out["tone_now"]["warmth"] == "warm"
    assert out["tone_now"]["signals"]["warm"] == 2
