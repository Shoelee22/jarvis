"""Autopilot approval-queue pack (Phase 11): batch-approved multi-step plans.

JARVIS proposes a multi-step plan; the user approves it in ONE batch
(this user batches approvals — this whole pack is designed for that).
The queue lives in sqlite so pending approvals survive restarts.

FLOW:
    1. autopilot.propose  -> validate steps, store proposal (status pending),
       NEVER executes anything. Returns a proposal id.
    2. autopilot.queue    -> list pending proposals with step summaries.
    3. autopilot.approve  -> {id} or {"all"}: executes the steps IN ORDER
       through the REAL registry with confirmed=True (the batch approval IS
       the user's confirmation). Per-step permission grants are respected
       (deny -> skip, never execute).
    4. autopilot.reject   -> {id, reason}: rejected proposals can never be
       approved afterwards — rejection stops the plan.
    5. autopilot.status   -> counts by status + recent proposals with
       per-step outcomes.

SAFETY:
    - autopilot.approve is HIGH risk (executes arbitrary registered tools)
      and goes through the policy confirmation flow like any high-risk tool.
      The user approves the whole plan in one confirmation; individual steps
      do not re-prompt (confirmed=True), but each step still goes through
      the policy engine via Registry.call.
    - Before executing ANY approved plan, the Phase 11 kill switch is
      checked: ``from . import autonomy_pack; autonomy_pack.is_killed()``.
      The sibling autonomy_pack may not be installed yet, so the import is
      defensive — ImportError means "no kill switch wired in", treated as
      not killed and noted honestly in the result.
    - Per-step permission grants (sibling workstream: permissions_pack,
      ``permissions_pack.get_level(tool)``) are consulted for every step.
      A ``deny`` level SKIPS the step and records
      {"skipped": "denied by permissions grant"} — never executes.
      Missing permissions_pack -> treated as allow (nothing denied yet).
      The lookup is guarded by a timeout and FAILS CLOSED: if the lookup
      hangs, the step is skipped with a note instead of executed.
    - Handlers take dict -> return dict and never raise.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/autopilot.db,
      overridable with the JARVIS_AUTOPILOT_DB env var (tests use a tmp file).

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    In sidecar/jarvis/ipc/server.py, right after ``bind_agent(agent)``, add::

        from ..tools.builtin import autopilot_pack
        autopilot_pack.bind_agent(agent)

    Parent wiring:  from jarvis.agent.policy import RISK_TABLE
                    RISK_TABLE.update(autopilot_pack.RISK_TABLE_ADDITIONS)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "autopilot.db"

# How long to wait for permissions_pack.get_level() before failing closed.
_PERMISSION_TIMEOUT_S = 5.0

# Sibling Phase 11 pack: permission grants per tool. May not be installed yet
# (parallel workstream); resolved lazily in _get_permission_level so tests can
# monkeypatch this module attribute directly.
try:
    from . import permissions_pack  # noqa: F401
except ImportError:
    permissions_pack = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/delegate_tool.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# Kill switch (sibling workstream: autonomy_pack). Defensive: it may not
# exist yet — ImportError means "no kill switch wired in".
# ---------------------------------------------------------------------------
def _kill_switch() -> tuple[bool, str]:
    """Return (killed, note). Defensive against the pack not existing yet."""
    try:
        from . import autonomy_pack  # noqa: PLC0415
    except ImportError:
        return False, "autonomy_pack not installed — no kill switch wired in yet"
    try:
        return bool(autonomy_pack.is_killed()), ""
    except Exception as e:
        return False, f"autonomy_pack.is_killed() unavailable ({e})"


def _get_permission_level(tool: str) -> tuple[str, str]:
    """Per-tool permission grant -> (level, note).

    level is one of: 'allow' | 'deny' | 'timeout'.
    The sibling permissions_pack speaks 'allow' | 'ask' | 'deny' | None:
      - 'ask' is treated as 'allow' here, because the batch approval that
        triggered this execution IS the user's answer to the ask.
      - None / anything else -> 'allow' (nothing denied yet).
    Defensive timeout: permissions_pack.get_level() currently deadlocks on
    its own non-reentrant _DB_LOCK (get_level holds it, then calls
    _get_store() which re-acquires it). Until the sibling workstream fixes
    that, every lookup is guarded by a daemon-thread timeout; on timeout the
    level is 'timeout' and the caller must fail closed (skip the step).
    """
    def _lookup():
        pp = globals().get("permissions_pack")
        if pp is None:
            try:
                from . import permissions_pack as _pp  # noqa: PLC0415
            except ImportError:
                return "allow"
            pp = _pp
        return pp.get_level(tool)

    box: dict = {}
    t = threading.Thread(target=lambda: box.__setitem__("v", _lookup()),
                         daemon=True)
    t.start()
    t.join(_PERMISSION_TIMEOUT_S)
    if t.is_alive():
        return "timeout", ("permissions_pack.get_level() timed out — "
                           "step skipped as a precaution")
    try:
        level = box["v"]
    except KeyError:
        return "allow", ""
    if level == "deny":
        return "deny", ""
    if level in ("allow", "ask", None):
        return "allow", ""
    return "allow", ""  # unknown level string: nothing denied yet


# ---------------------------------------------------------------------------
# SQLite state machine (single connection, check_same_thread=False, one lock)
# ---------------------------------------------------------------------------
_DB_LOCK = threading.Lock()
_store_cache: dict = {"path": None, "conn": None}


def _db_path() -> Path:
    override = os.environ.get("JARVIS_AUTOPILOT_DB")
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
            conn.execute("""CREATE TABLE IF NOT EXISTS proposals(
                id TEXT PRIMARY KEY, goal TEXT NOT NULL, steps TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL, decided_at INTEGER,
                decision_reason TEXT, results TEXT)""")
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


def _row_to_dict(r) -> dict:
    d = dict(r)
    try:
        d["steps"] = json.loads(d["steps"])
    except (TypeError, ValueError):
        d["steps"] = []
    try:
        d["results"] = json.loads(d["results"]) if d["results"] else None
    except (TypeError, ValueError):
        d["results"] = None
    return d


def _get_proposal(pid: str) -> dict | None:
    def _q(conn):
        r = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return _row_to_dict(r) if r else None

    return _exec(_q)


def _list_proposals(status: str | None = None, limit: int = 50) -> list[dict]:
    def _q(conn):
        if status:
            rows = conn.execute(
                "SELECT * FROM proposals WHERE status=? ORDER BY created_at",
                (status,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    return _exec(_q)


# ---------------------------------------------------------------------------
# Handlers (dict -> dict, never raise)
# ---------------------------------------------------------------------------
def _wrap(fn):
    def _h(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # never let an autopilot tool crash the loop
            return {"error": f"{type(e).__name__}: {e}"}

    return _h


def _validate_steps(steps) -> tuple[list[dict] | None, str | None]:
    """Validate steps[{tool, args, why}] -> (normalized, None) or (None, error)."""
    if not isinstance(steps, list) or not steps:
        return None, "steps must be a non-empty list of {tool, args, why}"
    norm: list[dict] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            return None, f"step {i} is not an object"
        tool = s.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return None, f"step {i}: 'tool' must be a non-empty string"
        tool = tool.strip()
        sargs = s.get("args", {})
        if not isinstance(sargs, dict):
            return None, f"step {i} ({tool}): 'args' must be an object"
        why = s.get("why")
        if not isinstance(why, str) or not why.strip():
            return None, f"step {i} ({tool}): 'why' must be a non-empty string"
        norm.append({"tool": tool, "args": sargs, "why": why.strip()})
    return norm, None


@_wrap
def propose_handler(args: dict) -> dict:
    goal = (args.get("goal") or "")
    goal = goal.strip() if isinstance(goal, str) else ""
    if not goal:
        return {"error": "goal is empty — describe what the plan should achieve"}
    norm, err = _validate_steps(args.get("steps"))
    if err:
        return {"error": err}
    pid = "ap_" + uuid.uuid4().hex[:8]
    now = int(time.time())

    def _ins(conn):
        conn.execute(
            "INSERT INTO proposals(id, goal, steps, status, created_at)"
            " VALUES(?,?,?,?,?)",
            (pid, goal, json.dumps(norm), "pending", now))

    _exec(_ins)
    # NEVER executes: proposing only stores.
    return {"ok": True, "id": pid, "goal": goal,
            "steps": len(norm), "status": "pending"}


def _step_summary(step: dict) -> dict:
    return {"tool": step["tool"], "why": step["why"]}


@_wrap
def queue_handler(args: dict) -> dict:
    pending = _list_proposals(status="pending")
    return {"pending": [{"id": p["id"], "goal": p["goal"],
                         "created_at": p["created_at"],
                         "steps": [_step_summary(s) for s in p["steps"]]}
                        for p in pending],
            "count": len(pending)}


def _execute_proposal(proposal: dict, kill_note: str) -> dict:
    """Run one pending proposal's steps in order through the real registry.

    Assumes the kill switch was checked and the agent is bound.
    """
    outcomes: list[dict] = []
    failures = 0
    for i, step in enumerate(proposal["steps"]):
        tool = step["tool"]
        level, note = _get_permission_level(tool)
        if level in ("deny", "timeout"):
            reason = ("denied by permissions grant" if level == "deny"
                      else note or "permission lookup timed out")
            outcomes.append({"step": i, "tool": tool,
                             "outcome": "skipped",
                             "detail": {"skipped": reason}})
            failures += 1
            continue
        try:
            res = _AGENT.registry.call(tool, step.get("args") or {},
                                       actor="autopilot", confirmed=True)
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        ok = bool(res.get("ok")) and not res.get("needs_confirmation")
        outcomes.append({"step": i, "tool": tool,
                         "outcome": "ok" if ok else "failed",
                         "detail": res})
        if not ok:
            failures += 1
    status = "done" if failures == 0 else "partial"
    now = int(time.time())

    def _upd(conn):
        conn.execute(
            "UPDATE proposals SET status=?, decided_at=?, decision_reason=?,"
            " results=? WHERE id=?",
            (status, now, f"approved by user (kill_note: {kill_note or 'n/a'})",
             json.dumps(outcomes), proposal["id"]))

    _exec(_upd)
    return {"id": proposal["id"], "goal": proposal["goal"],
            "status": status, "steps": outcomes,
            "executed": len(outcomes) - failures, "skipped_or_failed": failures}


@_wrap
def approve_handler(args: dict) -> dict:
    killed, kill_note = _kill_switch()
    if killed:
        return {"error": "autonomy is OFF (kill switch) — re-enable with autonomy.on"}
    raw = args.get("id")
    if raw == "all":
        pending = _list_proposals(status="pending")
        if not pending:
            return {"ok": True, "approved": [], "note": "no pending proposals"}
        targets = pending
    else:
        pid = raw.strip() if isinstance(raw, str) else ""
        if not pid:
            return {"error": "id is required — a proposal id or \"all\""}
        proposal = _get_proposal(pid)
        if proposal is None:
            return {"error": f"no such proposal {pid!r}"}
        if proposal["status"] == "rejected":
            return {"error": ("proposal %r was rejected — rejected proposals can "
                              "never be approved") % pid}
        if proposal["status"] in ("done", "partial"):
            return {"error": f"proposal {pid!r} was already executed ({proposal['status']})"}
        targets = [proposal]
    if _AGENT is None:
        return {"error": "autopilot not wired to a live agent session"}
    ran = [_execute_proposal(p, kill_note) for p in targets]
    return {"ok": True, "approved": ran}


@_wrap
def reject_handler(args: dict) -> dict:
    raw = args.get("id")
    pid = raw.strip() if isinstance(raw, str) else ""
    if not pid:
        return {"error": "id is required — the proposal id to reject"}
    reason = (args.get("reason") or "").strip() if isinstance(args.get("reason"), str) else ""
    if not reason:
        return {"error": "reason is required — say why the plan was rejected"}
    proposal = _get_proposal(pid)
    if proposal is None:
        return {"error": f"no such proposal {pid!r}"}
    if proposal["status"] != "pending":
        return {"error": f"proposal {pid!r} is already {proposal['status']} — cannot reject"}
    now = int(time.time())

    def _upd(conn):
        conn.execute(
            "UPDATE proposals SET status='rejected', decided_at=?,"
            " decision_reason=? WHERE id=?",
            (now, reason, pid))

    _exec(_upd)
    return {"ok": True, "id": pid, "status": "rejected", "reason": reason}


@_wrap
def status_handler(args: dict) -> dict:
    def _counts(conn):
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM proposals GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    counts = _exec(_counts)
    recent = _list_proposals(limit=10)
    summary = []
    for p in recent:
        item = {"id": p["id"], "goal": p["goal"], "status": p["status"],
                "created_at": p["created_at"],
                "decided_at": p["decided_at"],
                "decision_reason": p["decision_reason"],
                "step_count": len(p["steps"])}
        if p["results"] is not None:
            item["step_outcomes"] = [
                {"step": o["step"], "tool": o["tool"], "outcome": o["outcome"]}
                for o in p["results"]]
        summary.append(item)
    return {"counts": counts, "recent": summary}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "autopilot.propose",
     "description": ("Propose a multi-step plan for batch approval: {goal, steps[{tool, "
                     "args, why}]}. Validates the steps and stores the proposal as "
                     "pending — it NEVER executes anything. Returns a proposal id. "
                     "Approve later with autopilot.approve."),
     "handler": propose_handler, "risk": "low", "needs_network": False,
     "schema": {"goal": "string", "steps": "array"}},
    {"name": "autopilot.queue",
     "description": "List pending proposals with their step summaries (tool + why).",
     "handler": queue_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "autopilot.approve",
     "description": ("Approve and EXECUTE a proposal's steps in order through the real "
                     "registry (batch approval is the user's confirmation). id is a "
                     "proposal id or \"all\". Steps with a deny permission grant are "
                     "skipped, never executed. Checks the autonomy kill switch first. "
                     "HIGH risk — needs confirmation."),
     "handler": approve_handler, "risk": "high", "needs_network": False,
     "schema": {"id": "string"}},
    {"name": "autopilot.reject",
     "description": ("Reject a proposal with a reason — rejection stops the plan. A "
                     "rejected proposal can never be approved afterwards."),
     "handler": reject_handler, "risk": "medium", "needs_network": False,
     "schema": {"id": "string", "reason": "string"}},
    {"name": "autopilot.status",
     "description": ("Counts of proposals by status, plus recent proposals with "
                     "per-step outcomes."),
     "handler": status_handler, "risk": "low", "needs_network": False,
     "schema": {}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(autopilot_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "autopilot.propose": ("low", False),
    "autopilot.queue": ("low", False),
    "autopilot.approve": ("high", False),
    "autopilot.reject": ("medium", False),
    "autopilot.status": ("low", False),
}


def register(reg) -> None:
    """Wire the five Autopilot tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
