"""Project workspaces pack (Phase 18): JARVIS owns work over weeks, not turns.

Durable project workspaces with a goal, milestones, deadlines, a progress
log, and linkage into the Phase 13 open-loop promise tracker so project
promises are followed through, not just recorded.

Tools:
    projects.create         low     Create a project {name, goal, deadline?,
                                promise?}. A ``promise`` also registers a
                                loops.track entry (lazy loops_pack import;
                                honest note when loops_pack is absent).
    projects.list           low     List projects {status?: active|archived|all}.
    projects.status         low     Full status: goal, deadline, milestone
                                progress %, overdue milestones, linked loop
                                state, recent log entries.
    projects.milestone_add  low     Add a milestone {project, title, due?}.
    projects.milestone_done low     Mark a milestone done {project, milestone_id}.
    projects.log            low     Append a progress-log entry
                                {project, entry, file?}.
    projects.archive        medium  Archive a finished project (reversible).
    projects.delete         high    Delete a project and all its data.
                                IRREVERSIBLE — needs confirmation.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/projects.db,
      overridable with the JARVIS_PROJECTS_DB env var (tests use a tmp file).
    - Promise linkage never fabricates: when loops_pack is missing, the
      promise is stored on the project and returned as an unregistered
      candidate instead of pretending it was tracked.

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    In sidecar/jarvis/tools/builtin/__init__.py, inside build_registry()::

        from . import projects_pack
        ... in the Phase 18 section:
        for _pack in (projects_pack, schedules_pack, meetings_pack, security_pack):
            _pack.register(reg)
            _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "projects.db"

_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_PROJECTS_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Schema is ensured on every connection: the DB path can be overridden
    # per-call via JARVIS_PROJECTS_DB (tests), so import-time setup is not
    # enough. CREATE TABLE IF NOT EXISTS is idempotent and cheap.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects(
             name TEXT PRIMARY KEY,
             goal TEXT NOT NULL,
             deadline_epoch INTEGER,
             status TEXT NOT NULL DEFAULT 'active',
             promise TEXT,
             created_at TEXT NOT NULL,
             archived_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS milestones(
             id TEXT PRIMARY KEY,
             project TEXT NOT NULL,
             title TEXT NOT NULL,
             due_epoch INTEGER,
             done INTEGER NOT NULL DEFAULT 0,
             done_at TEXT,
             created_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS project_log(
             id TEXT PRIMARY KEY,
             project TEXT NOT NULL,
             entry TEXT NOT NULL,
             file_path TEXT,
             created_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS project_loops(
             project TEXT PRIMARY KEY,
             loop_id TEXT NOT NULL,
             promise TEXT NOT NULL,
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


def _parse_deadline(raw):
    """ISO datetime/date or epoch -> (epoch_seconds | None, error | None)."""
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, "deadline must be an ISO datetime/date string or epoch seconds"
    if isinstance(raw, (int, float)):
        return int(raw), None
    if not isinstance(raw, str) or not raw.strip():
        return None, "deadline must be an ISO datetime/date string or epoch seconds"
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.split("+")[0].split(".")[0]
                                   if "%z" not in fmt else text, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None, (f"deadline {raw!r} is not a valid ISO datetime "
                          "(e.g. '2026-10-15' or '2026-10-15T09:00:00+05:30')")
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp()), None


def _valid_name(name) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return "name must be a non-empty string"
    name = name.strip()
    if len(name) > 80:
        return "name must be <= 80 characters"
    return None


def _get_project(name: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE name=?",
                           (name,)).fetchone()
        return dict(row) if row else None


def _fmt_deadline(epoch) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _register_promise(promise: str, deadline_epoch) -> dict:
    """Lazy loops_pack linkage. Never pretends registration happened."""
    try:
        from . import loops_pack  # noqa: PLC0415
    except ImportError:
        return {"registered": False,
                "reason": "loops_pack not installed — promise kept on the project only"}
    handler = getattr(loops_pack, "loops_track_handler", None)
    if not callable(handler):
        return {"registered": False,
                "reason": "loops_pack exposes no track handler — promise kept on the project only"}
    check = {"tool": "projects.status", "args": {}}
    out = handler({"promise": promise,
                   "deadline": deadline_epoch or int(time.time()) + 30 * 86400,
                   "check": check})
    if out.get("ok"):
        return {"registered": True, "loop_id": out.get("id")}
    return {"registered": False, "reason": out.get("error", "loops.track refused")}


@_safe
def create_handler(args: dict) -> dict:
    name = (args.get("name") or "").strip() if isinstance(args.get("name"), str) else ""
    err = _valid_name(args.get("name"))
    if err:
        return {"error": f"projects.create: {err}"}
    goal = args.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return {"error": "projects.create: goal must be a non-empty string"}
    goal = goal.strip()
    if len(goal) > 2000:
        return {"error": "projects.create: goal must be <= 2000 characters"}
    deadline_epoch, derr = _parse_deadline(args.get("deadline"))
    if derr:
        return {"error": f"projects.create: {derr}"}
    if _get_project(name) is not None:
        return {"error": f"projects.create: project {name!r} already exists"}

    promise = args.get("promise")
    promise = promise.strip() if isinstance(promise, str) and promise.strip() else None
    link = None
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO projects(name, goal, deadline_epoch, status, promise, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (name, goal, deadline_epoch, "active", promise, _now()))
        conn.commit()
    if promise:
        link = _register_promise(f"[project:{name}] {promise}", deadline_epoch)
        if link.get("registered"):
            with _LOCK, _connect() as conn:
                conn.execute(
                    "INSERT INTO project_loops(project, loop_id, promise, created_at)"
                    " VALUES(?,?,?,?)",
                    (name, link["loop_id"], promise, _now()))
                conn.commit()
    out = {"ok": True, "name": name, "goal": goal,
           "deadline": _fmt_deadline(deadline_epoch), "status": "active"}
    if promise:
        out["promise_link"] = link
    return out


@_safe
def list_handler(args: dict) -> dict:
    status = args.get("status", "active")
    if status not in ("active", "archived", "all"):
        return {"error": "projects.list: status must be active, archived, or all"}
    with _LOCK, _connect() as conn:
        if status == "all":
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        else:
            rows = conn.execute("SELECT * FROM projects WHERE status=? ORDER BY created_at",
                                (status,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        with _LOCK, _connect() as conn:
            ms = conn.execute("SELECT done FROM milestones WHERE project=?",
                              (d["name"],)).fetchall()
        total, done = len(ms), sum(1 for m in ms if m["done"])
        items.append({
            "name": d["name"], "goal": d["goal"],
            "deadline": _fmt_deadline(d["deadline_epoch"]),
            "status": d["status"],
            "milestones": {"total": total, "done": done},
            "created_at": d["created_at"],
        })
    return {"ok": True, "status_filter": status, "projects": items}


@_safe
def status_handler(args: dict) -> dict:
    name = args.get("name")
    err = _valid_name(name)
    if err:
        return {"error": f"projects.status: {err}"}
    name = name.strip()
    proj = _get_project(name)
    if proj is None:
        return {"error": f"projects.status: unknown project {name!r}"}
    with _LOCK, _connect() as conn:
        ms = [dict(r) for r in conn.execute(
            "SELECT * FROM milestones WHERE project=? ORDER BY created_at",
            (name,)).fetchall()]
        logs = [dict(r) for r in conn.execute(
            "SELECT * FROM project_log WHERE project=? ORDER BY created_at DESC LIMIT 5",
            (name,)).fetchall()]
        link = conn.execute("SELECT * FROM project_loops WHERE project=?",
                            (name,)).fetchone()
    now = int(time.time())
    milestones = []
    overdue = 0
    for m in ms:
        due = _fmt_deadline(m["due_epoch"])
        is_overdue = bool(m["due_epoch"] and not m["done"] and m["due_epoch"] < now)
        overdue += 1 if is_overdue else 0
        milestones.append({"id": m["id"], "title": m["title"], "due": due,
                           "done": bool(m["done"]), "done_at": m["done_at"],
                           "overdue": is_overdue})
    total, done = len(ms), sum(1 for m in ms if m["done"])
    loop_state = None
    if link:
        loop_state = {"loop_id": link["loop_id"], "promise": link["promise"]}
        try:
            from . import loops_pack  # noqa: PLC0415
            row_fn = getattr(loops_pack, "_loop_row", None)
            if callable(row_fn):
                row = row_fn(link["loop_id"])
                if row:
                    loop_state["loop_status"] = row.get("status")
        except Exception:  # honest: linkage state unreadable, don't fabricate
            loop_state["loop_status"] = "unknown"
    return {
        "ok": True,
        "name": proj["name"], "goal": proj["goal"],
        "deadline": _fmt_deadline(proj["deadline_epoch"]),
        "deadline_overdue": bool(proj["deadline_epoch"] and proj["deadline_epoch"] < now
                                 and proj["status"] == "active"),
        "status": proj["status"],
        "progress": {"milestones_total": total, "milestones_done": done,
                     "percent": round(100 * done / total) if total else None},
        "overdue_milestones": overdue,
        "milestones": milestones,
        "linked_loop": loop_state,
        "recent_log": [{"entry": l["entry"], "file": l["file_path"],
                        "at": l["created_at"]} for l in logs],
    }


@_safe
def milestone_add_handler(args: dict) -> dict:
    name = args.get("project")
    err = _valid_name(name)
    if err:
        return {"error": f"projects.milestone_add: project {err}"}
    name = name.strip()
    if _get_project(name) is None:
        return {"error": f"projects.milestone_add: unknown project {name!r}"}
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return {"error": "projects.milestone_add: title must be a non-empty string"}
    title = title.strip()
    if len(title) > 300:
        return {"error": "projects.milestone_add: title must be <= 300 characters"}
    due_epoch, derr = _parse_deadline(args.get("due"))
    if derr:
        return {"error": f"projects.milestone_add: {derr}"}
    mid = "ms_" + uuid.uuid4().hex[:8]
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO milestones(id, project, title, due_epoch, done, created_at)"
            " VALUES(?,?,?,?,0,?)",
            (mid, name, title, due_epoch, _now()))
        conn.commit()
    return {"ok": True, "project": name, "milestone_id": mid, "title": title,
            "due": _fmt_deadline(due_epoch)}


@_safe
def milestone_done_handler(args: dict) -> dict:
    name = args.get("project")
    err = _valid_name(name)
    if err:
        return {"error": f"projects.milestone_done: project {err}"}
    name = name.strip()
    mid = args.get("milestone_id")
    if not isinstance(mid, str) or not mid.strip():
        return {"error": "projects.milestone_done: milestone_id is required"}
    mid = mid.strip()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM milestones WHERE id=? AND project=?",
                           (mid, name)).fetchone()
        if row is None:
            return {"error": f"projects.milestone_done: unknown milestone {mid!r}"
                             f" in project {name!r}"}
        conn.execute("UPDATE milestones SET done=1, done_at=? WHERE id=?",
                     (_now(), mid))
        conn.commit()
    return {"ok": True, "project": name, "milestone_id": mid,
            "title": row["title"]}


@_safe
def log_handler(args: dict) -> dict:
    name = args.get("project")
    err = _valid_name(name)
    if err:
        return {"error": f"projects.log: project {err}"}
    name = name.strip()
    if _get_project(name) is None:
        return {"error": f"projects.log: unknown project {name!r}"}
    entry = args.get("entry")
    if not isinstance(entry, str) or not entry.strip():
        return {"error": "projects.log: entry must be a non-empty string"}
    entry = entry.strip()
    if len(entry) > 5000:
        return {"error": "projects.log: entry must be <= 5000 characters"}
    file_path = args.get("file")
    if file_path is not None:
        if not isinstance(file_path, str) or not file_path.strip():
            return {"error": "projects.log: file must be a non-empty string path"}
        file_path = file_path.strip()
        if not Path(file_path).exists():
            return {"error": f"projects.log: file not found: {file_path!r}"}
    lid = "pl_" + uuid.uuid4().hex[:8]
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO project_log(id, project, entry, file_path, created_at)"
            " VALUES(?,?,?,?,?)",
            (lid, name, entry, file_path, _now()))
        conn.commit()
    return {"ok": True, "project": name, "log_id": lid}


@_safe
def archive_handler(args: dict) -> dict:
    name = args.get("name")
    err = _valid_name(name)
    if err:
        return {"error": f"projects.archive: {err}"}
    name = name.strip()
    proj = _get_project(name)
    if proj is None:
        return {"error": f"projects.archive: unknown project {name!r}"}
    if proj["status"] == "archived":
        return {"ok": True, "name": name, "status": "archived",
                "note": "already archived"}
    with _LOCK, _connect() as conn:
        conn.execute("UPDATE projects SET status='archived', archived_at=? WHERE name=?",
                     (_now(), name))
        conn.commit()
    return {"ok": True, "name": name, "status": "archived"}


@_safe
def delete_handler(args: dict) -> dict:
    name = args.get("name")
    err = _valid_name(name)
    if err:
        return {"error": f"projects.delete: {err}"}
    name = name.strip()
    if _get_project(name) is None:
        return {"error": f"projects.delete: unknown project {name!r}"}
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM project_loops WHERE project=?", (name,))
        conn.execute("DELETE FROM project_log WHERE project=?", (name,))
        conn.execute("DELETE FROM milestones WHERE project=?", (name,))
        conn.execute("DELETE FROM projects WHERE name=?", (name,))
        conn.commit()
    return {"ok": True, "name": name, "deleted": True,
            "note": "project, milestones, log, and loop links deleted"}


TOOL_DEFS = [
    {"name": "projects.create",
     "description": ("Create a durable project workspace {name, goal, deadline?, "
                     "promise?}. A promise also registers a loops.track entry "
                     "so the project is followed through."),
     "handler": create_handler, "risk": "low", "needs_network": False,
     "schema": {"name": "string", "goal": "string", "deadline": "string?",
                "promise": "string?"}},
    {"name": "projects.list",
     "description": "List project workspaces {status?: active|archived|all}.",
     "handler": list_handler, "risk": "low", "needs_network": False,
     "schema": {"status": "string?"}},
    {"name": "projects.status",
     "description": ("Full project status: milestones, progress %, overdue "
                     "items, linked promise-loop state, recent log {name}."),
     "handler": status_handler, "risk": "low", "needs_network": False,
     "schema": {"name": "string"}},
    {"name": "projects.milestone_add",
     "description": "Add a milestone to a project {project, title, due?}.",
     "handler": milestone_add_handler, "risk": "low", "needs_network": False,
     "schema": {"project": "string", "title": "string", "due": "string?"}},
    {"name": "projects.milestone_done",
     "description": "Mark a project milestone done {project, milestone_id}.",
     "handler": milestone_done_handler, "risk": "low", "needs_network": False,
     "schema": {"project": "string", "milestone_id": "string"}},
    {"name": "projects.log",
     "description": ("Append a progress-log entry to a project "
                     "{project, entry, file?}. The file must exist."),
     "handler": log_handler, "risk": "low", "needs_network": False,
     "schema": {"project": "string", "entry": "string", "file": "string?"}},
    {"name": "projects.archive",
     "description": "Archive a finished project (reversible) {name}.",
     "handler": archive_handler, "risk": "medium", "needs_network": False,
     "schema": {"name": "string"}},
    {"name": "projects.delete",
     "description": ("Delete a project and all its data. IRREVERSIBLE — "
                     "needs confirmation {name}."),
     "handler": delete_handler, "risk": "high", "needs_network": False,
     "schema": {"name": "string"}},
]

RISK_TABLE_ADDITIONS = {
    "projects.create": ("low", False),
    "projects.list": ("low", False),
    "projects.status": ("low", False),
    "projects.milestone_add": ("low", False),
    "projects.milestone_done": ("low", False),
    "projects.log": ("low", False),
    "projects.archive": ("medium", False),
    "projects.delete": ("high", False),
}


def register(reg) -> None:
    """Wire the eight projects pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
