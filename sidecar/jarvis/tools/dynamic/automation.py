"""Automation engine: named multi-step routines stored in SQLite, executed
through the main tool registry so every step re-passes the agent policy.

FUTURE WIRING NOTE: cron triggers are STORED in the routines table only.
The sidecar's existing reminder/timer loop can pick them up later; that
wiring is deliberately not done here.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..base import Tool
from ...config import DATA_DIR

# Config helper contract (owned by a sibling worker):
#   from .config import load_dynamic_config, section
#   section("automation", {"db_path": ""})
try:  # pragma: no cover - graceful if the parallel worker's file lands later
    from .config import section as _section_fn
except Exception:  # noqa: BLE001
    _section_fn = None  # type: ignore[assignment]


_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_MAX_STEPS = 50


def _automation_section() -> dict:
    """Resolve the automation config section, tolerating the helper's absence."""
    if _section_fn is not None:
        try:
            got = _section_fn("automation", {"db_path": ""})
            return dict(got) if got else {"db_path": ""}
        except Exception:  # noqa: BLE001
            pass
    return {"db_path": ""}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_cron(cron: str | None) -> bool:
    """Loose 5-field shape check: exactly 5 space-separated fields,
    each containing only cron-legal characters."""
    if cron is None:
        return True
    parts = cron.strip().split()
    if len(parts) != 5:
        return False
    allowed = set("0123456789*/,-")
    return all(part and set(part) <= allowed for part in parts)


