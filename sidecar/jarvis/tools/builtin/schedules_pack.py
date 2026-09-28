"""Natural-language schedules pack (Phase 18): JARVIS owns recurring time.

The dynamic ``automation`` provider already stores named cron routines, but
it speaks only cron and keeps no history. This pack adds the human layer:

Tools:
    schedules.parse           low     Natural language -> {cron, summary}.
                                "every weekday at 9am", "every 2 hours",
                                "mondays and thursdays at 6pm", "on the 1st
                                of each month at 8am". Honest error when the
                                phrasing is not understood — never guesses.
    schedules.next            low     Next N run times for a cron expression
                                {cron, n?}. Pure stdlib cron math.
    schedules.quiet_hours     low     Get/set the quiet window {start?, end?}
                                as HH:MM. Routines stay silent inside it.
    schedules.create_routine  medium  Parse NL schedule + create a cron
                                routine in the automation engine
                                {name, schedule, steps}. Delegates storage
                                and execution to the dynamic automation
                                provider — this pack only adds the NL layer.
    schedules.record_run      low     Append a run-history entry
                                {routine, status: ok|failed|skipped, detail?}.
    schedules.history         low     Recent runs + success rate for a routine
                                {routine, n?}.
    schedules.due             low     Cron routines due inside the tick window
                                {window_minutes?}, honoring quiet hours.
                                Designed for the workers tick.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - schedules.due never executes anything — it only reports.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/schedules.db,
      overridable with the JARVIS_SCHEDULES_DB env var (tests use a tmp file).
    - The automation engine's data dir follows the JARVIS_DATA env override
      (tests point it at a tmp dir); without the override it uses the
      user's live automation.db.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "schedules.db"

_LOCK = threading.Lock()

_DAYS = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
    "sunday": 0, "sun": 0,
}
_DAY_NAMES = ["sunday", "monday", "tuesday", "wednesday", "thursday",
              "friday", "saturday"]
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december"]


def _db_path() -> Path:
    override = os.environ.get("JARVIS_SCHEDULES_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _automation_data_dir() -> Path:
    override = os.environ.get("JARVIS_DATA")
    if override:
        return Path(override)
    from ...config import DATA_DIR
    return DATA_DIR


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Schema ensured per connection (see projects_pack: DB path is
    # per-call overridable via env, so import-time setup is not enough).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings(
             key TEXT PRIMARY KEY, value TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS run_history(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             routine TEXT NOT NULL,
             started_epoch INTEGER NOT NULL,
             finished_epoch INTEGER,
             status TEXT NOT NULL,
             detail TEXT)"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_history_routine"
        " ON run_history(routine, started_epoch DESC)"
    )
    conn.commit()
    return conn


def _safe(fn):
    def wrapper(args: dict):
        try:
            return fn(args or {})
        except Exception as exc:  # never raise out of a tool handler
            return {"error": f"{fn.__name__}: {type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# cron parsing + next-run math (stdlib only)
# ---------------------------------------------------------------------------

def _parse_cron_field(field: str, lo: int, hi: int) -> set[int] | None:
    """Parse one cron field. Returns the matching int set, or None if bad."""
    values: set[int] = set()

    def add_range(start: int, end: int, step: int) -> bool:
        if not (lo <= start <= hi and lo <= end <= hi):
            return False
        if step < 1:
            return False
        if start > end:
            return False
        values.update(range(start, end + 1, step))
        return True

    for part in field.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        base = part
        if "/" in part:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit():
                return None
            step = int(step_s)
        if base == "*":
            if not add_range(lo, hi, step):
                return None
        elif "-" in base:
            a, b = base.split("-", 1)
            if not (a.lstrip("-").isdigit() and b.isdigit()):
                return None
            if not add_range(int(a), int(b), step):
                return None
        else:
            if not base.isdigit():
                return None
            if not add_range(int(base), int(base), step):
                return None
    return values


def _parse_cron(cron: str) -> tuple[dict | None, str | None]:
    parts = cron.strip().split()
    if len(parts) != 5:
        return None, "cron must have exactly 5 fields (minute hour dom month dow)"
    bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    out = {}
    keys = ("minute", "hour", "dom", "month", "dow")
    for key, part, (lo, hi) in zip(keys, parts, bounds):
        vals = _parse_cron_field(part, lo, hi)
        if vals is None:
            return None, f"cron field {key}={part!r} is not valid"
        if key == "dow" and 7 in vals:
            vals.discard(7)
            vals.add(0)
        out[key] = vals
    return out, None


def _cron_matches(dt: datetime, spec: dict,
                  dom_star: bool, dow_star: bool) -> bool:
    if dt.minute not in spec["minute"]:
        return False
    if dt.hour not in spec["hour"]:
        return False
    if dt.month not in spec["month"]:
        return False
    dom_ok = dt.day in spec["dom"]
    # cron's Sunday=0; python weekday() Monday=0 -> convert
    dow = (dt.weekday() + 1) % 7
    dow_ok = dow in spec["dow"]
    if not dom_star and not dow_star:
        day_ok = dom_ok or dow_ok
    elif not dom_star:
        day_ok = dom_ok
    elif not dow_star:
        day_ok = dow_ok
    else:
        day_ok = True
    return day_ok


def _cron_next(after: datetime, cron: str) -> tuple[datetime | None, str | None]:
    spec, err = _parse_cron(cron)
    if err:
        return None, err
    parts = cron.strip().split()
    dom_star = parts[2] == "*"
    dow_star = parts[4] == "*"
    cand = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = after + timedelta(days=366)
    while cand <= limit:
        if _cron_matches(cand, spec, dom_star, dow_star):
            return cand, None
        cand += timedelta(minutes=1)
    return None, "no run in the next 366 days (cron may never match)"


def _cron_prev(before: datetime, cron: str) -> tuple[datetime | None, str | None]:
    spec, err = _parse_cron(cron)
    if err:
        return None, err
    parts = cron.strip().split()
    dom_star = parts[2] == "*"
    dow_star = parts[4] == "*"
    cand = before.replace(second=0, microsecond=0)
    limit = before - timedelta(days=366)
    while cand >= limit:
        if _cron_matches(cand, spec, dom_star, dow_star):
            return cand, None
        cand -= timedelta(minutes=1)
    return None, "no run in the past 366 days"


# ---------------------------------------------------------------------------
# natural-language schedule parsing
# ---------------------------------------------------------------------------

def _parse_time_token(tok: str) -> tuple[int, int] | None:
    tok = tok.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", tok)
    if not m:
        return None
    hour, minute, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if not (0 <= minute <= 59):
        return None
    if ap:
        if not (1 <= hour <= 12):
            return None
        if ap == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif not (0 <= hour <= 23):
        return None
    return hour, minute


def _day_list(text: str) -> list[int] | None:
    found: list[int] = []
    for word in re.findall(r"[a-z]+", text.lower()):
        key = word
        if key not in _DAYS and key.endswith("s"):
            key = key[:-1]  # plural: "mondays" -> "monday"
        if key in _DAYS and _DAYS[key] not in found:
            found.append(_DAYS[key])
    return sorted(found) if found else None


def parse_natural_schedule(text: str) -> tuple[str | None, str | None, str | None]:
    """NL -> (cron, human_summary, error). Never guesses: error when unsure."""
    if not isinstance(text, str) or not text.strip():
        return None, None, "schedule text must be a non-empty string"
    t = text.strip().lower()

    m = re.search(r"every\s+(\d+)\s+minutes?", t)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 59:
            return None, None, "minute interval must be 1..59"
        cron = f"*/{n} * * * *"
        return cron, f"every {n} minute(s)", None
    m = re.search(r"every\s+(\d+)\s+hours?", t)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 23:
            return None, None, "hour interval must be 1..23"
        cron = f"0 */{n} * * *"
        return cron, f"every {n} hour(s)", None
    if re.search(r"\bhourly\b", t):
        return "0 * * * *", "every hour", None

    # find a time token after "at"
    tm = re.search(r"at\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)", t)
    tod = _parse_time_token(tm.group(1)) if tm else None
    if tm and tod is None:
        return None, None, f"could not understand the time in {text!r}"
    hour, minute = tod if tod else (9, 0)
    time_src = "as stated" if tod else "default 9:00am"

    if re.search(r"\bweekdays?\b", t):
        return (f"{minute} {hour} * * 1-5",
                f"every weekday at {hour:02d}:{minute:02d} ({time_src})", None)
    if re.search(r"\bweekends?\b", t):
        return (f"{minute} {hour} * * 0,6",
                f"every weekend day at {hour:02d}:{minute:02d} ({time_src})", None)
    days = _day_list(t)
    if days:
        names = ", ".join(_DAY_NAMES[d] for d in days)
        return (f"{minute} {hour} * * {','.join(str(d) for d in days)}",
                f"every {names} at {hour:02d}:{minute:02d} ({time_src})", None)
    m = re.search(r"(?:on\s+the\s+|the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+"
                  r"(?:of\s+(?:each|every)\s+month|each\s+month|every\s+month|monthly)",
                  t)
    if m is None:
        m = re.search(r"monthly\s+(?:on\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        dom = int(m.group(1))
        if not 1 <= dom <= 28:
            return None, None, "day-of-month must be 1..28 (safe for every month)"
        return (f"{minute} {hour} {dom} * *",
                f"on the {dom} of each month at {hour:02d}:{minute:02d} ({time_src})",
                None)
    if re.search(r"\bdaily\b|every\s+day\b", t):
        return (f"{minute} {hour} * * *",
                f"every day at {hour:02d}:{minute:02d} ({time_src})", None)
    return None, None, (
        f"could not parse {text!r} as a schedule. Try e.g. "
        "'every weekday at 9am', 'every 2 hours', "
        "'mondays and thursdays at 6pm', 'on the 1st of each month at 8am'.")


# ---------------------------------------------------------------------------
# quiet hours
# ---------------------------------------------------------------------------

def _get_setting(key: str) -> str | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?",
                           (key,)).fetchone()
        return row["value"] if row else None


def _set_setting(key: str, value: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
                     (key, value))
        conn.commit()


def _parse_hhmm(raw) -> tuple[int, int] | None:
    if not isinstance(raw, str):
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi


def _in_quiet_hours(now: datetime | None = None) -> tuple[bool, str | None]:
    start = _get_setting("quiet_start")
    end = _get_setting("quiet_end")
    if not start or not end:
        return False, None
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    now = now or datetime.now().astimezone()
    cur = (now.hour, now.minute)
    if (sh, sm) <= (eh, em):
        quiet = (sh, sm) <= cur < (eh, em)
    else:  # wraps midnight
        quiet = cur >= (sh, sm) or cur < (eh, em)
    return quiet, f"{start}–{end}"


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------

@_safe
def parse_handler(args: dict) -> dict:
    cron, summary, err = parse_natural_schedule(args.get("text"))
    if err:
        return {"error": f"schedules.parse: {err}"}
    return {"ok": True, "cron": cron, "summary": summary}


@_safe
def next_handler(args: dict) -> dict:
    cron = args.get("cron")
    if not isinstance(cron, str) or not cron.strip():
        return {"error": "schedules.next: cron is required (5-field expression)"}
    n = args.get("n", 5)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return {"error": "schedules.next: n must be an integer"}
    n = max(1, min(n, 50))
    after = datetime.now().astimezone()
    runs = []
    for _ in range(n):
        nxt, err = _cron_next(after, cron)
        if err:
            if not runs:
                return {"error": f"schedules.next: {err}"}
            break
        runs.append(nxt.isoformat(timespec="minutes"))
        after = nxt
    return {"ok": True, "cron": cron.strip(), "next_runs": runs}


@_safe
def quiet_hours_handler(args: dict) -> dict:
    start, end = args.get("start"), args.get("end")
    if start is None and end is None:
        s, e = _get_setting("quiet_start"), _get_setting("quiet_end")
        quiet, window = _in_quiet_hours()
        return {"ok": True, "quiet_start": s, "quiet_end": e,
                "currently_quiet": quiet, "window": window,
                "note": "no quiet hours configured" if not s else None}
    sh, eh = _parse_hhmm(start), _parse_hhmm(end)
    if sh is None or eh is None:
        return {"error": "schedules.quiet_hours: start and end must be HH:MM "
                         "(24h, e.g. '22:00')"}
    _set_setting("quiet_start", f"{sh[0]:02d}:{sh[1]:02d}")
    _set_setting("quiet_end", f"{eh[0]:02d}:{eh[1]:02d}")
    return {"ok": True, "quiet_start": f"{sh[0]:02d}:{sh[1]:02d}",
            "quiet_end": f"{eh[0]:02d}:{eh[1]:02d}",
            "note": "routines stay silent inside this window"}


@_safe
def create_routine_handler(args: dict) -> dict:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return {"error": "schedules.create_routine: name is required"}
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"error": "schedules.create_routine: steps must be a non-empty list"}
    cron, summary, err = parse_natural_schedule(args.get("schedule"))
    if err:
        return {"error": f"schedules.create_routine: {err}"}
    try:
        from jarvis.tools.dynamic.automation import AutomationEngine  # noqa: PLC0415
    except ImportError as exc:
        return {"error": "schedules.create_routine: automation engine not "
                         f"available ({exc})"}
    engine = AutomationEngine(data_dir=_automation_data_dir())
    out = engine.create(name.strip(), trigger="cron", cron=cron, steps=steps)
    if isinstance(out, dict) and out.get("ok") is False:
        return {"error": f"schedules.create_routine: {out.get('error', out)}"}
    return {"ok": True, "name": name.strip(), "cron": cron,
            "schedule_summary": summary}


@_safe
def record_run_handler(args: dict) -> dict:
    routine = args.get("routine")
    if not isinstance(routine, str) or not routine.strip():
        return {"error": "schedules.record_run: routine is required"}
    status = args.get("status")
    if status not in ("ok", "failed", "skipped"):
        return {"error": "schedules.record_run: status must be ok, failed, or skipped"}
    detail = args.get("detail")
    if detail is not None and not isinstance(detail, str):
        return {"error": "schedules.record_run: detail must be a string"}
    now = int(time.time())
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO run_history(routine, started_epoch, finished_epoch,"
            " status, detail) VALUES(?,?,?,?,?)",
            (routine.strip(), now, now, status, detail))
        conn.commit()
    return {"ok": True, "routine": routine.strip(), "status": status}


@_safe
def history_handler(args: dict) -> dict:
    routine = args.get("routine")
    if not isinstance(routine, str) or not routine.strip():
        return {"error": "schedules.history: routine is required"}
    routine = routine.strip()
    n = args.get("n", 10)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return {"error": "schedules.history: n must be an integer"}
    n = max(1, min(n, 100))
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_history WHERE routine=? ORDER BY started_epoch DESC"
            " LIMIT ?", (routine, n)).fetchall()
        total = conn.execute("SELECT COUNT(*),"
                             " SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END)"
                             " FROM run_history WHERE routine=?",
                             (routine,)).fetchone()
    runs = [{"status": r["status"],
             "at": datetime.fromtimestamp(r["started_epoch"]).astimezone()
             .isoformat(timespec="seconds"),
             "detail": r["detail"]} for r in rows]
    count, ok_count = total[0], total[1] or 0
    return {"ok": True, "routine": routine, "runs": runs,
            "stats": {"total_runs": count, "ok_runs": ok_count,
                      "success_rate": round(ok_count / count, 3) if count else None}}


@_safe
def due_handler(args: dict) -> dict:
    window = args.get("window_minutes", 5)
    try:
        window = int(window)
    except (TypeError, ValueError):
        return {"error": "schedules.due: window_minutes must be an integer"}
    window = max(1, min(window, 120))
    quiet, qwindow = _in_quiet_hours()
    if quiet:
        return {"ok": True, "quiet": True, "quiet_window": qwindow, "due": [],
                "note": "inside quiet hours — nothing is due"}
    try:
        from jarvis.tools.dynamic.automation import AutomationEngine  # noqa: PLC0415
    except ImportError as exc:
        return {"error": f"schedules.due: automation engine not available ({exc})"}
    engine = AutomationEngine(data_dir=_automation_data_dir())
    now = datetime.now().astimezone()
    cutoff = now - timedelta(minutes=window)
    due = []
    for routine in engine.list():
        cron = routine.get("cron")
        if routine.get("trigger") != "cron" or not cron:
            continue
        prev, err = _cron_prev(now, cron)
        if err or prev is None:
            continue
        if prev >= cutoff:
            due.append({"routine": routine["name"], "cron": cron,
                        "scheduled_for": prev.isoformat(timespec="minutes")})
    return {"ok": True, "quiet": False, "window_minutes": window, "due": due,
            "note": "report only — nothing was executed"}


TOOL_DEFS = [
    {"name": "schedules.parse",
     "description": ("Parse a natural-language schedule into a cron expression "
                     "{text}. Honest error when the phrasing is not understood."),
     "handler": parse_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string"}},
    {"name": "schedules.next",
     "description": "Next N run times for a 5-field cron expression {cron, n?}.",
     "handler": next_handler, "risk": "low", "needs_network": False,
     "schema": {"cron": "string", "n": "int?"}},
    {"name": "schedules.quiet_hours",
     "description": ("Get or set the quiet window {start?, end?} as HH:MM. "
                     "Routines stay silent inside it."),
     "handler": quiet_hours_handler, "risk": "low", "needs_network": False,
     "schema": {"start": "string?", "end": "string?"}},
    {"name": "schedules.create_routine",
     "description": ("Parse a natural-language schedule and create a cron "
                     "routine in the automation engine {name, schedule, steps}."),
     "handler": create_routine_handler, "risk": "medium", "needs_network": False,
     "schema": {"name": "string", "schedule": "string", "steps": "list"}},
    {"name": "schedules.record_run",
     "description": ("Append a run-history entry {routine, status: "
                     "ok|failed|skipped, detail?}."),
     "handler": record_run_handler, "risk": "low", "needs_network": False,
     "schema": {"routine": "string", "status": "string", "detail": "string?"}},
    {"name": "schedules.history",
     "description": ("Recent runs and success rate for a routine "
                     "{routine, n?}."),
     "handler": history_handler, "risk": "low", "needs_network": False,
     "schema": {"routine": "string", "n": "int?"}},
    {"name": "schedules.due",
     "description": ("Cron routines due inside the tick window "
                     "{window_minutes?}, honoring quiet hours. Report only — "
                     "never executes."),
     "handler": due_handler, "risk": "low", "needs_network": False,
     "schema": {"window_minutes": "int?"}},
]

RISK_TABLE_ADDITIONS = {
    "schedules.parse": ("low", False),
    "schedules.next": ("low", False),
    "schedules.quiet_hours": ("low", False),
    "schedules.create_routine": ("medium", False),
    "schedules.history": ("low", False),
    "schedules.record_run": ("low", False),
    "schedules.due": ("low", False),
}


def register(reg) -> None:
    """Wire the seven schedules pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
