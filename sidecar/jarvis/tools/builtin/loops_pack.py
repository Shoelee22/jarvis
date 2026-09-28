"""Prediction + Open Loops pack (Phase 13): JARVIS never drops a promise.

Two halves:

PREDICTION (heuristic, LLM-free, read-only)
    ``predict.next`` scores the 5 most likely next user requests from
    tool-call history (audit log or caller-supplied synthetic history):
    recency + time-of-day buckets + day-of-week + per-day frequency.
    Confidence is 0..1 and HONEST: thin signal yields low confidences and
    says so — never fake certainty. The prediction machinery's own tools
    (``predict.*``, ``loops.*``) are excluded from scoring so the model
    cannot predict itself.
    ``predict.prepare`` pre-warms for the top prediction — READ-ONLY prep
    only (pre-triage the inbox, pre-fetch weather). Every prep step's tool
    must resolve to low risk; a single medium+ step FAILS THE WHOLE CALL
    (fail closed). Nothing is sent, written, or executed user-visibly.

OPEN LOOPS (promise tracking)
    ``loops.track`` registers an open loop: {promise, deadline, check}.
    ``check`` = {tool, args} describing how to verify resolution — and the
    check tool MUST be low-risk. This is validated AT TRACK TIME against
    the live registry (then the policy RISK_TABLE): medium+ or unknown
    check tools are REFUSED with an explanatory error.
    ``loops.check`` is the sweeper, designed to run on the workers tick:
    for each due loop it runs the declared check tool. Resolved on its
    own -> marked done QUIETLY. Overdue and unresolved -> a NUDGE is queued
    as an autopilot proposal ("Still no reply from the client on the
    quote — draft a follow-up?"). Nudges are proposals only — NEVER
    auto-sent, never auto-executed. One nudge per loop (status 'nudged');
    later ticks re-run the check and close quietly if it resolved.
    ``loops.done`` manually closes a loop; unknown ids are an honest error.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - Loops/predictions NEVER execute medium+ risk actions: they propose,
      queue, or read. loops.check may only invoke the loop's DECLARED check
      tool, which was proven low-risk at track time; the track-time check
      is re-verified at check time (defense in depth — risk tables can
      change between track and check).
    - autopilot_pack is imported LAZILY inside the nudge path (no top-level
      import cycles with sibling packs).
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/loops.db,
      overridable with the JARVIS_LOOPS_DB env var (tests use a tmp file).
      History for prediction defaults to the sidecar audit log
      (JARVIS_LOOPS_AUDIT_DB override, else DATA_DIR/jarvis.db), with an
      explicit ``history`` argument for synthetic/test data.

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    In sidecar/jarvis/ipc/server.py, right after ``bind_agent(agent)``, add::

        from ..tools.builtin import loops_pack
        loops_pack.bind_agent(agent)
        from jarvis.agent.policy import RISK_TABLE
        RISK_TABLE.update(loops_pack.RISK_TABLE_ADDITIONS)

    Then, in workers_pack.tick_handler (or the sidecar's main loop), call::

        from . import loops_pack
        loop_outcomes = loops_pack.run_check()   # safe on every ~60s tick

    ``run_check()`` is the module-level sweeper the ``loops.check`` tool
    handler wraps, so the tick path and the tool path share one code path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ...config import DATA_DIR

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "loops.db"

# Tools owned by this pack — excluded from prediction scoring so the model
# never "predicts" its own machinery.
_SELF_PREFIXES = ("predict.", "loops.")

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/workers_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# sqlite plumbing (per-op connections + one lock, like teach_pack)
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_LOOPS_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS open_loops("
        " id TEXT PRIMARY KEY,"
        " promise TEXT NOT NULL,"
        " deadline INTEGER NOT NULL,"
        " check_json TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'open',"
        " created_at INTEGER NOT NULL,"
        " last_checked INTEGER,"
        " resolved_at INTEGER,"
        " note TEXT)"
    )
    return conn


def _now() -> int:
    return int(time.time())


def _wrap(fn):
    def _h(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # never let a loops tool crash the loop
            return {"error": f"{type(e).__name__}: {e}"}

    return _h


# ---------------------------------------------------------------------------
# Risk resolution (lazy: no import cycles with policy at module load)
# ---------------------------------------------------------------------------
def _tool_risk(tool_name: str) -> tuple[str | None, str]:
    """Resolve a tool's risk -> (risk | None, source).

    Checks the bound agent's live registry first, then the policy
    RISK_TABLE. Unknown tools return (None, 'unknown') — callers must treat
    unknown as unusable for checks/prep.
    """
    reg = getattr(_AGENT, "registry", None) if _AGENT is not None else None
    if reg is not None:
        tool = reg.tools.get(tool_name)
        if tool is not None:
            return tool.risk, "registry"
    try:
        from ...agent.policy import RISK_TABLE  # noqa: PLC0415

        if tool_name in RISK_TABLE:
            return RISK_TABLE[tool_name][0], "risk_table"
    except Exception:
        pass
    return None, "unknown"


def _require_low_risk(tool_name: str, what: str) -> str | None:
    """Return an error string unless tool_name is provably low-risk."""
    risk, _source = _tool_risk(tool_name)
    if risk is None:
        return (f"{what}: unknown tool {tool_name!r} — check/prep tools must "
                f"be known low-risk tools (not registered, not in RISK_TABLE)")
    if risk != "low":
        return (f"{what}: refused — tool {tool_name!r} is {risk}-risk; "
                f"only low-risk tools may be used here")
    return None


# ---------------------------------------------------------------------------
# Prediction scoring (heuristic, LLM-free)
# ---------------------------------------------------------------------------
def _audit_history(window_days: int) -> list[tuple[int, str]]:
    """(ts, tool) rows from the sidecar audit log. Empty list on any problem."""
    cutoff = _now() - window_days * 86400
    audit_path = os.environ.get("JARVIS_LOOPS_AUDIT_DB",
                                str(DATA_DIR / "jarvis.db"))
    try:
        conn = sqlite3.connect(audit_path)
        try:
            rows = conn.execute(
                "SELECT ts, tool FROM audit_log WHERE ts>? ORDER BY ts",
                (cutoff,)).fetchall()
            return [(int(r[0]), str(r[1])) for r in rows if r[1]]
        except sqlite3.OperationalError:
            return []  # table missing (fresh install) — thin signal, not an error
        finally:
            conn.close()
    except Exception:
        return []


def _parse_history_arg(history) -> tuple[list[tuple[int, str]], str | None]:
    """Validate the caller-supplied synthetic history -> ((ts, tool), error)."""
    if not isinstance(history, list):
        return [], "history must be a list of {tool, ts} objects"
    out: list[tuple[int, str]] = []
    for i, item in enumerate(history):
        if not isinstance(item, dict):
            return [], f"history[{i}] is not an object"
        tool = item.get("tool")
        ts = item.get("ts")
        if not isinstance(tool, str) or not tool:
            return [], f"history[{i}].tool must be a non-empty string"
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            return [], f"history[{i}].ts must be an epoch timestamp"
        out.append((ts, tool))
    return out, None


def _score_predictions(events: list[tuple[int, str]], hour: int, dow: int,
                       window_days: int, n: int) -> dict:
    """Rank tools by recency + time-of-day + day-of-week + frequency."""
    now = _now()
    by_tool: dict[str, list[int]] = defaultdict(list)
    for ts, tool in events:
        if any(tool.startswith(p) for p in _SELF_PREFIXES):
            continue  # never predict the prediction machinery itself
        by_tool[tool].append(ts)

    thin = len(events) < 5
    scored: list[dict] = []
    for tool, tss in by_tool.items():
        last = max(tss)
        hours_ago = max(0.0, (now - last) / 3600.0)
        recency = 1.0 / (1.0 + hours_ago / 12.0)
        days_seen = len({datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                         for ts in tss})
        frequency = min(1.0, (days_seen / max(1, window_days)) * 3.0)
        tod_hits = sum(
            1 for ts in tss
            if abs((datetime.fromtimestamp(ts).hour - hour + 12) % 24 - 12) <= 1.5
        ) / len(tss)
        dow_hits = sum(
            1 for ts in tss if datetime.fromtimestamp(ts).weekday() == dow
        ) / len(tss)
        conf = (0.45 * recency + 0.25 * frequency
                + 0.20 * tod_hits + 0.10 * dow_hits)
        if thin:
            conf *= 0.4  # thin signal: say so with low numbers, not silence
        conf = round(min(conf, 0.95), 2)
        hh = datetime.fromtimestamp(last).strftime("%H:%M")
        scored.append({
            "tool": tool,
            "confidence": conf,
            "why": (f"used {days_seen}d of last {window_days}, "
                    f"{tod_hits:.0%} near {hour:02d}:00, "
                    f"last used {hh}"),
        })
    scored.sort(key=lambda p: (-p["confidence"], p["tool"]))
    note = ("Thin signal: fewer than 5 tool-call events in the window — "
            "confidences are low by design, not by certainty."
            if thin else
            f"Scored {len(events)} events over the last {window_days} days.")
    return {"predictions": scored[:max(1, n)], "signal": "thin" if thin else "ok",
            "events_analyzed": len(events), "note": note,
            "hour": hour, "dow": dow}


@_wrap
def predict_next_handler(args: dict) -> dict:
    """predict.next — rank the 5 most likely next user requests.

    Args: {hour?, dow?, history?, window_days?, n?}. history is a list of
    {tool, ts} (epoch); when omitted the sidecar audit log is read.
    Read-only; no LLM; honest low confidences on thin signal.
    """
    now_dt = datetime.now()
    hour = args.get("hour", now_dt.hour)
    dow = args.get("dow", now_dt.weekday())
    window_days = args.get("window_days", 14)
    n = args.get("n", 5)
    try:
        hour = int(hour)
        dow = int(dow)
        window_days = int(window_days)
        n = int(n)
    except (TypeError, ValueError):
        return {"error": "hour, dow, window_days and n must be integers"}
    if not (0 <= hour <= 23):
        return {"error": "hour must be 0-23"}
    if not (0 <= dow <= 6):
        return {"error": "dow must be 0 (Mon) - 6 (Sun)"}
    if not (1 <= window_days <= 90):
        return {"error": "window_days must be 1-90"}
    n = max(1, min(n, 20))

    if "history" in args and args["history"] is not None:
        events, err = _parse_history_arg(args["history"])
        if err:
            return {"error": err}
        source = "argument"
    else:
        events = _audit_history(window_days)
        source = "audit_log"
    out = _score_predictions(events, hour, dow, window_days, n)
    out["history_source"] = source
    return out


# ---------------------------------------------------------------------------
# Pre-warming (read-only prep driven by the top prediction)
# ---------------------------------------------------------------------------
# Built-in prep plans: prediction tool -> read-only steps. Each step is
# validated at runtime (registered + low-risk); missing tools are skipped
# with a note, medium+ tools fail the whole call.
PREP_PLANS: dict[str, list[dict]] = {
    "morning.briefing": [
        {"tool": "inbox.triage", "args": {"filter": "unread", "n": 20}},
        {"tool": "calendar.today", "args": {}},
        {"tool": "weather.now", "args": {}},
    ],
}


def _validate_steps(steps, what: str) -> tuple[list[dict] | None, str | None]:
    if not isinstance(steps, list) or not steps:
        return None, f"{what}: steps must be a non-empty list of {{tool, args}}"
    norm: list[dict] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            return None, f"{what}: step {i} is not an object"
        tool = s.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return None, f"{what}: step {i} 'tool' must be a non-empty string"
        sargs = s.get("args", {})
        if not isinstance(sargs, dict):
            return None, f"{what}: step {i} ({tool}) 'args' must be an object"
        norm.append({"tool": tool.strip(), "args": sargs})
    return norm, None


@_wrap
def predict_prepare_handler(args: dict) -> dict:
    """predict.prepare — pre-warm for a prediction. READ-ONLY.

    Args: {prediction?, steps?, hour?, dow?, window_days?}. When
    ``prediction`` is omitted, predict.next is run and the top prediction
    is used. ``steps`` overrides the built-in prep plan. Every step's tool
    must be registered AND low-risk — one medium+ step fails the whole
    call. Nothing is sent, written, or shown to the user.
    """
    prediction = args.get("prediction")
    if prediction is None:
        top = predict_next_handler(
            {k: args[k] for k in ("hour", "dow", "window_days")
             if k in args})
        if "error" in top:
            return top
        if not top["predictions"]:
            return {"prepared": [],
                    "note": ("no prediction available (thin/empty signal) — "
                             "nothing to pre-warm")}
        prediction = top["predictions"][0]["tool"]
    if not isinstance(prediction, str) or not prediction.strip():
        return {"error": "prediction must be a non-empty tool name"}
    prediction = prediction.strip()

    raw_steps = args.get("steps")
    if raw_steps is not None:
        steps, err = _validate_steps(raw_steps, "predict.prepare")
        if err:
            return {"error": err}
    else:
        steps = PREP_PLANS.get(prediction)
        if steps is None:
            return {"prediction": prediction, "prepared": [],
                    "note": (f"no built-in prep plan for {prediction!r} — "
                             "pass explicit low-risk read-only steps")}

    if _AGENT is None:
        return {"error": "predict.prepare needs the live agent; not bound "
                         "(server.py must call loops_pack.bind_agent(agent))"}
    registry = getattr(_AGENT, "registry", None)
    if registry is None:
        return {"error": "bound agent exposes no .registry"}

    prepared: list[dict] = []
    skipped: list[str] = []
    # Fail closed: validate EVERY step before executing ANY.
    for s in steps:
        err = _require_low_risk(s["tool"], "predict.prepare")
        if err and "unknown tool" in err:
            skipped.append(f"{s['tool']}: not registered — skipped")
        elif err:
            return {"error": err, "prediction": prediction,
                    "prepared": [], "note": "nothing was executed"}
    runnable = [s for s in steps
                if not any(sk.startswith(s["tool"] + ":") for sk in skipped)]
    for s in runnable:
        try:
            res = registry.call(s["tool"], s["args"], actor="predict")
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        summary = ""
        if isinstance(res, dict):
            summary = str(res.get("result", res.get("error", "")))[:300]
        prepared.append({"tool": s["tool"], "args": s["args"],
                         "ok": bool(res.get("ok")), "summary": summary})
    return {"prediction": prediction, "prepared": prepared,
            "prepared_count": len(prepared), "skipped": skipped,
            "note": "read-only pre-warm: nothing was sent, written, or shown"}


# ---------------------------------------------------------------------------
# Open loops
# ---------------------------------------------------------------------------
def _parse_deadline(raw) -> tuple[int | None, str | None]:
    """ISO datetime (or epoch) -> (epoch_seconds, error)."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw), None
    if not isinstance(raw, str) or not raw.strip():
        return None, "deadline must be an ISO datetime string or epoch seconds"
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None, (f"deadline {raw!r} is not a valid ISO datetime "
                      "(e.g. '2026-09-28T09:00:00+05:30')")
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive -> server-local
    return int(dt.timestamp()), None