class AutomationEngine:
    """SQLite-backed store of named routines.

    routines(name TEXT PRIMARY KEY, trigger TEXT, cron TEXT, steps_json TEXT,
             created_at TEXT). steps = [{"tool": str, "args": dict}].
    """

    def __init__(self, data_dir: Path | str | None = None, main_registry=None):
        if data_dir is not None:
            self.db_path = Path(data_dir) / "automation.db"
        else:
            configured = _automation_section().get("db_path") or ""
            self.db_path = Path(configured) if configured else (DATA_DIR / "automation.db")
        self.main_registry = main_registry
        self._lock = threading.Lock()
        self._init_db()

    # ----- storage -----
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS routines(
                     name TEXT PRIMARY KEY,
                     trigger TEXT NOT NULL DEFAULT 'manual',
                     cron TEXT,
                     steps_json TEXT NOT NULL,
                     created_at TEXT NOT NULL)"""
            )

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "name": row["name"],
            "trigger": row["trigger"],
            "cron": row["cron"],
            "steps": json.loads(row["steps_json"]),
            "created_at": row["created_at"],
        }

    # ----- public API -----
    def validate_routine(self, name: str, trigger: str, cron, steps) -> str | None:
        """Return an error message, or None if the routine args are valid."""
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return "name must match ^[a-z0-9_]{1,64}$"
        if trigger not in ("manual", "cron"):
            return "trigger must be 'manual' or 'cron'"
        if trigger == "cron" and not _valid_cron(cron):
            return "cron must be a 5-field expression (e.g. '0 9 * * *')"
        if cron is not None and not _valid_cron(cron):
            return "cron must be a 5-field expression (e.g. '0 9 * * *')"
        if not isinstance(steps, list) or not (1 <= len(steps) <= _MAX_STEPS):
            return f"steps must be a non-empty list of at most {_MAX_STEPS}"
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                return f"step {i}: must be an object"
            if not isinstance(step.get("tool"), str) or not step["tool"]:
                return f"step {i}: missing 'tool'"
            if not isinstance(step.get("args"), dict):
                return f"step {i}: missing 'args' dict"
        return None

    def create(self, name: str, trigger: str = "manual", cron=None, steps=()) -> dict:
        """Upsert a routine. Returns dict with error key or created/steps."""
        err = self.validate_routine(name, trigger, cron, steps)
        if err:
            return {"error": err}
        steps_json = json.dumps([{"tool": s["tool"], "args": s["args"]} for s in steps])
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO routines(name, trigger, cron, steps_json, created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     trigger=excluded.trigger, cron=excluded.cron,
                     steps_json=excluded.steps_json""",
                (name, trigger, cron, steps_json, _utcnow()),
            )
        return {"created": name, "steps": len(steps)}

    def get(self, name: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM routines WHERE name=?", (name,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM routines ORDER BY name").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete(self, name: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM routines WHERE name=?", (name,))
        return cur.rowcount > 0

    def names(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT name FROM routines ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    def run(self, name: str, confirmed: bool = False) -> dict:
        """Execute a routine's steps SEQUENTIALLY through the main registry.
        Never raises; stops on first failure or confirmation demand."""
        if self.main_registry is None:
            return {"error": "no main registry bound"}
        routine = self.get(name)
        if routine is None:
            return {"error": f"routine '{name}' not found"}
        results = []
        for i, step in enumerate(routine["steps"]):
            try:
                out = self.main_registry.call(
                    step["tool"], step["args"],
                    actor=f"automation:{name}", confirmed=confirmed)
            except Exception as e:  # registry misbehaving is not our crash
                out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if out.get("needs_confirmation") and not confirmed:
                results.append({
                    "tool": step["tool"], "ok": False,
                    "summary": out.get("confirm_text", "step needs confirmation")})
                return {"ok": False, "routine": name, "stopped_at": i,
                        "reason": "step needs confirmation", "results": results}
            ok = bool(out.get("ok"))
            payload = out.get("result") if ok else (out.get("error") or out)
            results.append({
                "tool": step["tool"], "ok": ok,
                "summary": str(payload)[:500]})
            if not ok:
                return {"ok": False, "routine": name, "stopped_at": i,
                        "reason": "step failed", "results": results}
        return {"routine": name, "completed": len(results), "results": results}


# ---------------------------------------------------------------------------
# Tool handlers: dict -> dict, never raise.
# ---------------------------------------------------------------------------

def _engine_from(args: dict) -> AutomationEngine:
    """Fresh engine view for handler calls (handlers must be dict->dict)."""
    return AutomationEngine(data_dir=None, main_registry=None)


def handle_create(args: dict) -> dict:
    try:
        eng = AutomationEngine()
        name = args.get("name", "")
        trigger = args.get("trigger", "manual")
        cron = args.get("cron")
        steps = args.get("steps")
        return eng.create(name, trigger=trigger, cron=cron, steps=steps or [])
    except Exception as e:  # never raise
        return {"error": f"{type(e).__name__}: {e}"}


def handle_run(args: dict) -> dict:
    try:
        eng = AutomationEngine()
        name = args.get("name", "")
        confirmed = bool(args.get("confirmed", False))
        return eng.run(name, confirmed=confirmed)
    except Exception as e:  # never raise
        return {"error": f"{type(e).__name__}: {e}"}


def handle_list(args: dict) -> dict:  # noqa: ARG001
    try:
        eng = AutomationEngine()
        return {"routines": [
            {"name": r["name"], "trigger": r["trigger"],
             "cron": r["cron"], "steps": r["steps"]}
            for r in eng.list()]}
    except Exception as e:  # never raise
        return {"error": f"{type(e).__name__}: {e}"}


def handle_describe(args: dict) -> dict:
    try:
        eng = AutomationEngine()
        routine = eng.get(args.get("name", ""))
        if routine is None:
            return {"error": f"routine '{args.get('name', '')}' not found"}
        return {"routine": routine}
    except Exception as e:  # never raise
        return {"error": f"{type(e).__name__}: {e}"}


def handle_delete(args: dict) -> dict:
    try:
        eng = AutomationEngine()
        if eng.delete(args.get("name", "")):
            return {"deleted": args.get("name")}
        return {"error": f"routine '{args.get('name', '')}' not found"}
    except Exception as e:  # never raise
        return {"error": f"{type(e).__name__}: {e}"}


def _make_run_named_handler(name: str):
    """Handler for the dynamic per-routine shortcut tool automation.run.<name>."""
    def handler(args: dict) -> dict:
        try:
            eng = AutomationEngine()
            return eng.run(name, confirmed=bool(args.get("confirmed", False)))
        except Exception as e:  # never raise
            return {"error": f"{type(e).__name__}: {e}"}
    handler.__name__ = f"handle_run_{name}"
    return handler


_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string"},
        "args": {"type": "object"},
    },
    "required": ["tool", "args"],
}

_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string",
                 "description": "Routine name, ^[a-z0-9_]{1,64}$"},
        "trigger": {"type": "string", "enum": ["manual", "cron"]},
        "cron": {"type": "string",
                 "description": "5-field cron expression (required when trigger=cron)"},
        "steps": {"type": "array", "items": _STEP_SCHEMA, "maxItems": 50},
    },
    "required": ["name", "steps"],
}

_RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "confirmed": {"type": "boolean",
                      "description": "Pass-through confirmation for confirm-risk steps"},
    },
    "required": ["name"],
}

_CONFIRMED_ONLY_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmed": {"type": "boolean",
                      "description": "Pass-through confirmation for confirm-risk steps"},
    },
}

STATIC_TOOLS: dict[str, Tool] = {
    "automation.create": Tool(
        name="automation.create",
        description="Create (or replace) a named multi-step routine. Steps run "
                    "sequentially through the main registry on automation.run.",
        schema=_CREATE_SCHEMA, handler=handle_create, risk="medium"),
    "automation.run": Tool(
        name="automation.run",
        description="Execute a named routine's steps sequentially. Stops on "
                    "first failure or confirmation demand.",
        schema=_RUN_SCHEMA, handler=handle_run, risk="high"),
    "automation.list": Tool(
        name="automation.list",
        description="List all stored routines (name, trigger, cron, steps).",
        schema={"type": "object", "properties": {}}, handler=handle_list,
        risk="low"),
    "automation.describe": Tool(
        name="automation.describe",
        description="Show the full definition of one routine.",
        schema={"type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"]},
        handler=handle_describe, risk="low"),
    "automation.delete": Tool(
        name="automation.delete",
        description="Delete a named routine.",
        schema={"type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"]},
        handler=handle_delete, risk="medium"),
}


