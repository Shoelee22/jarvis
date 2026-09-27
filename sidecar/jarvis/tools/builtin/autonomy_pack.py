"""Autonomy Budget, Kill Switch, and run-until-done loop (Phase 11).

Three pieces sibling packs (workers, proactive, autopilot) build on:

1. AUTONOMY BUDGET — a rolling 60-minute cap on autonomous low-risk tool
   calls. ``spend(n)`` records usage; when the cap is hit, autonomous
   execution stops with reason "budget_exhausted" instead of failing
   silently.
2. KILL SWITCH — ``autonomy.off`` sets a persistent ``killed`` flag. Sibling
   packs check ``is_killed()`` before doing anything autonomous. ``autonomy.off``
   is deliberately LOW risk: the policy engine allows low-risk tools without
   confirmation, so the kill switch can never be blocked by the very gate it
   is meant to override. ``autonomy.on`` (re-enable) is HIGH risk on purpose —
   turning autonomy back on is the dangerous direction, so it goes through
   the normal confirmation flow.
3. RUN-UNTIL-DONE LOOP — ``agent.run_until`` / ``agent.run_continue`` run a
   bounded chunk of the live Agent's ReAct loop (like ``Agent.delegate`` does,
   but with a rolling summary instead of a role), checkpointing every N steps.
   Every tool call inside the loop is issued with actor="autonomy" so the
   audit log tags it, and every executed tool call spends 1 unit of budget.

PARENT WIRING (sidecar/jarvis/ipc/server.py), right after bind_agent(agent)::

    from ..tools.builtin import autonomy_pack
    autonomy_pack.bind_agent(agent)

    from jarvis.agent.policy import RISK_TABLE
    RISK_TABLE.update(autonomy_pack.RISK_TABLE_ADDITIONS)

KILL-SWITCH COVERAGE FOR WORKERS (parent: 3-line patch in workers_pack.tick_handler,
at the top of the handler body)::

    from . import autonomy_pack
    if autonomy_pack.is_killed():
        return {"ticked": [], "skipped": [], "skipped_reasons": {},
                "killed": True, "note": "autonomy kill switch engaged — tick halted"}

Same pattern applies to any other autonomous entry point (proactive sweeps,
autopilot posts): check ``is_killed()`` first, halt honestly if True.

State lives in sqlite (stdlib only): ~/workspace/jarvis/data/autonomy.db,
overridable with the JARVIS_AUTONOMY_DB env var (tests use a tmp file).
Handlers take dict -> return dict and never raise.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "autonomy.db"

WINDOW_SECONDS = 3600          # rolling budget window: trailing 60 minutes
DEFAULT_BUDGET = 10            # default max autonomous low-risk actions per hour
MAX_BUDGET = 1000
DEFAULT_MAX_STEPS = 30         # run_until total step cap unless overridden
DEFAULT_CHECKPOINT_EVERY = 5   # steps per chunk unless overridden
SUMMARY_MAX_CHARS = 1500       # rolling-summary carry size cap

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/workers_pack.py and delegate_tool.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# SQLite state machine (single connection, check_same_thread=False, one lock)
# ---------------------------------------------------------------------------
_DB_LOCK = threading.Lock()
_store_cache: dict = {"path": None, "conn": None}


def _db_path() -> Path:
    import os

    override = os.environ.get("JARVIS_AUTONOMY_DB")
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
            conn.execute("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS usage(ts INTEGER NOT NULL, what TEXT NOT NULL)")
            conn.execute("""CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY, goal TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
                steps_used INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'checkpoint',
                pending_tool TEXT, updated_at INTEGER NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts)")
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


# ---------------------------------------------------------------------------
# Module-level API for sibling packs (autopilot / proactive import these)
# ---------------------------------------------------------------------------
def is_killed() -> bool:
    """True when the kill switch is OFF-state active.

    Missing DB / missing row / unreadable store -> False (fail open for
    reads: a missing store cannot have a kill flag set).
    """
    try:
        def _q(conn):
            r = conn.execute("SELECT v FROM settings WHERE k='killed'").fetchone()
            return (r["v"] == "1") if r else False

        return bool(_exec(_q))
    except Exception:
        return False


