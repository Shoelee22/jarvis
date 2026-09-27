"""Meeting companion pack (Phase 18): conversations become actions.

studio.transcribe_notes mines action items from AUDIO; persona.followup
extracts commitments from a single utterance. This pack is the durable
meeting layer: log a meeting's notes, mine action items from the TEXT,
register each one as a Phase 13 open loop so it is followed through, and
track which meetings still have unresolved actions.

Tools:
    meetings.log        low     Record a meeting {title, date?, attendees?,
                            notes}. Date is ISO (defaults to today).
    meetings.list       low     List meetings {n?}, newest first.
    meetings.actions    low     Mine action items from a meeting's notes
                            {meeting_id, register?}. register=true creates
                            one loops.track entry per action (lazy
                            loops_pack import; honest note when absent).
    meetings.open       low     Meetings with unresolved action items,
                            reading live loop states. Never fabricates:
                            loops whose state is unreadable are marked
                            "unknown", not "resolved".

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - Action mining is rule-based (imperative lines, "action:"/"todo:"
      markers, owner + date hints). Precision over recall; the raw notes
      are always returned alongside so nothing is lost.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/meetings.db,
      overridable with the JARVIS_MEETINGS_DB env var (tests use a tmp file).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "meetings.db"

_LOCK = threading.Lock()

_ACTION_MARKERS = re.compile(
    r"^\s*[-*\u2022]?\s*(?:action|todo|follow[-\s]?up|next\s+step)\s*[:\-]\s*(.+)$",
    re.IGNORECASE)
_IMPERATIVE = re.compile(
    r"^\s*[-*\u2022]?\s*(?:please\s+|let's\s+|lets\s+)?"
    r"(send|share|draft|write|prepare|schedule|book|call|email|review|"
    r"update|finish|complete|deliver|follow\s+up|check|confirm|ask|"
    r"remind|circulate|publish|fix)\b\s*(.+)$",
    re.IGNORECASE)
_OWNER_HINT = re.compile(r"\b([A-Z][a-z]+)\s+(?:to|will|should)\b")
_DATE_HINT = re.compile(
    r"\b(by\s+\w+day|by\s+friday|by\s+tomorrow|by\s+next\s+week|"
    r"by\s+\d{4}-\d{2}-\d{2}|eod|eow)\b", re.IGNORECASE)


def _db_path() -> Path:
    override = os.environ.get("JARVIS_MEETINGS_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Schema ensured per connection (see projects_pack: DB path is
    # per-call overridable via env, so import-time setup is not enough).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS meetings(
             id TEXT PRIMARY KEY,
             title TEXT NOT NULL,
             date TEXT NOT NULL,
             attendees TEXT,
             notes TEXT NOT NULL,
             created_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS meeting_actions(
             id TEXT PRIMARY KEY,
             meeting_id TEXT NOT NULL,
             action TEXT NOT NULL,
             owner TEXT,
             due_hint TEXT,
             loop_id TEXT,
             created_at TEXT NOT NULL)"""
    )
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(fn):
    def wrapper(args: dict):
        try:
            return fn(args or {})
        except Exception as exc:  # never raise out of a tool handler
            return {"error": f"{fn.__name__}: {type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    return wrapper


def _parse_date(raw) -> tuple[str | None, str | None]:
    if raw is None:
        return datetime.now().astimezone().date().isoformat(), None
    if not isinstance(raw, str) or not raw.strip():
        return None, "date must be an ISO date string (YYYY-MM-DD)"
    text = raw.strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None, f"date {raw!r} is not a valid ISO date (YYYY-MM-DD)"
    return text, None


def _mine_actions(notes: str) -> list[dict]:
    """Rule-based action-item mining. Precision over recall."""
    actions: list[dict] = []
    seen: set[str] = set()
    for line in notes.splitlines():
        text = None
        m = _ACTION_MARKERS.match(line)
        if m:
            text = m.group(1).strip()
        else:
            m = _IMPERATIVE.match(line)
            if m:
                text = f"{m.group(1)} {m.group(2)}".strip()
        if not text or len(text) < 4:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        owner = _OWNER_HINT.search(line)
        due = _DATE_HINT.search(line)
        actions.append({
            "action": text[:300],
            "owner": owner.group(1) if owner else None,
            "due_hint": due.group(1) if due else None,
        })
    return actions


def _loop_status(loop_id: str) -> str:
    try:
        from . import loops_pack  # noqa: PLC0415
    except ImportError:
        return "unknown"
    row_fn = getattr(loops_pack, "_loop_row", None)
    if not callable(row_fn):
        return "unknown"
    try:
        row = row_fn(loop_id)
    except Exception:
        return "unknown"
    return row.get("status", "unknown") if row else "missing"


def _get_meeting(mid: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id=?",
                           (mid,)).fetchone()
        return dict(row) if row else None


@_safe
def log_handler(args: dict) -> dict:
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return {"error": "meetings.log: title must be a non-empty string"}
    title = title.strip()
    if len(title) > 200:
        return {"error": "meetings.log: title must be <= 200 characters"}
    notes = args.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        return {"error": "meetings.log: notes must be a non-empty string"}
    notes = notes.strip()
    if len(notes) > 20000:
        return {"error": "meetings.log: notes must be <= 20000 characters"}
    date, derr = _parse_date(args.get("date"))
    if derr:
        return {"error": f"meetings.log: {derr}"}
    attendees = args.get("attendees")
    if attendees is not None:
        if not isinstance(attendees, list) or not all(
                isinstance(a, str) for a in attendees):
            return {"error": "meetings.log: attendees must be a list of strings"}
        attendees = ", ".join(a.strip() for a in attendees if a.strip())
    mid = "mtg_" + uuid.uuid4().hex[:8]
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO meetings(id, title, date, attendees, notes, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (mid, title, date, attendees, notes, _now()))
        conn.commit()
    mined = _mine_actions(notes)
    return {"ok": True, "meeting_id": mid, "title": title, "date": date,
            "actions_detected": len(mined),
            "note": ("run meetings.actions to review and register them "
                     "as tracked loops") if mined else None}


@_safe
def list_handler(args: dict) -> dict:
    n = args.get("n", 10)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return {"error": "meetings.list: n must be an integer"}
    n = max(1, min(n, 100))
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, date, attendees, created_at FROM meetings"
            " ORDER BY date DESC, created_at DESC LIMIT ?", (n,)).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            cnt = conn.execute("SELECT COUNT(*) FROM meeting_actions"
                               " WHERE meeting_id=?", (d["id"],)).fetchone()[0]
            d["actions"] = cnt
            items.append(d)
    return {"ok": True, "meetings": items}


