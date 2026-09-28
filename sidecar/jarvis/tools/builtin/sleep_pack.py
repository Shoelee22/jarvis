"""Sleep / Dream cycle pack (Phase 13, workstream A): the nightly run.

While the user sleeps, JARVIS does the quiet work — then queues everything
for the morning batch approval this user loves:

    sleep.cycle   low   The nightly run (~02:00 local, called from the
                        workers tick). (1) memory consolidation is PROPOSED,
                        never executed blind; (2) reads the day's audit log
                        and writes a short day summary; (3) scans tomorrow's
                        calendar + peeks at the inbox, composes tomorrow's
                        morning briefing DRAFT and queues it via
                        autopilot.propose; (4) calls memory.routines and
                        queues any routine proposals. The night's summary is
                        persisted in sqlite (sleep_nights). Idempotent: one
                        row per night unless force=true.
    sleep.review  low   Read back a past night's summary from sqlite.
                        Honest error when no summary exists for the date.
    sleep.dream   low   Analyze the week's audit log for failure patterns
                        (same tool failing 3+ times with the same signature)
                        and thrash patterns (same request 3+ times). For each
                        pattern, draft a candidate tool spec, validate it
                        against the forge's safe-primitive matcher, and queue
                        it as an autopilot proposal ("I noticed X failed you
                        3 times this week — I drafted a helper tool. Approve
                        to forge it?"). Says so honestly when there is
                        nothing to dream about.

HARD RULE, ENFORCED IN CODE: sleep NEVER executes a medium- or high-risk
tool while the user sleeps. Every registry call from this pack goes through
_regcall(), which looks up the tool's risk and REFUSES anything that is not
"low" (unknown risk fails closed too). Medium-risk work the night needs —
memory.consolidate, tools.forge, rules.add, workers.spawn — is queued as an
autopilot proposal for the user's morning batch approval instead. _regcall
also never passes confirmed=True, so even a low-risk tool that somehow
needed confirmation could not slip through.

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    1. Register + risk table (same as every pack)::

           from jarvis.tools.builtin import sleep_pack
           sleep_pack.register(registry)
           from jarvis.agent.policy import RISK_TABLE
           RISK_TABLE.update(sleep_pack.RISK_TABLE_ADDITIONS)

    2. Bind the agent (sleep.cycle calls collaborators through the live
       registry)::

           sleep_pack.bind_agent(agent)

    3. Nightly trigger — in sidecar/jarvis/ipc/server.py, inside the
       existing _workers_tick_loop (which already calls workers.tick every
       60s), add right after the workers.tick call::

           from datetime import datetime as _dt
           try:
               if _dt.now().hour == 2:
                   registry.call("sleep.cycle", {}, actor="scheduler")
           except Exception:
               pass

       sleep.cycle is idempotent per night (one sleep_nights row per date),
       so the ~60 invocations during the 2am hour collapse into a single
       run; the rest return {"already_ran": True}.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/sleep.db,
      overridable with the JARVIS_SLEEP_DB env var (tests use a tmp file).
    - The audit log is read from ~/workspace/jarvis/data/jarvis.db
      (JARVIS_SLEEP_AUDIT_DB override), the same default mind_pack uses.
      A missing/unreadable audit table is reported honestly, never faked.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from ...config import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "sleep.db"
DEFAULT_AUDIT_PATH = DATA_DIR / "jarvis.db"

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/workers_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# SQLite state (one table: sleep_nights)
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_SLEEP_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _audit_db_path() -> Path:
    override = os.environ.get("JARVIS_SLEEP_AUDIT_DB")
    return Path(override) if override else DEFAULT_AUDIT_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sleep_nights("
        " date TEXT PRIMARY KEY,"
        " summary_json TEXT NOT NULL,"
        " created_at INTEGER NOT NULL)"
    )
    conn.commit()
    return conn


def _has_night(date_key: str) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM sleep_nights WHERE date=?", (date_key,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def _save_night(date_key: str, summary: dict) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sleep_nights(date, summary_json, created_at)"
                " VALUES(?,?,?)",
                (date_key, json.dumps(summary), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()


def _load_night(date_key: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT summary_json, created_at FROM sleep_nights WHERE date=?",
                (date_key,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Risk-gated registry calls — THE enforcement point.
# ---------------------------------------------------------------------------
def _tool_risk(reg, name: str) -> str:
    """Risk label for a tool name: low/medium/high/unknown (fail closed)."""
    try:
        tool = getattr(reg, "tools", {}).get(name)
    except Exception:
        tool = None
    if tool is not None:
        return getattr(tool, "risk", None) or "unknown"
    try:
        from ...agent.policy import RISK_TABLE  # noqa: PLC0415 (lazy: no cycles)
        entry = RISK_TABLE.get(name)
        if entry:
            return entry[0]
    except Exception:
        pass
    return "unknown"


def _regcall(name: str, args: dict | None, actor: str = "sleep") -> dict:
    """Call a tool through the bound agent's registry — low-risk only.

    Anything that is not verifiably "low" risk (medium, high, or unknown)
    is REFUSED without being invoked, and the caller is expected to queue
    it as an autopilot proposal instead. confirmed=True is never passed.
    """
    if _AGENT is None:
        return {"skipped": True, "tool": name,
                "reason": "sleep pack is not bound to a live agent "
                          "(bind_agent was not called)"}
    reg = getattr(_AGENT, "registry", None)
    if reg is None:
        return {"skipped": True, "tool": name,
                "reason": "bound agent exposes no .registry"}
    risk = _tool_risk(reg, name)
    if risk != "low":
        return {"skipped": True, "tool": name, "risk": risk,
                "reason": ("sleep never executes medium+ risk (or unknown-risk) "
                           "tools while the user sleeps — queue it as an "
                           "autopilot proposal instead")}
    try:
        # NOTE: confirmed=False always — a low-risk tool never needs it, and
        # passing True here would be the one way to smuggle execution past
        # the policy engine.
        return reg.call(name, args or {}, actor=actor)
    except Exception as exc:  # never let a collaborator crash the night
        return {"ok": False, "error": f"registry.call raised: {exc}"}


def _unwrap(res: dict | None) -> dict | None:
    """Unwrap a Registry.call result envelope -> handler output (or None)."""
    if isinstance(res, dict) and res.get("ok"):
        inner = res.get("result")
        return inner if isinstance(inner, dict) else None
    return None


def _propose(goal: str, steps: list[dict]) -> dict:
    """Queue an autopilot proposal; never executes anything."""
    res = _regcall("autopilot.propose", {"goal": goal, "steps": steps},
                   actor="sleep")
    if isinstance(res, dict) and res.get("skipped"):
        return {"queued": False, "reason": res.get("reason", "skipped")}
    inner = _unwrap(res)
    if inner and inner.get("ok"):
        return {"queued": True, "id": inner.get("id")}
    err = (res.get("error") if isinstance(res, dict) else None) or "unknown"
    return {"queued": False, "reason": f"autopilot.propose failed: {err}"}


# ---------------------------------------------------------------------------
# Audit log reading (defensive: missing table -> honest "unavailable")
# ---------------------------------------------------------------------------
def _read_audit(since_ts: int, limit: int = 5000) -> list[dict] | None:
    """Audit rows with ts >= since_ts, oldest first. None => unavailable."""
    try:
        conn = sqlite3.connect(str(_audit_db_path()))
    except Exception:
        return None
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT ts, actor, tool, args_json, result_summary, risk"
                " FROM audit_log WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
                (since_ts, limit),
            ).fetchall()
        except sqlite3.Error:
            return None  # table missing / schema mismatch: honest, not fatal
        return [dict(r) for r in rows]
    finally:
        conn.close()


_ERROR_RE = re.compile(r"(?i)^[\w.]*error\s*:")
_GATED_MARKER = "awaiting confirmation"


def _is_failure(summary: str) -> bool:
    s = (summary or "")
    low = s.lower()
    return bool(_ERROR_RE.match(s)) or "traceback" in low \
        or low.startswith("denied") or "policy deny" in low


# ---------------------------------------------------------------------------
# Night window: 00:00-06:00 local -> summarize the full previous calendar
# day; a manual run at any other hour summarizes the trailing 24h.
# ---------------------------------------------------------------------------
def _night_window(now: float | None = None) -> tuple[str, int, int]:
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now)
    if 0 <= local.hour < 6:
        start = local.replace(hour=0, minute=0, second=0, microsecond=0) \
            - timedelta(days=1)
        end = start + timedelta(days=1)
        date_key = start.date().isoformat()
    else:
        end = local
        start = end - timedelta(days=1)
        date_key = local.date().isoformat()
    return date_key, int(start.timestamp()), int(end.timestamp())


# ---------------------------------------------------------------------------
# Day summary
# ---------------------------------------------------------------------------
def _build_day_summary(rows: list[dict], date_key: str,
                       w0: int, w1: int) -> dict:
    by_actor: Counter = Counter()
    by_risk: Counter = Counter()
    tool_counts: Counter = Counter()
    failures: Counter = Counter()   # (tool, signature) -> n
    fail_samples: dict = {}
    gated = 0
    for r in rows:
        tool = str(r.get("tool") or "?")
        by_actor[str(r.get("actor") or "?")] += 1
        by_risk[str(r.get("risk") or "?")] += 1
        tool_counts[tool] += 1
        summary = str(r.get("result_summary") or "")
        if _GATED_MARKER in summary:
            gated += 1
        if _is_failure(summary):
            sig = re.sub(r"\d+", "#", summary[:120]).strip().lower()
            key = (tool, sig)
            failures[key] += 1
            fail_samples.setdefault(key, summary[:200])

    top_tools = [{"tool": t, "calls": c}
                 for t, c in tool_counts.most_common(8)]
    # "What the user asked most": the capabilities the agent used on the
    # user's behalf, excluding the sleep machinery itself.
    most_used = [{"tool": t, "calls": c} for t, c in tool_counts.most_common()
                 if not t.startswith("sleep.")][:5]
    fail_list = [{"tool": t, "failures": c, "signature": s,
                  "sample": fail_samples[(t, s)]}
                 for (t, s), c in failures.most_common(8)]

    return {
        "date": date_key,
        "window": {"from_ts": w0, "to_ts": w1,
                   "from_iso": datetime.fromtimestamp(w0).isoformat(),
                   "to_iso": datetime.fromtimestamp(w1).isoformat()},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "audit_available": True,
        "totals": {
            "tool_calls": len(rows),
            "by_actor": dict(by_actor),
            "by_risk": dict(by_risk),
            "failures": sum(failures.values()),
            "confirmation_gates": gated,
        },
        "top_tools": top_tools,
        "most_used_capabilities": most_used,
        "failures": fail_list,
    }


def _empty_summary(date_key: str, w0: int, w1: int, reason: str) -> dict:
    return {
        "date": date_key,
        "window": {"from_ts": w0, "to_ts": w1},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "audit_available": False,
        "reason": reason,
        "totals": {"tool_calls": 0, "by_actor": {}, "by_risk": {},
                   "failures": 0, "confirmation_gates": 0},
        "top_tools": [], "most_used_capabilities": [], "failures": [],
    }


# ---------------------------------------------------------------------------
# Morning briefing draft
# ---------------------------------------------------------------------------
def _compose_briefing(summary: dict, cal_res: dict | None,
                      inbox_res: dict | None, tomorrow_iso: str,
                      review_res: dict | None = None,
                      integrity_res: dict | None = None) -> str:
    t = summary.get("totals", {})
    lines = [f"# Morning briefing — {tomorrow_iso}", "",
             "## Yesterday at a glance"]
    if summary.get("audit_available"):
        lines.append(
            f"- {t.get('tool_calls', 0)} tool calls, "
            f"{t.get('failures', 0)} failed, "
            f"{t.get('confirmation_gates', 0)} needed your confirmation.")
        used = summary.get("most_used_capabilities") or []
        if used:
            lines.append("- You leaned on: " +
                         ", ".join(f"{u['tool']} ({u['calls']}x)" for u in used[:3]))
        fails = summary.get("failures") or []
        if fails:
            lines.append("- Overnight failures worth a look: " +
                         ", ".join(f"{f['tool']} ({f['failures']}x)" for f in fails[:3]))
    else:
        lines.append(f"- (day summary unavailable: {summary.get('reason', 'n/a')})")

    lines += ["", "## Today's calendar"]
    cal = _unwrap(cal_res) or {}
    events = cal.get("events") if isinstance(cal, dict) else None
    if isinstance(cal_res, dict) and cal_res.get("skipped"):
        lines.append("- Calendar not checked overnight "
                     f"({str(cal_res.get('reason', 'skipped'))[:100]})")
    elif cal.get("error"):
        lines.append(f"- Calendar unreadable overnight: {cal['error'][:120]}")
    elif events:
        for e in events[:8]:
            start = str(e.get("start_iso", "?"))[11:16]
            lines.append(f"- {start} — {e.get('title', '(untitled)')}")
        if len(events) > 8:
            lines.append(f"- …and {len(events) - 8} more.")
    else:
        lines.append("- Nothing on the calendar. A clear day, sir.")

    lines += ["", "## Inbox needing attention"]
    ib = _unwrap(inbox_res) or {}
    msgs = ib.get("messages") if isinstance(ib, dict) else None
    if isinstance(inbox_res, dict) and inbox_res.get("skipped"):
        lines.append("- Inbox not peeked at overnight "
                     f"({str(inbox_res.get('reason', 'skipped'))[:100]})")
    elif ib.get("error"):
        lines.append(f"- Inbox peek failed overnight: {ib['error'][:120]}")
    elif msgs:
        for m in msgs[:5]:
            subj = m.get("subject_or_first_line") or "(no subject)"
            frm = m.get("from") or m.get("sender") or "?"
            lines.append(f"- **{subj}** — {frm} "
                         f"(urgency {m.get('urgency', '?')})")
        if len(msgs) > 5:
            lines.append(f"- …and {len(msgs) - 5} more below the fold.")
    else:
        lines.append("- Inbox is quiet. Nothing urgent waiting.")

    lines += ["", "_Draft composed overnight — nothing was executed. "
              "Approving the queued proposals runs their steps in the morning._"]

    # Phase 22: the dreamer's accountability section, folded into the draft.
    rev = _unwrap(review_res) or {}
    if rev and not rev.get("error"):
        lines += ["", rev.get("briefing_md", "")]
    # Phase 30: the integrity loop's verdict line.
    integ = _unwrap(integrity_res) or {}
    if integ and not integ.get("error"):
        lines += ["", f"_State of the brain overnight: "
                      f"**{integ.get('verdict', 'unknown')}**"
                      f"{(' — ' + '; '.join(integ.get('issues', [])[:3])) if integ.get('issues') else ''}_"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def sleep_cycle_handler(args: dict) -> dict:
    """sleep.cycle {} — the nightly run. Idempotent per night.

    Steps: (1) memory consolidation -> queued as a proposal (medium risk,
    never executed blind); (2) day summary from the audit log; (3) morning
    briefing draft from calendar + inbox -> queued via autopilot.propose;
    (4) memory.routines -> each routine proposal queued. Persists the night
    in sqlite.
    """
    try:
        a = args or {}
        force = bool(a.get("force"))
        date_key, w0, w1 = _night_window()
        if not force and _has_night(date_key):
            return {"already_ran": True, "date": date_key,
                    "note": ("this night was already processed — pass "
                             "{'force': true} to re-run it")}
        report: dict = {"date": date_key,
                        "window": {"from_ts": w0, "to_ts": w1},
                        "steps": {}}

        # --- Step 1: memory consolidation. Medium risk -> NEVER executed
        # while the user sleeps; _regcall refuses it and we queue a proposal.
        cons = _regcall("memory.consolidate", {}, actor="sleep")
        if isinstance(cons, dict) and cons.get("skipped"):
            prop = _propose(
                "Nightly memory consolidation (drafted overnight — medium "
                "risk, needs your tap)",
                [{"tool": "memory.consolidate", "args": {},
                  "why": ("Merge duplicate memories, resolve contradictions "
                          "(newer wins), drop stale entries. The sleep cycle "
                          "never runs this blind.")}])
            report["steps"]["consolidate"] = {
                "executed": False,
                "reason": cons.get("reason", "skipped (not low risk)"),
                "queued_proposal": prop}
        else:
            # Only reachable if a deployment labels memory.consolidate low.
            report["steps"]["consolidate"] = {
                "executed": bool(isinstance(cons, dict) and cons.get("ok")),
                "result": cons}

        # --- Step 2: day summary from the audit log.
        rows = _read_audit(w0)
        if rows is None:
            summary = _empty_summary(
                date_key, w0, w1,
                f"audit log unreadable at {_audit_db_path()} "
                "(missing file or audit_log table)")
        else:
            summary = _build_day_summary(rows, date_key, w0, w1)
        report["steps"]["day_summary"] = {
            "tool_calls": summary["totals"]["tool_calls"],
            "failures": summary["totals"]["failures"],
            "audit_available": summary["audit_available"]}

        # --- Episodic memory candidates (Phase 14): propose, don't auto-write.
        try:
            from . import episodic_pack
            _ep_cands = episodic_pack.note_from_audit(rows or [])
        except Exception:
            _ep_cands = []
        _ep_queued = 0
        for _cand in _ep_cands:
            _pr = _propose(
                f"Episodic memory candidate: {_cand.get('title', 'untitled')}",
                [{"tool": "memory.episode", "args": _cand,
                  "why": ("Drafted overnight from the audit log — record as "
                          "an episode with one tap.")}])
            if isinstance(_pr, dict) and _pr.get("queued"):
                _ep_queued += 1
        report["steps"]["episodic_candidates"] = {
            "candidates": len(_ep_cands), "queued": _ep_queued}

        # --- Relationship nudges (Phase 15): propose-only, never auto-send.
        _nudge_queued = 0
        try:
            _nr = _regcall("people.nudges", {}, actor="sleep")
            _nudges = ((_nr or {}).get("result") or {}).get("nudges", [])
            for _n in _nudges:
                _pr2 = _propose(
                    f"Relationship nudge: {_n.get('text', 'check in')}",
                    [{"tool": "people.note",
                      "args": {"name": _n.get("name", ""),
                               "note": "Reached out after JARVIS nudge."},
                      "why": "Log the interaction to reset the contact clock."}])
                if isinstance(_pr2, dict) and _pr2.get("queued"):
                    _nudge_queued += 1
        except Exception:
            _nudges = []
        report["steps"]["relationship_nudges"] = {
            "nudges": len(_nudges), "queued": _nudge_queued}

        # --- Step 3: morning briefing draft -> approval queue.
        if w1 - w0 >= 86300:
            # Full-day window ending at local midnight: the briefing is for
            # the day that just began.
            tomorrow = datetime.fromtimestamp(w1).date().isoformat()
        else:
            # Manual run: the briefing is for tomorrow.
            tomorrow = (datetime.fromtimestamp(w1)
                        + timedelta(days=1)).date().isoformat()
        cal_res = _regcall("calendar.read", {"date": tomorrow, "days": 1},
                           actor="sleep")
        inbox_res = _regcall("inbox.triage", {"limit": 10}, actor="sleep")
        # Phase 22: the accountable dreamer reviews the cycles ledger.
        review_res = _regcall("brain.review", {}, actor="sleep")
        # Phase 30: the integrity loop runs the whole accountability stack.
        integrity_res = _regcall("brain.integrity", {"sample_traces": 5},
                                 actor="sleep")
        briefing = _compose_briefing(summary, cal_res, inbox_res, tomorrow,
                                    review_res, integrity_res)
        bprop = _propose(
            f"Morning briefing for {tomorrow} "
            "(draft composed overnight — nothing was executed):\n\n" + briefing,
            [{"tool": "inbox.triage", "args": {"limit": 10},
              "why": ("Refresh the inbox live in the morning so the briefing "
                      "above is current when you read it.")}])
        report["steps"]["briefing"] = {
            "for_date": tomorrow,
            "chars": len(briefing),
            "queued_proposal": bprop,
            "calendar_ok": _unwrap(cal_res) is not None,
            "inbox_ok": _unwrap(inbox_res) is not None}

        # --- Step 4: routine detection -> each proposal to the queue.
        rout_res = _regcall("memory.routines",
                            {"days": 14, "min_days": 4}, actor="sleep")
        rout = _unwrap(rout_res) or {}
        rprops = rout.get("proposals") if isinstance(rout, dict) else None
        queued_routines = []
        if isinstance(rprops, list):
            for p in rprops:
                if not isinstance(p, dict):
                    continue
                tool = str(p.get("tool") or "?")
                pattern = str(p.get("pattern") or "")
                suggestion = str(p.get("suggestion") or "")
                tod = p.get("tod_minutes")
                sched = "daily@%02d:%02d" % (tod // 60, tod % 60) \
                    if isinstance(tod, int) else "daily@08:00"
                slug = re.sub(r"[^a-z0-9_]", "_", tool.lower())[:20] or "routine"
                qp = _propose(
                    "Overnight routine detection — standing-rule proposal:\n\n"
                    f"**Observed:** {pattern}\n**Suggestion:** {suggestion}\n\n"
                    "Approve and the routine below is created as a background "
                    "worker (needs workers.autonomy_opt_in). Nothing is "
                    "scheduled until you approve.",
                    [{"tool": "workers.spawn",
                      "args": {"name": f"rt_{slug}",
                               "goal": f"Standing routine: run '{tool}' "
                                       f"({pattern})",
                               "schedule": sched, "role": "planner"},
                      "why": pattern or suggestion}])
                queued_routines.append({"tool": tool, "pattern": pattern,
                                        "queued_proposal": qp})
        report["steps"]["routines"] = {
            "detected": len(queued_routines),
            "proposals": queued_routines,
            "routines_tool_ok": _unwrap(rout_res) is not None}

        # --- Persist the night.
        stored = {"summary": summary,
                  "briefing_chars": len(briefing),
                  "proposals_queued": sum(
                      1 for s in ("consolidate", "briefing")
                      if (report["steps"][s].get("queued_proposal") or {})
                      .get("queued"))
                  + len(queued_routines)}
        _save_night(date_key, stored)
        report["stored"] = True
        return {"ok": True, **report}
    except Exception as exc:  # belt-and-braces: never raise
        return {"error": f"sleep.cycle failed: {exc}"}


def sleep_review_handler(args: dict) -> dict:
    """sleep.review {date} — read back a past night's summary from sqlite."""
    try:
        date = ((args or {}).get("date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return {"error": "date must look like YYYY-MM-DD "
                             f"(got {date!r})"}
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"error": f"{date!r} is not a real calendar date"}
        row = _load_night(date)
        if row is None:
            return {"error": (f"no sleep summary for {date} — the night cycle "
                              "may not have run that night (it runs ~02:00 "
                              "local when the workers tick is wired)")}
        try:
            summary = json.loads(row["summary_json"])
        except (TypeError, ValueError):
            return {"error": f"stored summary for {date} is corrupt"}
        return {"date": date, "summary": summary,
                "stored_at": row["created_at"]}
    except Exception as exc:
        return {"error": f"sleep.review failed: {exc}"}


