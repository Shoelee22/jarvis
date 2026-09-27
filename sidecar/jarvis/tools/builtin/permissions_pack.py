"""Permission Grants pack (Phase 11, workstream 1): sqlite-backed per-tool
permission levels.

Each grant row says, for one tool (or a whole namespace, or everything):
    allow = auto-confirm even high-risk tools (fail-open override)
    ask   = force confirmation even for low-risk tools (paranoid)
    deny  = refuse with a clear message (fail-closed — always wins)

Precedence for ``get_level(tool)`` (the module-level API the parent's
policy hook uses — parent edits agent/policy.py, NOT this file):

    exact tool match > ``namespace.*`` > ``*`` > default > None

None means "no row and no default": fall back to the normal policy engine
behavior (low/medium allowed, high confirms, unknown denied). Unset is NOT
allow-everything — fail-closed.

``get_level`` is called on EVERY tool call, so it must be cheap: results are
kept in a small in-process cache dict, invalidated on any grant/revoke/
set_default (and on DB-path change). Import-safe: top-level imports are
stdlib only; ``from ..base import Tool`` happens only inside ``register()``;
this module NEVER imports agent.policy (the parent imports this lazily from
policy.py, so a top-level policy import would be circular).

SAFETY:
    - permissions.grant and permissions.set_default are HIGH risk: granting
      `allow`, especially on a wildcard or a high-risk tool, goes through
      the normal policy confirmation flow like any other high-risk tool.
      Wildcard grants are LOUD: the handler returns a `warning` field naming
      how many known tools the wildcard covers.
    - Handlers take dict -> return dict and never raise.
    - State lives in sqlite (stdlib only):
      ~/workspace/jarvis/data/permissions.db, overridable with the
      JARVIS_PERMISSIONS_DB env var (tests use a tmp file).
"""
from __future__ import annotations

import fnmatch
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "permissions.db"

LEVELS = ("allow", "ask", "deny")
DEFAULT_LEVELS = ("allow", "ask", "deny", "unset")

# Valid tool pattern: "*" | "namespace.*" | dotted tool name like "web.search"
_DOTTED_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]*(?:\.[A-Za-z0-9_][A-Za-z0-9_\-]*)*$")


def _validate_pattern(pattern: str) -> str | None:
    """Return the normalized pattern, or None when invalid."""
    p = (pattern or "").strip()
    if not p:
        return None
    if p == "*":
        return p
    if p.endswith(".*"):
        ns = p[:-2]
        return p if _DOTTED_RE.match(ns) else None
    return p if _DOTTED_RE.match(p) else None


# ---------------------------------------------------------------------------
# SQLite store (single cached connection, check_same_thread=False, one lock)
# ---------------------------------------------------------------------------
_DB_LOCK = threading.Lock()
_store_cache: dict = {"path": None, "conn": None}

# Snapshot of known tool names captured at register() time, used only to
# count how many tools a wildcard grant covers for the loud warning.
_TOOL_NAMES_SNAPSHOT: list[str] = []

# Small in-process cache: tool name -> effective level (or None = unset).
# Invalidated on any write (grant/revoke/set_default) and on DB-path change.
_level_cache: dict = {"path": None, "levels": {}}


