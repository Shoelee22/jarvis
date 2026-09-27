"""Unit tests for the Phase 6 Data Tool Pack. All offline; real CSV/JSON/sqlite
fixtures in tmp_path. DATA_DIR is monkeypatched per-test; the tool-enabled
check is stubbed so tests don't depend on the real tools_config.yaml."""
import csv
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import data_pack  # noqa: E402


# --------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr("jarvis.config.DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(data_pack, "_enabled", lambda name: None)  # all enabled
    # pytest's tmp_path is /tmp/... — outside the home jail; widen it for tests
    import jarvis.tools.builtin.fs_tools as fs_tools
    monkeypatch.setattr(fs_tools, "ALLOWED_ROOTS", [Path.home(), tmp_path])
    return tmp_path


@pytest.fixture()
def csv_file(tmp_path):
    p = tmp_path / "people.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "age", "city"])
        w.writerow(["Asha", "30", "Jaipur"])
        w.writerow(["Ravi", "25", "Surat"])
        w.writerow(["Meera", "35", "Jaipur"])
    return str(p)


@pytest.fixture()
def json_file(tmp_path):
    p = tmp_path / "doc.json"
    p.write_text(json.dumps({"a": {"b": [{"c": 42}, {"c": 7}]}, "x": "hi"}))
    return str(p)


@pytest.fixture()
def sqlite_db(tmp_path):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    p = data / "test.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)")
    conn.executemany("INSERT INTO items (name, qty) VALUES (?, ?)",
                     [("a", 1), ("b", 2), ("c", 3)])
    conn.commit()
    conn.close()
    return str(p)


# --------------------------------------------------------------- csv.read
def test_csv_read(csv_file):
    r = data_pack.csv_read({"path": csv_file, "limit": 2})
    assert r["columns"] == ["name", "age", "city"]
    assert len(r["rows"]) == 2
    assert r["total_rows"] == 3
    assert r["rows"][0]["name"] == "Asha"


def test_csv_read_default_limit(csv_file):
    r = data_pack.csv_read({"path": csv_file})
    assert r["total_rows"] == 3 and len(r["rows"]) == 3


def test_csv_read_never_raises(tmp_path):
    r = data_pack.csv_read({"path": str(tmp_path / "missing.csv")})
    assert "error" in r


# ------------------------------------------------------------- csv.filter
def test_csv_filter_eq_string(csv_file):
    r = data_pack.csv_filter({"path": csv_file, "column": "city", "op": "eq", "value": "Jaipur"})
    assert r["count"] == 2
    assert {row["name"] for row in r["rows"]} == {"Asha", "Meera"}


def test_csv_filter_numeric_gt(csv_file):
    r = data_pack.csv_filter({"path": csv_file, "column": "age", "op": "gt", "value": 25})
    assert r["count"] == 2  # numeric: 30 and 35


def test_csv_filter_numeric_gt_string_value(csv_file):
    # string-typed arg must still compare numerically
    r = data_pack.csv_filter({"path": csv_file, "column": "age", "op": "lte", "value": "25"})
    assert r["count"] == 1 and r["rows"][0]["name"] == "Ravi"


def test_csv_filter_contains(csv_file):
    r = data_pack.csv_filter({"path": csv_file, "column": "name", "op": "contains", "value": "ee"})
    assert r["count"] == 1 and r["rows"][0]["name"] == "Meera"


def test_csv_filter_bad_op_and_bad_column(csv_file):
    assert "error" in data_pack.csv_filter({"path": csv_file, "column": "age", "op": "like", "value": "x"})
    assert "error" in data_pack.csv_filter({"path": csv_file, "column": "nope", "op": "eq", "value": "x"})


# -------------------------------------------------------------- csv.stats
def test_csv_stats(csv_file):
    r = data_pack.csv_stats({"path": csv_file, "column": "age"})
    assert r["count"] == 3
    assert r["mean"] == pytest.approx(30.0)
    assert r["median"] == pytest.approx(30.0)
    assert r["min"] == 25.0 and r["max"] == 35.0
    assert r["stdev"] == pytest.approx(5.0)