# ---------------------------------------------------------------------------
# Dreaming: failure/thrash patterns -> drafted tool specs -> proposals
# ---------------------------------------------------------------------------
def _norm_args(args_json: str) -> str:
    try:
        obj = json.loads(args_json or "{}")
    except (TypeError, ValueError):
        return str(args_json or "")[:200]
    try:
        return json.dumps(obj, sort_keys=True)[:200]
    except (TypeError, ValueError):
        return str(args_json or "")[:200]


_DREAM_SKIP_TOOLS = {
    "autopilot.propose", "autopilot.queue", "autopilot.status",
    "autopilot.approve", "autopilot.reject",
}


def _find_dream_patterns(rows: list[dict]) -> list[dict]:
    """Failure groups (same tool + error signature, >=3) and thrash groups
    (same tool + same args, >=3). Most-failing first, capped at 5."""
    fail_groups: Counter = Counter()
    fail_sample_args: dict = {}
    repeat_groups: Counter = Counter()
    for r in rows:
        tool = str(r.get("tool") or "")
        if not tool or tool.startswith("sleep.") or tool in _DREAM_SKIP_TOOLS:
            continue
        summary = str(r.get("result_summary") or "")
        norm = _norm_args(str(r.get("args_json") or ""))
        if _is_failure(summary):
            sig = re.sub(r"\d+", "#", summary[:100]).strip().lower()
            key = (tool, sig)
            fail_groups[key] += 1
            fail_sample_args.setdefault(key, norm)
        repeat_groups[(tool, norm)] += 1

    patterns = []
    seen_tools: set[str] = set()
    for (tool, sig), count in fail_groups.most_common():
        if count >= 3:
            patterns.append({"kind": "failure", "tool": tool, "count": count,
                             "signature": sig,
                             "sample_args": fail_sample_args[(tool, sig)]})
            seen_tools.add(tool)
    for (tool, norm), count in repeat_groups.most_common():
        if count >= 3 and tool not in seen_tools:
            patterns.append({"kind": "repeat", "tool": tool, "count": count,
                             "signature": f"same arguments ({norm[:80]})",
                             "sample_args": norm})
            seen_tools.add(tool)
    return patterns[:5]


