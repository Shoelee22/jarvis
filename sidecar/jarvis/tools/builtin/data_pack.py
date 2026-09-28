"""Data Tool Pack (Phase 6 / Pack 3): CSV/JSON/SQLite data work, expense
tracking, habit streaks, and unit conversions. All offline.

Every handler takes a dict and returns a dict; handlers NEVER raise —
errors come back as {"error": ...} (the Registry wraps further anyway).

Config toggles reuse the creator pack's tools_config.yaml plumbing
(from .creator import _load_config, _enabled). File paths are jailed:
arbitrary CSV/JSON files go through fs_tools._resolve (home-rooted),
while sqlite databases are jailed to the sidecar DATA_DIR.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import re
import sqlite3
import statistics
from pathlib import Path

from .creator import _enabled
from .fs_tools import _resolve as _resolve_home

ROW_CAP = 500          # db.query result cap
CSV_LIMIT_DEFAULT = 50

# ---------------------------------------------------------------- config --
def _data_dir() -> Path:
    """DATA_DIR read at call time so tests can monkeypatch jarvis.config."""
    from ... import config
    return Path(config.DATA_DIR)


def _resolve_data_db(path: str) -> Path:
    """Jail a sqlite db path to DATA_DIR (relative paths land inside it)."""
    root = _data_dir().resolve()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if p != root and root not in p.parents:
        raise PermissionError(f"db path outside DATA_DIR: {path}")
    if p.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
        raise ValueError(f"not a sqlite file: {path}")
    return p


# --------------------------------------------------------------- helpers --
def _guarded(tool_name: str, args: dict, fn) -> dict:
    err = _enabled(tool_name)
    if err:
        return err
    try:
        return fn(args or {})
    except Exception as e:  # noqa: BLE001 — handlers never raise
        return {"error": f"{type(e).__name__}: {e}"}


def _csv_rows(path: str) -> tuple[list[str], list[dict]]:
    with _resolve_home(path).open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return [], []
        return list(reader.fieldnames), [dict(r) for r in reader]


_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _as_float(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and _NUM_RE.match(v.strip()):
        return float(v.strip())
    return None


# --------------------------------------------------------------- csv.read --
def csv_read(args: dict) -> dict:
    def go(a: dict) -> dict:
        limit = int(a.get("limit", CSV_LIMIT_DEFAULT))
        columns, rows = _csv_rows(a["path"])
        return {"columns": columns, "rows": rows[: max(limit, 0)],
                "total_rows": len(rows)}
    return _guarded("csv.read", args, go)


# ------------------------------------------------------------- csv.filter --
_FILTER_OPS = {"eq", "ne", "contains", "gt", "lt", "gte", "lte"}


def _matches(cell, op: str, value) -> bool:
    cell_num, val_num = _as_float(cell), _as_float(value)
    if cell_num is not None and val_num is not None:
        if op == "eq":
            return cell_num == val_num
        if op == "ne":
            return cell_num != val_num
        if op == "gt":
            return cell_num > val_num
        if op == "lt":
            return cell_num < val_num
        if op == "gte":
            return cell_num >= val_num
        if op == "lte":
            return cell_num <= val_num
    c, v = "" if cell is None else str(cell), "" if value is None else str(value)
    if op == "eq":
        return c == v
    if op == "ne":
        return c != v
    if op == "contains":
        return v in c
    if op == "gt":
        return c > v
    if op == "lt":
        return c < v
    if op == "gte":
        return c >= v
    if op == "lte":
        return c <= v
    raise ValueError(f"unknown op: {op}")


def csv_filter(args: dict) -> dict:
    def go(a: dict) -> dict:
        op = str(a.get("op", "")).lower()
        if op not in _FILTER_OPS:
            return {"error": f"unknown op '{op}'; use one of {sorted(_FILTER_OPS)}"}
        limit = int(a.get("limit", CSV_LIMIT_DEFAULT))
        columns, rows = _csv_rows(a["path"])
        column = a.get("column")
        if column not in columns:
            return {"error": f"column '{column}' not in {columns}"}
        value = a.get("value")
        matched = [r for r in rows if _matches(r.get(column), op, value)]
        return {"column": column, "op": op, "value": value,
                "rows": matched[: max(limit, 0)], "count": len(matched)}
    return _guarded("csv.filter", args, go)


# -------------------------------------------------------------- csv.stats --
def csv_stats(args: dict) -> dict:
    def go(a: dict) -> dict:
        columns, rows = _csv_rows(a["path"])
        column = a.get("column")
        if column not in columns:
            return {"error": f"column '{column}' not in {columns}"}
        nums = [n for r in rows if (n := _as_float(r.get(column))) is not None]
        if not nums:
            return {"error": f"column '{column}' has no numeric values"}
        return {"column": column, "count": len(nums),
                "mean": statistics.fmean(nums),
                "median": statistics.median(nums),
                "min": min(nums), "max": max(nums),
                "stdev": statistics.stdev(nums) if len(nums) > 1 else 0.0}
    return _guarded("csv.stats", args, go)


# ------------------------------------------------------------- json.query --
def json_query(args: dict) -> dict:
    def go(a: dict) -> dict:
        with _resolve_home(a["path"]).open(encoding="utf-8", errors="replace") as f:
            doc = json.load(f)
        dotpath = str(a.get("dotpath", ""))
        node = doc
        for seg in (s for s in dotpath.split(".") if s != ""):
            if isinstance(node, dict):
                if seg not in node:
                    return {"error": f"bad dotpath '{dotpath}': key '{seg}' not found"}
                node = node[seg]
            elif isinstance(node, list):
                if not seg.lstrip("+-").isdigit():
                    return {"error": f"bad dotpath '{dotpath}': '{seg}' is not a list index"}
                idx = int(seg)
                if not (-len(node) <= idx < len(node)):
                    return {"error": f"bad dotpath '{dotpath}': index {idx} out of range"}
                node = node[idx]
            else:
                return {"error": f"bad dotpath '{dotpath}': cannot descend into scalar at '{seg}'"}
        return {"dotpath": dotpath, "value": node}
    return _guarded("json.query", args, go)


# --------------------------------------------------------------- db.query --
_READONLY_HEADS = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
_READONLY_ERR = "read-only: only SELECT/WITH/PRAGMA allowed"


def _is_readonly(sql: str) -> bool:
    s = sql.strip()
    while True:  # strip leading comments before checking the head keyword
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:].strip() if nl >= 0 else ""
        elif s.startswith("/*"):
            end = s.find("*/")
            if end < 0:
                return False
            s = s[end + 2:].strip()
        else:
            break
    head = s.split(None, 1)[0].upper() if s else ""
    return head in _READONLY_HEADS


def db_query(args: dict) -> dict:
    def go(a: dict) -> dict:
        sql = str(a.get("sql", ""))
        if not _is_readonly(sql):
            return {"error": _READONLY_ERR}
        db_path = _resolve_data_db(a["db"])
        params = a.get("params") or ()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            cur = conn.execute(sql, params) if params else conn.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = [list(r) for r in cur.fetchmany(ROW_CAP)]
            truncated = cur.fetchone() is not None
        finally:
            conn.close()
        return {"columns": columns, "rows": rows,
                "row_count": len(rows), "truncated": truncated}
    return _guarded("db.query", args, go)


# ------------------------------------------------------------ expenses ---
_FINANCE_DB = "finance.db"


def _finance_conn() -> sqlite3.Connection:
    _data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_data_dir() / _FINANCE_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount_paise INTEGER NOT NULL,
        category TEXT NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL)""")
    return conn


