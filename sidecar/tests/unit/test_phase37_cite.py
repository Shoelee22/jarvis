"""Phase 37 — stronger cited deep research (offline)."""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import research_pack


def _seed(tmp_path):
    os.environ["JARVIS_RESEARCH_DB"] = str(tmp_path / "r.db")
    bid = "brief_testcite01"
    briefing = {
        "question": "What is the capital of France?",
        "claims": [
            {"claim": "Paris is the capital of France",
             "source_url": "https://example.com/france",
             "source_title": "France facts", "status": "verified"},
            {"claim": "Paris has about 2M residents",
             "source_url": "https://example.com/france",
             "source_title": "France facts", "status": "verified"},
            {"claim": "The mayor owns a unicorn",
             "source_url": None, "source_title": None,
             "status": "could not verify", "reason": "no source found"},
        ],
        "contradictions": [],
    }
    research_pack._save_briefing(bid, briefing["question"],
                                 "investigate", briefing)
    return bid


def test_cite_numbers_and_dedupes_sources(tmp_path):
    bid = _seed(tmp_path)
    r = research_pack.research_cite_handler({"id": bid})
    assert r.get("ok"), r
    assert r["stats"] == {"claims_cited": 2, "claims_unverified": 1,
                          "sources": 1}
    assert r["cited"][0]["citation"] == 1
    assert r["cited"][1]["citation"] == 1  # same source, same number
    assert r["unverified"][0]["claim"] == "The mayor owns a unicorn"


def test_report_marks_unverified_never_cited(tmp_path):
    bid = _seed(tmp_path)
    report = research_pack.research_cite_handler({"id": bid})["report"]
    assert "- Paris is the capital of France [1]" in report
    assert "## Unverified (not cited" in report
    assert "[1] France facts — https://example.com/france" in report
    # the unverified claim has no citation marker
    for line in report.splitlines():
        if "unicorn" in line:
            assert "[" not in line.split("unicorn")[0][-4:]


def test_cite_unknown_id_honest(tmp_path):
    os.environ["JARVIS_RESEARCH_DB"] = str(tmp_path / "r.db")
    r = research_pack.research_cite_handler({"id": "brief_nope"})
    assert "error" in r and "no briefing" in r["error"]


def test_registration_contract():
    spec = next(s for s in research_pack.TOOL_DEFS
                if s["name"] == "research.cite")
    assert spec["risk"] == "low" and spec["needs_network"] is False
    assert research_pack.RISK_TABLE_ADDITIONS["research.cite"] == (
        "low", False)
