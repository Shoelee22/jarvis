"""Phase 19 — Metacognitive brain tests (offline).

Covers: steelman counter-case from counter-evidence; premortem inversion
ranked by weight; assumptions fragility summing to ~1 with single-source
flag; second_order causal chaining; predict validation; score_prediction
Brier math; calibration buckets; deliberate end-to-end; honest thin-evidence
behaviour; the registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, metacog_pack


QUESTION = "Should we launch the new pricing tier for the gym SaaS?"
CONTEXT = (
    "The new pricing tier supports higher revenue per gym because the value "
    "is proven. "
    "Early tests show the tier works well and brings clear benefits to gym "
    "owners. "
    "However the pricing change carries risk: churn could rise if the price "
    "feels wrong. "
    "Launching the tier leads to higher support load. "
    "Higher support load triggers slower response times."
)


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    brain_pack.bind_agent(None)


# ---------------------------------------------------------------------------
# steelman
# ---------------------------------------------------------------------------

def test_steelman_finds_counter_case(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.steelman_handler(
        {"question": QUESTION, "context": CONTEXT})
    assert "error" not in res, res
    assert res["counter_items"], "expected counter-evidence"
    assert res["counter_mass"] > 0
    assert res["supporting_mass"] > 0
    roles = {i["role"] for i in res["counter_items"]}
    assert roles, "counter items should carry a role"


def test_steelman_thin_evidence_honest(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.steelman_handler(
        {"question": QUESTION, "context": ""})
    assert "error" not in res, res
    assert res["counter_items"] == []
    assert "No counter-evidence found" in res["counter_case"]


def test_steelman_requires_question(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.steelman_handler({"question": "  "})
    assert "error" in res


# ---------------------------------------------------------------------------
# premortem
# ---------------------------------------------------------------------------

def test_premortem_inverts_support_ranked(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.premortem_handler(
        {"decision": "Launch the new pricing tier", "context": CONTEXT})
    assert "error" not in res, res
    hyps = res["failure_hypotheses"]
    assert len(hyps) >= 2, "two supporting sentences -> two hypotheses"
    weights = [h["weight"] for h in hyps]
    assert weights == sorted(weights, reverse=True), "ranked by weight desc"
    assert abs(sum(h["share_of_case"] for h in hyps) - 1.0) < 0.01
    assert "six months later" in res["scenario"]


def test_premortem_requires_decision(tmp_path):
    _use_db(tmp_path)
    assert "error" in metacog_pack.premortem_handler({"decision": ""})


# ---------------------------------------------------------------------------
# assumptions
# ---------------------------------------------------------------------------

def test_assumptions_fragility_and_flags(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.assumptions_handler(
        {"question": QUESTION, "context": CONTEXT})
    assert "error" not in res, res
    asm = res["assumptions"]
    assert asm, "expected load-bearing assumptions"
    frags = [a["fragility"] for a in asm]
    assert frags == sorted(frags, reverse=True), "ranked by fragility desc"
    assert abs(sum(frags) - 1.0) < 0.01, "fragilities are shares of the case"
    assert any("single-source" in f for f in res["flags"]), \
        f"fewer than 3 supporters should flag single-source risk: {res['flags']}"


# ---------------------------------------------------------------------------
# second_order
# ---------------------------------------------------------------------------

def test_second_order_chains(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.second_order_handler(
        {"decision": "Launch the tier", "context": CONTEXT})
    assert "error" not in res, res
    assert res["causal_claims_found"] >= 2
    chained = [c for c in res["chains"] if c["level_2"]]
    assert chained, "expected at least one two-level chain"
    assert chained[0]["link_terms"], "chain should name the link terms"


def test_second_order_no_causation(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.second_order_handler(
        {"decision": "Launch", "context": "The sky is blue. Pricing matters."})
    assert "error" not in res, res
    assert res["chains"] == []
    assert res["causal_claims_found"] == 0


# ---------------------------------------------------------------------------
# predict / score_prediction / calibration — real math
# ---------------------------------------------------------------------------

def test_predict_validation(tmp_path):
    _use_db(tmp_path)
    assert "error" in metacog_pack.predict_handler(
        {"question": QUESTION, "p": 1.5})
    assert "error" in metacog_pack.predict_handler(
        {"question": QUESTION, "p": -0.1})
    assert "error" in metacog_pack.predict_handler(
        {"question": "   ", "p": 0.5})
    assert "error" in metacog_pack.predict_handler(
        {"question": QUESTION, "p": "high"})
    ok = metacog_pack.predict_handler(
        {"question": QUESTION, "p": 0.7})
    assert ok["prediction_id"].startswith("pred-")
    assert ok["p"] == 0.7


def test_score_prediction_brier_math(tmp_path):
    _use_db(tmp_path)
    pid = metacog_pack.predict_handler(
        {"question": "Will churn rise?", "p": 0.7})["prediction_id"]
    res = metacog_pack.score_prediction_handler(
        {"prediction_id": pid, "outcome": 1})
    assert "error" not in res, res
    assert res["brier_contribution"] == round((0.7 - 1) ** 2, 4) == 0.09
    assert res["running_brier"] == 0.09
    assert res["n_resolved"] == 1
    # double-resolve rejected
    dup = metacog_pack.score_prediction_handler(
        {"prediction_id": pid, "outcome": 0})
    assert "already resolved" in dup["error"]
    # unknown id rejected
    assert "unknown" in metacog_pack.score_prediction_handler(
        {"prediction_id": "pred-nope", "outcome": 1})["error"]
    # bad outcome rejected
    pid2 = metacog_pack.predict_handler(
        {"question": "Q?", "p": 0.5})["prediction_id"]
    assert "error" in metacog_pack.score_prediction_handler(
        {"prediction_id": pid2, "outcome": "maybe"})


def test_score_prediction_outcome_forms(tmp_path):
    _use_db(tmp_path)
    for outcome, expected in [("yes", 1.0), ("no", 0.0), (True, 1.0),
                              (False, 0.0), (0, 0.0)]:
        pid = metacog_pack.predict_handler(
            {"question": "Q?", "p": 0.5})["prediction_id"]
        res = metacog_pack.score_prediction_handler(
            {"prediction_id": pid, "outcome": outcome})
        assert res["outcome"] == expected, (outcome, res)


def test_calibration_buckets_and_brier(tmp_path):
    _use_db(tmp_path)
    cases = [(0.8, 1), (0.8, 1), (0.3, 0), (0.3, 1)]
    for p, o in cases:
        pid = metacog_pack.predict_handler(
            {"question": "Q?", "p": p})["prediction_id"]
        metacog_pack.score_prediction_handler(
            {"prediction_id": pid, "outcome": o})
    cal = metacog_pack.calibration_handler({})
    assert cal["n_resolved"] == 4
    assert cal["n_pending"] == 0
    # Brier = (0.04 + 0.04 + 0.09 + 0.49) / 4 = 0.165
    assert cal["brier_score"] == 0.165, cal["brier_score"]
    by_range = {(b["range"][0], b["range"][1]): b for b in cal["buckets"]}
    hi = by_range[(0.8, 1.0)]
    assert hi["n"] == 2 and hi["avg_p"] == 0.8 and hi["hit_rate"] == 1.0
    lo = by_range[(0.2, 0.4)]
    assert lo["n"] == 2 and lo["avg_p"] == 0.3 and lo["hit_rate"] == 0.5
    assert "too few" in cal["verdict"]


def test_calibration_empty(tmp_path):
    _use_db(tmp_path)
    cal = metacog_pack.calibration_handler({})
    assert cal["n_resolved"] == 0
    assert "no resolved predictions" in cal["verdict"]


def test_calibration_pending_counted(tmp_path):
    _use_db(tmp_path)
    metacog_pack.predict_handler({"question": "Q?", "p": 0.6})
    cal = metacog_pack.calibration_handler({})
    assert cal["n_pending"] == 1
    assert cal["n_resolved"] == 0


# ---------------------------------------------------------------------------
# deliberate — the full loop
# ---------------------------------------------------------------------------

def test_deliberate_end_to_end(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.deliberate_handler(
        {"question": QUESTION, "context": CONTEXT})
    assert "error" not in res, res
    for key in ("conclusion", "steelman", "premortem", "assumptions",
                "second_order", "p_raw", "p_raw_mapping",
                "calibration_note", "protocol", "trace_id"):
        assert key in res, f"missing dossier section: {key}"
    assert 0.05 <= res["p_raw"] <= 0.95
    assert "Only 0 resolved" in res["calibration_note"]
    assert "steelman" in res["protocol"]


def test_deliberate_with_decision(tmp_path):
    _use_db(tmp_path)
    res = metacog_pack.deliberate_handler({
        "question": "Which tier should we launch?",
        "context": CONTEXT,
        "options": ["Launch the new pricing tier now",
                    "Keep the current pricing unchanged"],
        "criteria": [{"name": "revenue", "weight": 2},
                     {"name": "risk", "weight": 1}],
    })
    assert "error" not in res, res
    assert "decision" in res
    assert res["decision"]["winner"] in (
        "option_1", "option_2")


def test_deliberate_requires_question(tmp_path):
    _use_db(tmp_path)
    assert "error" in metacog_pack.deliberate_handler({"question": ""})


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
    metacog_pack.register(reg)
    expected = {"brain.steelman", "brain.premortem", "brain.assumptions",
                "brain.second_order", "brain.predict",
                "brain.score_prediction", "brain.calibration",
                "brain.deliberate", "brain.audit", "brain.contradictions",
                "brain.correct"}
    assert set(reg.tools) == expected
    assert set(metacog_pack.RISK_TABLE_ADDITIONS) == expected
