"""Offline tests for the Phase 15 Deep Research pack (research_pack).

Exercised against fake registry doubles (brain.plan / web.search / web.fetch
stubs), a fake agent bound via research_pack.bind_agent, and a tmp sqlite
research DB.

Covers:
  - plan decomposition via a fake registry double (brain.plan persisted)
  - dedupe removes duplicate URLs / duplicate claim texts
  - contradiction flagging fires on synthetic conflicting claims
  - citations attached to every verified claim
  - honest failure when the fetch layer is unavailable (mocked to raise)
  - briefing round-trip persistence (investigate -> briefing -> read back)
  - unbound pack degrades honestly to plan-only
  - research.compare builds a cited table
  - handlers never raise; risk table additions are all low risk
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.research_pack as rp


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
PAGE_ALPHA = (
    "The Zephyr X200 weighs 2.1 kg and costs 1299 dollars. "
    "Its battery lasts 14 hours on a single charge. "
    "The Zephyr X200 weighs 2.1 kg and costs 1299 dollars."
)
PAGE_BETA = (
    "The Aero Z5 weighs 1.6 kg and costs 999 dollars. "
    "Its battery lasts 18 hours on a single charge. "
    "Reviewers praised the Aero Z5 display for its color accuracy."
)


def _make_registry(search_results=None, fetch_pages=None, raise_fetch=False):
    """Fake registry: .tools entries carry .risk; .call returns envelopes."""
    search_results = search_results if search_results is not None else []
    fetch_pages = fetch_pages if fetch_pages is not None else {}

    def h_plan(args):
        return {"plan_id": "plan_fake_1", "goal": args.get("goal"),
                "steps": len(args.get("steps") or [])}

    def h_search(args):
        return {"query": args.get("query"), "results": search_results}

    def h_fetch(args):
        if raise_fetch:
            raise RuntimeError("playwright browser unavailable")
        url = args.get("url")
        if url not in fetch_pages:
            return {"error": "fetch failed: 404"}
        return {"url": url, "title": f"title of {url}",
                "text": fetch_pages[url], "truncated": False}

    handlers = {"brain.plan": h_plan, "web.search": h_search,
                "web.fetch": h_fetch}

    class _FakeRegistry:
        def __init__(self):
            self.tools = {n: SimpleNamespace(risk="low") for n in handlers}
            self.calls = []

        def call(self, name, args, actor=None):
            self.calls.append(name)
            return {"ok": True, "result": handlers[name](args)}

    return _FakeRegistry()


class _FakeAgent:
    def __init__(self, registry):
        self.registry = registry


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_RESEARCH_DB", str(tmp_path / "research.db"))
    monkeypatch.setattr(rp, "_AGENT", None)
    yield tmp_path


def _bind(reg, monkeypatch):
    monkeypatch.setattr(rp, "_AGENT", _FakeAgent(reg))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_decompose_splits_into_subquestions():
    subs = rp._decompose(
        "What is the battery life of the Zephyr X200 and how much does it weigh?", 5)
    assert len(subs) >= 2
    assert any("battery" in s for s in subs)
    assert any("weigh" in s for s in subs)


def test_decompose_falls_back_to_raw_question():
    subs = rp._decompose("quokkas", 5)
    assert subs == ["quokkas"]


def test_investigate_persists_plan_via_fake_registry(tmpdb, monkeypatch):
    reg = _make_registry(
        search_results=[{"title": "t", "url": "https://a.example/p1"}],
        fetch_pages={"https://a.example/p1": PAGE_ALPHA})
    _bind(reg, monkeypatch)
    out = rp.research_investigate_handler({"question": "Zephyr X200 specs"})
    assert out.get("ok") is True
    assert out["executed"] is True
    assert out["plan"]["brain_plan"].get("plan_id") == "plan_fake_1"
    assert "brain.plan" in reg.calls
    # every plan step is a low-risk web.search
    assert all(s["tool"] == "web.search" for s in out["plan"]["steps"])


def test_dedupe_removes_duplicate_urls(tmpdb, monkeypatch):
    results = [
        {"title": "t1", "url": "https://a.example/p1"},
        {"title": "t1 dup", "url": "https://a.example/p1/"},  # same, slash
        {"title": "t2", "url": "https://b.example/p2"},
    ]
    pages = {"https://a.example/p1": PAGE_ALPHA,
             "https://b.example/p2": PAGE_BETA}
    reg = _make_registry(search_results=results, fetch_pages=pages)
    _bind(reg, monkeypatch)
    out = rp.research_investigate_handler(
        {"question": "Zephyr X200 and Aero Z5 weight and price"})
    assert out.get("ok") is True
    assert out["stats"]["duplicates_dropped"] >= 1
    urls = [c["source_url"] for c in out["claims"] if c.get("verified")]
    assert len(urls) == len({rp._norm_url(u) for u in urls})


def test_contradiction_flagging_fires_on_synthetic_conflict():
    findings = [
        {"claim": "The Zephyr X200 battery lasts 14 hours on a charge.",
         "source_url": "https://a.example/p1", "source_title": "A",
         "verified": True},
        {"claim": "Our tests show the Zephyr X200 battery lasts 9 hours.",
         "source_url": "https://c.example/p3", "source_title": "C",
         "verified": True},
    ]
    flags = rp._flag_contradictions(findings)
    assert len(flags) >= 1
    flag = flags[0]
    assert flag["detector"] == "heuristic"
    assert "14 hours" in flag["claim_a"] or "14" in " ".join(flag["numbers_a"])
    assert flag["source_a"] == "https://a.example/p1"
    assert flag["source_b"] == "https://c.example/p3"


def test_no_contradiction_when_numbers_agree():
    findings = [
        {"claim": "The Zephyr X200 battery lasts 14 hours on a charge.",
         "source_url": "https://a.example/p1", "verified": True},
        {"claim": "Zephyr X200 battery: 14 hours of video playback.",
         "source_url": "https://c.example/p3", "verified": True},
    ]
    assert rp._flag_contradictions(findings) == []


def test_citations_attached_to_every_verified_claim(tmpdb, monkeypatch):
    reg = _make_registry(
        search_results=[{"title": "t", "url": "https://a.example/p1"},
                        {"title": "t2", "url": "https://b.example/p2"}],
        fetch_pages={"https://a.example/p1": PAGE_ALPHA,
                     "https://b.example/p2": PAGE_BETA})
    _bind(reg, monkeypatch)
    out = rp.research_investigate_handler(
        {"question": "Zephyr X200 and Aero Z5 specs", "depth": 2})
    verified = [c for c in out["claims"] if c.get("verified")]
    assert verified, "expected some verified claims"
    for c in verified:
        assert c.get("source_url"), f"claim missing citation: {c!r}"
    # unverified entries are explicitly marked, never silent
    for c in out["claims"]:
        if not c.get("verified"):
            assert c.get("status") == "could not verify"


def test_fetch_unavailable_marks_could_not_verify(tmpdb, monkeypatch):
    reg = _make_registry(
        search_results=[{"title": "t", "url": "https://a.example/p1"}],
        raise_fetch=True)  # the fetch layer itself blows up
    _bind(reg, monkeypatch)
    out = rp.research_investigate_handler(
        {"question": "Zephyr X200 battery life"})
    assert out.get("ok") is True
    assert out["stats"]["searches_ok"] == 1
    assert out["stats"]["fetches_failed"] >= 1
    verified = [c for c in out["claims"] if c.get("verified")]
    assert verified == []
    assert any(u["status"] == "could not verify"
               for u in out["unverified"])


def test_search_failure_is_honest_not_invented(tmpdb, monkeypatch):
    class _NoNet(_FakeAgent):
        pass

    reg = _make_registry()
    # web.search envelope carries an error instead of results
    orig = reg.call

    def boom(name, args, actor=None):
        if name == "web.search":
            return {"ok": True, "result": {"error": "network unavailable"}}
        return orig(name, args, actor)

    reg.call = boom
    _bind(reg, monkeypatch)
    out = rp.research_investigate_handler({"question": "anything at all"})
    assert out["stats"]["searches_failed"] == 1
    assert all(u["status"] == "could not verify" for u in out["unverified"])
    assert "could not verify" in out["unverified"][0]["reason"] or \
        "network" in out["unverified"][0]["reason"]


def test_unbound_pack_returns_plan_only(tmpdb, monkeypatch):
    monkeypatch.setattr(rp, "_AGENT", None)
    out = rp.research_investigate_handler({"question": "Zephyr X200 specs"})
    assert out.get("ok") is True
    assert out["bound"] is False
    assert out["executed"] is False
    assert "bind_agent" in out["message"]
    assert len(out["plan"]["steps"]) >= 1
    assert "id" in out and out["note"].startswith("plan-only")


def test_briefing_round_trip(tmpdb, monkeypatch):
    reg = _make_registry(
        search_results=[{"title": "t", "url": "https://a.example/p1"}],
        fetch_pages={"https://a.example/p1": PAGE_ALPHA})
    _bind(reg, monkeypatch)
    inv = rp.research_investigate_handler(
        {"question": "Zephyr X200 round-trip check"})
    bid = inv["id"]
    back = rp.research_briefing_handler({"id": bid})
    assert back.get("ok") is True
    assert back["question"] == "Zephyr X200 round-trip check"
    assert back["briefing"]["claims"] == inv["claims"]
    assert back["kind"] == "investigate"


def test_briefing_unknown_id_is_honest_error(tmpdb):
    out = rp.research_briefing_handler({"id": "brief_deadbeefcafe"})
    assert "error" in out
    assert "no briefing" in out["error"]


def test_compare_builds_cited_table(tmpdb, monkeypatch):
    pages = {"https://a.example/p1": PAGE_ALPHA,
             "https://b.example/p2": PAGE_BETA}

    class _Smart(_FakeAgent):
        pass

    reg = _make_registry(fetch_pages=pages)
    # route each option's search to its own page
    orig = reg.call

    def routed(name, args, actor=None):
        if name == "web.search":
            q = args.get("query", "")
            url = "https://a.example/p1" if "X200" in q else "https://b.example/p2"
            return {"ok": True,
                    "result": {"query": q,
                               "results": [{"title": "t", "url": url}]}}
        return orig(name, args, actor)

    reg.call = routed
    _bind(reg, monkeypatch)
    out = rp.research_compare_handler(
        {"topic": "laptop", "options": ["Zephyr X200", "Aero Z5"]})
    assert out.get("ok") is True
    assert out["executed"] is True
    assert len(out["rows"]) == 2
    assert len(out["columns"]) >= 1  # heuristic attributes found
    assert out["extraction"] == "heuristic"
    for row in out["rows"]:
        assert len(row["cells"]) == len(out["columns"])
        for cell in row["cells"]:
            if cell["cited"]:
                assert cell["source_url"], "cited cell needs a URL"
    # round-trip through the briefing reader
    back = rp.research_briefing_handler({"id": out["id"]})
    assert back.get("ok") is True
    assert back["kind"] == "compare"


def test_compare_rejects_single_option(tmpdb):
    out = rp.research_compare_handler(
        {"topic": "laptop", "options": ["Zephyr X200"]})
    assert "error" in out


def test_compare_unbound_is_plan_only(tmpdb, monkeypatch):
    monkeypatch.setattr(rp, "_AGENT", None)
    out = rp.research_compare_handler(
        {"topic": "laptop", "options": ["A", "B"]})
    assert out.get("ok") is True
    assert out["executed"] is False
    assert "not bound" in out["message"]


def test_handlers_never_raise(tmpdb, monkeypatch):
    assert "error" in rp.research_investigate_handler(None)
    assert "error" in rp.research_investigate_handler({"question": ""})
    assert "error" in rp.research_investigate_handler({"question": "x", "depth": "z"})
    assert "error" in rp.research_briefing_handler({})
    assert "error" in rp.research_compare_handler({"topic": "", "options": []})


def test_risk_table_additions_are_low_risk():
    # Later phases extend the pack (research.cite) — the originals keep
    # their contract; every entry stays low risk.
    assert {"research.investigate", "research.briefing",
            "research.compare"} <= set(rp.RISK_TABLE_ADDITIONS)
    for name, (risk, net) in rp.RISK_TABLE_ADDITIONS.items():
        assert risk == "low", name
        assert isinstance(net, bool)


def test_tool_defs_shape_matches_teach_pack_contract():
    names = {t["name"] for t in rp.TOOL_DEFS}
    assert names == set(rp.RISK_TABLE_ADDITIONS)
    for t in rp.TOOL_DEFS:
        for key in ("name", "description", "handler", "risk",
                    "needs_network", "schema"):
            assert key in t, (t.get("name"), key)
        assert callable(t["handler"])
        assert t["risk"] == "low"


def test_register_wires_three_tools():
    from jarvis.tools.base import Registry
    reg = Registry()
    rp.register(reg)
    for name in ("research.investigate", "research.briefing",
                 "research.compare"):
        assert name in reg.tools
        assert reg.tools[name].risk == "low"
    assert reg.tools["research.investigate"].needs_network is True
    assert reg.tools["research.briefing"].needs_network is False