def _budget_per_hour() -> int:
    def _q(conn):
        r = conn.execute("SELECT v FROM settings WHERE k='budget_per_hour'").fetchone()
        return int(r["v"]) if r else DEFAULT_BUDGET

    try:
        b = _exec(_q)
    except Exception:
        return DEFAULT_BUDGET
    return max(1, min(MAX_BUDGET, b))


def _used_last_hour() -> int:
    cutoff = int(time.time()) - WINDOW_SECONDS

    def _q(conn):
        r = conn.execute("SELECT COUNT(*) AS c FROM usage WHERE ts >= ?",
                         (cutoff,)).fetchone()
        return int(r["c"])

    try:
        return _exec(_q)
    except Exception:
        return 0


def spend(n: int = 1, what: str = "") -> dict:
    """Charge ``n`` autonomous actions against the rolling-hour budget.

    Returns {"ok": True, "remaining": int} on success, or
    {"error": ..., "reason": "killed" | "budget_exhausted"}.
    Killed is checked first: a dead switch beats the budget math.
    """
    try:
        n = max(1, int(n or 1))
    except (TypeError, ValueError):
        n = 1
    if is_killed():
        return {"error": "autonomy kill switch is engaged — no autonomous actions allowed",
                "reason": "killed"}
    budget = _budget_per_hour()
    used = _used_last_hour()
    if used + n > budget:
        return {"error": (f"autonomy budget exhausted: {used}/{budget} used in the "
                          f"last 60 minutes (needs {n} more)"),
                "reason": "budget_exhausted"}
    now = int(time.time())

    def _ins(conn):
        conn.executemany("INSERT INTO usage(ts, what) VALUES(?, ?)",
                         [(now, (what or "")[:200]) for _ in range(n)])

    _exec(_ins)
    return {"ok": True, "remaining": budget - used - n}