_INT_RE = re.compile(r"^[+-]?\d+$")


def _parse_paise(v) -> int | None:
    """Integer paise only: int ok; int-parseable str ok; floats rejected."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return int(v.strip())
    return None


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:.2f}"


def expenses_add(args: dict) -> dict:
    def go(a: dict) -> dict:
        if "amount_paise" not in a:
            return {"error": "amount_paise is required"}
        paise = _parse_paise(a["amount_paise"])
        if paise is None:
            return {"error": "amount_paise must be an integer (paise); floats are rejected"}
        category = str(a.get("category", "")).strip()
        if not category:
            return {"error": "category is required"}
        note = a.get("note")
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        conn = _finance_conn()
        try:
            cur = conn.execute(
                "INSERT INTO expenses (amount_paise, category, note, created_at)"
                " VALUES (?, ?, ?, ?)", (paise, category, note, now))
            conn.commit()
            new_id = cur.lastrowid
        finally:
            conn.close()
        return {"id": new_id, "amount_paise": paise, "category": category}
    return _guarded("expenses.add", args, go)


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def expenses_report(args: dict) -> dict:
    def go(a: dict) -> dict:
        month = a.get("month")
        where, params = "", []
        if month is not None:
            month = str(month)
            if not _MONTH_RE.match(month):
                return {"error": "month must be 'YYYY-MM'"}
            where, params = "WHERE strftime('%Y-%m', created_at) = ?", [month]
        conn = _finance_conn()
        try:
            rows = conn.execute(
                f"SELECT category, SUM(amount_paise) FROM expenses {where}"
                " GROUP BY category ORDER BY category", params).fetchall()
            grand = conn.execute(
                f"SELECT COALESCE(SUM(amount_paise), 0) FROM expenses {where}",
                params).fetchone()[0]
        finally:
            conn.close()
        by_category = [{"category": c, "total_paise": t, "total_rupees": _rupees(t)}
                       for c, t in rows]
        return {"month": month, "by_category": by_category,
                "grand_total_paise": grand, "grand_total_rupees": _rupees(grand)}
    return _guarded("expenses.report", args, go)


# -------------------------------------------------------------- habits ---
_HABITS_DB = "habits.db"


def _habits_conn() -> sqlite3.Connection:
    _data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_data_dir() / _HABITS_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS checkins (
        habit TEXT NOT NULL,
        date TEXT NOT NULL,
        ts TEXT NOT NULL,
        PRIMARY KEY (habit, date))""")
    return conn


