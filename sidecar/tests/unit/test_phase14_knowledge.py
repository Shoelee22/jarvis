"""Phase 14 — Offline knowledge pack tests.

Covers: dataset minimums (118 elements, 150+ countries, 20+ constants,
60+ timeline events, 30+ science facts), element field/type sanity,
famous-value spot checks (Au=79, France=Paris/EUR, c=299792458,
water boils at 100C), ranked search for "gold"/"Paris"/"relativity",
stats honesty (counts match the JSONs exactly), index rebuild when a
JSON is newer than the db, the Python fallback engine when FTS5 is
forced off, the registration contract, and never-raise handlers.
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, "sidecar")

import pytest

from jarvis.tools.builtin import knowledge_pack as kp


def _json(name):
    return json.loads((kp.DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    """Isolate the search index: tests never touch the real knowledge.db."""
    db = tmp_path / "knowledge.db"
    monkeypatch.setenv("JARVIS_KNOWLEDGE_DB", str(db))
    monkeypatch.setattr(kp, "_CACHE", {})
    monkeypatch.setattr(kp, "_FTS5_OK", None)
    return db


def _meta(db):
    conn = sqlite3.connect(str(db))
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataset minimums + schema sanity
# ---------------------------------------------------------------------------
def test_dataset_minimums():
    assert len(_json("elements")) == 118
    assert len(_json("countries")) >= 150
    assert len(_json("constants")) >= 20
    assert len(_json("timeline")) >= 60
    assert len(_json("science_facts")) >= 30


def test_element_schema_and_types():
    elements = _json("elements")
    assert sorted(e["number"] for e in elements) == list(range(1, 119))
    for e in elements:
        assert isinstance(e["symbol"], str) and e["symbol"]
        assert isinstance(e["name"], str) and e["name"]
        assert isinstance(e["number"], int) and 1 <= e["number"] <= 118
        assert isinstance(e["mass"], (int, float)) and e["mass"] > 0
        assert isinstance(e["group"], int) and 1 <= e["group"] <= 18
        assert isinstance(e["period"], int) and 1 <= e["period"] <= 7
        assert e["mass_kind"] in ("weight", "isotope")


def test_element_spot_checks():
    by_number = {e["number"]: e for e in _json("elements")}
    au = by_number[79]
    assert au["symbol"] == "Au" and au["name"] == "Gold"
    assert au["group"] == 11 and au["period"] == 6
    assert by_number[1]["symbol"] == "H"
    assert by_number[118]["symbol"] == "Og"


def test_country_schema():
    for c in _json("countries"):
        assert set(c) == {"name", "capital", "currency", "continent"}
        assert len(c["currency"]) == 3 and c["currency"].isupper()


# ---------------------------------------------------------------------------
# Famous-value spot checks through the handlers
# ---------------------------------------------------------------------------
def test_knowledge_element_lookup(tmpdb):
    out = kp.knowledge_element_handler({"symbol": "Au"})
    assert out["element"]["number"] == 79
    out = kp.knowledge_element_handler({"name": "gold"})
    assert out["element"]["symbol"] == "Au"
    out = kp.knowledge_element_handler({"number": 79})
    assert out["element"]["name"] == "Gold"
    out = kp.knowledge_element_handler({"q": "79"})
    assert out["element"]["symbol"] == "Au"
    out = kp.knowledge_element_handler({"symbol": "au"})  # case-insensitive
    assert out["element"]["number"] == 79
    out = kp.knowledge_element_handler({"symbol": "Xx"})
    assert "error" in out and "suggestions" in out
    out = kp.knowledge_element_handler({"number": 999})
    assert "error" in out


def test_knowledge_country_lookup(tmpdb):
    out = kp.knowledge_country_handler({"name": "France"})
    assert out["country"]["capital"] == "Paris"
    assert out["country"]["currency"] == "EUR"
    assert out["country"]["continent"] == "Europe"
    out = kp.knowledge_country_handler({"name": "france"})  # case-insensitive
    assert out["country"]["capital"] == "Paris"
    out = kp.knowledge_country_handler({"name": "Frnace"})  # fuzzy
    assert out["country"]["name"] == "France"
    out = kp.knowledge_country_handler({"name": "Atlantis"})
    assert "error" in out


def test_knowledge_constant_lookup(tmpdb):
    out = kp.knowledge_constant_handler({"symbol": "c"})
    assert out["constant"]["value"] == 299792458
    assert out["constant"]["unit"] == "m/s"
    out = kp.knowledge_constant_handler({"name": "speed of light"})
    assert out["constant"]["value"] == 299792458
    out = kp.knowledge_constant_handler({"symbol": "π"})
    assert out["constant"]["value"] == pytest.approx(3.141592653589793)
    out = kp.knowledge_constant_handler({"name": "no such constant"})
    assert "error" in out


def test_water_boils_fact_present():
    facts = _json("science_facts")
    matches = [f for f in facts
               if "boil" in f["title"].lower() and "100" in f["fact"]]
    assert matches, "expected a 'water boils at 100C' fact"


def test_knowledge_timeline_range(tmpdb):
    out = kp.knowledge_timeline_handler({"from_year": 1939, "to_year": 1945})
    assert out["count"] >= 3
    years = [e["year"] for e in out["events"]]
    assert years == sorted(years)
    titles = [e["title"] for e in out["events"]]
    assert "World War II begins" in titles
    assert "World War II ends" in titles
    # BCE range works with negative years.
    out = kp.knowledge_timeline_handler({"from_year": -500, "to_year": -400})
    assert any(e["year"] == -490 for e in out["events"])  # Marathon
    assert all(e["year_label"].endswith("BCE") for e in out["events"])
    # Empty range is fine; inverted range is an error.
    out = kp.knowledge_timeline_handler({"from_year": 1800, "to_year": 1800})
    assert out["count"] == 0 and out["events"] == []
    assert "error" in kp.knowledge_timeline_handler(
        {"from_year": 2000, "to_year": 1900})
    assert "error" in kp.knowledge_timeline_handler({})


# ---------------------------------------------------------------------------
# Ranked search
# ---------------------------------------------------------------------------
def test_knowledge_ask_ranked(tmpdb):
    out = kp.knowledge_ask_handler({"query": "gold"})
    assert out["engine"] == "fts5"
    assert out["count"] > 0
    # BM25 legitimately ranks the "Gold's symbol" science fact (two mentions)
    # above the element entry (one); both must be present.
    assert any(h["dataset"] == "elements" and "Gold (Au)" in h["title"]
               for h in out["hits"])

    out = kp.knowledge_ask_handler({"query": "Paris"})
    assert out["count"] > 0
    assert "countries" in {h["dataset"] for h in out["hits"]}

    out = kp.knowledge_ask_handler({"query": "relativity"})
    assert out["count"] > 0
    assert any(h["dataset"] == "timeline" and "Einstein" in h["title"]
               for h in out["hits"])

    for hit in out["hits"]:
        assert set(hit) >= {"dataset", "title", "snippet", "matched_field",
                            "rank"}
    assert "error" in kp.knowledge_ask_handler({"query": "   "})
    assert "error" in kp.knowledge_ask_handler({"query": "gold", "n": "x"})


def test_knowledge_stats_honest(tmpdb):
    out = kp.knowledge_stats_handler({})
    expected = {d: len(_json(d)) for d in kp.DATASETS}
    assert out["datasets"] == expected
    assert out["total_documents"] == sum(expected.values())
    assert out["fts5_available"] is True
    assert out["index"]["engine"] == "fts5"
    assert out["index"]["documents_indexed"] == sum(expected.values())
    assert out["db_path"].endswith("knowledge.db")


# ---------------------------------------------------------------------------
# Index lifecycle: rebuild when JSONs are newer; fallback engine
# ---------------------------------------------------------------------------
def test_index_rebuilds_when_json_newer(tmpdb):
    kp.knowledge_ask_handler({"query": "gold"})
    before = _meta(tmpdb)["source_mtime"]
    target = kp.DATA_DIR / "constants.json"
    orig = os.stat(target).st_mtime
    try:
        os.utime(target, (time.time() + 120, time.time() + 120))
        kp.knowledge_ask_handler({"query": "gold"})
        after = _meta(tmpdb)["source_mtime"]
        assert float(after) > float(before)
    finally:
        os.utime(target, (orig, orig))


def test_python_fallback_engine_when_fts5_forced_off(tmpdb, monkeypatch):
    monkeypatch.setattr(kp, "_FTS5_OK", False)
    out = kp.knowledge_ask_handler({"query": "gold"})
    assert out["engine"] == "python-fallback"
    assert out["count"] > 0
    assert any(h["dataset"] == "elements" and "Gold (Au)" in h["title"]
               for h in out["hits"])
    stats = kp.knowledge_stats_handler({})
    assert stats["fts5_available"] is False
    assert stats["index"]["engine"] == "python-fallback"


# ---------------------------------------------------------------------------
# Registration contract + never-raise handlers
# ---------------------------------------------------------------------------
def test_register_contract_and_risk_table():
    from jarvis.tools.base import Registry
    reg = Registry()
    kp.register(reg)
    expected = {"knowledge.ask", "knowledge.element", "knowledge.country",
                "knowledge.constant", "knowledge.timeline", "knowledge.stats"}
    assert expected <= set(reg.tools)
    for name in expected:
        assert reg.tools[name].risk == "low", name
        assert reg.tools[name].needs_network is False, name
    assert kp.RISK_TABLE_ADDITIONS == {n: ("low", False) for n in expected}
    assert len(kp.TOOL_DEFS) == 6
    for spec in kp.TOOL_DEFS:
        assert {"name", "description", "handler", "risk", "needs_network",
                "schema"} <= set(spec)


def test_handlers_never_raise_on_bad_input(tmpdb):
    assert "error" in kp.knowledge_ask_handler(None)
    # Non-string query is coerced, out-of-range n is clamped — no raise.
    out = kp.knowledge_ask_handler({"query": 123, "n": -5})
    assert isinstance(out, dict) and "error" not in out
    assert "error" in kp.knowledge_element_handler(None)
    assert "error" in kp.knowledge_element_handler({})
    assert "error" in kp.knowledge_country_handler(None)
    assert "error" in kp.knowledge_country_handler({})
    assert "error" in kp.knowledge_constant_handler(None)
    assert "error" in kp.knowledge_constant_handler({})
    assert "error" in kp.knowledge_timeline_handler(None)
    assert "error" in kp.knowledge_timeline_handler(
        {"from_year": "soon", "to_year": "later"})
    assert "error" not in kp.knowledge_stats_handler(None)
