"""Phase 15 Data Analyst pack tests.

Covers: profile exact numbers, group-by vs hand-computed values, free-text
parse echo, perfect-linear correlation, chart ASCII fallback (matplotlib
blocked) and real-PNG branch, clean never touching the original, path
containment refusing escapes, and register() wiring.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import sys

import pytest

import jarvis.tools.builtin.analyst_pack as ap
from jarvis.tools.base import Registry

CSV_DATA = (
    "name,region,sales,units\n"
    "Ada,North,100,2\n"
    "Bob,South,200,3\n"
    "Ada,North,100,2\n"       # exact duplicate of the first row
    "Cara, East ,300,1\n"     # whitespace padding
    "Dan,South,400,\n"        # null units
    "Eve,North,500,5\n"
    "Fay,South,600,6\n"
    "Gus,East,700,7\n"
    "Hana,North,800,8\n"
)

LINEAR_DATA = "x,y\n1,2\n2,4\n3,6\n4,8\n"


@pytest.fixture(autouse=True)
def _jailed_root(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_ROOT", str(tmp_path))
    return tmp_path


def _csv(tmp_path, name="sales.csv", content=CSV_DATA):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _sha256(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _col(stats, name):
    return next(c for c in stats["column_stats"] if c["name"] == name)


# ---------------------------------------------------------------- profile ---

def test_profile_exact_numbers(tmp_path):
    p = _csv(tmp_path)
    out = ap.profile_handler({"path": str(p)})
    assert "error" not in out, out
    assert out["rows"] == 9
    assert out["columns"] == ["name", "region", "sales", "units"]
    assert out["column_count"] == 4

    sales = _col(out, "sales")
    assert sales["inferred_type"] == "int"
    assert sales["null_count"] == 0
    assert sales["min"] == 100
    assert sales["max"] == 800

    units = _col(out, "units")
    assert units["inferred_type"] == "int"
    assert units["null_count"] == 1
    assert units["min"] == 1
    assert units["max"] == 8

    region = _col(out, "region")
    assert region["inferred_type"] == "str"
    assert region["null_count"] == 0
    assert region["distinct_count"] == 3  # North, South, East (stripped)

    name = _col(out, "name")
    assert name["distinct_count"] == 8  # Ada appears twice


def test_profile_empty_file_errors(tmp_path):
    p = _csv(tmp_path, name="empty.csv", content="")
    out = ap.profile_handler({"path": str(p)})
    assert "error" in out


# -------------------------------------------------------------------- ask ---

def test_ask_groupby_mean_hand_computed(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p), "question": {
        "op": "groupby", "by": "region", "agg": {"sales": "mean"}}})
    assert "error" not in out, out
    means = out["sales.mean"]
    assert means["North"] == pytest.approx(375.0)   # (100+100+500+800)/4
    assert means["South"] == pytest.approx(400.0)   # (200+400+600)/3
    assert means["East"] == pytest.approx(500.0)    # (300+700)/2
    assert "mean(sales) grouped by region" in out["performed"]


def test_ask_groupby_sum_skips_nulls(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p), "question": {
        "op": "groupby", "by": "region", "agg": {"units": "sum"}}})
    assert "error" not in out, out
    assert out["units.sum"]["North"] == pytest.approx(17.0)  # 2+2+5+8
    assert out["units.sum"]["South"] == pytest.approx(9.0)   # 3+6 (null skipped)
    assert out["units.sum"]["East"] == pytest.approx(8.0)    # 1+7


def test_ask_top(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p),
                          "question": {"op": "top", "col": "region", "n": 2}})
    assert "error" not in out, out
    assert out["top"] == [{"value": "North", "count": 4},
                          {"value": "South", "count": 3}]
    assert "top 2 values of 'region'" in out["performed"]


def test_ask_correlation_perfect_linear(tmp_path):
    p = _csv(tmp_path, name="linear.csv", content=LINEAR_DATA)
    out = ap.ask_handler({"path": str(p),
                          "question": {"op": "correlation", "x": "x", "y": "y"}})
    assert "error" not in out, out
    assert out["pearson_r"] == pytest.approx(1.0, abs=1e-12)
    assert out["n_pairs"] == 4
    assert "Pearson correlation between 'x' and 'y'" in out["performed"]


def test_ask_free_text_parse_echoes_aggregation(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p),
                          "question": "average sales by region"})
    assert "error" not in out, out
    assert out["sales.mean"]["North"] == pytest.approx(375.0)
    assert "mean(sales) grouped by region" in out["performed"]


def test_ask_free_text_top_and_correlation(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p), "question": "top 1 region"})
    assert "error" not in out, out
    assert out["top"] == [{"value": "North", "count": 4}]

    lin = _csv(tmp_path, name="linear.csv", content=LINEAR_DATA)
    out = ap.ask_handler({"path": str(lin),
                          "question": "correlation between x and y"})
    assert "error" not in out, out
    assert out["pearson_r"] == pytest.approx(1.0, abs=1e-12)


def test_ask_unparseable_question_refuses_to_guess(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p), "question": "what is the vibe here"})
    assert "error" in out
    assert "could not understand" in out["error"]


def test_ask_bad_op_errors(tmp_path):
    p = _csv(tmp_path)
    out = ap.ask_handler({"path": str(p), "question": {"op": "pivot"}})
    assert "error" in out
    assert "unsupported op" in out["error"]


# ------------------------------------------------------------------ chart ---

def test_chart_ascii_fallback_when_matplotlib_blocked(tmp_path, monkeypatch):
    p = _csv(tmp_path)
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    monkeypatch.setattr(ap, "_matplotlib_available", lambda: False)
    out = ap.chart_handler({"path": str(p), "x": "region", "y": "sales",
                            "kind": "bar"})
    assert "error" not in out, out
    assert out["backend"] == "ascii-fallback"
    assert "North" in out["chart"] and "#" in out["chart"]
    # underlying numbers travel with the chart
    data = {d["x"]: d["y"] for d in out["data"]}
    assert data["North"] == pytest.approx(375.0)
    assert data["East"] == pytest.approx(500.0)
    assert len(out["data"]) == 3


@pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None,
                    reason="matplotlib not installed")
def test_chart_real_png_when_matplotlib_available(tmp_path):
    p = _csv(tmp_path)
    out = ap.chart_handler({"path": str(p), "x": "region", "y": "sales",
                            "kind": "bar"})
    assert "error" not in out, out
    assert out["backend"] == "matplotlib"
    png = out["png_path"]
    raw = open(png, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # a real PNG, not a fake image
    assert len(raw) > 1000


def test_chart_bad_kind_errors(tmp_path):
    p = _csv(tmp_path)
    out = ap.chart_handler({"path": str(p), "x": "region", "y": "sales",
                            "kind": "pie"})
    assert "error" in out
    assert "unsupported kind" in out["error"]


# ------------------------------------------------------------------ clean ---

def test_clean_never_touches_original(tmp_path):
    p = _csv(tmp_path)
    before_hash = _sha256(p)
    out = ap.clean_handler({"path": str(p), "ops": {
        "dedupe": True, "trim": True, "coerce": {"sales": "int"}}})
    assert "error" not in out, out
    # original untouched: same bytes, same row count
    assert _sha256(p) == before_hash
    assert out["rows_before"] == 9
    assert out["rows_after"] == 8  # one exact duplicate removed
    assert out["cleaned_path"] != str(p)
    assert out["cleaned_path"].endswith("_cleaned.csv")
    with open(out["cleaned_path"], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8
    regions = {r["region"] for r in rows}
    assert " East " not in regions  # trimmed
    assert "East" in regions


def test_clean_does_not_overwrite_existing_cleaned(tmp_path):
    p = _csv(tmp_path)
    first = ap.clean_handler({"path": str(p), "ops": {"trim": True}})
    second = ap.clean_handler({"path": str(p), "ops": {"trim": True}})
    assert "error" not in first and "error" not in second
    assert first["cleaned_path"] != second["cleaned_path"]
    assert second["cleaned_path"].endswith("_cleaned_2.csv")


# ------------------------------------------------------- path containment ---

def test_path_escape_refused_everywhere(tmp_path):
    for handler, args in [
        (ap.profile_handler, {"path": "/etc/passwd"}),
        (ap.ask_handler, {"path": "/etc/passwd",
                          "question": {"op": "top", "col": "x", "n": 1}}),
        (ap.chart_handler, {"path": "/etc/passwd", "x": "a", "y": "b"}),
        (ap.clean_handler, {"path": "/etc/passwd", "ops": {}}),
    ]:
        out = handler(args)
        assert "error" in out, handler
        assert "outside the allowed data root" in out["error"]


def test_dotdot_escape_refused(tmp_path):
    p = tmp_path / "real.csv"
    p.write_text("a\n1\n", encoding="utf-8")
    out = ap.profile_handler({"path": str(tmp_path / ".." / "real.csv")})
    assert "error" in out
    assert "outside the allowed data root" in out["error"]


def test_symlink_escape_refused(tmp_path):
    # a symlink inside the root pointing outside must still be refused
    link = tmp_path / "link.csv"
    try:
        link.symlink_to("/etc/passwd")
    except OSError:
        pytest.skip("cannot create symlinks here")
    out = ap.profile_handler({"path": str(link)})
    assert "error" in out
    assert "outside the allowed data root" in out["error"]


def test_missing_file_errors(tmp_path):
    out = ap.profile_handler({"path": str(tmp_path / "nope.csv")})
    assert "error" in out
    assert "does not exist" in out["error"]


# ------------------------------------------------------------------ wiring ---

def test_pack_wiring():
    assert {d["name"] for d in ap.TOOL_DEFS} == {
        "data.profile", "data.ask", "data.chart", "data.clean"}
    assert ap.RISK_TABLE_ADDITIONS == {
        "data.profile": ("low", False),
        "data.ask": ("low", False),
        "data.chart": ("low", False),
        "data.clean": ("medium", False),
    }
    reg = Registry()
    ap.register(reg)
    for name, risk in [("data.profile", "low"), ("data.ask", "low"),
                       ("data.chart", "low"), ("data.clean", "medium")]:
        assert name in reg.tools
        assert reg.tools[name].risk == risk
        assert reg.tools[name].needs_network is False


def test_handlers_never_raise(tmp_path):
    p = _csv(tmp_path)
    bad = [
        (ap.profile_handler, {}),
        (ap.ask_handler, {"path": str(p)}),
        (ap.chart_handler, {"path": str(p)}),
        (ap.clean_handler, {"path": str(p), "ops": "nope"}),
        (ap.clean_handler, {"path": str(p), "ops": {"coerce": {"zzz": "int"}}}),
    ]
    for handler, args in bad:
        out = handler(args)
        assert isinstance(out, dict) and "error" in out