def _habit_dates(conn: sqlite3.Connection, habit: str) -> set[str]:
    rows = conn.execute("SELECT date FROM checkins WHERE habit = ?", (habit,)).fetchall()
    return {r[0] for r in rows}


def _streak_ending(dates: set[str], end: _dt.date) -> int:
    n, d = 0, end
    while d.isoformat() in dates:
        n += 1
        d -= _dt.timedelta(days=1)
    return n


def habits_checkin(args: dict) -> dict:
    def go(a: dict) -> dict:
        habit = str(a.get("habit", "")).strip()
        if not habit:
            return {"error": "habit is required"}
        raw_date = a.get("date")
        if raw_date is None:
            day = _dt.date.today()
        else:
            try:
                day = _dt.date.fromisoformat(str(raw_date))
            except ValueError:
                return {"error": f"date must be ISO 'YYYY-MM-DD', got '{raw_date}'"}
        if day > _dt.date.today():
            return {"error": "date cannot be in the future"}
        date_s = day.isoformat()
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        conn = _habits_conn()
        try:
            conn.execute("INSERT OR REPLACE INTO checkins (habit, date, ts)"
                         " VALUES (?, ?, ?)", (habit, date_s, now))
            conn.commit()
            dates = _habit_dates(conn, habit)
        finally:
            conn.close()
        return {"habit": habit, "date": date_s,
                "streak": _streak_ending(dates, day)}
    return _guarded("habits.checkin", args, go)


def habits_report(args: dict) -> dict:
    def go(a: dict) -> dict:
        habit_filter = a.get("habit")
        days = int(a.get("days", 30))
        if days < 1:
            return {"error": "days must be >= 1"}
        today = _dt.date.today()
        cutoff = (today - _dt.timedelta(days=days - 1)).isoformat()
        conn = _habits_conn()
        try:
            if habit_filter:
                habits = [(str(habit_filter),)]
            else:
                habits = conn.execute("SELECT DISTINCT habit FROM checkins").fetchall()
            out = []
            for (h,) in habits:
                dates = _habit_dates(conn, h)
                in_window = [d for d in dates if d >= cutoff]
                past = [d for d in dates if d <= today.isoformat()]
                end = _dt.date.fromisoformat(max(past)) if past else today
                out.append({"habit": h, "checkins": len(in_window),
                            "streak": _streak_ending(dates, end)})
            out.sort(key=lambda x: x["habit"])
        finally:
            conn.close()
        return {"days": days, "habits": out}
    return _guarded("habits.report", args, go)


