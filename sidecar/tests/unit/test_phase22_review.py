"""Phase 22 — The accountable dreamer (offline).

Covers: brain.review stale detection (custom threshold), mean Brier over
closed cycles with scored forecasts, recent-lessons section, briefing_md
shape, the honest empty-ledger state, the sleep_pack briefing hook, and
the registration contract.
"""
import json
import os
import sys
import time

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, cognition_pack, sleep_pack


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    brain_pack.bind_agent(None)


def _iso_days_ago(days: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _insert_cycle(cycle_id, goal, created_days_ago, closed=False,
                  brier=None):
    conn = cognition_pack._connect()
    try:
        refl = None
        closed_at = None
        outcome = None
        if closed:
            closed_at = _iso_days_ago(0)
            outcome = "it worked"
            refl = json.dumps({
                "lessons_filed": ["warn owners before pricing changes"],
                "forecast_scoring": ({"brier_contribution": brier}
                                     if brier is not None else None),
            })
        conn.execute(
            "INSERT INTO cycles(cycle_id, created_at, goal, survey_json,"
            " deliberation_json, forecast_id, plan_id, closed_at, outcome,"
            " reflection_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, _iso_days_ago(created_days_ago), goal, "{}",
             "{}", None, None, closed_at, outcome, refl))
        conn.commit()
    finally:
        conn.close()


def _insert_lesson(text):
    conn = brain_pack._connect()
    try:
        conn.execute(
            "INSERT INTO lessons(created_at, text, normalized, source)"
            " VALUES (?,?,?,?)",
            (_iso_days_ago(1), text, text.lower(), "test"))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

def test_review_flags_stale_cycles(tmp_path):
    _use_db(tmp_path)
    _insert_cycle("c-fresh", "fresh decision", created_days_ago=1)
    _insert_cycle("c-stale", "stale decision", created_days_ago=10)
    res = cognition_pack.review_handler({})
    assert "error" not in res, res
    assert res["n_open"] == 2
    assert res["n_stale"] == 1
    stale = [c for c in res["open_cycles"] if c["stale"]]
    assert stale[0]["cycle_id"] == "c-stale"
    assert stale[0]["age_days"] >= 10
    assert "STALE" in res["briefing_md"]


def test_review_custom_stale_threshold(tmp_path):
    _use_db(tmp_path)
    _insert_cycle("c-mid", "mid decision", created_days_ago=5)
    res = cognition_pack.review_handler({"stale_days": 3})
    assert res["n_stale"] == 1
    res2 = cognition_pack.review_handler({"stale_days": 30})
    assert res2["n_stale"] == 0


def test_review_bad_threshold_rejected(tmp_path):
    _use_db(tmp_path)
    assert "error" in cognition_pack.review_handler({"stale_days": "x"})


def test_review_mean_brier_over_closed(tmp_path):
    _use_db(tmp_path)
    _insert_cycle("c1", "g1", created_days_ago=9, closed=True, brier=0.25)
    _insert_cycle("c2", "g2", created_days_ago=8, closed=True, brier=0.09)
    _insert_cycle("c3", "g3", created_days_ago=7, closed=True, brier=None)
    res = cognition_pack.review_handler({})
    assert "error" not in res, res
    assert res["closed_cycles"] == 3
    assert res["n_scored_forecasts"] == 2
    assert res["mean_brier"] == round((0.25 + 0.09) / 2, 4)
    assert "Mean Brier" in res["briefing_md"]


def test_review_no_scored_forecasts_honest(tmp_path):
    _use_db(tmp_path)
    _insert_cycle("c1", "g1", created_days_ago=2)
    res = cognition_pack.review_handler({})
    assert res["mean_brier"] is None
    assert "No scored forecasts yet" in res["briefing_md"]


def test_review_recent_lessons(tmp_path):
    _use_db(tmp_path)
    _insert_lesson("Always warn gym owners before a pricing change.")
    res = cognition_pack.review_handler({})
    assert "error" not in res, res
    assert len(res["recent_lessons"]) == 1
    assert "pricing change" in res["recent_lessons"][0]
    assert "Lessons filed this week" in res["briefing_md"]


def test_review_empty_ledger_honest(tmp_path):
    _use_db(tmp_path)
    res = cognition_pack.review_handler({})
    assert "error" not in res, res
    assert res["n_open"] == 0
    assert "Every decision has its outcome recorded" in res["briefing_md"]


def test_review_method_discloses_heuristic(tmp_path):
    _use_db(tmp_path)
    res = cognition_pack.review_handler({})
    assert "heuristic" in res["method"]
    assert "auto-closes" in res["method"]


# ---------------------------------------------------------------------------
# sleep_pack hook
# ---------------------------------------------------------------------------

def test_briefing_includes_review_section():
    md = sleep_pack._compose_briefing(
        {"totals": {"tool_calls": 1, "by_actor": {}, "by_risk": {},
                    "failures": 0, "confirmation_gates": 0},
         "top_tools": [], "most_used_capabilities": [], "failures": []},
        None, None, "2026-09-28",
        review_res={"ok": True,
                    "result": {"briefing_md": "## Open decisions awaiting outcomes\n- none"}})
    assert "Open decisions awaiting outcomes" in md


def test_briefing_tolerates_missing_review():
    md = sleep_pack._compose_briefing(
        {"totals": {"tool_calls": 0, "by_actor": {}, "by_risk": {},
                    "failures": 0, "confirmation_gates": 0},
         "top_tools": [], "most_used_capabilities": [], "failures": []},
        None, None, "2026-09-28", review_res=None)
    assert "Morning briefing" in md


# ---------------------------------------------------------------------------
# registration contract
# ---------------------------------------------------------------------------

class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    cognition_pack.register(reg)
    assert "brain.review" in reg.tools
    assert "brain.review" in cognition_pack.RISK_TABLE_ADDITIONS
