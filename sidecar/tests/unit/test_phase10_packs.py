"""Phase 10 pack tests: structural contract for all 9 packs, math spot-checks,
and honesty paths (missing packages / config / network -> actionable errors).

Run from ~/workspace/jarvis/sidecar:
    python -m pytest tests/unit/test_phase10_packs.py -q
"""
import json
import sqlite3
import urllib.request

import pytest

from jarvis.tools.base import Registry
from jarvis.tools.builtin import (
    pdf_pack, translate_pack, finance_pack, travel_pack, edu_pack,
    health_pack, devops_pack, news_pack, weather_pack,
)

PACKS = [pdf_pack, translate_pack, finance_pack, travel_pack, edu_pack,
         health_pack, devops_pack, news_pack, weather_pack]

VALID_RISKS = {"low", "medium", "high"}


# ------------------------------------------------------------------ contract
def test_tool_defs_structure():
    """Every TOOL_DEFS entry: name, description, callable handler, valid
    risk, needs_network bool, schema dict."""
    for pack in PACKS:
        assert isinstance(pack.TOOL_DEFS, list) and pack.TOOL_DEFS, pack
        for spec in pack.TOOL_DEFS:
            assert isinstance(spec["name"], str) and spec["name"], pack
            assert "." in spec["name"], pack
            assert isinstance(spec["description"], str) and spec["description"].strip(), spec
            assert callable(spec["handler"]), spec
            assert spec["risk"] in VALID_RISKS, spec
            assert isinstance(spec["needs_network"], bool), spec
            assert isinstance(spec["schema"], dict), spec


def test_risk_table_additions_match_1_to_1():
    for pack in PACKS:
        names = {s["name"] for s in pack.TOOL_DEFS}
        assert set(pack.RISK_TABLE_ADDITIONS) == names, pack
        for spec in pack.TOOL_DEFS:
            risk, net = pack.RISK_TABLE_ADDITIONS[spec["name"]]
            assert risk == spec["risk"]
            assert net == spec["needs_network"]


def test_risk_table_risks_valid():
    for pack in PACKS:
        for name, (risk, net) in pack.RISK_TABLE_ADDITIONS.items():
            assert risk in VALID_RISKS, (pack, name)
            assert isinstance(net, bool), (pack, name)


def test_register_against_real_registry():
    reg = Registry()
    for pack in PACKS:
        pack.register(reg)
    specs = {s["name"]: s for s in reg.spec_list()}
    for pack in PACKS:
        for spec in pack.TOOL_DEFS:
            assert spec["name"] in specs, spec["name"]
            tool = reg.tools[spec["name"]]
            assert tool.risk == spec["risk"]
            assert tool.needs_network == spec["needs_network"]
            # Handlers are wired to the registered Tool objects.
            out = tool.handler({})
            assert isinstance(out, dict)


def test_no_duplicate_tool_names():
    names = [s["name"] for p in PACKS for s in p.TOOL_DEFS]
    assert len(names) == len(set(names))


def test_needs_network_flags_sane():
    flagged = {s["name"] for p in PACKS for s in p.TOOL_DEFS if s["needs_network"]}
    assert flagged == {"travel.flight_search", "news.headlines", "news.digest",
                       "weather.now", "weather.forecast"}


# ------------------------------------------------------------------- finance
def test_finance_emi_known_value():
    out = finance_pack.finance_emi({"principal": 100000, "annual_rate_pct": 12,
                                    "months": 12})
    assert "error" not in out, out
    assert abs(out["emi_paise"] / 100 - 8884.88) < 0.02, out
    assert out["total_interest_paise"] == out["total_payable_paise"] - 100000 * 100


def test_finance_emi_zero_rate():
    out = finance_pack.finance_emi({"principal": 120000, "annual_rate_pct": 0,
                                    "months": 12})
    assert out["emi_paise"] == 120000 * 100 // 12


def test_finance_sip_order_of_magnitude():
    out = finance_pack.finance_sip({"monthly": 10000, "annual_return_pct": 12,
                                    "years": 10})
    assert "error" not in out, out
    fv = out["future_value_paise"] / 100
    assert 2_300_000 < fv < 2_350_000, out  # ~23.2 lakh
    assert len(out["yearly_schedule"]) == 10
    assert out["gains_paise"] == out["future_value_paise"] - out["invested_paise"]