@_safe
def actions_handler(args: dict) -> dict:
    mid = args.get("meeting_id")
    if not isinstance(mid, str) or not mid.strip():
        return {"error": "meetings.actions: meeting_id is required"}
    mid = mid.strip()
    meeting = _get_meeting(mid)
    if meeting is None:
        return {"error": f"meetings.actions: unknown meeting {mid!r}"}
    register = bool(args.get("register", False))
    mined = _mine_actions(meeting["notes"])
    registered: list[dict] = []
    loop_note = None
    if register and mined:
        try:
            from . import loops_pack  # noqa: PLC0415
            track = getattr(loops_pack, "loops_track_handler", None)
        except ImportError:
            track = None
        if not callable(track):
            loop_note = ("loops_pack not installed — actions returned "
                         "unregistered; nothing was tracked")
        else:
            with _LOCK, _connect() as conn:
                existing = {r["action"] for r in conn.execute(
                    "SELECT action FROM meeting_actions WHERE meeting_id=?",
                    (mid,)).fetchall()}
            for a in mined:
                if a["action"] in existing:
                    continue
                promise = a["action"]
                if a["owner"]:
                    promise = f"{a['owner']}: {promise}"
                check = {"tool": "meetings.open", "args": {}}
                out = track({"promise": f"[meeting:{meeting['title']}] {promise}",
                             "deadline": int(time.time()) + 14 * 86400,
                             "check": check})
                aid = "ma_" + uuid.uuid4().hex[:8]
                loop_id = out.get("id") if out.get("ok") else None
                with _LOCK, _connect() as conn:
                    conn.execute(
                        "INSERT INTO meeting_actions(id, meeting_id, action,"
                        " owner, due_hint, loop_id, created_at)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (aid, mid, a["action"], a["owner"], a["due_hint"],
                         loop_id, _now()))
                    conn.commit()
                registered.append({"action_id": aid, "action": a["action"],
                                   "loop_id": loop_id,
                                   "tracked": bool(out.get("ok")),
                                   "track_error": None if out.get("ok")
                                   else out.get("error")})
    with _LOCK, _connect() as conn:
        stored = [dict(r) for r in conn.execute(
            "SELECT * FROM meeting_actions WHERE meeting_id=? ORDER BY created_at",
            (mid,)).fetchall()]
    items = [{"action_id": r["id"], "action": r["action"], "owner": r["owner"],
              "due_hint": r["due_hint"], "loop_id": r["loop_id"]} for r in stored]
    out = {"ok": True, "meeting_id": mid, "mined": mined,
           "registered": registered, "stored_actions": items}
    if loop_note:
        out["note"] = loop_note
    return out