def _dream_name(tool: str, taken: set[str]) -> str:
    base = "dream_" + re.sub(r"[^a-z0-9_]", "_", tool.lower()).strip("_")
    base = re.sub(r"_+", "_", base)[:24] or "dream_tool"
    name, i = base, 2
    while name in taken:
        suffix = f"_{i}"
        name = base[: 30 - len(suffix)] + suffix
        i += 1
    taken.add(name)
    return name


def _draft_spec(pattern: dict, forge_pack, taken: set[str],
                registry_tools: set[str]) -> dict | None:
    """Draft {name, description, intent} and validate it against the forge.

    Returns None when the spec is invalid (bad name, collision, or the
    intent matches no safe primitive) — the dream is then skipped honestly.
    """
    tool, count = pattern["tool"], pattern["count"]
    name = _dream_name(tool, taken)
    if not forge_pack._NAME_RE.match(name):
        return None
    if name in forge_pack._KNOWN_TOOL_NAMES or name in registry_tools:
        return None
    if pattern["kind"] == "failure":
        description = (
            f"Dream-forged helper: '{tool}' failed you {count} times this "
            f"week ({pattern['signature'][:90]}). A safer wrapper that "
            "validates inputs first so the request succeeds first time.")
        intent = (
            f"Do what the '{tool}' tool does for inputs like "
            f"{pattern['sample_args'][:120]}, but first validate and sanitize "
            "the inputs with regex pattern matching and extraction, then "
            "fetch or read the underlying data and return a clean summary; "
            "retry once on transient failure and send a notify alert if it "
            "still fails.")
    else:
        description = (
            f"Dream-forged shortcut: you ran '{tool}' {count} times this "
            "week with the same arguments. A one-call shortcut with your "
            "usual arguments baked in.")
        intent = (
            f"Shortcut for '{tool}': read the usual saved inputs, fetch the "
            "current data for them, and return the summary in a single call "
            "without asking for the same arguments again.")
    if not forge_pack.match_intent(intent):
        return None  # not forgeable from safe primitives: skip honestly
    if len(description) > 500 or len(intent) > 2000:
        return None
    return {"name": name, "description": description, "intent": intent}