# --------------------------------------------------------- units.convert --
_LENGTH = {"m": 1.0, "meter": 1.0, "meters": 1.0,
           "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0,
           "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
           "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
           "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
           "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01}
_MASS = {"kg": 1.0, "kilogram": 1.0, "kilograms": 1.0,
         "g": 0.001, "gram": 0.001, "grams": 0.001,
         "lb": 0.45359237, "pound": 0.45359237, "pounds": 0.45359237,
         "oz": 0.028349523125, "ounce": 0.028349523125, "ounces": 0.028349523125}
_TIME = {"s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
         "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
         "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
         "d": 86400.0, "day": 86400.0, "days": 86400.0}
_DATA = {"b": 1.0, "byte": 1.0, "bytes": 1.0,
         "kb": 1000.0, "kilobyte": 1000.0, "kilobytes": 1000.0,
         "mb": 1_000_000.0, "megabyte": 1_000_000.0, "megabytes": 1_000_000.0,
         "gb": 1_000_000_000.0, "gigabyte": 1_000_000_000.0,
         "gigabytes": 1_000_000_000.0}  # decimal (SI) bytes
_TEMP = {"c", "celsius", "f", "fahrenheit", "k", "kelvin"}

_UNIT_TABLES = {"length": _LENGTH, "mass": _MASS, "time": _TIME, "data": _DATA}


def _unit_dim(unit: str) -> tuple[str, float] | None:
    u = unit.strip().lower()
    if u in _TEMP:
        return ("temp", 0.0)
    for dim, table in _UNIT_TABLES.items():
        if u in table:
            return (dim, table[u])
    return None


def _to_celsius(v: float, unit: str) -> float:
    u = unit.strip().lower()
    if u in ("c", "celsius"):
        return v
    if u in ("f", "fahrenheit"):
        return (v - 32) * 5 / 9
    return v - 273.15  # k / kelvin


def _from_celsius(v: float, unit: str) -> float:
    u = unit.strip().lower()
    if u in ("c", "celsius"):
        return v
    if u in ("f", "fahrenheit"):
        return v * 9 / 5 + 32
    return v + 273.15  # k / kelvin


def units_convert(args: dict) -> dict:
    def go(a: dict) -> dict:
        value = a.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"error": "value must be a number"}
        src = _unit_dim(str(a.get("from", "")))
        dst = _unit_dim(str(a.get("to", "")))
        if src is None or dst is None:
            return {"error": "unknown unit"}
        if src[0] != dst[0]:
            return {"error": f"incompatible units: '{a.get('from')}' vs '{a.get('to')}'"}
        if src[0] == "temp":
            result = _from_celsius(_to_celsius(float(value), str(a["from"])), str(a["to"]))
        else:
            result = float(value) * src[1] / dst[1]
        return {"value": value, "from": str(a["from"]), "to": str(a["to"]),
                "result": result}
    return _guarded("units.convert", args, go)


# ------------------------------------------------ registration helper ---
# (Parent wires these into the Registry; __init__.py/policy.py untouched.)
REGISTRATIONS = [
    ("csv.read", "Read a CSV file (jailed); preview rows.", csv_read, "low", False),
    ("csv.filter", "Filter CSV rows by column op.", csv_filter, "low", False),
    ("csv.stats", "Numeric stats for a CSV column.", csv_stats, "low", False),
    ("json.query", "Extract a value from JSON by dotpath.", json_query, "low", False),
    ("db.query", "Read-only SQL over a sqlite db (jailed to DATA_DIR).",
     db_query, "medium", False),
    ("expenses.add", "Log an expense in integer paise.", expenses_add, "low", False),
    ("expenses.report", "Expense totals by category.", expenses_report, "low", False),
    ("habits.checkin", "Check in a daily habit; returns streak.", habits_checkin,
     "low", False),
    ("habits.report", "Habit checkin counts + current streaks.", habits_report,
     "low", False),
    ("units.convert", "Convert between length/mass/temp/data/time units.",
     units_convert, "low", False),
]
