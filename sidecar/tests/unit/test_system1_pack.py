"""Unit tests for Phase 16 — JEV System 1 decision layer (system1_pack).

laya is NOT installed on the dev VM (and must stay optional), so every test
injects a FakeRouter through the pack's _get_router seam. Tests cover:

  * honest gating: disabled config / missing laya -> {"ok": False} errors,
    never simulated verdicts;
  * question building for ask/choose/score/batch (types, criteria shapes);
  * result slimming (p_yes/verdict, choice, score/label, batch answers);
  * validation errors (empty statement, <2 options, bad scale, bad question);
  * status reporting;
  * registration: all five tools present in the built registry.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from jarvis.tools.builtin import system1_pack as s1  # noqa: E402


# ------------------------------------------------------------------ fakes

class FakeRouter:
    """Mimics laya.Router.predict's result shape for each question type."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def predict(self, state, questions, **kwargs):
        self.calls.append({"state": state, "questions": questions, "kwargs": kwargs})
        out_answers = {}
        for name, q in questions.items():
            qtype = q["type"]
            if qtype == "noul":
                out_answers[name] = {"noul": 0.82}
            elif qtype == "choice":
                first = next(iter(q["criteria"]))
                out_answers[name] = {"choice": first, "scores": {first: 0.9}}
            elif qtype == "score":
                out_answers[name] = {"score": 2, "label": q["criteria"][2]}
        out_answers.update(self.answers)
        return {"answers": out_answers, "routing": {"model": "english"}}


@pytest.fixture(autouse=True)
def _fresh_router_cache():
    s1.reset_router_cache()
    yield
    s1.reset_router_cache()


def _inject_fake(monkeypatch, router=None):
    monkeypatch.setattr(s1, "_get_router", lambda: (router or FakeRouter(), None))
    monkeypatch.setattr(s1, "_cfg", lambda: {"enabled": True, "model": "auto"})


# ------------------------------------------------------------------ gating

def test_ask_disabled_returns_honest_error(monkeypatch):
    monkeypatch.setattr(s1, "_cfg", lambda: {"enabled": False})
    out = s1.ask_handler({"statement": "the sky is blue"})
    assert out["ok"] is False
    assert "disabled" in out["error"]
    assert out.get("simulated") is False


