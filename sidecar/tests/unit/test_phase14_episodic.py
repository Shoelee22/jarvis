"""Phase 14 "HYPER" episodic memory pack tests: recording, time-aware recall,
timeline ordering, fact-link round-trip, register() wiring, and the
note_from_audit synthesis helper for the sleep crew."""
from __future__ import annotations

import datetime as _dt

import pytest

import jarvis.tools.builtin.episodic_pack as ep
from jarvis.tools.base import Registry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_EPISODIC_DB", str(tmp_path / "episodic.db"))


@pytest.fixture()
def _fixed_sunday(monkeypatch):
    # 2026-09-27 is a Sunday. "last Tuesday" must resolve to 2026-09-22.
    monkeypatch.setattr(ep, "_today", lambda: _dt.date(2026, 9, 27))


def _rec(title, what, when=None, people=None, outcome=None):
    args = {"title": title, "what_happened": what}
    if when is not None:
        args["when"] = when
    if people is not None:
        args["people"] = people
    if outcome is not None:
        args["outcome"] = outcome
    return ep.episode_handler(args)


# ---------------------------------------------------------- memory.episode
def test_record_and_recall_by_query():
    r = _rec("Finished the gym website",
             "Deployed the Iron Culture site with the new pricing page.",
             when="2026-09-24")
    assert "episode_id" in r and r["date"] == "2026-09-24"
    assert not r.get("date_is_guess")
    hits = ep.recall_handler({"query": "gym website"})
    assert hits["count"] == 1
    e = hits["episodes"][0]
    assert e["title"] == "Finished the gym website"
    assert e["date"] == "2026-09-24"


def test_recall_searches_outcome_and_people():
    _rec("Electrician visit", "The fan in the bedroom stopped working.",
         when="2026-09-20", people=["Ramesh the electrician"],
         outcome="Replaced the capacitor; fan works now.")
    hits = ep.recall_handler({"query": "capacitor"})
    assert hits["count"] == 1
    hits = ep.recall_handler({"query": "Ramesh"})
    assert hits["count"] == 1
    # "when did I last talk to the electrician?" — no window, recency order
    _rec("Electrician callback", "Ramesh called to confirm the invoice.",
         when="2026-09-25", people=["Ramesh the electrician"])
    hits = ep.recall_handler({"query": "electrician"})
    assert hits["count"] == 2
    assert hits["episodes"][0]["date"] == "2026-09-25"


def test_episode_validates_args():
    assert "error" in ep.episode_handler({})
    assert "error" in ep.episode_handler({"title": "x"})
    assert "error" in ep.episode_handler({"title": "x", "what_happened": "y",
                                          "people": "not-a-list"})


def test_unparseable_when_stored_with_guess_flag():
    r = _rec("Weird day", "Something happened.", when="sometime last blursday")
    assert r.get("date_is_guess") is True
    assert "note" in r
    hits = ep.recall_handler({"query": "weird day"})
    assert hits["count"] == 1
    assert hits["episodes"][0]["date_is_guess"] is True


# ----------------------------------------------------------- memory.recall
def test_recall_last_tuesday_window(_fixed_sunday):
    _rec("Deep work Tuesday", "Finished the Pixalified outreach batch.",
         when="2026-09-22")   # last Tuesday
    _rec("Older Tuesday", "Old stuff.", when="2026-09-15")
    _rec("Wednesday", "Mid-week.", when="2026-09-23")
    hits = ep.recall_handler({"when": "last Tuesday"})
    assert hits["count"] == 1
    assert hits["episodes"][0]["title"] == "Deep work Tuesday"


def test_recall_combines_text_and_window(_fixed_sunday):
    _rec("Gym session", "Leg day at Iron Culture.", when="2026-09-22")
    _rec("Gym session", "Push day at Iron Culture.", when="2026-09-15")
    hits = ep.recall_handler({"query": "gym", "when": "last week"})
    # last week = Mon 2026-09-14 .. Sun 2026-09-20: only the 15th qualifies
    assert hits["count"] == 1
    assert hits["episodes"][0]["date"] == "2026-09-15"


def test_recall_month_window():
    _rec("Sept thing", "Happened in September.", when="2026-09-05")
    _rec("Oct thing", "Happened in October.", when="2026-10-02")
    hits = ep.recall_handler({"when": "2026-09"})
    assert hits["count"] == 1
    assert hits["episodes"][0]["title"] == "Sept thing"


def test_recall_needs_query_or_window():
    assert "error" in ep.recall_handler({})
    assert "error" in ep.recall_handler({"when": "not a time at all"})


# --------------------------------------------------------- memory.timeline
def test_timeline_returns_chronological_order():
    _rec("Third", "Last event.", when="2026-09-10")
    _rec("First", "First event.", when="2026-09-01")
    _rec("Second", "Middle event.", when="2026-09-05")
    r = ep.timeline_handler({"from": "2026-09-01", "to": "2026-09-30"})
    assert [e["title"] for e in r["episodes"]] == ["First", "Second", "Third"]
    assert r["count"] == 3


