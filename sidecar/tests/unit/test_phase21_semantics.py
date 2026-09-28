"""Phase 21 — Semantic brain tests (offline, fake embedding backend).

The real sentence-transformers backend is NOT installed in CI, so every
test uses an injectable deterministic fake: texts containing 'gym' map to
[1,0,0], texts containing 'cat' map to [0,1,0], everything else to
[0,0,1]. These tests verify the WIRING (cosine used when available,
honest fallback when not) — not that embeddings are good. The empirical
claim (semantic beats keyword on real text) is checked by
sidecar/semantics_benchmark.py on a machine with the real model.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import (
    brain_pack, cognition_pack, episodic_pack, semantics_pack, system1_pack,
)


def _group_fake(texts):
    vecs = []
    for t in texts:
        tl = str(t).lower()
        if "gym" in tl:
            vecs.append([1.0, 0.0, 0.0])
        elif "cat" in tl:
            vecs.append([0.0, 1.0, 0.0])
        else:
            vecs.append([0.0, 0.0, 1.0])
    return vecs


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    os.environ["JARVIS_EPISODIC_DB"] = str(tmp_path / "episodic.db")
    os.environ["JARVIS_KNOWLEDGE_DB"] = str(tmp_path / "knowledge.db")
    brain_pack.bind_agent(None)


def _with_fake():
    semantics_pack.clear_backend_override()
    semantics_pack._backend = None
    semantics_pack._backend_error = None
    semantics_pack.set_backend_override(_group_fake)


def _without_backend():
    semantics_pack.clear_backend_override()
    semantics_pack._backend = None
    semantics_pack._backend_error = None


# ---------------------------------------------------------------------------
# the five semantic tools
# ---------------------------------------------------------------------------

def test_embed_returns_vector_and_dim():
    _with_fake()
    res = semantics_pack.embed_handler({"text": "gym prices"})
    assert "error" not in res, res
    assert res["dim"] == 3
    assert res["embedding"] == [1.0, 0.0, 0.0]
    assert res["model"] == "test-override"


def test_embed_requires_text():
    _with_fake()
    assert "error" in semantics_pack.embed_handler({"text": "  "})


def test_embed_unavailable_is_honest():
    _without_backend()
    res = semantics_pack.embed_handler({"text": "gym prices"})
    assert "error" in res, res
    assert res["backend"] == "unavailable"
    assert res["simulated"] is False


def test_similar_paraphrase_vs_unrelated():
    _with_fake()
    same = semantics_pack.similar_handler({"a": "gym prices",
                                           "b": "gym costs"})
    diff = semantics_pack.similar_handler({"a": "gym prices",
                                           "b": "cat naps"})
    assert same["similarity"] == 1.0
    assert diff["similarity"] == 0.0
    assert "negation" in same["read"].lower()  # known failure mode is disclosed


def test_similar_unavailable_is_honest():
    _without_backend()
    res = semantics_pack.similar_handler({"a": "x", "b": "y"})
    assert res["backend"] == "unavailable"


def test_related_ranks_by_similarity():
    _with_fake()
    res = semantics_pack.related_handler({
        "query": "gym membership",
        "texts": ["cat naps quietly", "clouds drift slowly",
                  "gym membership fees"]})
    assert "error" not in res, res
    assert res["ranking"][0]["text"] == "gym membership fees"
    assert res["ranking"][0]["similarity"] == 1.0
    assert all(r["similarity"] == 0.0 for r in res["ranking"][1:])


def test_cluster_groups_near_duplicates():
    _with_fake()
    res = semantics_pack.cluster_handler({
        "texts": ["gym prices", "gym costs", "cat naps"],
        "threshold": 0.9})
    assert "error" not in res, res
    assert len(res["clusters"]) == 2
    big = max(res["clusters"], key=lambda c: c["size"])
    assert big["size"] == 2
    assert big["representative"] == "gym prices"


def test_cluster_bad_threshold_rejected():
    _with_fake()
    assert "error" in semantics_pack.cluster_handler(
        {"texts": ["a"], "threshold": 5})


def test_diverse_picks_spread():
    _with_fake()
    res = semantics_pack.diverse_handler({
        "texts": ["gym alpha", "cat beta", "sky gamma", "gym delta"],
        "n": 3})
    assert "error" not in res, res
    assert len(res["picks"]) == 3
    assert "gym delta" not in res["picks"]  # redundant with gym alpha


# ---------------------------------------------------------------------------
# think / decide semantic integration
# ---------------------------------------------------------------------------

THINK_Q = "Should the gym raise prices?"
THINK_CTX = ("The gym pricing works because revenue is proven. "
             "Members love the gym community and it brings benefits. "
             "The weather is nice today.")


def test_think_semantic_uses_cosine(tmp_path):
    _use_db(tmp_path)
    _with_fake()
    res = brain_pack.brain_think_handler(
        {"question": THINK_Q, "context": THINK_CTX, "semantic": True})
    assert "error" not in res, res
    assert res["scoring"].startswith("semantic:"), res["scoring"]
    assert "semantic_note" in res
    assert "cosine" in res["conclusion"].lower()
    assert res["evidence_items"] >= 1
    # every semantic weight must be a valid cosine in (0, 1]
    for ev in _trace_evidence(res["trace_id"]):
        assert 0.0 < ev["weight"] <= 1.0


def _trace_evidence(trace_id):
    conn = brain_pack._connect()
    try:
        rows = conn.execute(
            "SELECT evidence_json FROM traces WHERE trace_id=?",
            (trace_id,)).fetchone()
    finally:
        conn.close()
    import json
    return json.loads(rows[0]) if rows else []


def test_think_semantic_falls_back_when_unavailable(tmp_path):
    _use_db(tmp_path)
    _without_backend()
    res = brain_pack.brain_think_handler(
        {"question": THINK_Q, "context": THINK_CTX, "semantic": True})
    assert "error" not in res, res
    assert res["scoring"].startswith("heuristic:"), res["scoring"]
    assert "unavailable" in res["semantic_note"]


def test_think_default_stays_keyword(tmp_path):
    _use_db(tmp_path)
    _with_fake()
    res = brain_pack.brain_think_handler(
        {"question": THINK_Q, "context": THINK_CTX})
    assert "error" not in res, res
    assert res["scoring"].startswith("heuristic:")
    assert "semantic_note" not in res


def test_decide_semantic_scores_by_cosine(tmp_path):
    _use_db(tmp_path)
    _with_fake()
    res = brain_pack.brain_decide_handler({
        "options": [{"name": "raise", "text": "gym membership fees go up"},
                    {"name": "discount", "text": "cat food prices go down"}],
        "criteria": [{"name": "gym revenue", "weight": 1}],
        "semantic": True})
    assert "error" not in res, res
    assert res["scoring_note"].startswith("SEMANTIC:"), res["scoring_note"]
    assert res["winner"] == "raise", res["scorecard"]
    per = res["scorecard"][0]["per_criterion"][0]
    assert per["score"] == 10.0
    assert "cosine" in per["reason"]
    assert "negation" in res["scoring_note"]  # limitation disclosed


def test_decide_semantic_falls_back(tmp_path):
    _use_db(tmp_path)
    _without_backend()
    res = brain_pack.brain_decide_handler({
        "options": ["a one", "b two"],
        "criteria": [{"name": "c", "weight": 1}],
        "semantic": True})
    assert "error" not in res, res
    assert res["scoring_note"].startswith("HEURISTIC:")
    assert "unavailable" in res["semantic_note"]


# ---------------------------------------------------------------------------
# survey semantic re-rank
# ---------------------------------------------------------------------------

def _seed_two_episodes():
    episodic_pack.episode_handler({
        "title": "churn episode",
        "what_happened": "The pricing decision caused churn last quarter.",
        "when": "2025-01-01"})
    episodic_pack.episode_handler({
        "title": "gym win",
        "what_happened": "The gym pricing decision was approved smoothly.",
        "when": "2025-02-01"})


def test_semantic_rerank_unit():
    _with_fake()
    items = [{"text": "The pricing decision caused churn."},
             {"text": "The gym pricing decision was approved."}]
    ranked, used = cognition_pack._semantic_rerank(
        items, lambda e: e["text"], "gym pricing decision")
    assert used is True
    assert ranked[0]["text"].startswith("The gym")
    assert ranked[1]["text"].startswith("The pricing decision caused")


def test_semantic_rerank_noop_without_backend():
    _without_backend()
    items = [{"text": "b"}, {"text": "a"}]
    ranked, used = cognition_pack._semantic_rerank(
        items, lambda e: e["text"], "gym")
    assert used is False
    assert ranked == items


def test_survey_semantic_rerank_integration(tmp_path):
    _use_db(tmp_path)
    _with_fake()
    _seed_two_episodes()
    res = cognition_pack.survey_handler(
        {"goal": "gym pricing decision for alpha beta"})
    assert "error" not in res, res
    assert res["semantic_rerank"] is True, res
    assert res["sources"]["episodes"] == 2
    assert res["episodes"][0]["text"].startswith("The gym"), res["episodes"]


def test_survey_no_rerank_without_backend(tmp_path):
    _use_db(tmp_path)
    _without_backend()
    _seed_two_episodes()
    res = cognition_pack.survey_handler(
        {"goal": "gym pricing decision for alpha beta"})
    assert "error" not in res, res
    assert res["semantic_rerank"] is False
    assert res["sources"]["episodes"] == 2


# ---------------------------------------------------------------------------
# duel semantic agreement (system1 disabled here -> honest None, no crash)
# ---------------------------------------------------------------------------

def test_duel_no_crash_without_backends():
    _without_backend()
    res = system1_pack.duel_handler({
        "question": "Which plan?",
        "options": {"a": "raise gym prices", "b": "cut gym hours"}})
    assert res["ok"] is True
    assert res["agree"] is None  # system1 unavailable -> no mechanical compare
    assert res["agree_semantic"] is None  # semantic backend unavailable


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
    semantics_pack.register(reg)
    expected = {"brain.embed", "brain.similar", "brain.related",
                "brain.cluster", "brain.diverse"}
    assert set(reg.tools) == expected
    assert set(semantics_pack.RISK_TABLE_ADDITIONS) == expected
