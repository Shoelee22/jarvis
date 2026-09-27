"""Relationship keeper / personal CRM pack (Phase 15, workstream D).

The CRM's job is to remember people so the user doesn't have to: who they
are, how the user knows them, birthdays and important dates, and — most
importantly — when the user last talked to them. Every interaction is
logged through people.note; that timestamped note stream is the core habit
the whole pack is built around.

    people.add          low   Add a person: {name, context}. context is
                              free-text ("colleague at VelocitiQ, met at the
                              Noida offsite; birthday 1990-03-14"). An
                              optional explicit {birthday: YYYY-MM-DD} arg and
                              {nudge_after_days} override are accepted.
    people.note         low   Append a timestamped interaction note
                              {name, note}. Every interaction logged.
    people.last_contact low   When you last interacted with someone + the
                              note (or an honest "never logged").
    people.nudges       low   Relationship maintenance: silence nudges at
                              nudge_after_days (default 21) days since last
                              contact, birthday nudges 7 days and 1 day
                              before. Stays SILENT when contact was recent.
                              Returns the nudge list AND offers each nudge
                              as an autopilot.propose proposal when the
                              pack is bound to a live agent (propose, never
                              auto-send — the sleep_pack contract).
    people.search       low   FTS over names, context, and notes.

NAME MATCHING: case-insensitive, whitespace-stripped. On an ambiguous
match (two "Rahuls") the handler returns the candidates and asks — it
never silently picks one.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/crm.db,
      overridable with the JARVIS_CRM_DB env var (tests use a tmp file).
    - Nudges only ever become autopilot proposals; nothing is ever sent to
      a person automatically. No contact action exists in this pack by
      design — the nudges hand the user (via the approval queue) the
      decision to reach out.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

from ...config import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "crm.db"

DEFAULT_NUDGE_AFTER_DAYS = 21
BIRTHDAY_NUDGE_DAYS = (7, 1)  # nudge 7 days and 1 day before

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/sleep_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# SQLite state
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_CRM_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS people("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL,"
        " name_norm TEXT NOT NULL,"
        " context TEXT NOT NULL DEFAULT '',"
        " birthday TEXT,"
        " important_dates TEXT NOT NULL DEFAULT '[]',"
        " nudge_after_days INTEGER NOT NULL DEFAULT 21,"
        " created_at INTEGER NOT NULL,"
        " last_contact_ts INTEGER,"
        " last_contact_note TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_people_name_norm"
        " ON people(name_norm)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notes("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " person_id INTEGER NOT NULL,"
        " ts INTEGER NOT NULL,"
        " note TEXT NOT NULL)"
    )
    # FTS index over names + context + all notes for the person. Plain
    # fts5 table (not external-content) so we can manage rows explicitly
    # without triggers; person_id stored UNINDEXED for lookups.
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS people_fts USING fts5("
        "person_id UNINDEXED, name, context, notes_agg)")
    conn.commit()
    return conn


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _fts_agg(conn, person_id: int) -> str:
    rows = conn.execute(
        "SELECT note FROM notes WHERE person_id=? ORDER BY ts", (person_id,)
    ).fetchall()
    return "\n".join(r["note"] for r in rows)


def _fts_upsert(conn, person: dict) -> None:
    conn.execute("DELETE FROM people_fts WHERE person_id=?", (person["id"],))
    conn.execute(
        "INSERT INTO people_fts(person_id, name, context, notes_agg)"
        " VALUES(?,?,?,?)",
        (str(person["id"]), person["name"], person.get("context") or "",
         _fts_agg(conn, person["id"])))


# ---------------------------------------------------------------------------
# Birthday parsing (explicit arg wins; otherwise first YYYY-MM-DD in context)
# ---------------------------------------------------------------------------
_BDAY_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _parse_birthday(context: str, explicit: str | None = None):
    candidates = [explicit] if explicit is not None else \
        [m.group(0) for m in _BDAY_RE.finditer(context or "")]
    for candidate in candidates:
        if not candidate:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate.strip()):
            return None, f"birthday must be YYYY-MM-DD (got {candidate!r})"
        try:
            datetime.strptime(candidate.strip(), "%Y-%m-%d")
        except ValueError:
            return None, f"{candidate!r} is not a real calendar date"
        return candidate.strip(), None
    return None, None


# ---------------------------------------------------------------------------
# Name matching: case-insensitive, whitespace-stripped; ambiguity is an
# error with candidates, never a silent pick.
# ---------------------------------------------------------------------------
def _match_candidates(conn, name: str) -> list[dict]:
    norm = _norm(name)
    if not norm:
        return []
    rows = conn.execute(
        "SELECT id, name, context FROM people ORDER BY name").fetchall()
    exact = [dict(r) for r in rows if _norm(r["name"]) == norm]
    if exact:
        return exact
    # Fuzzy fallback: query is a substring of the name or vice versa.
    return [dict(r) for r in rows
            if norm in _norm(r["name"]) or _norm(r["name"]) in norm]


def _resolve_person(conn, name: str) -> tuple[dict | None, dict | None]:
    """(person, problem). person full row dict, or problem dict with
    'candidates' / 'not_found'. Returns ambiguous candidates with enough
    context to disambiguate."""
    cands = _match_candidates(conn, name)
    if len(cands) == 1:
        row = conn.execute(
            "SELECT * FROM people WHERE id=?", (cands[0]["id"],)).fetchone()
        return dict(row), None
    if not cands:
        return None, {"error": f"no one named {name.strip()!r} in the CRM — "
                               "add them first with people.add"}
    if len(cands) > 1 and all(_norm(c["name"]) == _norm(name) for c in cands):
        label = "ambiguous"
    else:
        label = "multiple_matches"
    return None, {
        label: True,
        "message": (f"{len(cands)} people match {name.strip()!r} — "
                    "which one did you mean? (never picked silently)"),
        "candidates": [
            {"id": c["id"], "name": c["name"],
             "context": (c.get("context") or "")[:120]}
            for c in cands],
    }


# ---------------------------------------------------------------------------
# Risk-gated registry calls — copied contract from sleep_pack.py: only
# "low" risk tools are executed; unknown risk fails closed. confirmed=True
# is never passed.
# ---------------------------------------------------------------------------
def _tool_risk(reg, name: str) -> str:
    try:
        tool = getattr(reg, "tools", {}).get(name)
    except Exception:
        tool = None
    if tool is not None:
        return getattr(tool, "risk", None) or "unknown"
    try:
        from ...agent.policy import RISK_TABLE  # noqa: PLC0415 (lazy)
        entry = RISK_TABLE.get(name)
        if entry:
            return entry[0]
    except Exception:
        pass
    return "unknown"


def _regcall(name: str, args: dict | None, actor: str = "crm") -> dict:
    if _AGENT is None:
        return {"skipped": True, "tool": name,
                "reason": "crm pack is not bound to a live agent "
                          "(bind_agent was not called)"}
    reg = getattr(_AGENT, "registry", None)
    if reg is None:
        return {"skipped": True, "tool": name,
                "reason": "bound agent exposes no .registry"}
    risk = _tool_risk(reg, name)
    if risk != "low":
        return {"skipped": True, "tool": name, "risk": risk,
                "reason": ("crm only executes low-risk tools — queue it as "
                           "an autopilot proposal instead")}
    try:
        return reg.call(name, args or {}, actor=actor)
    except Exception as exc:
        return {"ok": False, "error": f"registry.call raised: {exc}"}


def _unwrap(res: dict | None) -> dict | None:
    if isinstance(res, dict) and res.get("ok"):
        inner = res.get("result")
        return inner if isinstance(inner, dict) else None
    return None


def _propose(goal: str, steps: list[dict]) -> dict:
    """Queue an autopilot proposal; never executes or sends anything."""
    res = _regcall("autopilot.propose", {"goal": goal, "steps": steps},
                   actor="crm")
    if isinstance(res, dict) and res.get("skipped"):
        return {"queued": False, "reason": res.get("reason", "skipped")}
    inner = _unwrap(res)
    if inner and inner.get("ok"):
        return {"queued": True, "id": inner.get("id")}
    err = (res.get("error") if isinstance(res, dict) else None) or "unknown"
    return {"queued": False, "reason": f"autopilot.propose failed: {err}"}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def people_add_handler(args: dict) -> dict:
    """people.add {name, context, birthday?, nudge_after_days?}.

    Adds a person. birthday is parsed from context if it holds a
    YYYY-MM-DD, or given explicitly. nudge_after_days defaults to 21.
    """
    try:
        a = args or {}
        name = (a.get("name") or "").strip()
        if not name:
            return {"error": "name is required"}
        context = str(a.get("context") or "").strip()
        birthday, berr = _parse_birthday(
            context, a.get("birthday") if a.get("birthday") is not None
            else None)
        if berr:
            return {"error": berr}
        try:
            nad = int(a.get("nudge_after_days", DEFAULT_NUDGE_AFTER_DAYS))
        except (TypeError, ValueError):
            return {"error": "nudge_after_days must be an integer"}
        nad = max(1, min(nad, 3650))
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO people(name, name_norm, context, birthday,"
                    " important_dates, nudge_after_days, created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (name, _norm(name), context, birthday, "[]", nad,
                     int(time.time())))
                pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                person = dict(conn.execute(
                    "SELECT * FROM people WHERE id=?", (pid,)).fetchone())
                _fts_upsert(conn, person)
                conn.commit()
            finally:
                conn.close()
        return {"ok": True, "id": pid, "name": name, "birthday": birthday,
                "nudge_after_days": nad}
    except Exception as exc:
        return {"error": f"people.add failed: {exc}"}


def people_note_handler(args: dict) -> dict:
    """people.note {name, note} — append a timestamped interaction note."""
    try:
        a = args or {}
        note = (a.get("note") or "").strip()
        if not note:
            return {"error": "note is required"}
        raw_name = (a.get("name") or "").strip()
        if not raw_name:
            return {"error": "name is required"}
        now = int(time.time())
        with _LOCK:
            conn = _connect()
            try:
                person, problem = _resolve_person(conn, raw_name)
                if problem:
                    return problem
                pid = person["id"]
                conn.execute(
                    "INSERT INTO notes(person_id, ts, note) VALUES(?,?,?)",
                    (pid, now, note))
                conn.execute(
                    "UPDATE people SET last_contact_ts=?,"
                    " last_contact_note=? WHERE id=?",
                    (now, note, pid))
                person = dict(conn.execute(
                    "SELECT * FROM people WHERE id=?", (pid,)).fetchone())
                _fts_upsert(conn, person)
                conn.commit()
            finally:
                conn.close()
        return {"ok": True, "name": person["name"],
                "logged_at": datetime.fromtimestamp(now).isoformat(
                    timespec="seconds"),
                "note": note}
    except Exception as exc:
        return {"error": f"people.note failed: {exc}"}


def people_last_contact_handler(args: dict) -> dict:
    """people.last_contact {name} — when you last interacted + the note."""
    try:
        raw_name = ((args or {}).get("name") or "").strip()
        if not raw_name:
            return {"error": "name is required"}
        with _LOCK:
            conn = _connect()
            try:
                person, problem = _resolve_person(conn, raw_name)
                if problem:
                    return problem
                recent = conn.execute(
                    "SELECT ts, note FROM notes WHERE person_id=?"
                    " ORDER BY ts DESC LIMIT 3", (person["id"],)).fetchall()
            finally:
                conn.close()
        ts = person.get("last_contact_ts")
        if ts:
            days = (int(time.time()) - ts) / 86400.0
            return {
                "ok": True, "name": person["name"],
                "last_contact": datetime.fromtimestamp(ts).isoformat(
                    timespec="seconds"),
                "days_ago": round(days, 1),
                "note": person.get("last_contact_note"),
                "recent_notes": [
                    {"at": datetime.fromtimestamp(r["ts"]).isoformat(
                        timespec="seconds"), "note": r["note"]}
                    for r in recent],
            }
        return {"ok": True, "name": person["name"],
                "last_contact": None,
                "message": "no interaction ever logged for "
                           f"{person['name']} — log one with people.note"}
    except Exception as exc:
        return {"error": f"people.last_contact failed: {exc}"}


# ---------------------------------------------------------------------------
# Nudges
# ---------------------------------------------------------------------------
def _next_birthday_mmdd(birthday: str | None, today: date) -> tuple[int, int] | None:
    """Days until the next birthday (MM-DD from YYYY-MM-DD), or None."""
    if not birthday:
        return None
    try:
        y, m, d = (int(x) for x in birthday.split("-"))
    except (ValueError, AttributeError):
        return None
    for year in (today.year, today.year + 1):
        try:
            cand = date(year, m, d)
        except ValueError:
            continue  # e.g. Feb 29 in a non-leap year
        if cand >= today:
            return (cand - today).days, m
    return None


def _collect_nudges(today: date | None = None) -> list[dict]:
    today = today or date.today()
    now = int(time.time())
    nudges: list[dict] = []
    with _LOCK:
        conn = _connect()
        try:
            people = [dict(r) for r in conn.execute(
                "SELECT * FROM people ORDER BY name").fetchall()]
        finally:
            conn.close()
    for p in people:
        anchor = p.get("last_contact_ts") or p["created_at"]
        days_silent = (now - anchor) / 86400.0
        threshold = p.get("nudge_after_days") or DEFAULT_NUDGE_AFTER_DAYS
        if days_silent >= threshold:
            since = "never logged" if not p.get("last_contact_ts") else (
                f"last contact {int(days_silent)} days ago"
                + (f" — \"{(p.get('last_contact_note') or '')[:80]}\""
                   if p.get("last_contact_note") else ""))
            nudges.append({
                "kind": "silence",
                "person": p["name"],
                "days_since_contact": int(days_silent),
                "threshold_days": threshold,
                "message": (f"You haven't talked to {p['name']} in "
                            f"{int(days_silent)} days ({since}). "
                            "Reach out?"),
            })
        bres = _next_birthday_mmdd(p.get("birthday"), today)
        if bres is not None:
            days_until, _ = bres
            if days_until in BIRTHDAY_NUDGE_DAYS:
                bday = today.toordinal() + days_until
                when = date.fromordinal(bday).strftime("%A")
                nudges.append({
                    "kind": "birthday",
                    "person": p["name"],
                    "days_until": days_until,
                    "date": date.fromordinal(bday).isoformat(),
                    "message": (f"{p['name']}'s birthday is {when} "
                                f"({date.fromordinal(bday).isoformat()}"
                                f"{', in ' + str(days_until) + ' days' if days_until != 1 else ', tomorrow'}). "
                                "Plan something?"),
                })
    return nudges


def people_nudges_handler(args: dict) -> dict:
    """people.nudges {} — relationship maintenance.

    Silence nudges at nudge_after_days (default 21) days without logged
    contact; birthday nudges 7 days and 1 day before. Silent (empty list)
    when contact was recent. Each nudge is ALSO offered as an
    autopilot.propose proposal when the pack is bound — propose, never
    auto-send. Unbound, the nudges are still returned and the proposals
    field says so honestly.
    """
    try:
        nudges = _collect_nudges()
        proposals = []
        for n in nudges:
            steps = [{
                "tool": "people.note",
                "args": {"name": n["person"],
                         "note": "<log what happened once you've reached out>"},
                "why": ("Logging the interaction after you reach out resets "
                        "the nudge clock. The nudge itself sends nothing.")}]
            prop = _propose(n["message"], steps)
            proposals.append({"person": n["person"], "kind": n["kind"],
                              "queued_proposal": prop})
        out = {"ok": True, "nudges": nudges, "count": len(nudges)}
        if _AGENT is None:
            out["proposals"] = {
                "offered": False,
                "reason": ("crm pack is not bound to a live agent — nudges "
                           "returned for review only, nothing queued")}
        else:
            out["proposals"] = {
                "offered": True,
                "queued": sum(1 for p in proposals
                              if p["queued_proposal"].get("queued")),
                "detail": proposals,
            }
        return out
    except Exception as exc:
        return {"error": f"people.nudges failed: {exc}"}


# ---------------------------------------------------------------------------
# Search (FTS5 over name/context/notes; LIKE fallback when the query is not
# valid FTS5 syntax)
# ---------------------------------------------------------------------------
def people_search_handler(args: dict) -> dict:
    """people.search {query, n?} — FTS over names, context, and notes."""
    try:
        query = ((args or {}).get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        try:
            n = int((args or {}).get("n", 10))
        except (TypeError, ValueError):
            return {"error": "n must be an integer"}
        n = max(1, min(n, 50))
        with _LOCK:
            conn = _connect()
            try:
                hits: list[dict] = []
                try:
                    rows = conn.execute(
                        "SELECT person_id,"
                        " snippet(people_fts, 3, '<<', '>>', '…', 18) AS snip"
                        " FROM people_fts WHERE people_fts MATCH ?"
                        " LIMIT ?", (query, n)).fetchall()
                except sqlite3.OperationalError:
                    rows = []  # bad FTS5 syntax -> LIKE fallback below
                if not rows:
                    like = f"%{query}%"
                    rows = conn.execute(
                        "SELECT p.id AS person_id, p.name, p.context,"
                        " '' AS snip FROM people p"
                        " LEFT JOIN notes n2 ON n2.person_id = p.id"
                        " WHERE p.name LIKE ? OR p.context LIKE ?"
                        " OR n2.note LIKE ?"
                        " GROUP BY p.id LIMIT ?",
                        (like, like, like, n)).fetchall()
                pids = [int(r["person_id"]) for r in rows]
                pmap = {}
                if pids:
                    q = ",".join("?" for _ in pids)
                    for r in conn.execute(
                            f"SELECT * FROM people WHERE id IN ({q})",
                            tuple(pids)).fetchall():
                        pmap[r["id"]] = dict(r)
                for r in rows:
                    pid = int(r["person_id"])
                    p = pmap.get(pid)
                    if not p:
                        continue
                    hits.append({
                        "name": p["name"],
                        "birthday": p.get("birthday"),
                        "context": (p.get("context") or "")[:200],
                        "snippet": r["snip"] or None,
                        "last_contact": (
                            datetime.fromtimestamp(
                                p["last_contact_ts"]).isoformat(
                                    timespec="seconds")
                            if p.get("last_contact_ts") else None),
                    })
            finally:
                conn.close()
        return {"ok": True, "query": query, "count": len(hits),
                "hits": hits}
    except Exception as exc:
        return {"error": f"people.search failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract (same shape as sleep_pack.py / teach_pack.py)
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "people.add",
     "description": ("Add a person to the relationship CRM: {name, context}. "
                     "context is free-text (how you know them, important "
                     "dates; a YYYY-MM-DD in it is parsed as the birthday). "
                     "Optional explicit {birthday: YYYY-MM-DD} and "
                     "{nudge_after_days} (default 21) overrides."),
     "handler": people_add_handler, "risk": "low", "needs_network": False,
     "schema": {"name": "string", "context?": "string",
                "birthday?": "string", "nudge_after_days?": "int"}},
    {"name": "people.note",
     "description": ("Append a timestamped interaction note {name, note}. "
                     "The core habit: every interaction logged. Updates the "
                     "last-contact clock for nudges. Ambiguous names return "
                     "candidates instead of picking silently."),
     "handler": people_note_handler, "risk": "low", "needs_network": False,
     "schema": {"name": "string", "note": "string"}},
    {"name": "people.last_contact",
     "description": ("When you last interacted with someone and what the "
                     "note said {name} — plus up to 3 recent notes. Honest "
                     "'never logged' when no interaction exists."),
     "handler": people_last_contact_handler, "risk": "low",
     "needs_network": False, "schema": {"name": "string"}},
    {"name": "people.nudges",
     "description": ("Relationship maintenance. Silence nudges at "
                     "nudge_after_days (default 21) days without logged "
                     "contact; birthday nudges 7 days and 1 day before. "
                     "Stays SILENT when contact was recent. Each nudge is "
                     "offered as an autopilot.propose proposal when the "
                     "pack is bound — propose, never auto-send. The CRM "
                     "never contacts anyone by itself."),
     "handler": people_nudges_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "people.search",
     "description": ("Full-text search over names, context, and notes "
                     "{query, n?}. Falls back to substring matching when "
                     "the query is not valid FTS5 syntax."),
     "handler": people_search_handler, "risk": "low", "needs_network": False,
     "schema": {"query": "string", "n?": "int"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(crm_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "people.add": ("low", False),
    "people.note": ("low", False),
    "people.last_contact": ("low", False),
    "people.nudges": ("low", False),
    "people.search": ("low", False),
}


def register(reg) -> None:
    """Wire the five CRM pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