@_safe
def open_handler(args: dict) -> dict:
    with _LOCK, _connect() as conn:
        meetings = [dict(r) for r in conn.execute(
            "SELECT id, title, date FROM meetings ORDER BY date DESC").fetchall()]
        result = []
        for m in meetings:
            acts = [dict(r) for r in conn.execute(
                "SELECT * FROM meeting_actions WHERE meeting_id=?", (m["id"],)).fetchall()]
            open_acts = []
            for a in acts:
                st = _loop_status(a["loop_id"]) if a["loop_id"] else "untracked"
                if st in ("open", "nudged", "unknown", "untracked", "missing"):
                    open_acts.append({"action": a["action"], "owner": a["owner"],
                                      "due_hint": a["due_hint"],
                                      "loop_id": a["loop_id"],
                                      "loop_status": st})
            if open_acts:
                result.append({"meeting_id": m["id"], "title": m["title"],
                               "date": m["date"], "open_actions": open_acts})
    return {"ok": True, "meetings_with_open_actions": result,
            "count": sum(len(m["open_actions"]) for m in result)}


TOOL_DEFS = [
    {"name": "meetings.log",
     "description": ("Record a meeting {title, date?, attendees?, notes}. "
                     "Date is ISO, defaults to today."),
     "handler": log_handler, "risk": "low", "needs_network": False,
     "schema": {"title": "string", "date": "string?", "attendees": "list?",
                "notes": "string"}},
    {"name": "meetings.list",
     "description": "List meetings, newest first {n?}.",
     "handler": list_handler, "risk": "low", "needs_network": False,
     "schema": {"n": "int?"}},
    {"name": "meetings.actions",
     "description": ("Mine action items from a meeting's notes "
                     "{meeting_id, register?}. register=true tracks each as "
                     "a loops.track entry."),
     "handler": actions_handler, "risk": "low", "needs_network": False,
     "schema": {"meeting_id": "string", "register": "bool?"}},
    {"name": "meetings.open",
     "description": ("Meetings with unresolved action items, reading live "
                     "loop states. Unreadable loops are 'unknown', never "
                     "assumed resolved."),
     "handler": open_handler, "risk": "low", "needs_network": False,
     "schema": {}},
]

RISK_TABLE_ADDITIONS = {
    "meetings.log": ("low", False),
    "meetings.list": ("low", False),
    "meetings.actions": ("low", False),
    "meetings.open": ("low", False),
}


def register(reg) -> None:
    """Wire the four meetings pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