def _loop_row(lid: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            r = conn.execute("SELECT * FROM open_loops WHERE id=?",
                             (lid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()


def _loop_public(row: dict) -> dict:
    try:
        check = json.loads(row["check_json"])
    except (TypeError, ValueError):
        check = {}
    return {
        "id": row["id"],
        "promise": row["promise"],
        "deadline": datetime.fromtimestamp(
            row["deadline"]).astimezone().isoformat(timespec="seconds"),
        "deadline_epoch": row["deadline"],
        "check": check,
        "status": row["status"],
        "created_at": row["created_at"],
        "last_checked": row["last_checked"],
        "resolved_at": row["resolved_at"],
        "note": row["note"],
    }


@_wrap
def loops_track_handler(args: dict) -> dict:
    """loops.track {promise, deadline, check} — register an open loop.

    check = {tool, args}: how loops.check verifies resolution. The check
    tool MUST be a known low-risk tool — validated at track time (live
    registry, then RISK_TABLE); medium+ or unknown tools are refused.
    """
    promise = args.get("promise")
    if not isinstance(promise, str) or not promise.strip():
        return {"error": "promise must be a non-empty string"}
    promise = promise.strip()
    if len(promise) > 500:
        return {"error": "promise must be <= 500 characters"}

    deadline_epoch, err = _parse_deadline(args.get("deadline"))
    if err:
        return {"error": err}

    check = args.get("check")
    if not isinstance(check, dict):
        return {"error": "check must be an object {tool, args}"}
    check_tool = check.get("tool")
    if not isinstance(check_tool, str) or not check_tool.strip():
        return {"error": "check.tool must be a non-empty string"}
    check_tool = check_tool.strip()
    check_args = check.get("args", {})
    if not isinstance(check_args, dict):
        return {"error": "check.args must be an object"}
    err = _require_low_risk(check_tool, "loops.track")
    if err:
        return {"error": err}

    lid = "loop_" + uuid.uuid4().hex[:8]
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO open_loops(id, promise, deadline, check_json,"
                " status, created_at) VALUES(?,?,?,?, 'open', ?)",
                (lid, promise, deadline_epoch,
                 json.dumps({"tool": check_tool, "args": check_args}),
                 _now()))
            conn.commit()
        finally:
            conn.close()
    row = _loop_row(lid)
    return {"ok": True, **_loop_public(row),
            "note": ("loop tracked — loops.check will run the declared "
                     "low-risk check on the workers tick")}


@_wrap
def loops_done_handler(args: dict) -> dict:
    """loops.done {id} — manually close an open loop."""
    raw = args.get("id")
    lid = raw.strip() if isinstance(raw, str) else ""
    if not lid:
        return {"error": "id is required — the loop id to close"}
    row = _loop_row(lid)
    if row is None:
        return {"error": f"no such loop {lid!r} (see loops.check output for ids)"}
    if row["status"] == "done":
        return {"ok": True, "id": lid, "status": "done",
                "note": "loop was already closed"}
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE open_loops SET status='done', resolved_at=?,"
                " note='closed manually via loops.done' WHERE id=?",
                (_now(), lid))
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "id": lid, "status": "done",
            "promise": row["promise"]}


def _set_loop(lid: str, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(f"UPDATE open_loops SET {cols} WHERE id=?",
                         (*fields.values(), lid))
            conn.commit()
        finally:
            conn.close()


def _run_check_tool(check: dict) -> dict:
    """Invoke the loop's declared check tool. Returns the raw registry result
    (or an error dict when the agent is not bound)."""
    if _AGENT is None:
        return {"ok": False,
                "error": "loops.check needs the live agent; not bound "
                         "(server.py must call loops_pack.bind_agent(agent))"}
    registry = getattr(_AGENT, "registry", None)
    if registry is None:
        return {"ok": False, "error": "bound agent exposes no .registry"}
    tool = check.get("tool", "")
    # Defense in depth: re-verify low-risk at check time (risk tables can
    # change between track and check).
    err = _require_low_risk(tool, "loops.check")
    if err:
        return {"ok": False, "error": err}
    try:
        return registry.call(tool, check.get("args") or {}, actor="loops")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _queue_nudge(loop: dict, check: dict) -> dict:
    """Queue a nudge as an autopilot proposal. Proposal only — never sends.

    Prefers the live registry's autopilot.propose; falls back to the
    autopilot_pack handler via lazy import (no top-level cycles).
    """
    promise = loop["promise"]
    deadline_iso = datetime.fromtimestamp(
        loop["deadline"]).astimezone().strftime("%Y-%m-%d %H:%M")
    goal = (f"Still waiting: {promise} (deadline was {deadline_iso}). "
            f"Draft a follow-up?")
    steps = [{"tool": check["tool"], "args": check.get("args") or {},
              "why": ("re-run the loop's resolution check first — it may "
                      "have resolved on its own")}]
    payload = {"goal": goal, "steps": steps}

    reg = getattr(_AGENT, "registry", None) if _AGENT is not None else None
    if reg is not None and "autopilot.propose" in getattr(reg, "tools", {}):
        try:
            res = reg.call("autopilot.propose", payload, actor="loops")
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if not res.get("ok"):
            return {"ok": False,
                    "error": res.get("error", "autopilot.propose failed")}
        inner = res.get("result") or {}
        return {"ok": True, "proposal_id": inner.get("id"), "via": "registry",
                "goal": goal}
    try:
        from . import autopilot_pack  # noqa: PLC0415  (lazy: no cycles)
    except ImportError:
        return {"ok": False,
                "error": "autopilot pack not installed — nudge could not be queued"}
    try:
        out = autopilot_pack.propose_handler(payload)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if "error" in out:
        return {"ok": False, "error": out["error"]}
    return {"ok": True, "proposal_id": out.get("id"), "via": "direct",
            "goal": goal}


def run_check(now: int | None = None) -> dict:
    """Sweep open loops. Safe to call on every workers tick (~60s).

    For each due loop: run its declared low-risk check; resolved -> done
    quietly; overdue + unresolved -> one nudge proposal via autopilot
    (never auto-sent). Returns per-loop outcomes. Never raises.
    """
    try:
        now = _now() if now is None else int(now)
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM open_loops WHERE status IN ('open','nudged')"
                    " ORDER BY deadline").fetchall()
                loops = [dict(r) for r in rows]
            finally:
                conn.close()
        outcomes: list[dict] = []
        for loop in loops:
            lid = loop["id"]
            try:
                check = json.loads(loop["check_json"])
            except (TypeError, ValueError):
                check = {}
            outcome = {"id": lid, "promise": loop["promise"],
                       "status_before": loop["status"]}
            if now < loop["deadline"]:
                outcome["outcome"] = "waiting"
                outcome["detail"] = "deadline not reached yet"
                outcomes.append(outcome)
                continue
            if not isinstance(check.get("tool"), str):
                outcome["outcome"] = "check_failed"
                outcome["detail"] = "stored check spec is corrupt"
                outcomes.append(outcome)
                continue
            _set_loop(lid, last_checked=now)
            res = _run_check_tool(check)
            if not res.get("ok"):
                outcome["outcome"] = "check_failed"
                outcome["detail"] = res.get("error", "check tool failed")
                _set_loop(lid, note=f"check failed: {outcome['detail']}"[:500])
                outcomes.append(outcome)
                continue
            result = res.get("result")
            resolved = isinstance(result, dict) and bool(result.get("resolved"))
            if resolved:
                _set_loop(lid, status="done", resolved_at=now,
                          note="resolved on its own; closed quietly by loops.check")
                outcome["outcome"] = "resolved_quietly"
                outcome["detail"] = ("check tool reported resolved — "
                                     "loop closed, no nudge sent")
            elif loop["status"] == "nudged":
                outcome["outcome"] = "nudge_already_queued"
                outcome["detail"] = ("still unresolved; nudge proposal already "
                                     "queued — not re-proposing")
            else:
                nudge = _queue_nudge(loop, check)
                if nudge.get("ok"):
                    _set_loop(lid, status="nudged",
                              note="nudge queued as autopilot proposal")
                    outcome["outcome"] = "nudge_proposed"
                    outcome["detail"] = {
                        "proposal_id": nudge.get("proposal_id"),
                        "via": nudge.get("via"),
                        "goal": nudge.get("goal"),
                    }
                else:
                    outcome["outcome"] = "nudge_failed"
                    outcome["detail"] = nudge.get("error", "unknown")
                    _set_loop(lid,
                              note=f"nudge failed: {outcome['detail']}"[:500])
            outcomes.append(outcome)
        return {"checked": len(outcomes), "outcomes": outcomes, "ts": now}
    except Exception as e:  # the tick must never die because of a sweep
        return {"checked": 0, "outcomes": [],
                "error": f"run_check failed: {type(e).__name__}: {e}"}


@_wrap
def loops_check_handler(args: dict) -> dict:  # noqa: ARG001
    """loops.check {} — sweep open loops (the workers-tick entry point)."""
    return run_check()


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "predict.next",
     "description": ("Rank the 5 most likely next user requests from tool-call "
                     "history (audit log, or caller-supplied {tool, ts} history): "
                     "recency + time-of-day buckets + day-of-week + frequency, "
                     "LLM-free. Confidence 0..1 is HONEST — thin signal yields "
                     "low confidences and says so. Read-only; never predicts "
                     "its own predict.*/loops.* tools."),
     "handler": predict_next_handler, "risk": "low", "needs_network": False,
     "schema": {"hour?": "int", "dow?": "int", "history?": "array",
                "window_days?": "int", "n?": "int"}},
    {"name": "predict.prepare",
     "description": ("Pre-warm for a prediction — READ-ONLY prep only (e.g. "
                     "pre-triage the inbox so the 9am briefing is instant). "
                     "prediction defaults to predict.next's top hit; steps "
                     "override the built-in plan. EVERY step's tool must be "
                     "registered and low-risk — one medium+ step fails the "
                     "whole call. Never sends, writes, or shows anything to "
                     "the user."),
     "handler": predict_prepare_handler, "risk": "low", "needs_network": False,
     "schema": {"prediction?": "string", "steps?": "array", "hour?": "int",
                "dow?": "int", "window_days?": "int"}},
    {"name": "loops.track",
     "description": ("Register an open loop: {promise, deadline (ISO datetime), "
                     "check: {tool, args}} — e.g. promise='client replied to "
                     "quote', check={tool: 'inbox.triage', args: {...}}. The "
                     "check tool MUST be a known low-risk tool: validated at "
                     "track time (live registry, then RISK_TABLE); medium+ "
                     "or unknown check tools are REFUSED with an explanation."),
     "handler": loops_track_handler, "risk": "low", "needs_network": False,
     "schema": {"promise": "string", "deadline": "string", "check": "object"}},
    {"name": "loops.check",
     "description": ("Sweep open loops — designed for the workers tick. For "
                     "each due loop, run its DECLARED low-risk check tool "
                     "(re-verified low-risk at check time). Resolved on its "
                     "own -> marked done quietly. Overdue + unresolved -> a "
                     "NUDGE is queued as an autopilot proposal (never "
                     "auto-sent); one nudge per loop. Returns per-loop "
                     "outcomes. Also callable as loops_pack.run_check()."),
     "handler": loops_check_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "loops.done",
     "description": ("Manually close an open loop by id. Unknown ids return "
                     "an honest error."),
     "handler": loops_done_handler, "risk": "low", "needs_network": False,
     "schema": {"id": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(loops_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "predict.next": ("low", False),
    "predict.prepare": ("low", False),
    "loops.track": ("low", False),
    "loops.check": ("low", False),
    "loops.done": ("low", False),
}


def register(reg) -> None:
    """Wire the five Prediction + Open Loops tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