def sleep_dream_handler(args: dict) -> dict:
    """sleep.dream {days?} — turn repeated failures into drafted tool specs.

    tools.forge itself is medium risk, so the dream never executes it: each
    validated spec is queued as an autopilot proposal whose step would forge
    it on the user's morning approval.
    """
    try:
        a = args or {}
        try:
            days = int(a.get("days", 7))
        except (TypeError, ValueError):
            return {"error": "days must be an integer"}
        days = max(1, min(days, 30))
        rows = _read_audit(int(time.time()) - days * 86400)
        if rows is None:
            return {"error": ("audit log unreadable at "
                              f"{_audit_db_path()} — cannot dream without it")}
        patterns = _find_dream_patterns(rows)
        if not patterns:
            return {"dreams": [], "patterns_found": 0,
                    "events_analyzed": len(rows), "window_days": days,
                    "message": (f"No repeated failures or thrice-repeated "
                                f"requests in the last {days} days — nothing "
                                "to dream about.")}
        # Lazy import: forge_pack is a sibling; never at module top level.
        try:
            from . import forge_pack  # noqa: PLC0415
        except ImportError as exc:
            return {"error": f"dream needs forge_pack, which is not installed: {exc}"}
        reg_tools: set[str] = set()
        if _AGENT is not None:
            try:
                reg_tools = set(getattr(_AGENT.registry, "tools", {}) or {})
            except Exception:
                reg_tools = set()
        taken: set[str] = set(reg_tools)
        dreams = []
        for pat in patterns:
            spec = _draft_spec(pat, forge_pack, taken, reg_tools)
            if spec is None:
                dreams.append({"pattern": {k: pat[k] for k in
                                           ("kind", "tool", "count", "signature")},
                               "dreamed": False,
                               "reason": ("spec failed validation (bad name, "
                                          "collision, or intent matched no "
                                          "safe forge primitives) — skipped "
                                          "honestly")})
                continue
            goal = (f"I noticed '{pat['tool']}' "
                    f"{'failed you' if pat['kind'] == 'failure' else 'was asked for'} "
                    f"{pat['count']} times this week "
                    f"({pat['signature'][:80]}). I drafted a helper tool for "
                    "it — approve to forge it? (Forged tools are born "
                    "inactive; test before activating.)")
            prop = _propose(goal, [{
                "tool": "tools.forge",
                "args": {"name": spec["name"],
                         "description": spec["description"],
                         "intent": spec["intent"]},
                "why": ("Forge the dream-drafted helper. Medium risk, so it "
                        "only runs on your morning approval — never while "
                        "you sleep.")}])
            dreams.append({"pattern": {k: pat[k] for k in
                                       ("kind", "tool", "count", "signature")},
                           "dreamed": True,
                           "spec": spec,
                           "queued_proposal": prop})
        return {"dreams": dreams, "patterns_found": len(patterns),
                "events_analyzed": len(rows), "window_days": days}
    except Exception as exc:
        return {"error": f"sleep.dream failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract (same shape as teach_pack.py)
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "sleep.cycle",
     "description": ("The nightly run (~02:00 local, called from the workers "
                     "tick; idempotent per night). (1) memory consolidation "
                     "is PROPOSED, never executed blind; (2) reads the day's "
                     "audit log into a short day summary; (3) scans tomorrow's "
                     "calendar + peeks at the inbox, composes the morning "
                     "briefing DRAFT and queues it via autopilot.propose; "
                     "(4) runs memory.routines and queues each routine "
                     "proposal. Persists the night in sqlite. NEVER executes "
                     "a medium/high-risk tool — anything riskier than low is "
                     "refused and queued as a proposal instead."),
     "handler": sleep_cycle_handler, "risk": "low", "needs_network": False,
     "schema": {"force?": "bool"}},
    {"name": "sleep.review",
     "description": ("Read back a past night's summary from sqlite "
                     "({date: YYYY-MM-DD}). Honest error when no summary "
                     "exists for that date."),
     "handler": sleep_review_handler, "risk": "low", "needs_network": False,
     "schema": {"date": "string"}},
    {"name": "sleep.dream",
     "description": ("Analyze the week's audit log for failure patterns "
                     "(same tool failing 3+ times) and thrash patterns "
                     "(same request 3+ times). For each, draft a candidate "
                     "tool spec, validate it against the forge's safe "
                     "primitives, and queue it as an autopilot proposal — "
                     "the forge itself only runs on your morning approval. "
                     "Says so honestly when there is nothing to dream about."),
     "handler": sleep_dream_handler, "risk": "low", "needs_network": False,
     "schema": {"days?": "int"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(sleep_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "sleep.cycle": ("low", False),
    "sleep.review": ("low", False),
    "sleep.dream": ("low", False),
}


def register(reg) -> None:
    """Wire the three Sleep pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