def test_csv_stats_non_numeric(csv_file):
    assert "error" in data_pack.csv_stats({"path": csv_file, "column": "name"})


# ------------------------------------------------------------- json.query
def test_json_query(json_file):
    assert data_pack.json_query({"path": json_file, "dotpath": "a.b.0.c"})["value"] == 42
    assert data_pack.json_query({"path": json_file, "dotpath": "x"})["value"] == "hi"
    assert data_pack.json_query({"path": json_file, "dotpath": "a.b.1"})["value"] == {"c": 7}


def test_json_query_bad_path(json_file):
    assert "error" in data_pack.json_query({"path": json_file, "dotpath": "a.nope"})
    assert "error" in data_pack.json_query({"path": json_file, "dotpath": "a.b.9"})
    assert "error" in data_pack.json_query({"path": json_file, "dotpath": "x.y"})


# --------------------------------------------------------------- db.query
def test_db_query_select(sqlite_db):
    r = data_pack.db_query({"db": sqlite_db, "sql": "SELECT name, qty FROM items WHERE qty > ?",
                            "params": [1]})
    assert r["columns"] == ["name", "qty"]
    assert r["rows"] == [["b", 2], ["c", 3]]
    assert r["truncated"] is False


def test_db_query_rejects_writes(sqlite_db):
    for sql in ["DROP TABLE items", "DELETE FROM items", "INSERT INTO items VALUES (1,'x',1)",
                "UPDATE items SET qty=0", "  -- sneaky\nDROP TABLE items"]:
        r = data_pack.db_query({"db": sqlite_db, "sql": sql})
        assert r["error"] == "read-only: only SELECT/WITH/PRAGMA allowed", sql
    # table must be untouched
    r = data_pack.db_query({"db": sqlite_db, "sql": "SELECT COUNT(*) FROM items"})
    assert r["rows"] == [[3]]


def test_db_query_jail(sqlite_db, tmp_path):
    outside = tmp_path / "outside.db"
    r = data_pack.db_query({"db": str(outside), "sql": "SELECT 1"})
    assert "error" in r  # outside DATA_DIR
    # relative path resolves inside DATA_DIR
    r = data_pack.db_query({"db": "test.db", "sql": "SELECT COUNT(*) FROM items"})
    assert r["rows"] == [[3]]


# ------------------------------------------------------------ expenses
def test_expenses_add_and_report():
    r1 = data_pack.expenses_add({"amount_paise": 25050, "category": "food", "note": "lunch"})
    assert isinstance(r1["id"], int) and r1["amount_paise"] == 25050
    r2 = data_pack.expenses_add({"amount_paise": "5000", "category": "travel"})  # int-parseable str ok
    assert isinstance(r2["id"], int) and r2["id"] != r1["id"]
    rep = data_pack.expenses_report({})
    by_cat = {c["category"]: c for c in rep["by_category"]}
    assert by_cat["food"]["total_paise"] == 25050
    assert by_cat["food"]["total_rupees"] == "₹250.50"
    assert by_cat["travel"]["total_paise"] == 5000
    assert rep["grand_total_paise"] == 30050
    assert rep["grand_total_rupees"] == "₹300.50"


def test_expenses_add_rejects_floats_and_garbage():
    assert "error" in data_pack.expenses_add({"amount_paise": 25.5, "category": "x"})
    assert "error" in data_pack.expenses_add({"amount_paise": "25.5", "category": "x"})
    assert "error" in data_pack.expenses_add({"amount_paise": "abc", "category": "x"})
    assert "error" in data_pack.expenses_add({"category": "x"})  # missing
    assert "error" in data_pack.expenses_add({"amount_paise": 100})  # no category