def test_finance_lumpsum_known_value():
    out = finance_pack.finance_lumpsum({"principal": 100000,
                                        "annual_return_pct": 10, "years": 5})
    assert abs(out["future_value_paise"] / 100 - 161051.0) < 0.02, out


def test_finance_validation():
    assert "error" in finance_pack.finance_emi({"principal": -5,
                                                "annual_rate_pct": 12, "months": 12})
    assert "error" in finance_pack.finance_sip({"monthly": 1000,
                                                "annual_return_pct": 200, "years": 5})
    assert "error" in finance_pack.finance_emi({"annual_rate_pct": 12, "months": 12})


def test_finance_budget_summary_honest_when_no_store(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.config.DATA_DIR", str(tmp_path))
    out = finance_pack.finance_budget_summary({})
    assert "error" in out and "finance.db" in out["error"], out


def test_finance_budget_summary_reads_expenses_store(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.config.DATA_DIR", str(tmp_path))
    db = tmp_path / "finance.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE expenses (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                 " amount_paise INTEGER NOT NULL, category TEXT NOT NULL,"
                 " note TEXT, created_at TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO expenses (amount_paise, category, note, created_at)"
        " VALUES (?, ?, ?, ?)",
        [(25000, "food", "lunch", "2026-09-01T12:00:00+00:00"),
         (15000, "food", "dinner", "2026-09-02T12:00:00+00:00"),
         (500000, "rent", "sep", "2026-09-03T12:00:00+00:00")])
    conn.commit()
    conn.close()
    out = finance_pack.finance_budget_summary({"month": "2026-09"})
    assert "error" not in out, out
    assert out["grand_total_paise"] == 540000, out
    assert out["top_category"] == "rent"
    assert out["expense_count"] == 3
    assert "error" in finance_pack.finance_budget_summary({"month": "09-2026"})


# -------------------------------------------------------------------- health
def test_health_bmi_known_value():
    out = health_pack.health_bmi({"weight_kg": 70, "height_cm": 175})
    assert out["bmi"] == 22.86, out
    assert out["who_category"] == "healthy weight"


def test_health_bmr_mifflin():
    out = health_pack.health_bmr({"weight_kg": 70, "height_cm": 175,
                                  "age": 30, "sex": "male"})
    assert out["bmr_kcal_per_day"] == 1648.75, out
    female = health_pack.health_bmr({"weight_kg": 70, "height_cm": 175,
                                     "age": 30, "sex": "female"})
    assert female["bmr_kcal_per_day"] == 1482.75, female


def test_health_tdee():
    out = health_pack.health_tdee({"bmr": 1648.75,
                                   "activity_level": "moderate"})
    assert out["tdee_kcal_per_day"] == 2555.56, out
    assert "error" in health_pack.health_tdee({"bmr": 1500,
                                               "activity_level": "hyper"})
    assert "error" in health_pack.health_bmi({"weight_kg": 0, "height_cm": 175})


def test_health_workout_plan_structure():
    out = health_pack.health_workout_plan({"goal": "strength", "level": "beginner",
                                           "days_per_week": 3})
    assert len(out["schedule"]) == 3
    assert all(len(d["exercises"]) == 3 for d in out["schedule"])
    assert "honest_note" in out
    assert "error" in health_pack.health_workout_plan({"goal": "telekinesis"})


# ----------------------------------------------------------------- pdf honesty
def test_pdf_honesty_without_pypdf(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_pack, "_pypdf", lambda: None)
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    out = pdf_pack.pdf_extract_text({"file": str(f)})
    assert "error" in out and "pip install pypdf" in out["error"], out
    out = pdf_pack.pdf_merge({"files": [str(f), str(f)],
                              "output": str(tmp_path / "o.pdf")})
    assert "pip install pypdf" in out["error"]
    out = pdf_pack.pdf_info({"file": str(f)})
    assert "pip install pypdf" in out["error"]


def test_pdf_missing_file_errors(monkeypatch, tmp_path):
    # Honest even about missing inputs (independent of pypdf presence).
    out = pdf_pack.pdf_info({"file": str(tmp_path / "nope.pdf")})
    if "error" in out:
        assert "not found" in out["error"] or "pip install pypdf" in out["error"]


def test_pdf_page_spec_validation():
    pages, err = pdf_pack._parse_pages("1-3,5", 10)
    assert pages == [0, 1, 2, 4] and err is None
    _, err = pdf_pack._parse_pages("11", 10)
    assert err and "out of bounds" in err
    pages, err = pdf_pack._parse_pages(None, 10)
    assert pages is None and err is None


# ----------------------------------------------------------- translate honesty
def test_translate_honesty_without_packages(monkeypatch):
    monkeypatch.setattr(translate_pack, "_argos", lambda: False)
    out = translate_pack.translate_text({"text": "hello", "target_lang": "es"})
    assert "error" in out and "pip install argostranslate" in out["error"], out
    monkeypatch.setattr(translate_pack, "_langdetect", lambda: False)
    out = translate_pack.translate_detect({"text": "hello world test text"})
    assert "error" in out and "pip install langdetect" in out["error"], out


def test_translate_validation(monkeypatch):
    monkeypatch.setattr(translate_pack, "_argos", lambda: True)
    out = translate_pack.translate_text({"text": "  ", "target_lang": "es"})
    assert "error" in out  # empty text rejected before touching argos


# -------------------------------------------------------------- devops honesty
def test_docker_ps_honesty_when_missing(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    out = devops_pack.docker_ps({})
    assert "error" in out and "docker" in out["error"].lower(), out


def test_git_status_honest_errors(tmp_path, monkeypatch):
    out = devops_pack.git_status({"repo": str(tmp_path / "nope")})
    assert "error" in out and "does not exist" in out["error"]
    import shutil
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    out = devops_pack.git_status({"repo": str(tmp_path)})
    assert "error" in out and "git not found" in out["error"]


# ---------------------------------------------------------------- news honesty
def test_news_honesty_without_feedparser(monkeypatch):
    monkeypatch.setattr(news_pack, "_feedparser", lambda: None)
    out = news_pack.news_headlines({"feed": "bbc_world"})
    assert "error" in out and "pip install feedparser" in out["error"], out
    out = news_pack.news_digest({"feed": "bbc_world", "count": 3})
    assert "pip install feedparser" in out["error"]


def test_news_preset_resolution():
    url, err = news_pack._resolve_feed("bbc_world")
    assert url == "https://feeds.bbci.co.uk/news/world/rss.xml" and err is None
    url, err = news_pack._resolve_feed("nope")
    assert err and "unknown preset" in err
    url, err = news_pack._resolve_feed("")
    assert err and "feed is required" in err


def test_news_parses_feed_with_fake_feedparser(monkeypatch):
    class Entry:
        def __init__(self):
            self.title, self.link = "T", "http://x"
            self.published, self.updated, self.summary = "p", "", "s"

    class FakeFeed:
        bozo, entries = 0, [Entry()]

        class feed:
            title = "Fake"

    monkeypatch.setattr(news_pack, "_feedparser",
                        lambda: type("fp", (), {"parse": staticmethod(lambda u: FakeFeed())}))
    monkeypatch.setattr(news_pack, "_egress_ok", lambda tool, url: (True, None))
    out = news_pack.news_digest({"feed": "https://example.com/rss", "count": 1})
    assert out["digest"][0]["title"] == "T", out


# ------------------------------------------------------------------ weather
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_urlopen(monkeypatch, payload):
    seen = {}

    def fake(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResp(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(weather_pack, "_egress_ok", lambda tool: None)
    return seen


def test_weather_now_parses_fixture(monkeypatch):
    seen = _mock_urlopen(monkeypatch, {
        "timezone": "Asia/Kolkata",
        "current": {"time": "2026-09-27T10:00", "temperature_2m": 31.2,
                    "relative_humidity_2m": 64, "weather_code": 2,
                    "wind_speed_10m": 12.5}})
    out = weather_pack.weather_now({"latitude": 28.6, "longitude": 77.2})
    assert "error" not in out, out
    assert out["temperature_c"] == 31.2
    assert out["conditions"] == "partly cloudy"
    assert "latitude=28.6" in seen["url"] and "current=" in seen["url"]
    assert seen["url"].startswith("https://api.open-meteo.com/v1/forecast")


def test_weather_forecast_parses_fixture(monkeypatch):
    seen = _mock_urlopen(monkeypatch, {
        "daily": {"time": ["2026-09-27", "2026-09-28"],
                  "temperature_2m_max": [33.0, 32.0],
                  "temperature_2m_min": [24.0, 23.5],
                  "weathercode": [0, 61]}})
    out = weather_pack.weather_forecast({"latitude": 28.6, "longitude": 77.2,
                                         "days": 2})
    assert "error" not in out, out
    assert len(out["days"]) == 2
    assert out["days"][0]["conditions"] == "clear sky"
    assert out["days"][1]["max_c"] == 32.0
    assert "daily=" in seen["url"]


def test_weather_network_failure_honest(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.request.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(weather_pack, "_egress_ok", lambda tool: None)
    out = weather_pack.weather_now({"latitude": 28.6, "longitude": 77.2})
    assert "error" in out and "boom" in out["error"], out


def test_weather_coord_validation():
    assert "error" in weather_pack.weather_now({"latitude": 999, "longitude": 0})
    assert "error" in weather_pack.weather_now({})


# ------------------------------------------------------------------- travel
def test_travel_itinerary_is_labeled_template():
    out = travel_pack.travel_itinerary({"destination": "Jaipur", "days": 3,
                                        "interests": ["food", "history"]})
    assert "error" not in out, out
    assert len(out["itinerary"]) == 3
    assert out["itinerary"][0]["theme"] == "arrival + orientation"
    assert "TEMPLATE STARTER" in out["honest_note"]
    assert "error" in travel_pack.travel_itinerary({"destination": "X", "days": 0})


def test_travel_packing_list_logic():
    out = travel_pack.travel_packing_list({"destination": "Goa", "days": 7,
                                           "trip_type": "beach"})
    assert "sunscreen SPF 50+" in out["trip_extras"]
    assert any("x 7" in c for c in out["clothing"]), out
    assert "error" in travel_pack.travel_packing_list({"destination": "X",
                                                       "days": 3,
                                                       "trip_type": "space"})


def test_travel_flight_search_honest_without_key(monkeypatch):
    monkeypatch.setattr(travel_pack, "_load_config", lambda: {})
    out = travel_pack.travel_flight_search({"origin": "DEL",
                                            "destination": "BOM",
                                            "date": "2026-10-10"})
    assert "error" in out and "Duffel" in out["error"], out
    assert "tools_config.yaml" in out["error"]


# ---------------------------------------------------------------------- edu
def test_edu_flashcards_from_terms():
    out = edu_pack.edu_flashcards({"topic": "bio", "count": 2,
                                   "terms": [{"term": "mitochondria",
                                              "definition": "powerhouse"},
                                             {"term": "nucleus",
                                              "definition": "control center"}]})
    assert out["cards"][0] == {"front": "mitochondria", "back": "powerhouse"}
    assert out["count"] == 2


def test_edu_flashcards_scaffold_is_honest():
    out = edu_pack.edu_flashcards({"topic": "chemistry", "count": 3})
    assert out["source"] == "scaffold"
    assert "SCAFFOLD ONLY" in out["honest_note"]
    assert len(out["cards"]) == 3


def test_edu_quiz_requires_word_list():
    assert "error" in edu_pack.edu_quiz({"topic": "x"})
    words = [{"term": f"t{i}", "definition": f"d{i}"} for i in range(5)]
    out = edu_pack.edu_quiz({"topic": "vocab", "count": 2, "word_list": words})
    q = out["questions"][0]
    assert len(q["options"]) == 4
    assert q["options"][q["answer_index"]] == q["answer"]


def test_edu_explain_is_outline_only():
    out = edu_pack.edu_explain({"topic": "photosynthesis"})
    assert len(out["outline"]) == 5
    assert "STUDY SCAFFOLD" in out["honest_note"]