def test_timeline_validates_range():
    assert "error" in ep.timeline_handler({"from": "2026-09-10",
                                           "to": "2026-09-01"})
    assert "error" in ep.timeline_handler({"from": "whenever",
                                           "to": "2026-09-01"})


# ------------------------------------------------ memory.link / memory.links
def test_link_round_trip():
    r = _rec("Signed the client", "Acme Corp signed the retainer.",
             when="2026-09-26")
    eid = r["episode_id"]
    link = ep.link_handler({"episode_id": eid,
                            "fact": "Acme Corp is a paying client"})
    assert link["fact_source"] == "mind_pack"
    assert link["fact"] == "Acme Corp is a paying client"
    listed = ep.links_handler({"episode_id": eid})
    assert listed["count"] == 1
    assert listed["links"][0]["fact"] == "Acme Corp is a paying client"
    assert listed["links"][0]["fact_source"] == "mind_pack"


def test_link_validates():
    assert "error" in ep.link_handler({"episode_id": "nope",
                                       "fact": "something"})
    r = _rec("Lonely episode", "Nothing linked.", when="2026-09-26")
    assert "error" in ep.link_handler({"episode_id": r["episode_id"]})
    assert "error" in ep.links_handler({})


# -------------------------------------------------------- note_from_audit
def _audit_row(tool, day, hour, summary="", actor="agent"):
    ts = _dt.datetime(2026, 9, day, hour, 0, 0).timestamp()
    return {"ts": ts, "actor": actor, "tool": tool, "args_json": summary,
            "result_summary": summary, "risk": "low"}


def test_note_from_audit_builds_candidates_and_skips_noise():
    rows = []
    # noise: heartbeats + ticks + the sleep cycle's own bookkeeping
    for i in range(10):
        rows.append(_audit_row("heartbeat", 26, 2))
    for i in range(8):
        rows.append(_audit_row("sleep.tick", 26, 3))
    rows.append(_audit_row("sleep.cycle", 26, 4))
    rows.append(_audit_row("autopilot.status", 26, 9))
    # signal: built something with website.build
    rows.append(_audit_row("website.build", 26, 10,
                           "built a bakery homepage for the demo"))
    rows.append(_audit_row("website.build", 26, 11, "tweaked the hero copy"))
    # signal: repeated research
    for h in (12, 13, 14, 15):
        rows.append(_audit_row("web.search", 26, h, "best CRM for gyms"))
    # signal: error spike
    for h in (16, 17, 18):
        rows.append(_audit_row("gmail.send", 26, h,
                               "error: SMTP connection refused"))
    cands = ep.note_from_audit(rows)
    titles = [c["title"] for c in cands]
    assert any("website.build" in t for t in titles)
    assert any("Researched" in t for t in titles)
    assert any("Error spike in gmail.send" in t for t in titles)
    # noise tools produce no candidates
    assert not any("heartbeat" in t or "sleep.tick" in t or "sleep.cycle" in t
                   for t in titles)
    # all candidates are episode-shaped and carry a date
    for c in cands:
        assert c["title"] and c["what_happened"] and c["when"] == "2026-09-26"
    # propose, don't auto-write: nothing was persisted
    assert ep.recall_handler({"when": "2026-09-26"})["count"] == 0


def test_note_from_audit_quiet_day_yields_nothing():
    rows = [_audit_row("heartbeat", 26, 2),
            _audit_row("web.search", 26, 10, "one quick lookup")]
    assert ep.note_from_audit(rows) == []
    assert ep.note_from_audit([]) == []
    assert ep.note_from_audit([{"junk": 1}]) == []  # never raises


def test_note_from_audit_accepts_generic_row_shape():
    ts = _dt.datetime(2026, 9, 26, 10, 0, 0).timestamp()
    rows = [{"timestamp": ts, "actor": "agent", "tool": "code.run",
             "args_summary": "generated the outreach script"}]
    cands = ep.note_from_audit(rows)
    assert len(cands) == 1
    assert "code.run" in cands[0]["title"]


# ----------------------------------------------------------------- wiring
def test_register_and_risk_table():
    reg = Registry()
    ep.register(reg)
    assert {s["name"] for s in ep.TOOL_DEFS} == {
        "memory.episode", "memory.recall", "memory.timeline",
        "memory.link", "memory.links",
            "memory.consolidate"}
    for spec in ep.TOOL_DEFS:
        assert spec["name"] in reg.tools
        assert reg.tools[spec["name"]].risk == "low"
        assert reg.tools[spec["name"]].needs_network is False
        assert callable(reg.tools[spec["name"]].handler)
    assert set(ep.RISK_TABLE_ADDITIONS) == {s["name"] for s in ep.TOOL_DEFS}
    for name, (risk, net) in ep.RISK_TABLE_ADDITIONS.items():
        assert risk == "low"
        assert net is False


def test_tool_defs_have_handlers_and_schemas():
    for spec in ep.TOOL_DEFS:
        assert spec["description"] and spec["handler"]
        assert isinstance(spec["schema"], dict)