def test_expenses_report_month_filter():
    data_pack.expenses_add({"amount_paise": 1000, "category": "misc"})
    this_month = dt.date.today().strftime("%Y-%m")
    rep = data_pack.expenses_report({"month": this_month})
    assert rep["month"] == this_month and rep["grand_total_paise"] == 1000
    assert "error" in data_pack.expenses_report({"month": "2026-13"})
    assert "error" in data_pack.expenses_report({"month": "nope"})


# -------------------------------------------------------------- habits
def test_habits_checkin_and_streak():
    today = dt.date.today()
    dates = [today - dt.timedelta(days=i) for i in range(3)]
    for d in dates:
        r = data_pack.habits_checkin({"habit": "gym", "date": d.isoformat()})
        assert r["habit"] == "gym" and r["date"] == d.isoformat()
    r = data_pack.habits_checkin({"habit": "gym"})  # today again -> streak 3
    assert r["streak"] == 3


def test_habits_checkin_streak_break():
    today = dt.date.today()
    data_pack.habits_checkin({"habit": "read", "date": today.isoformat()})
    data_pack.habits_checkin({"habit": "read", "date": (today - dt.timedelta(days=2)).isoformat()})
    r = data_pack.habits_checkin({"habit": "read"})  # today; gap at day-1 -> streak 1
    assert r["streak"] == 1


def test_habits_checkin_validation():
    assert "error" in data_pack.habits_checkin({})
    assert "error" in data_pack.habits_checkin({"habit": "x", "date": "27-09-2026"})
    future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert "error" in data_pack.habits_checkin({"habit": "x", "date": future})


def test_habits_report():
    data_pack.habits_checkin({"habit": "gym"})
    data_pack.habits_checkin({"habit": "read"})
    data_pack.habits_checkin({"habit": "read"})
    rep = data_pack.habits_report({"days": 30})
    by_h = {h["habit"]: h for h in rep["habits"]}
    assert by_h["gym"]["checkins"] == 1 and by_h["gym"]["streak"] == 1
    assert by_h["read"]["checkins"] == 1  # same-day re-checkin is one row
    single = data_pack.habits_report({"habit": "gym"})
    assert len(single["habits"]) == 1 and single["habits"][0]["habit"] == "gym"


# --------------------------------------------------------- units.convert
@pytest.mark.parametrize("frm,to,value,expected", [
    ("km", "mi", 10, 10 / 1.609344),
    ("mi", "km", 1, 1.609344),
    ("ft", "m", 1, 0.3048),
    ("in", "cm", 1, 2.54),
    ("kg", "lb", 1, 2.2046226218),
    ("oz", "g", 16, 453.59237),
    ("c", "f", 0, 32.0),
    ("f", "c", 212, 100.0),
    ("c", "k", 0, 273.15),
    ("mb", "kb", 1, 1000.0),
    ("gb", "b", 1, 1e9),
    ("h", "min", 2, 120.0),
    ("d", "s", 1, 86400.0),
])
def test_units_convert(frm, to, value, expected):
    r = data_pack.units_convert({"value": value, "from": frm, "to": to})
    assert r["result"] == pytest.approx(expected, rel=1e-6)


def test_units_convert_errors():
    assert data_pack.units_convert({"value": 1, "from": "furlong", "to": "m"})["error"] == "unknown unit"
    assert "incompatible" in data_pack.units_convert({"value": 1, "from": "kg", "to": "m"})["error"]
    assert "error" in data_pack.units_convert({"value": "ten", "from": "m", "to": "km"})


def test_handlers_never_raise(tmp_path):
    # every handler with garbage input returns a dict, never raises
    for (name, _, fn, _, _) in data_pack.REGISTRATIONS:
        for bad in (None, {}, {"path": str(tmp_path / "nope")}, {"sql": "DROP TABLE x"}):
            r = fn(bad)
            assert isinstance(r, dict), name
            if name not in ("expenses.report", "habits.report"):  # empty reports are legit successes
                assert "error" in r, (name, bad)