def _set_killed(on: bool) -> None:
    def _w(conn):
        conn.execute("INSERT INTO settings(k, v) VALUES('killed', ?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     ("1" if on else "0",))

    _exec(_w)


# ---------------------------------------------------------------------------
# Run-state persistence for agent.run_until / agent.run_continue
# ---------------------------------------------------------------------------
def _save_run(run_id: str, goal: str, summary: str, steps_used: int,
              status: str, pending_tool: str | None = None) -> None:
    def _w(conn):
        conn.execute(
            "INSERT INTO runs(run_id, goal, summary, steps_used, status, pending_tool, updated_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(run_id) DO UPDATE SET summary=excluded.summary,"
            " steps_used=excluded.steps_used, status=excluded.status,"
            " pending_tool=excluded.pending_tool, updated_at=excluded.updated_at",
            (run_id, goal, summary[:4000], steps_used, status,
             pending_tool, int(time.time())))

    _exec(_w)


def _load_run(run_id: str) -> dict | None:
    def _q(conn):
        r = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    return _exec(_q)


# ---------------------------------------------------------------------------
# Handlers (dict -> dict, never raise)
# ---------------------------------------------------------------------------
def _wrap(fn):
    def _h(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # never let an autonomy tool crash the loop
            return {"error": f"{type(e).__name__}: {e}"}

    return _h


@_wrap
def budget_handler(args: dict) -> dict:
    raw = args.get("n", DEFAULT_BUDGET)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return {"error": f"bad budget {raw!r}: must be an integer 1..{MAX_BUDGET}"}
    if not 1 <= n <= MAX_BUDGET:
        return {"error": f"bad budget {n}: must be 1..{MAX_BUDGET}"}

    def _w(conn):
        conn.execute("INSERT INTO settings(k, v) VALUES('budget_per_hour', ?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))

    _exec(_w)
    return {"budget": n, "window": "60min"}


@_wrap
def status_handler(args: dict) -> dict:
    budget = _budget_per_hour()
    used = _used_last_hour()
    day_start = int(datetime.now().replace(hour=0, minute=0, second=0,
                                           microsecond=0).timestamp())

    def _q(conn):
        r = conn.execute("SELECT COUNT(*) AS c FROM usage WHERE ts >= ?",
                         (day_start,)).fetchone()
        today = int(r["c"])
        rows = conn.execute(
            "SELECT ts, what FROM usage ORDER BY rowid DESC LIMIT 10").fetchall()
        return today, [{"ts": row["ts"], "what": row["what"]} for row in rows]

    today, recent = _exec(_q)
    return {"killed": is_killed(), "budget_per_hour": budget,
            "used_last_hour": used, "remaining": max(0, budget - used),
            "autonomous_actions_today": today, "recent": recent}


@_wrap
def off_handler(args: dict) -> dict:
    # LOW risk on purpose: the policy gate allows low-risk tools without
    # confirmation, so the kill switch is always immediately effective and can
    # never be blocked by the confirmation machinery it overrides.
    _set_killed(True)
    return {"killed": True,
            "note": ("workers/proactive/autopilot execution halted; "
                     "re-enable with autonomy.on")}


@_wrap
def on_handler(args: dict) -> dict:
    # HIGH risk on purpose: re-enabling autonomy is the dangerous direction,
    # so it must pass the normal confirmation flow (see RISK_TABLE_ADDITIONS).
    _set_killed(False)
    return {"killed": False, "note": "autonomy re-enabled"}


def _chunk_prompt(goal: str, summary: str) -> str:
    if not summary:
        return goal
    return (f"Goal: {goal}\n\nProgress so far (from previous chunks):\n{summary}\n\n"
            "Continue working toward the goal. If the goal is complete, reply with "
            "your final result and make no further tool calls.")


def _run_one_chunk(goal: str, summary: str, steps_used: int, max_steps: int,
                   chunk: int, run_id: str, confirmed_tools: set) -> dict:
    """Run a single chunked sub-Agent (delegate-style) and account budget.

    Returns the chunk result dict plus spend bookkeeping. Every registry call
    inside the chunk is forced to actor="autonomy" via a temporary instance
    patch on the shared registry (restored in finally) — core.py's Agent.run
    calls self.registry.call without an actor, and this pack must not edit
    core.py.
    """
    agent = _AGENT
    sub = agent.__class__(agent.llm, agent.registry, agent.audit,
                          memory=agent.memory, max_steps=chunk,
                          system_prompt=agent.system_prompt)
    registry = agent.registry
    orig_call = registry.call

    def _autonomy_call(name, args, actor="autonomy", audit=None, confirmed=False):
        return orig_call(name, args, actor="autonomy", audit=audit,
                         confirmed=confirmed)

    registry.call = _autonomy_call
    try:
        res = sub.run(_chunk_prompt(goal, summary), confirmed_tools=confirmed_tools)
    finally:
        registry.call = orig_call

    # Budget accounting: one unit per tool call in the chunk's timeline.
    for entry in res.get("timeline", []):
        bill = spend(1, what=f"run_until:{run_id}:{entry.get('tool', '?')}")
        if not bill.get("ok"):
            return {"halted": True, "reason": bill.get("reason"),
                    "error": bill.get("error"),
                    "steps_used": steps_used + len(res.get("timeline", [])),
                    "timeline": res.get("timeline", [])}
    new_steps = steps_used + len(res.get("timeline", []))
    new_summary = ((summary + "\n---\n" if summary else "") +
                   (res.get("reply") or ""))[:SUMMARY_MAX_CHARS]
    return {"halted": False, "res": res, "steps_used": new_steps,
            "summary": new_summary, "timeline": res.get("timeline", [])}


@_wrap
def run_until_handler(args: dict) -> dict:
    goal = (args.get("goal") or "").strip()
    if not goal:
        return {"error": "goal is empty — describe what the agent should accomplish"}
    if _AGENT is None:
        return {"error": "agent not bound — autonomy pack needs bind_agent(agent) from server.py"}
    if is_killed():
        return {"error": "autonomy kill switch is engaged", "reason": "killed"}
    try:
        max_steps = int(args.get("max_steps", DEFAULT_MAX_STEPS))
    except (TypeError, ValueError):
        return {"error": f"bad max_steps {args.get('max_steps')!r}: must be an integer"}
    max_steps = max(1, min(200, max_steps))
    try:
        chunk = int(args.get("checkpoint_every", DEFAULT_CHECKPOINT_EVERY))
    except (TypeError, ValueError):
        return {"error": f"bad checkpoint_every {args.get('checkpoint_every')!r}: must be an integer"}
    chunk = max(1, min(chunk, max_steps))

    run_id = uuid.uuid4().hex[:16]
    out = _run_one_chunk(goal, "", 0, max_steps, chunk, run_id, set())
    if out.get("halted"):
        _save_run(run_id, goal, "", out["steps_used"], "halted")
        return {"done": False, "reason": out["reason"], "error": out["error"],
                "run_id": run_id, "steps_used": out["steps_used"],
                "timeline": out["timeline"]}
    res, steps_used, summary = out["res"], out["steps_used"], out["summary"]

    if res.get("awaiting_confirmation"):
        _save_run(run_id, goal, summary, steps_used, "awaiting_confirmation",
                  pending_tool=res["awaiting_confirmation"])
        return {"done": False, "awaiting_confirmation": res["awaiting_confirmation"],
                "confirm_text": res.get("reply", ""), "run_id": run_id,
                "steps_used": steps_used, "summary": summary,
                "note": ("confirm the pending tool through the normal flow, then "
                         "call agent.run_continue with run_id and confirm=true")}
    if res.get("done"):
        _save_run(run_id, goal, summary, steps_used, "done")
        return {"done": True, "reply": res.get("reply", ""), "run_id": run_id,
                "steps_used": steps_used, "timeline": res.get("timeline", [])}
    # Steps remain: checkpoint and hand control back to the caller.
    _save_run(run_id, goal, summary, steps_used, "checkpoint")
    return {"done": False, "checkpoint": 1, "summary": summary, "run_id": run_id,
            "steps_used": steps_used, "max_steps": max_steps,
            "timeline": res.get("timeline", []),
            "note": "call agent.run_continue to proceed"}


@_wrap
def run_continue_handler(args: dict) -> dict:
    run_id = (args.get("run_id") or "").strip()
    if not run_id:
        return {"error": "run_id is required"}
    if _AGENT is None:
        return {"error": "agent not bound — autonomy pack needs bind_agent(agent) from server.py"}
    if is_killed():
        return {"error": "autonomy kill switch is engaged", "reason": "killed"}
    state = _load_run(run_id)
    if state is None:
        return {"error": f"no such run {run_id!r}"}
    if state["status"] == "done":
        return {"error": f"run {run_id!r} is already done"}
    goal, summary, steps_used = state["goal"], state["summary"], state["steps_used"]
    try:
        chunk = int(args.get("checkpoint_every", DEFAULT_CHECKPOINT_EVERY))
    except (TypeError, ValueError):
        return {"error": f"bad checkpoint_every {args.get('checkpoint_every')!r}: must be an integer"}
    chunk = max(1, chunk)
    try:
        max_steps = int(args.get("max_steps", DEFAULT_MAX_STEPS))
    except (TypeError, ValueError):
        max_steps = DEFAULT_MAX_STEPS
    max_steps = max(steps_used + 1, min(200, max_steps))

    confirmed_tools: set = set()
    if args.get("confirm") and state.get("pending_tool"):
        confirmed_tools.add(state["pending_tool"])

    out = _run_one_chunk(goal, summary, steps_used, max_steps, chunk,
                         run_id, confirmed_tools)
    if out.get("halted"):
        _save_run(run_id, goal, summary, out["steps_used"], "halted")
        return {"done": False, "reason": out["reason"], "error": out["error"],
                "run_id": run_id, "steps_used": out["steps_used"],
                "timeline": out["timeline"]}
    res = out["res"]
    steps_used, summary = out["steps_used"], out["summary"]

    if res.get("awaiting_confirmation"):
        _save_run(run_id, goal, summary, steps_used, "awaiting_confirmation",
                  pending_tool=res["awaiting_confirmation"])
        return {"done": False, "awaiting_confirmation": res["awaiting_confirmation"],
                "confirm_text": res.get("reply", ""), "run_id": run_id,
                "steps_used": steps_used, "summary": summary,
                "note": ("confirm the pending tool through the normal flow, then "
                         "call agent.run_continue with run_id and confirm=true")}
    if res.get("done") or steps_used >= max_steps:
        _save_run(run_id, goal, summary, steps_used, "done")
        return {"done": True, "reply": res.get("reply", ""), "run_id": run_id,
                "steps_used": steps_used, "timeline": res.get("timeline", [])}
    checkpoint_n = (steps_used // chunk) + 1
    _save_run(run_id, goal, summary, steps_used, "checkpoint")
    return {"done": False, "checkpoint": checkpoint_n, "summary": summary,
            "run_id": run_id, "steps_used": steps_used, "max_steps": max_steps,
            "timeline": res.get("timeline", []),
            "note": "call agent.run_continue to proceed"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "autonomy.budget",
     "description": ("Set the max number of autonomous low-risk tool actions allowed "
                     "per rolling 60-minute window. n is an integer 1..1000, default 10. "
                     "When the budget is exhausted, autonomous runs stop honestly "
                     "instead of acting."),
     "handler": budget_handler, "risk": "medium", "needs_network": False,
     "schema": {"n?": "int"}},
    {"name": "autonomy.status",
     "description": ("Show the autonomy state: kill-switch flag, budget per hour, used "
                     "in the last hour, remaining, autonomous actions today, and recent "
                     "usage entries."),
     "handler": status_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "autonomy.off",
     "description": ("KILL SWITCH. Immediately halts all autonomous execution "
                     "(workers, proactive sweeps, autopilot posts, run-until-done runs). "
                     "Takes effect at once — no confirmation, by design. Re-enable "
                     "with autonomy.on (which does need confirmation)."),
     "handler": off_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "autonomy.on",
     "description": ("Re-enable autonomous execution after the kill switch. "
                     "High risk — needs explicit confirmation."),
     "handler": on_handler, "risk": "high", "needs_network": False,
     "schema": {}},
    {"name": "agent.run_until",
     "description": ("Run the agent toward a goal in bounded chunks of "
                     "checkpoint_every steps (default 5, max_steps default 30). Each "
                     "chunk carries a rolling summary of progress. Every tool call "
                     "inside is tagged actor='autonomy' and spends 1 unit of the "
                     "autonomy budget. Returns a checkpoint (call agent.run_continue), "
                     "an awaiting_confirmation pause, or done. Requires a bound agent."),
     "handler": run_until_handler, "risk": "medium", "needs_network": False,
     "schema": {"goal": "string", "max_steps?": "int", "checkpoint_every?": "int"}},
    {"name": "agent.run_continue",
     "description": ("Resume a checkpointed agent.run_until run by run_id. Continues "
                     "autonomous execution — pass confirm=true to pre-confirm the "
                     "tool that was awaiting confirmation."),
     "handler": run_continue_handler, "risk": "medium", "needs_network": False,
     "schema": {"run_id": "string", "checkpoint_every?": "int",
                "max_steps?": "int", "confirm?": "bool"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(autonomy_pack.RISK_TABLE_ADDITIONS)
#
# Deliberate asymmetry: autonomy.off is LOW (never blocked — a kill switch
# that needs permission is no kill switch), autonomy.on is HIGH (re-enabling
# autonomy is the dangerous direction and must be confirmed).
RISK_TABLE_ADDITIONS = {
    "autonomy.budget": ("medium", False),
    "autonomy.status": ("low", False),
    "autonomy.off": ("low", False),
    "autonomy.on": ("high", False),
    "agent.run_until": ("medium", False),
    "agent.run_continue": ("medium", False),
}


def register(reg) -> None:
    """Wire the six Autonomy tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