def test_missing_laya_returns_install_hint(monkeypatch):
    # _get_router with laya absent and enabled -> install hint, no verdict
    monkeypatch.setattr(s1, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(s1, "_ROUTER", None)
    monkeypatch.setattr(s1, "_ROUTER_ERROR", None)
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "laya":
            raise ImportError("No module named 'laya'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = s1.ask_handler({"statement": "hello"})
    assert out["ok"] is False
    assert "pip install laya" in out["error"]
    assert out.get("simulated") is False


# ------------------------------------------------------------------ ask

def test_ask_returns_p_yes_and_verdict(monkeypatch):
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = s1.ask_handler({"statement": "Please refund the duplicate charge today."})
    assert out["ok"] is True
    assert out["backend"] == "laya"
    assert out["result"]["p_yes"] == 0.82
    assert out["result"]["verdict"] == "yes"
    q = router.calls[0]["questions"]["verdict"]
    assert q["type"] == "noul"


def test_ask_requires_statement(monkeypatch):
    _inject_fake(monkeypatch)
    out = s1.ask_handler({"statement": "   "})
    assert out["ok"] is False


# ------------------------------------------------------------------ choose

def test_choose_picks_first_criteria(monkeypatch):
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = s1.choose_handler({
        "text": "we were billed twice",
        "options": {"billing": "invoices, payments, refunds",
                    "technical": "bugs, outages"},
    })
    assert out["ok"] is True
    assert out["result"]["choice"] == "billing"
    q = router.calls[0]["questions"]["pick"]
    assert q["type"] == "choice"
    assert q["criteria"]["billing"].startswith("invoices")


def test_choose_needs_two_options(monkeypatch):
    _inject_fake(monkeypatch)
    out = s1.choose_handler({"text": "hi", "options": {"only": "one"}})
    assert out["ok"] is False


# ------------------------------------------------------------------ score

def test_score_returns_label(monkeypatch):
    _inject_fake(monkeypatch)
    out = s1.score_handler({
        "text": "please fix this now",
        "scale": ["not urgent", "soon", "blocking"],
    })
    assert out["ok"] is True
    assert out["result"]["label"] == "blocking"
    assert out["result"]["score"] == 2


def test_score_needs_scale(monkeypatch):
    _inject_fake(monkeypatch)
    out = s1.score_handler({"text": "hi", "scale": ["only"]})
    assert out["ok"] is False


# ------------------------------------------------------------------ batch

def test_batch_single_forward_pass_all_types(monkeypatch):
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = s1.batch_handler({
        "text": "Billed twice for March, refund today or we cancel.",
        "questions": {
            "department": {"type": "choice", "instructions": "Which team?",
                           "criteria": {"billing": "invoices", "tech": "bugs"}},
            "urgency": {"type": "score", "instructions": "How urgent?",
                        "criteria": ["low", "mid", "high"]},
            "churn": {"type": "noul", "instructions": "Threatens to cancel?"},
        },
    })
    assert out["ok"] is True
    # ONE predict call for all three questions — the JEV fast path
    assert len(router.calls) == 1
    ans = out["result"]["answers"]
    assert ans["department"]["choice"] == "billing"
    assert ans["urgency"]["label"] == "high"
    assert ans["churn"]["p_yes"] if "p_yes" in ans["churn"] else ans["churn"]["noul"] == 0.82


def test_batch_rejects_bad_question_type(monkeypatch):
    _inject_fake(monkeypatch)
    out = s1.batch_handler({
        "text": "hi",
        "questions": {"q": {"type": "essay", "instructions": "write"}},
    })
    assert out["ok"] is False
    assert "choice|score|noul" in out["error"]


def test_batch_requires_instructions(monkeypatch):
    _inject_fake(monkeypatch)
    out = s1.batch_handler({
        "text": "hi",
        "questions": {"q": {"type": "noul"}},
    })
    assert out["ok"] is False


# ------------------------------------------------------------------ status

def test_status_reports_disabled(monkeypatch):
    monkeypatch.setattr(s1, "_cfg", lambda: {"enabled": False})
    out = s1.status_handler({})
    assert out["ok"] is True
    assert out["enabled"] is False
    assert out["available"] is False


# ------------------------------------------------------------------ registry

def test_all_five_tools_registered():
    import tempfile
    from jarvis.tools.builtin import build_registry
    reg = build_registry(tempfile.mkdtemp())
    names = {s["name"] for s in reg.spec_list()}
    for n in ("system1.ask", "system1.choose", "system1.score",
              "system1.batch", "system1.status"):
        assert n in names


# ============================================================ Phase 17 tests
# triage / duel / verdict cache / brain fusion

def _inject_fake_ttl(monkeypatch, router, ttl):
    monkeypatch.setattr(s1, "_get_router", lambda: (router, None))
    monkeypatch.setattr(s1, "_cfg", lambda: {"enabled": True, "model": "auto",
                                             "cache_ttl_sec": ttl})


def test_triage_routes_each_item(monkeypatch):
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = s1.triage_handler({
        "items": ["my invoice is wrong", "the app crashes on login"],
        "buckets": {"billing": "invoices, payments, refunds",
                    "technical": "bugs, crashes, outages"},
    })
    assert out["ok"] is True
    assert out["forward_passes"] == 2
    assert len(router.calls) == 2  # one forward pass per item
    assert out["routes"][0]["bucket"] == "billing"
    assert out["routes"][1]["bucket"] == "billing"  # fake picks first criteria


def test_triage_validates_input(monkeypatch):
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    assert s1.triage_handler({"items": [], "buckets": {"a": "x", "b": "y"}}
                             )["ok"] is False
    assert s1.triage_handler({"items": ["x"], "buckets": {"a": "x"}}
                             )["ok"] is False
    assert router.calls == []


def test_cache_hit_skips_router(monkeypatch):
    router = FakeRouter()
    _inject_fake_ttl(monkeypatch, router, 300)
    args = {"statement": "the sky is blue"}
    first = s1.ask_handler(args)
    second = s1.ask_handler(args)
    assert first["ok"] and second["ok"]
    assert len(router.calls) == 1
    assert second.get("cached") is True
    assert first.get("cached", False) is False


def test_cache_disabled_by_default(monkeypatch):
    router = FakeRouter()
    _inject_fake_ttl(monkeypatch, router, 0)
    args = {"statement": "the sky is blue"}
    s1.ask_handler(args)
    s1.ask_handler(args)
    assert len(router.calls) == 2


def test_cache_ttl_expiry(monkeypatch):
    router = FakeRouter()
    _inject_fake_ttl(monkeypatch, router, 300)
    args = {"statement": "the sky is blue"}
    s1.ask_handler(args)
    # age the cache entry past the TTL
    for k in list(s1._CACHE):
        ts, ans = s1._CACHE[k]
        s1._CACHE[k] = (ts - 400, ans)
    s1.ask_handler(args)
    assert len(router.calls) == 2


def _use_brain_db(tmp_path):
    import os
    from jarvis.tools.builtin import brain_pack
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    brain_pack.bind_agent(None)
    return brain_pack


def test_decide_system1_fusion(monkeypatch, tmp_path):
    from jarvis.tools.builtin import brain_pack
    _use_brain_db(tmp_path)
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = brain_pack.brain_decide_handler({
        "options": [{"name": "a", "text": "cheap and fast"},
                    {"name": "b", "text": "expensive but premium quality"}],
        "criteria": [{"name": "cost", "weight": 1}],
        "system1": True,
    })
    assert "winner" in out and "system1" in out
    assert out["system1"]["winner"] == "a"  # fake picks first criteria key
    assert isinstance(out["system1"]["agree"], bool)
    assert len(router.calls) == 1


def test_decide_system1_unavailable(monkeypatch, tmp_path):
    from jarvis.tools.builtin import brain_pack
    _use_brain_db(tmp_path)
    monkeypatch.setattr(s1, "_cfg", lambda: {"enabled": False})
    out = brain_pack.brain_decide_handler({
        "options": [{"name": "a", "text": "x"}, {"name": "b", "text": "y"}],
        "criteria": [{"name": "cost", "weight": 1}],
        "system1": True,
    })
    assert out["system1"]["winner"] is None
    assert out["system1"]["agree"] is None
    assert "unavailable" in out["system1"]["note"]


def test_think_instant_first_impressions(monkeypatch, tmp_path):
    from jarvis.tools.builtin import brain_pack
    _use_brain_db(tmp_path)
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = brain_pack.brain_think_handler({
        "question": "Should we refund the customer?",
        "context": "The customer was billed twice for the same order.",
        "instant": True,
    })
    assert "first_impressions" in out
    assert len(out["first_impressions"]) == len(out["sub_questions"])
    assert all(fi["verdict"] in ("yes", "no") for fi in out["first_impressions"])
    assert all("p_yes" in fi for fi in out["first_impressions"])
    assert len(router.calls) == 1  # one batched forward pass
    assert out["system1_note"] and "laya" in out["system1_note"]


def test_think_without_instant_has_empty_impressions(tmp_path):
    from jarvis.tools.builtin import brain_pack
    _use_brain_db(tmp_path)
    out = brain_pack.brain_think_handler({
        "question": "Should we refund the customer?",
        "context": "The customer was billed twice.",
    })
    assert out["first_impressions"] == []
    assert out["system1_note"] is None


def test_duel_options_mode_agree_flag(monkeypatch, tmp_path):
    _use_brain_db(tmp_path)
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = s1.duel_handler({
        "question": "Which plan is cheaper?",
        "options": {"basic": "cheap plan, low cost",
                    "pro": "expensive plan, premium quality"},
    })
    assert out["ok"] is True
    assert out["system1"]["ok"] is True
    assert out["system2"]["ok"] is True
    assert isinstance(out["agree"], bool)
    assert out["system1"]["simulated"] is False


def test_duel_ask_mode_no_mechanical_agree(monkeypatch, tmp_path):
    _use_brain_db(tmp_path)
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    out = s1.duel_handler({
        "question": "Is the customer owed a refund?",
        "context": "The customer was billed twice for the same order.",
    })
    assert out["ok"] is True
    assert out["system1"]["verdict"] in ("yes", "no")
    assert out["system2"]["verdict"]  # brain conclusion text
    assert out["agree"] is None  # different verdict types: no fake agreement


def test_duel_requires_question(monkeypatch):
    router = FakeRouter()
    _inject_fake(monkeypatch, router)
    assert s1.duel_handler({})["ok"] is False
    assert router.calls == []
