"""Phase 20 — Closed-loop cognition tests (offline).

Covers: survey evidence brief (episodes + lessons + honest empty state);
cycle end-to-end (survey -> deliberate -> forecast -> plan, stored open);
close_cycle (outcome recorded, forecast scored, lessons filed, double-close
and unknown-id rejected); cycles ledger with open_only filter; the
registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, cognition_pack, episodic_pack


GOAL = "Should we launch the new pricing tier for the gym SaaS?"
CONTEXT = (
    "The new pricing tier supports higher revenue per gym because the value "
    "is proven. "
    "Early tests show the tier works well and brings clear benefits to gym "
    "owners. "
    "However the pricing change carries risk: churn could rise if the price "
    "feels wrong."
)


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    os.environ["JARVIS_EPISODIC_DB"] = str(tmp_path / "episodic.db")
    os.environ["JARVIS_KNOWLEDGE_DB"] = str(tmp_path / "knowledge.db")
    brain_pack.bind_agent(None)


def _seed_memory():
    episodic_pack.episode_handler({
        "title": "Last pricing change",
        "what_happened": ("We raised prices last year and churn rose for one "
                          "month before settling."),
        "when": "2025-06-01",
    })
    brain_pack.brain_reflect_handler({
        "on": "Lesson: we should always warn gym owners before a pricing "
              "change to avoid surprise churn."})


# ---------------------------------------------------------------------------
# survey
# ---------------------------------------------------------------------------

def test_survey_gathers_episodes_and_lessons(tmp_path):
    _use_db(tmp_path)
    _seed_memory()
    res = cognition_pack.survey_handler({"goal": GOAL})
    assert "error" not in res, res
    assert res["sources"]["episodes"] >= 1, res["sources"]
    assert res["sources"]["lessons"] >= 1, res["sources"]
    assert "Evidence brief" in res["brief_text"]
    assert "pricing" in res["brief_text"].lower()


def test_survey_empty_stores_honest(tmp_path):
    _use_db(tmp_path)
    res = cognition_pack.survey_handler({"goal": GOAL})
    assert "error" not in res, res
    assert res["sources"] == {"episodes": 0, "knowledge": 0, "lessons": 0}
    assert "No relevant episodes" in res["brief_text"]


def test_survey_requires_goal(tmp_path):
    _use_db(tmp_path)
    assert "error" in cognition_pack.survey_handler({"goal": "  "})


# ---------------------------------------------------------------------------
# cycle
# ---------------------------------------------------------------------------

def test_cycle_end_to_end(tmp_path):
    _use_db(tmp_path)
    _seed_memory()
    res = cognition_pack.cycle_handler({
        "goal": GOAL,
        "context": CONTEXT,
        "forecast": {"question": "Will churn rise after launch?", "p": 0.65},
        "plan_steps": [
            {"id": "s1", "tool": "notes.add",
             "args": {"text": "warn gym owners before pricing change"},
             "depends_on": []},
        ],
    })
    assert "error" not in res, res
    assert res["status"] == "open"
    assert res["cycle_id"].startswith("cycle-")
    assert res["forecast_id"] and res["forecast_id"].startswith("pred-")
    assert res["plan_id"] and res["plan_id"].startswith("plan-")
    d = res["deliberation"]
    assert d["trace_id"] and d["conclusion"]
    assert "p_raw" in d
    assert "brain.close_cycle" in res["next"]


def test_cycle_minimal(tmp_path):
    _use_db(tmp_path)
    res = cognition_pack.cycle_handler({"goal": GOAL})
    assert "error" not in res, res
    assert res["status"] == "open"
    assert res["forecast_id"] is None
    assert res["plan_id"] is None


def test_cycle_requires_goal(tmp_path):
    _use_db(tmp_path)
    assert "error" in cognition_pack.cycle_handler({"goal": ""})


# ---------------------------------------------------------------------------
# close_cycle
# ---------------------------------------------------------------------------

def test_close_cycle_scores_and_learns(tmp_path):
    _use_db(tmp_path)
    cyc = cognition_pack.cycle_handler({
        "goal": GOAL, "context": CONTEXT,
        "forecast": {"question": "Will churn rise?", "p": 0.65},
    })
    cid = cyc["cycle_id"]
    res = cognition_pack.close_cycle_handler({
        "cycle_id": cid,
        "outcome": ("We launched the tier. Lesson: churn did not rise because "
                    "we warned owners first; next time we should warn earlier."),
        "forecast_outcome": "no",
    })
    assert "error" not in res, res
    assert res["status"] == "closed"
    fs = res["forecast_scoring"]
    assert fs["outcome"] == 0.0
    assert fs["brier_contribution"] == round(0.65 ** 2, 4)
    assert res["lessons_filed"], "reflect should file the lesson"
    # ledger reflects the closure
    ledger = cognition_pack.cycles_handler({})
    assert ledger["n_open"] == 0
    assert ledger["cycles"][0]["status"] == "closed"


def test_close_cycle_rejects_bad_input(tmp_path):
    _use_db(tmp_path)
    assert "unknown" in cognition_pack.close_cycle_handler(
        {"cycle_id": "cycle-nope", "outcome": "x"})["error"]
    cyc = cognition_pack.cycle_handler({"goal": GOAL})
    cid = cyc["cycle_id"]
    assert "error" in cognition_pack.close_cycle_handler(
        {"cycle_id": cid, "outcome": "  "})
    ok = cognition_pack.close_cycle_handler(
        {"cycle_id": cid, "outcome": "It went fine."})
    assert ok["status"] == "closed"
    dup = cognition_pack.close_cycle_handler(
        {"cycle_id": cid, "outcome": "again"})
    assert "already closed" in dup["error"]


# ---------------------------------------------------------------------------
# cycles ledger
# ---------------------------------------------------------------------------

def test_cycles_ledger_open_only(tmp_path):
    _use_db(tmp_path)
    c1 = cognition_pack.cycle_handler({"goal": "Goal one"})
    c2 = cognition_pack.cycle_handler({"goal": "Goal two"})
    cognition_pack.close_cycle_handler(
        {"cycle_id": c1["cycle_id"], "outcome": "Done."})
    full = cognition_pack.cycles_handler({})
    assert full["n_total"] == 2 and full["n_open"] == 1
    open_only = cognition_pack.cycles_handler({"open_only": True})
    assert open_only["n_total"] == 1
    assert open_only["cycles"][0]["cycle_id"] == c2["cycle_id"]
    assert "awaiting" in full["note"]


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
    expected = {"brain.survey", "brain.cycle",
                "brain.close_cycle", "brain.cycles", "brain.review",
                "brain.handoff", "brain.resume"}
    # Later phases extend the pack (brain.debate, brain.integrity) —
    # the originals keep their contract.
    assert expected <= set(reg.tools)
    assert expected <= set(cognition_pack.RISK_TABLE_ADDITIONS)
