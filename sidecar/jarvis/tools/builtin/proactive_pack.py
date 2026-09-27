"""Proactive suggestions + standing rules pack (Phase 11, workstream 3).

Background workers OBSERVE and SUGGEST — they never execute.

Core invariant (enforced in code, not just docs):
    NOTHING in this pack executes a medium-or-higher-risk action.
    Rules may only CREATE autopilot proposals or run LOW-risk reads.
    - rules.add validates every ``then`` action at add time: a
      ``{"action": "tool", ...}`` step is rejected unless the tool exists in
      the policy RISK_TABLE (read-only) and its risk is exactly "low".
    - proactive.scan re-validates risk right before executing any read.
    - "propose" steps call autopilot_pack's propose entrypoint directly —
      proposing queues work, it never executes it.

Integration:
    bind_agent(agent) once (same pattern as workers_pack), then let a
    worker's goal be "call proactive.scan" on a schedule. The worker loop
    itself lives in workers_pack; proactive.scan IS the scheduled tick.

Kill switch:
    proactive.scan checks ``autonomy_pack.is_killed()`` (ImportError ->
    proceed). If killed it returns early with {"skipped": "autonomy off"}.

DB: ~/workspace/jarvis/data/proactive.db, overridable with
JARVIS_PROACTIVE_DB (tests use a tmp file).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "proactive.db"

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/workers_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# Honest trigger catalog.
#
# A trigger maps to exactly one low-risk READ tool. Triggers whose natural
# backing tool is medium-or-higher risk are DELIBERATELY excluded — e.g.
# ``price_watch`` would map to price.check, which is medium risk in
# agent/policy.py, so it is not offered here. Rules may only run low-risk
# reads or create proposals; anything else is rejected at add time.
# ---------------------------------------------------------------------------
TRIGGER_CATALOG = {
    "inbox_triage": {
        "tool": "inbox.triage",
        "args": {"limit": 30},
        "requires_params": (),
        "desc": ("Pull unread from every configured channel, ranked by urgency "
                 "(runs inbox.triage). Fires when any message is waiting."),
    },
    "calendar_today": {
        "tool": "calendar.read",
        "args": {"days": 1},
        "requires_params": (),
        "desc": ("Read today's local calendar events (runs calendar.read). "
                 "Fires when today has any events."),
    },
    "worker_results": {
        "tool": "workers.logs",
        "args": {"n": 10},
        "requires_params": ("name",),
        "desc": ("Read recent logs of one background worker (runs workers.logs). "
                 "Needs when.params.name. Fires when a log entry is an error."),
    },
}


def _risk_view() -> dict:
    """Merged read-only view of the policy RISK_TABLE plus sibling packs'
    RISK_TABLE_ADDITIONS (inbox_pack registers inbox.triage, workers_pack
    registers workers.logs). Never mutates policy.py."""
    from ...agent.policy import RISK_TABLE

    view = dict(RISK_TABLE)
    for mod in ("inbox_pack", "workers_pack"):
        try:
            pack = __import__(f"jarvis.tools.builtin.{mod}",
                              fromlist=["RISK_TABLE_ADDITIONS"])
            view.update(getattr(pack, "RISK_TABLE_ADDITIONS", {}) or {})
        except Exception:
            pass
    return view


def _tool_risk(tool: str) -> str | None:
    """Risk level for a tool name, or None when unknown. Read-only."""
    entry = _risk_view().get(tool)
    return entry[0] if entry else None


# ---------------------------------------------------------------------------
# Autopilot proposal materialization (proposing never executes).
#
# Calls autopilot_pack.propose_handler({"goal", "steps"}) DIRECTLY — the
# pack-handler convention (dict -> dict), exactly as the task requires.
# Proposing only stores a pending plan; it never executes anything.
# Plain-language steps are converted to autopilot's {tool, args, why} step
# shape; each is a "manual" instruction that runs only if the user approves
# the proposal. The goal is labeled "[proactive] ..." so the queue shows
# where the suggestion came from.
# ---------------------------------------------------------------------------
def _to_autopilot_steps(goal: str, steps: list) -> list:
    """Plain-language step strings -> autopilot {tool, args, why} steps."""
    norm = []
    for s in steps or []:
        s = str(s).strip()
        if not s:
            continue
        norm.append({"tool": "manual", "args": {"instruction": s},
                     "why": ("proactive suggestion step — carried out only "
                             "if you approve this proposal")})
    if not norm:
        norm.append({"tool": "manual", "args": {"instruction": goal},
                     "why": ("proactive suggestion — carried out only "
                             "if you approve this proposal")})
    return norm


def _propose(goal: str, steps: list, source: str = "proactive") -> dict:
    """Queue one proposal via autopilot_pack. Never executes anything."""
    try:
        from . import autopilot_pack  # noqa: PLC0415
    except ImportError as e:
        return {"ok": False,
                "error": f"autopilot queue unavailable (autopilot_pack not installed): {e}"}
    propose_handler = getattr(autopilot_pack, "propose_handler", None)
    if propose_handler is None:
        return {"ok": False,
                "error": "autopilot_pack has no propose_handler — cannot queue proposal"}
    payload = {"goal": f"[{source}] {goal}",
               "steps": _to_autopilot_steps(goal, steps)}
    try:
        out = propose_handler(payload)  # dict -> dict, proposing only stores
    except Exception as e:  # never let a missing autopilot crash the loop
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(out, dict):
        return out
    return {"ok": True, "result": out}


def _autonomy_killed() -> bool:
    """True when the Phase-11 kill switch is engaged. Missing autonomy_pack
    (sibling workstream not installed yet) -> not killed, proceed."""
    try:
        from . import autonomy_pack  # noqa: PLC0415
    except ImportError:
        return False
    try:
        return bool(autonomy_pack.is_killed())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SQLite state machine (single connection, check_same_thread=False, one lock)
# ---------------------------------------------------------------------------
_DB_LOCK = threading.Lock()
_store_cache: dict = {"path": None, "conn": None}


def _db_path() -> Path:
    import os

    override = os.environ.get("JARVIS_PROACTIVE_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _get_store() -> sqlite3.Connection:
    with _DB_LOCK:
        path = _db_path()
        if _store_cache["conn"] is None or _store_cache["path"] != str(path):
            if _store_cache["conn"] is not None:
                _store_cache["conn"].close()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("""CREATE TABLE IF NOT EXISTS rules(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                trigger TEXT NOT NULL,
                trigger_params TEXT NOT NULL DEFAULT '{}',
                then_json TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                last_run INTEGER,
                run_count INTEGER NOT NULL DEFAULT 0)""")
            conn.commit()
            _store_cache.update(path=str(path), conn=conn)
        return _store_cache["conn"]


def _exec(fn, *args, **kwargs):
    conn = _get_store()
    with _DB_LOCK:
        out = fn(conn, *args, **kwargs)
        conn.commit()
        return out


# ---------------------------------------------------------------------------
# Rule validation (the anti-escalation enforcement)
# ---------------------------------------------------------------------------
def _validate_when(when: dict) -> tuple[dict, str | None]:
    """Return (normalized, None) or (None, error)."""
    if not isinstance(when, dict):
        return None, "when must be an object like {trigger: 'inbox_triage', params?: {...}}"
    trigger = when.get("trigger")
    if trigger not in TRIGGER_CATALOG:
        catalog = ", ".join(sorted(TRIGGER_CATALOG))
        return None, (f"unknown trigger {trigger!r} — honest catalog: {catalog}")
    params = when.get("params") or {}
    if not isinstance(params, dict):
        return None, "when.params must be an object"
    spec = TRIGGER_CATALOG[trigger]
    for req in spec["requires_params"]:
        if not params.get(req):
            return None, f"trigger {trigger!r} needs when.params.{req}"
    # The catalog's own backing tool must be low risk — enforced at add time
    # so a policy change can never silently widen a rule's power.
    risk = _tool_risk(spec["tool"])
    if risk != "low":
        return None, (f"trigger {trigger!r} maps to '{spec['tool']}' "
                      f"(risk={risk}): rules may only run low-risk reads or create proposals")
    return {"trigger": trigger, "params": dict(params)}, None


def _validate_then(then: list) -> tuple[list, str | None]:
    """Return (normalized, None) or (None, error)."""
    if not isinstance(then, list) or not then:
        return None, "then must be a non-empty list of actions"
    out = []
    for i, act in enumerate(then):
        if not isinstance(act, dict):
            return None, f"then[{i}] must be an object"
        kind = act.get("action")
        if kind == "propose":
            goal = (act.get("goal") or "").strip()
            if not goal:
                return None, f"then[{i}]: propose needs a non-empty goal"
            steps = act.get("steps") or []
            if not isinstance(steps, list) or any(not isinstance(s, str) for s in steps):
                return None, f"then[{i}]: steps must be a list of strings"
            out.append({"action": "propose", "goal": goal, "steps": list(steps)})
        elif kind == "tool":
            tool = act.get("tool")
            args = act.get("args") or {}
            if not isinstance(tool, str) or not tool:
                return None, f"then[{i}]: tool needs a tool name"
            if not isinstance(args, dict):
                return None, f"then[{i}]: args must be an object"
            risk = _tool_risk(tool)
            if risk is None:
                return None, (f"rules cannot execute {tool!r} (risk=unknown): "
                              "rules may only run low-risk reads or create proposals")
            if risk != "low":
                return None, (f"rules cannot execute {tool!r} (risk={risk}): "
                              "rules may only run low-risk reads or create proposals")
            out.append({"action": "tool", "tool": tool, "args": dict(args)})
        else:
            return None, (f"then[{i}]: unknown action {kind!r} — "
                          "use 'propose' or 'tool'")
    return out, None


# ---------------------------------------------------------------------------
# Trigger reads via the bound agent's registry (actor="proactive")
# ---------------------------------------------------------------------------
def _regcall(tool: str, args: dict) -> dict:
    """Call through the bound agent's registry. Returns
    {"ok": bool, "data": ..., "error": ...}. Tolerates both the real
    Registry.call envelope ({"ok","result"}) and raw handler-style dicts."""
    if _AGENT is None or getattr(_AGENT, "registry", None) is None:
        return {"ok": False, "error": "agent not bound — bind via proactive_pack.bind_agent(agent)"}
    try:
        res = _AGENT.registry.call(tool, args or {}, actor="proactive")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(res, dict) and "ok" in res and ("result" in res or "error" in res):
        return {"ok": bool(res["ok"]), "data": res.get("result"),
                "error": res.get("error")}
    if isinstance(res, dict) and "error" in res:
        return {"ok": False, "error": str(res["error"])}
    return {"ok": True, "data": res}


def _trigger_fire(trigger: str, data: dict) -> tuple[bool, str]:
    """Decide whether a trigger's read result is actionable, and summarize it."""
    data = data or {}
    if trigger == "inbox_triage":
        msgs = data.get("messages") or []
        if not msgs:
            return False, "inbox quiet"
        top = msgs[0]
        subj = top.get("subject_or_first_line") or top.get("snippet") or "?"
        return True, f"{len(msgs)} message(s) waiting; top urgency: {str(subj)[:80]}"
    if trigger == "calendar_today":
        events = data.get("events") or []
        if not events:
            return False, "no events today"
        titles = ", ".join(str(e.get("title", "?"))[:40] for e in events[:3])
        return True, f"{len(events)} event(s) today: {titles}"
    if trigger == "worker_results":
        logs = data.get("logs") or data.get("entries") or []
        errs = [l for l in logs
                if str(l.get("kind", "")).lower() == "error"]
        if not errs:
            return False, "no worker errors"
        return True, f"{len(errs)} worker error(s); latest: {str(errs[0].get('text', ''))[:120]}"
    return False, "unknown trigger"


