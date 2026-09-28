"""Background Workers pack (Phase 9): persistent, schedule-driven agents.

A "worker" is a named goal that the sidecar pursues on its own cadence, e.g.
"every morning, check my inbox and brief me". Each tick, due workers are run
via the live Agent's bounded specialist loop (``Agent.delegate`` — the same
ReAct machinery the interactive session uses, with role-shaped behavior, tool
allowlist, policy engine and audit log).

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    In sidecar/jarvis/ipc/server.py, right after ``bind_agent(agent)``, add::

        from ..tools.builtin import workers_pack
        workers_pack.bind_agent(agent)

    Then, in the sidecar's main loop, call periodically (e.g. every 60 s)::

        registry.call("workers.tick", {}, actor="scheduler")

    There is no separate background thread or tick loop in this module on
    purpose: ``workers.tick`` IS the tick. The host process decides the
    cadence by calling it. If it is never called, workers never run — that is
    by design, and the tick handler is honest about what it did and skipped.

SAFETY:
    - Spawning a worker requires explicit opt-in:
      ``workers.autonomy_opt_in: true`` in
      ~/workspace/jarvis/dynamic/tools_config.phase9.yaml (default: False).
      Without it, workers.spawn returns an error explaining how to enable it.
    - workers.spawn is high risk (persistent autonomous action) and
      workers.kill is high risk (deletes history) — both go through the
      policy confirmation flow like any other high-risk tool.
    - Handlers take dict -> return dict and never raise; the registry wraps
      failures, but we keep the belt-and-braces here too.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/workers.db,
      overridable with the JARVIS_WORKERS_DB env var (tests use a tmp file).
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "dynamic" / "tools_config.phase9.yaml"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "workers.db"

SCHEDULE_EXAMPLES = (
    'schedule must be "interval:<seconds>" (e.g. "interval:3600") or '
    '"daily@HH:MM" 24h server-local (e.g. "daily@08:30")'
)
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/delegate_tool.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# Config: autonomy opt-in gate
# ---------------------------------------------------------------------------
def _autonomy_opt_in() -> bool:
    """Read workers.autonomy_opt_in from tools_config.phase9.yaml.

    Missing file / broken YAML / missing key -> False (safe default).
    """
    try:
        text = CONFIG_PATH.read_text()
    except OSError:
        return False
    # Prefer pyyaml when available; fall back to a tiny targeted regex so a
    # missing dependency never crashes the gate.
    try:
        import yaml  # noqa: PLC0415

        cfg = yaml.safe_load(text) or {}
        workers_cfg = cfg.get("workers") or {}
        return bool(workers_cfg.get("autonomy_opt_in", False))
    except Exception:
        pass
    m = re.search(r"autonomy_opt_in\s*:\s*(true|false|yes|no|on|off)",
                  text, re.IGNORECASE)
    if not m:
        return False
    return m.group(1).lower() in ("true", "yes", "on")


# ---------------------------------------------------------------------------
# Schedule parsing + due calculation
# ---------------------------------------------------------------------------
def _parse_schedule(schedule: str) -> dict:
    """Strict parse -> {"kind": "interval", "seconds": int} or
    {"kind": "daily", "hour": int, "minute": int}. Raises ValueError on
    anything else with a helpful message."""
    s = (schedule or "").strip()
    if s.startswith("interval:"):
        raw = s[len("interval:"):].strip()
        if not re.fullmatch(r"\d+", raw):
            raise ValueError(f"bad interval schedule {schedule!r}: {SCHEDULE_EXAMPLES}")
        seconds = int(raw)
        if seconds <= 0:
            raise ValueError(f"interval must be > 0 seconds: {SCHEDULE_EXAMPLES}")
        return {"kind": "interval", "seconds": seconds}
    m = re.fullmatch(r"daily@(\d{1,2}):(\d{2})", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23:
            raise ValueError(f"bad hour in {schedule!r}: {SCHEDULE_EXAMPLES}")
        return {"kind": "daily", "hour": hour, "minute": minute}
    raise ValueError(f"unrecognized schedule {schedule!r}: {SCHEDULE_EXAMPLES}")


def _due(parsed: dict, last_run: int | None, now: float) -> bool:
    """True when the worker should fire given its last run timestamp."""
    if last_run is None:
        return True  # first run happens on the next tick after spawn
    if parsed["kind"] == "interval":
        return now >= last_run + parsed["seconds"]
    # daily@HH:MM (server-local): fire once the next occurrence after last_run
    # has passed.
    local = datetime.fromtimestamp(last_run)
    # Recompute next occurrence robustly (handles month/year boundaries):
    from datetime import timedelta
    cand = local.replace(hour=parsed["hour"], minute=parsed["minute"],
                         second=0, microsecond=0)
    while cand.timestamp() <= last_run:
        cand = cand + timedelta(days=1)
    return now >= cand.timestamp()


# ---------------------------------------------------------------------------
# SQLite state machine (single connection, check_same_thread=False, one lock)
# ---------------------------------------------------------------------------
_DB_LOCK = threading.Lock()
_store_cache: dict = {"path": None, "conn": None}


def _db_path() -> Path:
    import os

    override = os.environ.get("JARVIS_WORKERS_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _get_store() -> sqlite3.Connection:
    """One cached connection; rebuilt if the path changed (env override)."""
    with _DB_LOCK:
        path = _db_path()
        if _store_cache["conn"] is None or _store_cache["path"] != str(path):
            if _store_cache["conn"] is not None:
                _store_cache["conn"].close()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("""CREATE TABLE IF NOT EXISTS workers(
                name TEXT PRIMARY KEY, goal TEXT NOT NULL, schedule TEXT NOT NULL,
                role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL, last_run INTEGER, run_count INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS worker_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                ts INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL)""")
            conn.commit()
            _store_cache.update(path=str(path), conn=conn)
        return _store_cache["conn"]


def _exec(fn, *args, **kwargs):
    """Run fn(conn, ...) under the global lock; commit."""
    conn = _get_store()
    with _DB_LOCK:
        out = fn(conn, *args, **kwargs)
        conn.commit()
        return out


def _worker_row(name: str) -> dict | None:
    def _q(conn, name):
        r = conn.execute("SELECT * FROM workers WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    return _exec(_q, name)


def _log(conn, name: str, kind: str, text: str, max_rows: int = 500) -> None:
    conn.execute("INSERT INTO worker_log(name, ts, kind, text) VALUES(?,?,?,?)",
                 (name, int(time.time()), kind, text[:2000]))
    # Bound log growth per worker.
    conn.execute("""DELETE FROM worker_log WHERE name=? AND id NOT IN
                    (SELECT id FROM worker_log WHERE name=? ORDER BY id DESC LIMIT ?)""",
                 (name, name, max_rows))


# ---------------------------------------------------------------------------
# Handlers (dict -> dict, never raise)
# ---------------------------------------------------------------------------
def _wrap(fn):
    def _h(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # never let a worker tool crash the loop
            return {"error": f"{type(e).__name__}: {e}"}

    return _h


@_wrap
def spawn_handler(args: dict) -> dict:
    name = (args.get("name") or "").strip().lower()
    goal = (args.get("goal") or "").strip()
    schedule = (args.get("schedule") or "").strip()
    role_name = (args.get("role") or "planner").strip().lower() or "planner"

    if not _autonomy_opt_in():
        return {"error": ("background workers need explicit opt-in: set "
                          "workers.autonomy_opt_in: true in tools_config.phase9.yaml")}
    if not NAME_RE.match(name):
        return {"error": ("bad name %r: must match ^[a-z][a-z0-9_]{2,30}$ "
                          "(lowercase, start with a letter)") % name}
    if not goal:
        return {"error": "goal is empty — describe what the worker should do each run"}
    from ...agent.roles import get_role, role_names

    role = get_role(role_name)
    if role is None:
        return {"error": f"unknown role {role_name!r} — choose from: {', '.join(role_names())}"}
    try:
        parsed = _parse_schedule(schedule)
    except ValueError as e:
        return {"error": str(e)}

    def _ins(conn):
        if conn.execute("SELECT 1 FROM workers WHERE name=?", (name,)).fetchone():
            raise ValueError(f"worker {name!r} already exists")
        conn.execute(
            "INSERT INTO workers(name,goal,schedule,role,status,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (name, goal, schedule, role.name, "active", int(time.time())))
        _log(conn, name, "spawn",
             f"spawned with schedule {schedule!r}, role {role.name!r}")

    try:
        _exec(_ins)
    except ValueError as e:
        return {"error": str(e)}
    return {"ok": True, "name": name, "schedule": schedule,
            "parsed": parsed, "role": role.name, "status": "active"}


@_wrap
def tick_handler(args: dict) -> dict:
    now = time.time()
    ticked: list[str] = []
    skipped: list[str] = []
    reasons: dict[str, str] = {}
    # --- Phase 11: kill switch ---
    try:
        from . import autonomy_pack as _auto
        if _auto.is_killed():
            return {"ticked": [], "skipped": ["all"],
                    "skipped_reasons": {"all": "autonomy is OFF (kill switch) — re-enable with autonomy.on"}}
    except Exception:
        pass
    # --- Phase 13: sweep open loops (promise tracking) on every tick ---
    try:
        from . import loops_pack as _loops
        loop_sweep = _loops.run_check()
    except Exception as e:  # tick must never die because of a sweep
        loop_sweep = {"error": f"loops sweep failed: {e}"}

    def _all(conn):
        return [dict(r) for r in
                conn.execute("SELECT * FROM workers WHERE status='active' ORDER BY name")]

    workers = _exec(_all)
    for w in workers:
        name = w["name"]
        try:
            parsed = _parse_schedule(w["schedule"])
        except ValueError as e:
            _exec(_log, name, "error", f"bad stored schedule: {e}")
            skipped.append(name)
            reasons[name] = "bad stored schedule"
            continue
        if not _due(parsed, w["last_run"], now):
            skipped.append(name)
            reasons[name] = "not due"
            continue
        if _AGENT is None:
            _exec(_log, name, "skip", "agent not bound, skipped")
            skipped.append(name)
            reasons[name] = "agent not bound"
            continue
        res = _AGENT.delegate(w["role"], w["goal"], max_steps=6)
        reply = (res.get("reply") or "")[:500] if isinstance(res, dict) else str(res)[:500]
        ok = bool(res.get("ok")) if isinstance(res, dict) else False

        def _upd(conn, w=w, name=name, ok=ok, reply=reply):
            conn.execute("UPDATE workers SET last_run=?, run_count=run_count+1 WHERE name=?",
                         (int(now), name))
            _log(conn, name, "run" if ok else "error",
                 f"role={w['role']} steps={res.get('steps_used') if isinstance(res, dict) else '?'} "
                 f"ok={ok} reply={reply!r}")

        _exec(_upd)
        ticked.append(name)
    return {"ticked": ticked, "skipped": skipped, "skipped_reasons": reasons,
            "loops_sweep": loop_sweep}


@_wrap
def list_handler(args: dict) -> dict:
    def _all(conn):
        rows = conn.execute(
            "SELECT name,goal,schedule,role,status,created_at,last_run,run_count"
            " FROM workers ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    workers = _exec(_all)
    for w in workers:
        try:
            parsed = _parse_schedule(w["schedule"])
        except ValueError:
            parsed = None
        w["due_now"] = _due(parsed, w["last_run"], time.time()) if parsed else None
    return {"workers": workers}


def _status_flip(args: dict, to: str) -> dict:
    name = (args.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        return {"error": f"bad name {name!r}"}

    def _flip(conn):
        r = conn.execute("SELECT status FROM workers WHERE name=?", (name,)).fetchone()
        if r is None:
            return None
        conn.execute("UPDATE workers SET status=? WHERE name=?", (to, name))
        _log(conn, name, to, f"status -> {to}")
        return to

    result = _exec(_flip)
    if result is None:
        return {"error": f"no such worker {name!r}"}
    return {"ok": True, "name": name, "status": result}


def pause_handler(args: dict) -> dict:
    return _wrap(lambda a: _status_flip(a, "paused"))(args)


def resume_handler(args: dict) -> dict:
    return _wrap(lambda a: _status_flip(a, "active"))(args)


@_wrap
def logs_handler(args: dict) -> dict:
    name = (args.get("name") or "").strip().lower()
    try:
        n = int(args.get("n", 20))
    except (TypeError, ValueError):
        n = 20
    n = max(1, min(n, 200))

    def _q(conn):
        exists = conn.execute("SELECT 1 FROM workers WHERE name=?", (name,)).fetchone()
        rows = conn.execute(
            "SELECT ts,kind,text FROM worker_log WHERE name=? ORDER BY id DESC LIMIT ?",
            (name, n)).fetchall()
        return bool(exists), [dict(r) for r in rows]

    exists, entries = _exec(_q)
    if not exists:
        return {"error": f"no such worker {name!r}"}
    return {"name": name, "logs": entries}


@_wrap
def kill_handler(args: dict) -> dict:
    name = (args.get("name") or "").strip().lower()

    def _del(conn):
        r = conn.execute("SELECT 1 FROM workers WHERE name=?", (name,)).fetchone()
        if r is None:
            return False
        conn.execute("DELETE FROM worker_log WHERE name=?", (name,))
        conn.execute("DELETE FROM workers WHERE name=?", (name,))
        return True

    if not _exec(_del):
        return {"error": f"no such worker {name!r}"}
    return {"ok": True, "name": name, "killed": True}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "workers.spawn",
     "description": ("Create a background worker: a named goal the sidecar pursues on a "
                     "schedule. schedule is 'interval:<seconds>' or 'daily@HH:MM'. Requires "
                     "workers.autonomy_opt_in: true in tools_config.phase9.yaml. "
                     "Persistent autonomous action — needs confirmation."),
     "handler": spawn_handler, "risk": "high", "needs_network": False,
     "schema": {"name": "string", "goal": "string", "schedule": "string", "role?": "string"}},
    {"name": "workers.tick",
     "description": ("Run every active worker that is due (from last_run and schedule). "
                     "This IS the scheduler tick — the sidecar's main loop should call "
                     "registry.call('workers.tick', {}) periodically. Logs each run to "
                     "worker_log and updates last_run/run_count."),
     "handler": tick_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "workers.list",
     "description": "List all workers with status, schedule, last_run, run_count and whether each is due now.",
     "handler": list_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "workers.pause",
     "description": "Pause a worker (it is skipped by workers.tick until resumed).",
     "handler": pause_handler, "risk": "medium", "needs_network": False,
     "schema": {"name": "string"}},
    {"name": "workers.resume",
     "description": "Resume a paused worker.",
     "handler": resume_handler, "risk": "medium", "needs_network": False,
     "schema": {"name": "string"}},
    {"name": "workers.logs",
     "description": "Show the newest n (default 20, max 200) log entries for a worker.",
     "handler": logs_handler, "risk": "low", "needs_network": False,
     "schema": {"name": "string", "n?": "int"}},
    {"name": "workers.kill",
     "description": "Delete a worker and all its log history. IRREVERSIBLE — needs confirmation.",
     "handler": kill_handler, "risk": "high", "needs_network": False,
     "schema": {"name": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(workers_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "workers.spawn": ("high", False),
    "workers.tick": ("low", False),
    "workers.list": ("low", False),
    "workers.pause": ("medium", False),
    "workers.resume": ("medium", False),
    "workers.logs": ("low", False),
    "workers.kill": ("high", False),
}


def register(reg) -> None:
    """Wire the seven Background Workers tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