def make_routine_tool(name: str) -> Tool:
    """Dynamic per-routine shortcut tool: automation.run.<name>."""
    return Tool(
        name=f"automation.run.{name}",
        description=f"Run the '{name}' routine (shortcut for automation.run).",
        schema=_CONFIRMED_ONLY_SCHEMA,
        handler=_make_run_named_handler(name),
        risk="high")


class AutomationProvider:
    """Dynamic tool provider for the automation engine.

    expand() = 5 static tools + live count of stored routines.
    """
    namespace = "automation"

    def __init__(self, data_dir: Path | str | None = None, main_registry=None):
        self.data_dir = data_dir
        self.main_registry = main_registry

    def _engine(self) -> AutomationEngine:
        return AutomationEngine(data_dir=self.data_dir,
                               main_registry=self.main_registry)

    def _routine_names(self) -> list[str]:
        try:
            return self._engine().names()
        except Exception:  # noqa: BLE001 - DB unreadable -> zero routines
            return []

    def expand(self) -> int:
        """5 static tools + one shortcut tool per stored routine (live DB read)."""
        return 5 + len(self._routine_names())

    def _bound_tools(self) -> dict[str, Tool]:
        """The 5 static tools bound to THIS provider's engine.

        The module-level STATIC_TOOLS handlers build a default
        AutomationEngine() (real DATA_DIR, no registry). Provider-resolved
        tools must honor the data_dir / main_registry passed to __init__
        (tests and build_dynamic_registry rely on this).
        """
        eng = self._engine()

        def h_create(args: dict) -> dict:
            a = args or {}
            try:
                return eng.create(
                    a.get("name", ""),
                    trigger=a.get("trigger", "manual"),
                    cron=a.get("cron"),
                    steps=a.get("steps") or [],
                )
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        def h_run(args: dict) -> dict:
            a = args or {}
            try:
                return eng.run(a.get("name", ""),
                               confirmed=bool(a.get("confirmed", False)))
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        def h_list(args: dict) -> dict:  # noqa: ARG001
            try:
                return {"routines": [
                    {"name": r["name"], "trigger": r["trigger"],
                     "cron": r["cron"], "steps": r["steps"]}
                    for r in eng.list()]}
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        def h_describe(args: dict) -> dict:
            a = args or {}
            try:
                routine = eng.get(a.get("name", ""))
                if routine is None:
                    return {"error": f"routine '{a.get('name', '')}' not found"}
                return {"routine": routine}
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        def h_delete(args: dict) -> dict:
            a = args or {}
            try:
                if eng.delete(a.get("name", "")):
                    return {"deleted": a.get("name", "")}
                return {"error": f"routine '{a.get('name', '')}' not found"}
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        bound = {
            "automation.create": ("automation.create", h_create, "medium"),
            "automation.run": ("automation.run", h_run, "high"),
            "automation.list": ("automation.list", h_list, "low"),
            "automation.describe": ("automation.describe", h_describe, "low"),
            "automation.delete": ("automation.delete", h_delete, "medium"),
        }
        tools: dict[str, Tool] = {}
        for key, (tname, handler, risk) in bound.items():
            static = STATIC_TOOLS[key]
            tools[key] = Tool(
                name=tname,
                description=static.description,
                schema=static.schema,
                handler=handler,
                risk=risk,
                needs_network=static.needs_network,
            )
        return tools

    def _bound_routine_tool(self, routine: str) -> Tool:
        eng = self._engine()

        def _run_named(args: dict, _eng=eng, _r=routine) -> dict:
            a = args or {}
            try:
                return _eng.run(_r, confirmed=bool(a.get("confirmed", False)))
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=f"automation.run.{routine}",
            description=f"Run the '{routine}' routine (shortcut for automation.run).",
            schema=_CONFIRMED_ONLY_SCHEMA,
            handler=_run_named,
            risk="high",
        )

    def resolve(self, name: str) -> Tool | None:
        tools = self._bound_tools()
        if name in tools:
            return tools[name]
        prefix = "automation.run."
        if name.startswith(prefix):
            routine = name[len(prefix):]
            if routine and routine in self._routine_names():
                return self._bound_routine_tool(routine)
        return None

    def sample_names(self, n: int = 5) -> list[str]:
        names = list(STATIC_TOOLS)
        names.extend(f"automation.run.{r}" for r in self._routine_names())
        return names[:n]