# ---------------------------------------------------------------------------
# Handlers (dict -> dict, never raise)
# ---------------------------------------------------------------------------
def _wrap(fn):
    def _h(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # never let a proactive tool crash the loop
            return {"error": f"{type(e).__name__}: {e}"}

    return _h


@_wrap
def add_handler(args: dict) -> dict:
    name = (args.get("name") or "").strip()[:80]
    when, err = _validate_when(args.get("when"))
    if err:
        return {"error": err}
    then, err = _validate_then(args.get("then"))
    if err:
        return {"error": err}

    def _ins(conn):
        cur = conn.execute(
            "INSERT INTO rules(name, enabled, trigger, trigger_params, then_json, created_at)"
            " VALUES(?, 1, ?, ?, ?, ?)",
            (name, when["trigger"], json.dumps(when["params"]),
             json.dumps(then), int(time.time())))
        return cur.lastrowid

    rid = _exec(_ins)
    return {"ok": True, "id": rid, "name": name,
            "when": when, "then": then, "enabled": True}


@_wrap
def list_handler(args: dict) -> dict:
    def _all(conn):
        rows = conn.execute(
            "SELECT id,name,enabled,trigger,trigger_params,then_json,"
            " created_at,last_run,run_count FROM rules ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d["enabled"])
            try:
                d["when"] = {"trigger": d.pop("trigger"),
                             "params": json.loads(d.pop("trigger_params") or "{}")}
            except Exception:
                d["when"] = {"trigger": d.pop("trigger"), "params": {}}
            try:
                d["then"] = json.loads(d.pop("then_json") or "[]")
            except Exception:
                d["then"] = []
            out.append(d)
        return out

    return {"rules": _exec(_all),
            "trigger_catalog": {k: v["desc"] for k, v in TRIGGER_CATALOG.items()}}


@_wrap
def remove_handler(args: dict) -> dict:
    try:
        rid = int(args.get("id"))
    except (TypeError, ValueError):
        return {"error": "id must be an integer rule id (see rules.list)"}

    def _del(conn):
        r = conn.execute("SELECT 1 FROM rules WHERE id=?", (rid,)).fetchone()
        if r is None:
            return False
        conn.execute("DELETE FROM rules WHERE id=?", (rid,))
        return True

    if not _exec(_del):
        return {"error": f"no such rule id {rid}"}
    return {"ok": True, "id": rid, "removed": True}


def _materialize(rule_id: int, action: dict) -> dict:
    """Run one validated ``then`` action. Returns {"proposed": bool, ...}.
    The ONLY side effects allowed here: queueing an autopilot proposal, or
    executing a low-risk read (re-validated at scan time)."""
    if action["action"] == "propose":
        out = _propose(action["goal"], action.get("steps", []), source="proactive")
        return {"proposed": bool(out.get("ok")), "proposal": out}
    # action == "tool": belt-and-braces re-validation — the policy table may
    # have changed since the rule was added.
    risk = _tool_risk(action["tool"])
    if risk != "low":
        return {"proposed": False,
                "error": (f"rules cannot execute {action['tool']!r} (risk={risk}): "
                          "rules may only run low-risk reads or create proposals")}
    res = _regcall(action["tool"], action.get("args") or {})
    return {"proposed": False, "read": res}


@_wrap
def scan_handler(args: dict) -> dict:
    # Kill switch first: a killed autonomy means no proactive work at all.
    if _autonomy_killed():
        return {"skipped": "autonomy off", "rules_checked": 0, "proposals_created": 0}
    if _AGENT is None:
        return {"error": "agent not bound — proactive.scan needs the live Agent "
                         "(bind via proactive_pack.bind_agent)"}

    def _enabled(conn):
        rows = conn.execute(
            "SELECT id,trigger,trigger_params,then_json FROM rules"
            " WHERE enabled=1 ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    rules = _exec(_enabled)
    checked = 0
    created = 0
    fired: list[int] = []
    problems: list[str] = []

    for rule in rules:
        rid = rule["id"]
        spec = TRIGGER_CATALOG.get(rule["trigger"])
        if spec is None:  # rule predates a catalog change — stay honest
            problems.append(f"rule {rid}: trigger {rule['trigger']!r} no longer in catalog")
            continue
        try:
            params = json.loads(rule["trigger_params"] or "{}")
            then = json.loads(rule["then_json"] or "[]")
        except Exception as e:
            problems.append(f"rule {rid}: corrupt stored rule ({e})")
            continue
        call_args = dict(spec["args"])
        call_args.update(params or {})
        res = _regcall(spec["tool"], call_args)
        checked += 1

        def _touch(conn, rid=rid):
            conn.execute("UPDATE rules SET last_run=?, run_count=run_count+1 WHERE id=?",
                         (int(time.time()), rid))

        _exec(_touch)
        if not res["ok"]:
            problems.append(f"rule {rid}: trigger read failed: {res['error']}")
            continue
        fire, summary = _trigger_fire(rule["trigger"], res.get("data") or {})
        if not fire:
            continue
        fired.append(rid)
        for action in then:
            m = _materialize(rid, action)
            if m.get("proposed"):
                created += 1
            elif m.get("error"):
                problems.append(f"rule {rid}: {m['error']}")
    out = {"rules_checked": checked, "proposals_created": created, "fired": fired}
    if problems:
        out["problems"] = problems
    return out


@_wrap
def suggest_handler(args: dict) -> dict:
    trigger = (args.get("trigger") or "").strip()
    proposal = args.get("proposal") or {}
    if not trigger:
        return {"error": "trigger is required — describe what was observed, in plain language"}
    if not isinstance(proposal, dict):
        return {"error": "proposal must be {goal, steps}"}
    goal = (proposal.get("goal") or "").strip()
    steps = proposal.get("steps") or []
    if not goal:
        return {"error": "proposal.goal is required"}
    if not isinstance(steps, list) or any(not isinstance(s, str) for s in steps):
        return {"error": "proposal.steps must be a list of strings"}
    out = _propose(goal, steps, source="proactive")
    if not out.get("ok"):
        return {"error": f"suggestion noted but not queued: {out.get('error')}"}
    return {"ok": True, "trigger": trigger, "proposal": out,
            "note": "queued as an autopilot proposal — nothing was executed"}


@_wrap
def briefing_handler(args: dict) -> dict:
    """Morning digest: inbox triage + today's calendar + worker logs.
    Each actionable item becomes a queued autopilot proposal — never executed."""
    if _AGENT is None:
        return {"error": "agent not bound — proactive.briefing needs the live Agent "
                         "(bind via proactive_pack.bind_agent)"}

    sections: list[str] = []
    proposals = 0
    failures: list[str] = []

    def _section(title: str, body: str) -> None:
        sections.append(f"## {title}\n{body}")

    # --- inbox ---
    res = _regcall("inbox.triage", {"limit": 20})
    if res["ok"]:
        msgs = (res.get("data") or {}).get("messages") or []
        if msgs:
            lines = []
            for m in msgs[:8]:
                subj = m.get("subject_or_first_line") or m.get("snippet") or "?"
                ch = m.get("channel", "?")
                lines.append(f"- [{ch}] {str(subj)[:90]} (urgency {m.get('urgency', '?')})")
            _section(f"Inbox — {len(msgs)} waiting", "\n".join(lines))
            for m in msgs[:5]:
                subj = m.get("subject_or_first_line") or m.get("snippet") or "message"
                p = _propose(f"Triage inbox item: {str(subj)[:80]}",
                             ["read the message via inbox.read",
                              "draft a reply and queue it for approval"],
                             source="proactive")
                if p.get("ok"):
                    proposals += 1
        else:
            _section("Inbox", "Quiet — nothing unread.")
    else:
        failures.append(f"inbox: {res['error']}")
        _section("Inbox", f"_unavailable: {res['error']}_")

    # --- calendar ---
    res = _regcall("calendar.read", {"days": 1})
    if res["ok"]:
        events = (res.get("data") or {}).get("events") or []
        today = date.today().isoformat()
        if events:
            lines = [f"- {str(e.get('start_iso', ''))[:16]} — {e.get('title', '?')}"
                     for e in events[:8]]
            _section(f"Calendar — today ({today})", "\n".join(lines))
            p = _propose(f"Prepare for today's {len(events)} calendar event(s)",
                         [f"review: {str(e.get('title', '?'))[:60]}" for e in events[:8]],
                         source="proactive")
            if p.get("ok"):
                proposals += 1
        else:
            _section(f"Calendar — today ({today})", "No events scheduled.")
    else:
        failures.append(f"calendar: {res['error']}")
        _section("Calendar", f"_unavailable: {res['error']}_")

    # --- workers ---
    res = _regcall("workers.list", {})
    if res["ok"]:
        workers = [w for w in (res.get("data") or {}).get("workers", [])
                   if w.get("status") == "active"][:5]
        if not workers:
            _section("Workers", "No active background workers.")
        else:
            wlines = []
            for w in workers:
                lres = _regcall("workers.logs", {"name": w["name"], "n": 5})
                if not lres["ok"]:
                    wlines.append(f"- {w['name']}: _logs unavailable: {lres['error']}_")
                    continue
                logs = (lres.get("data") or {}).get("logs") or []
                errs = [l for l in logs if str(l.get("kind", "")).lower() == "error"]
                status = f"{len(errs)} error(s) in last {len(logs)} log entries" if errs else "healthy"
                wlines.append(f"- {w['name']}: {status}")
                for e in errs[:2]:
                    p = _propose(f"Worker '{w['name']}' logged an error",
                                 [f"read workers.logs for {w['name']}",
                                  f"investigate: {str(e.get('text', ''))[:120]}"],
                                 source="proactive")
                    if p.get("ok"):
                        proposals += 1
            _section("Workers", "\n".join(wlines))
    else:
        failures.append(f"workers: {res['error']}")
        _section("Workers", f"_unavailable: {res['error']}_")

    if len(failures) == 3:
        return {"error": "briefing failed — all sources unavailable: " + "; ".join(failures)}

    digest = (f"# Morning briefing — {date.today().isoformat()}\n\n"
              + "\n\n".join(sections)
              + f"\n\n_Queued {proposals} proposal(s) for your approval — nothing was executed._")
    return {"digest": digest, "proposals_created": proposals}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "rules.add",
     "description": ("Define a standing if-this-then-that rule in plain language, e.g. "
                     "{when: {trigger: 'inbox_triage'}, then: [{action: 'propose', goal: '...', steps: [...]}]}. "
                     "Triggers come from an honest catalog (rules.list shows it). "
                     "'then' actions may ONLY create autopilot proposals or run low-risk reads — "
                     "a 'tool' step naming a medium/high-risk or unknown tool is REJECTED. "
                     "Nothing here executes on its own; proactive.scan evaluates rules. "
                     "Medium risk: creates persistent autonomous behavior."),
     "handler": add_handler, "risk": "medium", "needs_network": False,
     "schema": {"when": "dict", "then": "list", "name?": "string"}},
    {"name": "rules.list",
     "description": ("List all standing rules with their triggers and actions, "
                     "plus the honest trigger catalog."),
     "handler": list_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "rules.remove",
     "description": "Delete a standing rule by id (see rules.list).",
     "handler": remove_handler, "risk": "medium", "needs_network": False,
     "schema": {"id": "int"}},
    {"name": "proactive.scan",
     "description": ("Evaluate every enabled rule: run each trigger's low-risk read "
                     "(actor='proactive'), and when a trigger fires, queue the rule's "
                     "'then' actions as autopilot proposals or run its validated "
                     "low-risk reads. Designed to be a worker's scheduled goal "
                     "('call proactive.scan'). OBSERVES and SUGGESTS only — never "
                     "executes medium-or-higher-risk actions. Respects the autonomy "
                     "kill switch (returns {'skipped': 'autonomy off'} when killed)."),
     "handler": scan_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "proactive.suggest",
     "description": ("Queue one proactive suggestion: {trigger: 'what was observed', "
                     "proposal: {goal, steps}}. Creates an autopilot proposal labeled "
                     "source='proactive'. Never executes anything."),
     "handler": suggest_handler, "risk": "low", "needs_network": False,
     "schema": {"trigger": "string", "proposal": "dict"}},
    {"name": "proactive.briefing",
     "description": ("Morning digest: reads inbox.triage, calendar.read (today) and "
                     "workers.logs via the bound agent (actor='proactive', all low-risk "
                     "reads), composes a digest, and queues each actionable item as an "
                     "autopilot proposal. Nothing is executed. Reports honestly which "
                     "source is unavailable."),
     "handler": briefing_handler, "risk": "low", "needs_network": False,
     "schema": {}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(proactive_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "rules.add": ("medium", False),
    "rules.list": ("low", False),
    "rules.remove": ("medium", False),
    "proactive.scan": ("low", False),
    "proactive.suggest": ("low", False),
    "proactive.briefing": ("low", False),
}


def register(reg) -> None:
    """Wire the six proactive-suggestion tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