def _db_path() -> Path:
    override = os.environ.get("JARVIS_PERMISSIONS_DB")
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
            conn.execute("""CREATE TABLE IF NOT EXISTS grants(
                pattern TEXT PRIMARY KEY, level TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'unknown',
                created_ts INTEGER NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            conn.commit()
            _store_cache.update(path=str(path), conn=conn)
        return _store_cache["conn"]


def _exec(fn, *args, **kwargs):
    """Run fn(conn, ...) under the global lock; commit; invalidate cache."""
    conn = _get_store()
    with _DB_LOCK:
        out = fn(conn, *args, **kwargs)
        conn.commit()
        _level_cache["levels"].clear()
        _level_cache["path"] = str(_db_path())
        return out


def _read(fn, *args, **kwargs):
    """Read path without invalidating the level cache."""
    conn = _get_store()
    with _DB_LOCK:
        return fn(conn, *args, **kwargs)


def _default_level(conn) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key='default_level'").fetchone()
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Module-level API for the parent's policy hook
# ---------------------------------------------------------------------------
def _resolve(conn, tool: str) -> tuple[str | None, str | None]:
    """Return (level, matched_rule). matched_rule is 'exact', 'namespace.*',
    '*', 'default', or None."""
    # Exact match first.
    row = conn.execute("SELECT level FROM grants WHERE pattern=?", (tool,)).fetchone()
    if row:
        return row["level"], "exact"
    # Namespace wildcard: longest namespace wins (e.g. "web.search" matches
    # "web.*"; if both "web.*" and "web.search.*" existed the longer wins).
    best: tuple[str | None, str | None] = (None, None)
    for r in conn.execute("SELECT pattern, level FROM grants WHERE pattern LIKE '%.*'"):
        ns = r["pattern"][:-2]
        if tool.startswith(ns + ".") and (best[0] is None or len(ns) > len(best[0])):
            best = (ns, r["level"])
    if best[1] is not None:
        return best[1], best[0] + ".*"
    # Global wildcard.
    row = conn.execute("SELECT level FROM grants WHERE pattern='*'").fetchone()
    if row:
        return row["level"], "*"
    # Fallback default.
    default = _default_level(conn)
    if default is not None:
        return default, "default"
    return None, None


def get_level(tool: str) -> str | None:
    """Effective permission level for *tool*: 'allow' | 'ask' | 'deny'.

    Returns None when no grant row and no default match — meaning: use the
    normal policy engine behavior (fail-closed, NOT allow-everything).

    Precedence: exact match > ``namespace.*`` > ``*`` > default > None.
    An explicit ``deny`` at any level always wins over looser rows beneath it
    because higher-precedence rows are checked first.

    Cheap: cached in-process; the cache is invalidated on every write and
    whenever the DB path changes.
    """
    tool = (tool or "").strip()
    if not tool:
        return None
    # NB: _get_store() must be called OUTSIDE the lock — it acquires
    # _DB_LOCK itself (threading.Lock is not reentrant).
    conn = _get_store()
    with _DB_LOCK:
        path = str(_db_path())
        if _level_cache["path"] != path:
            _level_cache["path"] = path
            _level_cache["levels"].clear()
        cache = _level_cache["levels"]
        if tool in cache:
            return cache[tool]
        level, _ = _resolve(conn, tool)
        cache[tool] = level
        return level


# ---------------------------------------------------------------------------
# Handlers (dict -> dict, never raise)
# ---------------------------------------------------------------------------
def _wrap(fn):
    def _h(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # never let a permissions tool crash the loop
            return {"error": f"{type(e).__name__}: {e}"}

    return _h


def _count_wildcard_coverage(pattern: str) -> tuple[int, str]:
    """How many known tools a wildcard pattern covers, and where from."""
    names = _TOOL_NAMES_SNAPSHOT
    source = "registered-tool snapshot"
    if not names:
        # Honest fallback when the pack was never registered (e.g. unit
        # tests calling handlers directly): count existing grant rows that
        # the wildcard would cover.
        names = [r["pattern"] for r in
                 _read(lambda c: c.execute("SELECT pattern FROM grants").fetchall())
                 if not r["pattern"].endswith(".*")]
        source = "existing grant rows (pack not registered; full tool universe unknown)"
    n = sum(1 for name in names if fnmatch.fnmatchcase(name, pattern))
    return n, source


@_wrap
def grant_handler(args: dict) -> dict:
    pattern = _validate_pattern(args.get("tool") or "")
    level = (args.get("level") or "").strip().lower()
    actor = (args.get("actor") or "unknown").strip() or "unknown"

    if pattern is None:
        return {"error": (f"bad tool pattern {args.get('tool')!r}: must be a dotted "
                          "tool name (e.g. 'web.search'), a namespace wildcard "
                          "(e.g. 'web.*'), or '*' for everything")}
    if level not in LEVELS:
        return {"error": f"bad level {args.get('level')!r}: must be one of {list(LEVELS)}"}

    created_ts = int(time.time())

    def _upsert(conn):
        conn.execute(
            "INSERT INTO grants(pattern, level, actor, created_ts) VALUES(?,?,?,?) "
            "ON CONFLICT(pattern) DO UPDATE SET level=excluded.level, "
            "actor=excluded.actor, created_ts=excluded.created_ts",
            (pattern, level, actor, created_ts))

    _exec(_upsert)

    result: dict = {"ok": True, "tool": pattern, "level": level,
                    "actor": actor, "created_ts": created_ts}
    if pattern == "*" or pattern.endswith(".*"):
        n, source = _count_wildcard_coverage(pattern)
        result["warning"] = (
            f"WILDCARD GRANT: {pattern!r} with level {level!r} covers {n} "
            f"known tools ({source}). Use an exact tool name to scope this down. "
            + ("This grant lets those tools run WITHOUT confirmation."
               if level == "allow" else
               "This grant FORCES confirmation on those tools." if level == "ask"
               else "Those tools will be REFUSED outright."))
        result["covered_tools"] = n
    if level == "allow" and pattern not in ("*",) and not pattern.endswith(".*"):
        result["note"] = ("allow on %r auto-confirms it even when the policy "
                          "engine would normally ask first") % pattern
    return result


@_wrap
def revoke_handler(args: dict) -> dict:
    pattern = _validate_pattern(args.get("tool") or "")
    if pattern is None:
        return {"error": f"bad tool pattern {args.get('tool')!r}"}

    def _delete(conn):
        row = conn.execute("SELECT 1 FROM grants WHERE pattern=?", (pattern,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM grants WHERE pattern=?", (pattern,))
        return True

    if not _exec(_delete):
        return {"error": f"no grant exists for {pattern!r}"}
    return {"ok": True, "tool": pattern, "revoked": True}


@_wrap
def list_handler(args: dict) -> dict:
    def _all(conn):
        rows = conn.execute(
            "SELECT pattern, level, actor, created_ts FROM grants ORDER BY pattern"
        ).fetchall()
        return [dict(r) for r in rows], _default_level(conn)

    grants, default = _read(_all)
    return {"grants": grants,
            "default": default if default is not None else "unset",
            "default_meaning": ("unset = normal policy behavior: low/medium tools "
                                "allowed, high-risk tools ask for confirmation")}


@_wrap
def set_default_handler(args: dict) -> dict:
    level = (args.get("level") or "").strip().lower()
    actor = (args.get("actor") or "unknown").strip() or "unknown"
    if level not in DEFAULT_LEVELS:
        return {"error": f"bad level {args.get('level')!r}: must be one of {list(DEFAULT_LEVELS)}"}

    def _set(conn):
        if level == "unset":
            conn.execute("DELETE FROM settings WHERE key='default_level'")
            return None
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('default_level', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (level,))
        return level

    stored = _exec(_set)
    result: dict = {"ok": True, "default": "unset" if stored is None else stored,
                    "actor": actor}
    if stored == "ask":
        result["note"] = ("paranoid mode: EVERY tool call not covered by a grant "
                          "now requires confirmation, including low-risk ones")
    if stored == "allow":
        result["warning"] = ("DANGEROUS: default allow auto-confirms every tool "
                             "not covered by a grant, including high-risk ones")
    if stored == "deny":
        result["note"] = ("fail-closed: every tool not covered by a grant is "
                          "refused outright")
    return result


@_wrap
def check_handler(args: dict) -> dict:
    tool = (args.get("tool") or "").strip()
    if not tool:
        return {"error": "tool is required"}

    def _q(conn):
        return _resolve(conn, tool)

    level, matched = _read(_q)
    return {"tool": tool, "level": level if level is not None else "unset",
            "matched_rule": matched if matched is not None else "none",
            "meaning": ({"allow": "auto-confirm even high-risk",
                         "ask": "force confirmation even low-risk",
                         "deny": "refused outright",
                         "unset": "normal policy behavior (low/medium allowed, "
                                  "high confirms)"}[level if level is not None else "unset"])}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "permissions.grant",
     "description": ("Set a permission level for a tool: level in {allow, ask, deny}. "
                     "'allow' auto-confirms even high-risk tools; 'ask' forces "
                     "confirmation even for low-risk ones; 'deny' refuses outright "
                     "and always wins. Accepts exact dotted tool names ('web.search'), "
                     "namespace wildcards ('web.*'), or '*' for everything. Wildcard "
                     "grants are logged loudly. High risk — needs confirmation."),
     "handler": grant_handler, "risk": "high", "needs_network": False,
     "schema": {"tool": "string", "level": "string", "actor?": "string"}},
    {"name": "permissions.revoke",
     "description": "Remove the exact grant row for a tool/pattern (exact match only).",
     "handler": revoke_handler, "risk": "low", "needs_network": False,
     "schema": {"tool": "string", "actor?": "string"}},
    {"name": "permissions.list",
     "description": ("List all grant rows (pattern, level, actor, created_ts) plus the "
                     "effective default. Default 'unset' = normal policy behavior."),
     "handler": list_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "permissions.set_default",
     "description": ("Set the fallback level when no grant matches: {allow, ask, deny, "
                     "unset}. 'ask' = paranoid mode (everything confirms); 'unset' "
                     "(default) = normal policy behavior. High risk — needs "
                     "confirmation, especially default 'allow'."),
     "handler": set_default_handler, "risk": "high", "needs_network": False,
     "schema": {"level": "string", "actor?": "string"}},
    {"name": "permissions.check",
     "description": ("Show the effective level for a tool: exact match > 'namespace.*' "
                     "> '*' > default. 'unset' = normal policy behavior."),
     "handler": check_handler, "risk": "low", "needs_network": False,
     "schema": {"tool": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(permissions_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "permissions.grant": ("high", False),
    "permissions.revoke": ("low", False),
    "permissions.list": ("low", False),
    "permissions.set_default": ("high", False),
    "permissions.check": ("low", False),
}


def register(reg) -> None:
    """Wire the five Permission Grants tools into a Registry."""
    from ..base import Tool
    global _TOOL_NAMES_SNAPSHOT
    try:
        _TOOL_NAMES_SNAPSHOT = list(getattr(reg, "tools", {}).keys())
    except Exception:
        _TOOL_NAMES_SNAPSHOT = []
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
