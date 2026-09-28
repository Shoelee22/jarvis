"""Phase 23 — Contradiction miner (offline).

Covers: opposite-stance pairs inside one topic cluster, same-stance not
paired, neutral never paired, cross-cluster claims not paired, empty /
degenerate inputs, method disclosure, the keyword fallback path, the
brain.survey {contradictions:true} opt-in (and default-off), and the
registration contract.
"""
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import metacog_pack


FOR = "Raising gym prices works and members see the value"
AGAINST = "Raising gym prices fails and churn is a real risk"
FOR2 = "Gym pricing is proven to increase revenue"
UNRELATED = "The weather in the hills is pleasant today"
NEUTRAL = "The gym pricing meeting is scheduled for Tuesday"


def _pair_texts(res):
    return [(p["claim_a"], p["claim_b"]) for p in res["contradictions"]]


def test_finds_opposing_pair_in_cluster():
    res = metacog_pack.contradictions_handler({"claims": [FOR, AGAINST]})
    assert "error" not in res, res
    assert len(res["contradictions"]) == 1
    p = res["contradictions"][0]
    assert {p["stance_a"], p["stance_b"]} == {"for", "against"}
    assert p["severity"] > 0


def test_same_stance_not_paired():
    res = metacog_pack.contradictions_handler({"claims": [FOR, FOR2]})
    assert "error" not in res, res
    assert res["contradictions"] == []


def test_neutral_never_paired():
    res = metacog_pack.contradictions_handler({"claims": [FOR, NEUTRAL]})
    assert "error" not in res, res
    assert res["contradictions"] == []


def test_cross_cluster_not_paired():
    res = metacog_pack.contradictions_handler({"claims": [FOR, UNRELATED]})
    assert "error" not in res, res
    assert res["contradictions"] == []
    assert res["n_clusters"] >= 2


def test_empty_and_degenerate_inputs():
    assert "error" in metacog_pack.contradictions_handler({"claims": []})
    assert "error" in metacog_pack.contradictions_handler({})
    res = metacog_pack.contradictions_handler({"claims": ["only one"]})
    assert res["contradictions"] == []
    assert "fewer than 2" in res["note"]


def test_method_discloses_heuristic():
    res = metacog_pack.contradictions_handler({"claims": [FOR, AGAINST]})
    assert "heuristic" in res["method"]
    assert "jaccard" in res["method"] or "cosine" in res["method"]
    assert "not verdicts" in res["read"]


def test_dict_claims_accepted():
    res = metacog_pack.contradictions_handler(
        {"claims": [{"text": FOR}, {"text": AGAINST}]})
    assert len(res["contradictions"]) == 1


def test_pairs_sorted_by_severity():
    res = metacog_pack.contradictions_handler(
        {"claims": [FOR, AGAINST, FOR2]})
    sevs = [p["severity"] for p in res["contradictions"]]
    assert sevs == sorted(sevs, reverse=True)


# ---------------------------------------------------------------------------
# survey integration
# ---------------------------------------------------------------------------

class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    metacog_pack.register(reg)
    assert "brain.contradictions" in reg.tools
    assert "brain.contradictions" in metacog_pack.RISK_TABLE_ADDITIONS


def test_survey_default_off(tmp_path, monkeypatch):
    import os
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    from jarvis.tools.builtin import cognition_pack
    res = cognition_pack.survey_handler({"goal": "gym pricing decision"})
    assert "error" not in res, res
    assert "contradictions" not in res
    assert "Tensions to resolve" not in res["brief_text"]


def test_survey_opt_in_runs_miner(tmp_path, monkeypatch):
    import os
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog2.db")
    from jarvis.tools.builtin import cognition_pack, brain_pack
    # seed two opposing lessons in the brain store
    conn = brain_pack._connect()
    try:
        conn.execute(
            "INSERT INTO lessons(created_at, text, normalized, source)"
            " VALUES (datetime('now'),?,?,?)",
            (FOR, FOR.lower(), "test"))
        conn.execute(
            "INSERT INTO lessons(created_at, text, normalized, source)"
            " VALUES (datetime('now'),?,?,?)",
            (AGAINST, AGAINST.lower(), "test"))
        conn.commit()
    finally:
        conn.close()
    res = cognition_pack.survey_handler(
        {"goal": "gym pricing", "contradictions": True})
    assert "error" not in res, res
    assert "contradictions" in res
    contra = res["contradictions"]
    assert len(contra["contradictions"]) >= 1
    assert "Tensions to resolve" in res["brief_text"]
