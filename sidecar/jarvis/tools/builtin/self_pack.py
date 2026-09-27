"""Self-diagnosis and safe self-upgrade pack (Phase 13, workstream B).

Bounded recursive self-improvement: JARVIS mines its own audit log for
problems, drafts hardened replacements for tool handlers, sandbox-tests
them, and queues the swap as an autopilot proposal. The upgrade is NEVER
applied here — activation happens only through the user's approval path.

TOOLS
    ``self.diagnose`` (low) — mine the audit log and return ranked problem
    lists with evidence counts:
      * error_rank: tools with the most errors (name, error count, call
        count, error rate, sample error)
      * confirmation_friction: tools whose calls most often hit confirmation
        gates or policy denials (friction)
      * repeated_calls: same tool + similar args called 3+ times (the user
        re-asking the same question)
    If nothing is problematic the tool says so honestly — no fake findings.
    Honest schema note: the audit log records the policy verdict's *reason*
    text and the result summary, but not a separate verdict-action column, so
    "denials" are policy-deny reasons matched by text ("default deny",
    "denies"), and "confirmation gates" are "awaiting confirmation" rows.
    User denials of a confirmation prompt are not recorded by the audit
    schema and therefore cannot be mined — this is reported, not faked.

    ``self.upgrade`` {target, example_input?, tool?, changes?} (HIGH, needs
    confirmation) — draft a hardened replacement for one tool handler.
    ``target`` is either a module path inside the jarvis package with an
    optional ::function suffix (``tools/builtin/fs_tools.py::read``) or a
    registered tool name (``fs.read``; needs the agent bound, see below).
    The drafter is MECHANICAL, not an LLM: it preserves the original bytes
    verbatim and generates a hardening wrapper (args must be a dict, any
    exception from the original is contained into a structured {"error"}
    return, non-dict returns are normalized). Behavior is otherwise
    unchanged — this is deliberate: semantic fixes stay with the agent loop.
    HARD RULES (enforced in code):
      (a) FORBIDDEN targets are refused with a named rule:
          - rule FORBIDDEN-DIR: anything under agent/ or security/
          - rule FORBIDDEN-NAME: anything named policy, permissions_pack,
            autonomy_pack, or self_pack (path or bare name)
          - rule PATH-JAIL: the target must resolve to a .py file inside
            the jarvis package (no absolute paths, no ".." escapes)
      (b) the new version is written ONLY to
          tools/forged/upgrades/<slug>_v<version>.py — the original file is
          never modified (its bytes are asserted unchanged in tests);
      (c) the candidate is sandbox-tested with the same import-isolated
          run() technique tools.test_forged uses (importlib
          spec_from_file_location, call run(example_input), require a dict
          and no raise). test_forged's own manifest lookup cannot address
          upgrade files — they are not forged tools and must never be
          manifest-activated — so the identical sandbox mechanics are
          applied directly. If the test fails, NOTHING is proposed;
      (d) on a passing test the swap is queued via autopilot.propose with
          the goal "upgrade ready for <target>: <what changed>. Approve to
          activate?" — a single audit-trail step (notes.add) records the
          approval; the handler swap itself is applied by the parent ONLY
          after the user approves, never here.
    Every upgrade is versioned (v1, v2, ...) per target; the original bytes
    are preserved under tools/forged/upgrades/_originals/ for rollback.

    ``self.rollback`` {upgrade_id} (medium) — one-tap revert. Restores the
    original handler registration from the preserved copy (when the agent
    is bound and the upgrade was active), marks the upgrade reverted in
    sqlite. Honest errors for unknown ids and already-reverted upgrades.

STATE
    sqlite table self_upgrades(id, target, version, path, status, created_at,
    test_report) at ~/workspace/jarvis/data/self.db, overridable with the
    JARVIS_SELF_DB env var (tests use a tmp file). Originals preserved under
    tools/forged/upgrades/_originals/. A <slug>_v<version>.meta.json sits next
    to each upgrade file with {upgrade_id, target, version, tool, func,
    original_sha, original_path, proposal_id}.

SAFETY
    - Handlers take dict -> return dict and never raise.
    - forge_pack and autopilot_pack are imported LAZILY inside handlers (no
      top-level cycles); the audit DB path is resolved lazily too.
    - self.upgrade is high risk: the policy engine will ask for confirmation
      before it runs, and even then it only drafts + tests + proposes.

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    In sidecar/jarvis/tools/builtin/__init__.py, add self_pack to the
    Phase-12 import list and to the registration loop::

        for _pack in (voice_pack, vision_pack, mind_pack, shell_pack,
                      teach_pack, self_pack):
            _pack.register(reg)
            _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    No policy.py change is needed beyond that RISK_TABLE update.
    Optional, in sidecar/jarvis/ipc/server.py after bind_agent(agent)::

        from ..tools.builtin import self_pack
        self_pack.bind_agent(agent)

    Binding enables tool-name targets (``self.upgrade {"target": "fs.read"}``)
    and live handler restoration on ``self.rollback``. Without it, both
    report honestly instead of failing silently.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/teach_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _pkg_root() -> Path:
    """Root of the jarvis package; JARVIS_SELF_PKGROOT exists for tests."""
    override = os.environ.get("JARVIS_SELF_PKGROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent


def _audit_db_path() -> Path:
    override = os.environ.get("JARVIS_AUDIT_DB")
    if override:
        return Path(override)
    try:
        from ..config import DATA_DIR  # noqa: PLC0415  (lazy: no import cycle)
    except Exception:
        DATA_DIR = Path.home() / ".jarvis"
    return Path(DATA_DIR) / "jarvis.db"


def _self_db_path() -> Path:
    override = os.environ.get("JARVIS_SELF_DB")
    if override:
        return Path(override)
    return JARVIS_DIR / "data" / "self.db"


def _upgrades_dir() -> Path:
    """tools/forged/upgrades/. Lazy forge_pack import: no top-level cycle,
    and JARVIS_FORGED_DIR keeps working so tests never touch the real dir."""
    from . import forge_pack  # noqa: PLC0415

    d = forge_pack._forged_dir() / "upgrades"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _originals_dir() -> Path:
    d = _upgrades_dir() / "_originals"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# sqlite state (stdlib only)
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    path = _self_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS self_upgrades("
        " id TEXT PRIMARY KEY,"
        " target TEXT NOT NULL,"
        " version INTEGER NOT NULL,"
        " path TEXT NOT NULL,"
        " status TEXT NOT NULL,"
        " created_at TEXT NOT NULL,"
        " test_report TEXT NOT NULL)"
    )
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Target validation — the hard rules
# ---------------------------------------------------------------------------
FORBIDDEN_DIR_PREFIXES = ("agent", "security")
FORBIDDEN_STEMS = {"policy", "permissions_pack", "autonomy_pack", "self_pack"}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _forbidden_check(rel_posix: str) -> str | None:
    """Return the refusal error string, or None if the target is allowed."""
    parts = [p for p in rel_posix.split("/") if p not in ("", ".")]
    if parts and parts[0].lower() in FORBIDDEN_DIR_PREFIXES:
        return (
            f"self.upgrade refused (rule FORBIDDEN-DIR): '{rel_posix}' is under "
            f"'{parts[0]}/' — the agent core and security layers can never be "
            "self-upgraded"
        )
    for p in parts:
        stem = p[:-3] if p.lower().endswith(".py") else p
        if stem.lower() in FORBIDDEN_STEMS:
            return (
                f"self.upgrade refused (rule FORBIDDEN-NAME): '{stem}' is "
                "safety-critical (policy/permissions/autonomy/self) and can "
                "never be self-upgraded"
            )
    return None


def _jail_resolve(rel_posix: str) -> tuple[Path | None, str | None]:
    """Resolve rel_posix under the package root; refuse escapes/non-py."""
    if not rel_posix.lower().endswith(".py"):
        return None, (
            f"self.upgrade refused (rule PATH-JAIL): target must be a .py "
            f"module path inside the jarvis package (got '{rel_posix}')"
        )
    root = _pkg_root().resolve()
    candidate = (root / rel_posix).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, (
            f"self.upgrade refused (rule PATH-JAIL): '{rel_posix}' escapes the "
            "jarvis package — absolute paths and '..' are not allowed"
        )
    if candidate == root:
        return None, (
            "self.upgrade refused (rule PATH-JAIL): target must be a module "
            "file, not the package root"
        )
    return candidate, None


def _module_functions(source_path: Path) -> tuple[list[str] | None, str | None]:
    """Module-level public function names from ast (no import)."""
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        return None, f"cannot parse '{source_path.name}': {e}"
    names = [n.name for n in tree.body
             if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
    return names, None


def _resolve_tool_target(tool_name: str, func: str | None):
    """Resolve a registered tool name -> (rel_posix, func, tool_name)."""
    if _AGENT is None:
        return None, (
            f"self.upgrade: tool-name target '{tool_name}' needs the live agent "
            "(server.py must call self_pack.bind_agent(agent)); otherwise use a "
            "module path like 'tools/builtin/fs_tools.py::read'"
        )
    registry = getattr(_AGENT, "registry", None)
    if registry is None or tool_name not in getattr(registry, "tools", {}):
        return None, (
            f"self.upgrade: unknown tool '{tool_name}' — use a module path like "
            "'tools/builtin/fs_tools.py::read'"
        )
    handler = registry.tools[tool_name].handler
    module = sys.modules.get(getattr(handler, "__module__", "") or "")
    src = getattr(module, "__file__", None)
    if not src:
        return None, (
            f"self.upgrade: cannot locate the source file behind tool "
            f"'{tool_name}'"
        )
    root = _pkg_root().resolve()
    try:
        rel = Path(src).resolve().relative_to(root).as_posix()
    except ValueError:
        return None, (
            f"self.upgrade refused (rule PATH-JAIL): tool '{tool_name}' lives "
            "outside the jarvis package"
        )
    err = _forbidden_check(rel)
    if err:
        return None, err
    fname = func or getattr(handler, "__name__", None)
    if not fname:
        return None, (
            f"self.upgrade: cannot determine the handler function for tool "
            f"'{tool_name}' — pass 'path/to/module.py::func'"
        )
    names, perr = _module_functions(_pkg_root() / rel)
    if perr:
        return None, f"self.upgrade: {perr}"
    if fname not in (names or []):
        return None, (
            f"self.upgrade: '{fname}' is not a module-level function of '{rel}'"
        )
    return (rel, fname, tool_name), None


def _normalize_target(target, func_hint: str | None = None):
    """-> ((rel_posix, func, tool_name|None), None) or (None, error).

    Accepted forms:
      * "tools/builtin/fs_tools.py::read" (path form, ::func optional)
      * "tools/builtin/fs_tools.py"       (single public function, else error)
      * "fs.read"                         (registered tool name; needs agent)
      * "permissions_pack" / "self_pack"  (bare names -> FORBIDDEN-NAME)
    """
    if not isinstance(target, str) or not target.strip():
        return None, ("self.upgrade: target must be a non-empty string like "
                      "'tools/builtin/fs_tools.py::read'")
    t = target.strip().replace("\\", "/")
    func = func_hint
    if "::" in t:
        t, fpart = t.split("::", 1)
        fpart = fpart.strip()
        if not fpart:
            return None, "self.upgrade: empty function name after '::'"
        func = fpart
    t = t.strip()
    while t.startswith("./"):
        t = t[2:]

    # Bare-name form (also catches "permissions_pack", "self_pack").
    if "/" not in t and not t.lower().endswith(".py"):
        if t.lower() in FORBIDDEN_STEMS:
            return None, _forbidden_check(t)
        return _resolve_tool_target(t, func)

    # Path form.
    if not t.lower().endswith(".py") and "/" in t:
        t = t + ".py"
    err = _forbidden_check(t)
    if err:
        return None, err
    resolved, jerr = _jail_resolve(t)
    if jerr:
        return None, jerr
    if not resolved.is_file():
        return None, f"self.upgrade: no such module '{t}' in the jarvis package"
    names, perr = _module_functions(resolved)
    if perr:
        return None, f"self.upgrade: {perr}"
    rel = resolved.relative_to(_pkg_root().resolve()).as_posix()
    if func:
        if func not in names:
            return None, (
                f"self.upgrade: '{func}' is not a module-level function of "
                f"'{rel}' (candidates: {', '.join(names) or 'none'})"
            )
    else:
        if len(names) == 1:
            func = names[0]
        else:
            return None, (
                f"self.upgrade: '{rel}' defines {len(names)} public functions; "
                f"pick one with '::func' (candidates: {', '.join(names) or 'none'})"
            )
    return (rel, func, None), None


def _slug(rel_posix: str, func: str) -> str:
    return _SLUG_RE.sub("_", f"{rel_posix}::{func}".lower()).strip("_")


# ---------------------------------------------------------------------------
# Upgrade drafting (mechanical hardening wrapper — no LLM, no exec/eval)
# ---------------------------------------------------------------------------
_UPGRADE_TEMPLATE = '''"""Self-upgrade v@@VERSION@@ for @@TARGET@@.

Drafted by self.upgrade (Phase 13 workstream B) at @@DRAFTED_AT@@.
What changed: @@CHANGES@@
Original preserved at: @@ORIGINAL_PATH@@ (sha256 @@ORIGINAL_SHA@@)

Contract: run(args) -> dict, never raises. Loads the preserved original
handler and applies a hardening wrapper: args must be a dict, any exception
from the original is contained into a structured {"error"} return, and
non-dict returns are normalized to an error. Behavior is otherwise unchanged.
This file is INACTIVE until a user approves its autopilot proposal; the swap
is applied by the parent only after approval, never by self.upgrade.
"""

import importlib.util

_TARGET = "@@TARGET@@"
_VERSION = @@VERSION@@
_FUNC_NAME = "@@FUNC@@"
_ORIGINAL_PATH = @@ORIGINAL_PATH_JSON@@
_ORIGINAL_SHA = "@@ORIGINAL_SHA@@"


def _load_original():
    """Load the preserved original module and return the original function."""
    spec = importlib.util.spec_from_file_location(
        "jarvis_self_upgrade_original", _ORIGINAL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load preserved original: " + _ORIGINAL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, _FUNC_NAME, None)
    if not callable(fn):
        raise AttributeError(
            "preserved original has no callable %r" % (_FUNC_NAME,))
    return fn


def run(args):
    """Hardened replacement handler. dict -> dict, never raises."""
    try:
        if not isinstance(args, dict):
            return {"error": ("self-upgrade v%s: args must be a dict, got %s"
                              % (_VERSION, type(args).__name__)),
                    "target": _TARGET}
        try:
            original = _load_original()
        except Exception as e:
            return {"error": ("self-upgrade v%s: preserved original failed to "
                              "load: %s: %s" % (_VERSION, type(e).__name__, e)),
                    "target": _TARGET}
        try:
            out = original(dict(args))
        except Exception as e:
            return {"error": "%s: %s" % (type(e).__name__, e),
                    "target": _TARGET,
                    "contained_by": "self-upgrade v%s" % (_VERSION,)}
        if not isinstance(out, dict):
            return {"error": ("self-upgrade v%s: original returned %s, "
                              "expected dict" % (_VERSION, type(out).__name__)),
                    "target": _TARGET}
        return out
    except Exception as e:  # the wrapper itself must never raise
        return {"error": ("self-upgrade v%s wrapper failed: %s: %s"
                          % (_VERSION, type(e).__name__, e)),
                "target": _TARGET}
'''

_DEFAULT_CHANGES = ("hardening wrapper: args must be a dict, exceptions from the "
                    "original handler are contained into a structured {'error'} "
                    "return, non-dict returns are normalized to an error; "
                    "behavior otherwise unchanged")


def _render_upgrade(target_key: str, version: int, func: str,
                    original_path: str, original_sha: str,
                    changes: str) -> str:
    src = _UPGRADE_TEMPLATE
    src = src.replace("@@TARGET@@", target_key)
    src = src.replace("@@VERSION@@", str(version))
    src = src.replace("@@FUNC@@", func)
    src = src.replace("@@ORIGINAL_PATH@@", original_path)
    src = src.replace("@@ORIGINAL_PATH_JSON@@", json.dumps(original_path))
    src = src.replace("@@ORIGINAL_SHA@@", original_sha)
    src = src.replace("@@DRAFTED_AT@@", _now())
    src = src.replace("@@CHANGES@@", changes.replace("\n", " "))
    return src


def _sandbox_test_upgrade(path: Path, example_input: dict):
    """Import-isolated sandbox test of an upgrade candidate.

    Same mechanics as tools.test_forged: load the module in isolation,
    require a callable run(args), call it with a copy of the example input,
    require a dict back and no raise. Returns (passed, detail, output)."""
    try:
        spec = importlib.util.spec_from_file_location(
            "jarvis_self_upgrade_candidate", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return False, f"import failed: {type(e).__name__}: {e}", None
    run = getattr(mod, "run", None)
    if not callable(run):
        return False, "module has no run(args) function", None
    try:
        out = run(dict(example_input))
    except Exception as e:
        return False, f"run raised: {type(e).__name__}: {e}", None
    if not isinstance(out, dict):
        return False, "run did not return a dict", None
    return True, "run returned a dict without raising", out


# ---------------------------------------------------------------------------
# self.diagnose — mine the audit log
# ---------------------------------------------------------------------------
_ERROR_KEY_RE = re.compile(r"""['"]error['"]\s*:""")
_EXCEPTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*Error):")


def _read_audit_rows(db_path: Path, days: int) -> list[dict]:
    if not db_path.is_file():
        return []
    cutoff = int(time.time()) - days * 86400
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "audit_log" not in tables:
                return []
            rows = conn.execute(
                "SELECT ts, actor, tool, args_json, result_summary, risk"
                " FROM audit_log WHERE ts > ? ORDER BY ts",
                (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _classify(summary: str) -> str:
    """ok | tool-error | confirm-gate | deny"""
    s = summary or ""
    if s == "awaiting confirmation":
        return "confirm-gate"
    sl = s.lower()
    if "default deny" in sl or "denies" in sl:
        return "deny"
    if _ERROR_KEY_RE.search(s) or _EXCEPTION_RE.match(s):
        return "tool-error"
    return "ok"


def _sample_error(summary: str) -> str:
    s = summary or ""
    if s.startswith("{'error':") or s.startswith('{"error":'):
        try:
            return str(ast.literal_eval(s).get("error"))[:300]
        except (ValueError, SyntaxError, AttributeError):
            pass
    m = _EXCEPTION_RE.match(s)
    if m:
        return s[:300]
    return s[:300]


def _arg_shape(value) -> str:
    if isinstance(value, str):
        return f"str:{value[:40]}"
    try:
        return f"{type(value).__name__}:{str(value)[:40]}"
    except Exception:
        return "unprintable"


def self_diagnose_handler(args: dict) -> dict:
    """self.diagnose {days?} — ranked problem list mined from the audit log."""
    try:
        a = args or {}
        days = a.get("days", 7)
        try:
            days = int(days)
        except (TypeError, ValueError):
            return {"error": "days must be an integer"}
        days = max(1, min(days, 90))

        db_path = _audit_db_path()
        rows = _read_audit_rows(db_path, days)

        calls: dict[str, int] = {}
        errors: dict[str, int] = {}
        error_samples: dict[str, str] = {}
        gates: dict[str, int] = {}
        denies: dict[str, int] = {}
        friction_samples: dict[str, str] = {}
        repeats: dict[tuple, list] = {}

        for r in rows:
            tool = str(r.get("tool") or "?")
            summary = r.get("result_summary") or ""
            calls[tool] = calls.get(tool, 0) + 1
            kind = _classify(summary)
            if kind == "tool-error":
                errors[tool] = errors.get(tool, 0) + 1
                error_samples.setdefault(tool, _sample_error(summary))
            elif kind == "confirm-gate":
                gates[tool] = gates.get(tool, 0) + 1
                friction_samples.setdefault(tool, summary[:200])
            elif kind == "deny":
                denies[tool] = denies.get(tool, 0) + 1
                friction_samples.setdefault(tool, summary[:200])
            # repeated questions: same tool + similar args
            try:
                pargs = json.loads(r.get("args_json") or "{}")
            except (TypeError, ValueError):
                pargs = {}
            if isinstance(pargs, dict):
                sig = json.dumps({k: _arg_shape(v) for k, v in pargs.items()},
                                 sort_keys=True)
                key = (tool, sig)
                repeats.setdefault(key, []).append(pargs)

        error_rank = [
            {"tool": t, "errors": c, "calls": calls[t],
             "error_rate": round(c / calls[t], 3),
             "sample_error": error_samples[t]}
            for t, c in sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:10]

        friction_tools = set(gates) | set(denies)
        confirmation_friction = [
            {"tool": t,
             "confirmation_gates": gates.get(t, 0),
             "denials": denies.get(t, 0),
             "sample": friction_samples.get(t, "")}
            for t in sorted(friction_tools,
                            key=lambda t: (-(gates.get(t, 0) + denies.get(t, 0)), t))
        ][:10]

        repeated_calls = [
            {"tool": t, "times": len(v), "arg_signature": sig,
             "sample_args": {k: str(x)[:120] for k, x in v[0].items()}}
            for (t, sig), v in sorted(repeats.items(),
                                      key=lambda kv: (-len(kv[1]), kv[0][0]))
            if len(v) >= 3
        ][:10]

        all_clear = not error_rank and not confirmation_friction and not repeated_calls
        if all_clear:
            summary = (f"No problems found in the last {days} days "
                       f"({len(rows)} audit rows scanned): no tool errors, no "
                       "confirmation friction, no repeated calls.")
        else:
            bits = []
            if error_rank:
                bits.append(f"{len(error_rank)} tool(s) with errors "
                            f"(worst: {error_rank[0]['tool']} "
                            f"x{error_rank[0]['errors']})")
            if confirmation_friction:
                bits.append(f"{len(confirmation_friction)} tool(s) with "
                            "confirmation friction")
            if repeated_calls:
                bits.append(f"{len(repeated_calls)} repeated question pattern(s)")
            summary = (f"{len(rows)} audit rows scanned over {days} days: "
                       + "; ".join(bits) + ".")
        return {
            "window_days": days,
            "audit_db": str(db_path),
            "rows_scanned": len(rows),
            "tools_seen": len(calls),
            "error_rank": error_rank,
            "confirmation_friction": confirmation_friction,
            "repeated_calls": repeated_calls,
            "all_clear": all_clear,
            "summary": summary,
        }
    except Exception as exc:  # never raise
        return {"error": f"self.diagnose failed: {exc}"}


# ---------------------------------------------------------------------------
# self.upgrade — draft, sandbox-test, propose (never activate here)
# ---------------------------------------------------------------------------
def _next_version(target_key: str) -> int:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM self_upgrades WHERE target=?",
                (target_key,)).fetchone()
            return int(row[0]) + 1
        finally:
            conn.close()


def _record_upgrade(uid: str, target_key: str, version: int, path: str,
                    test_report: dict) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO self_upgrades(id, target, version, path, status,"
                " created_at, test_report) VALUES (?,?,?,?,?,?,?)",
                (uid, target_key, version, path, "proposed", _now(),
                 json.dumps(test_report)))
            conn.commit()
        finally:
            conn.close()


def self_upgrade_handler(args: dict) -> dict:
    """self.upgrade {target, example_input?, tool?, changes?}.

    Draft a hardened replacement for one tool handler, sandbox-test it, and
    queue the swap as an autopilot proposal. HIGH risk: confirmation-gated by
    the policy engine. Never overwrites the original file; never activates.
    """
    try:
        a = args or {}
        target = a.get("target")

        # Lazy imports inside the handler: no top-level cycles.
        from . import forge_pack  # noqa: PLC0415  (upgrades dir + env override)

        norm, err = _normalize_target(target)
        if err:
            return {"error": err}
        rel, func, resolved_tool = norm
        target_key = f"{rel}::{func}"
        slug = _slug(rel, func)

        pkg_file = (_pkg_root().resolve() / rel)
        original_bytes = pkg_file.read_bytes()
        original_sha = hashlib.sha256(original_bytes).hexdigest()

        upgrades_dir = forge_pack._forged_dir() / "upgrades"
        upgrades_dir.mkdir(parents=True, exist_ok=True)
        originals_dir = upgrades_dir / "_originals"
        originals_dir.mkdir(parents=True, exist_ok=True)

        # Preserve the original bytes alongside (for rollback). First write
        # wins: the original file must never change under us.
        preserved = originals_dir / f"{slug}_orig.py"
        if not preserved.is_file():
            preserved.write_bytes(original_bytes)

        version = _next_version(target_key)
        upgrade_id = f"upg_{slug}_v{version}"
        upgrade_path = upgrades_dir / f"{slug}_v{version}.py"

        tool_name = a.get("tool") or resolved_tool
        if tool_name is not None and (not isinstance(tool_name, str)
                                      or not tool_name.strip()):
            return {"error": "tool must be a non-empty string when provided"}
        tool_name = tool_name.strip() if isinstance(tool_name, str) else None

        changes = a.get("changes")
        changes_text = _DEFAULT_CHANGES
        if isinstance(changes, str) and changes.strip():
            changes_text = changes.strip()[:500] + " | " + _DEFAULT_CHANGES

        source = _render_upgrade(target_key, version, func, str(preserved),
                                 original_sha, changes_text)
        if "exec(" in source or "eval(" in source:
            return {"error": "self.upgrade: generated source failed the "
                             "no-exec/no-eval sanity check — refusing to write"}
        upgrade_path.write_text(source, encoding="utf-8")

        example_input = a.get("example_input", {})
        if not isinstance(example_input, dict):
            return {"error": "example_input must be a dict"}
        passed, detail, output = _sandbox_test_upgrade(upgrade_path,
                                                      example_input)
        test_report = {
            "sandbox": "pass" if passed else "fail",
            "detail": detail,
            "example_input": example_input,
            "output_sample": str(output)[:500] if output is not None else None,
            "original_sha": original_sha,
        }
        if not passed:
            # The candidate stays on disk as evidence, but NOTHING is
            # proposed and no upgrade row is recorded.
            return {"error": ("self.upgrade: sandbox test FAILED "
                              f"({detail}) — upgrade NOT proposed; candidate "
                              f"kept at {upgrade_path} for inspection"),
                    "upgrade_id": upgrade_id, "target": target_key,
                    "version": version}

        _record_upgrade(upgrade_id, target_key, version, str(upgrade_path),
                        test_report)

        meta = {
            "upgrade_id": upgrade_id, "target": target_key,
            "version": version, "tool": tool_name, "func": func,
            "module_rel": rel, "original_sha": original_sha,
            "original_path": str(preserved),
            "upgrade_path": str(upgrade_path),
            "created_at": _now(), "changes": changes_text,
            "proposal_id": None,
        }
        meta_path = upgrade_path.with_name(upgrade_path.stem + ".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Queue the swap as an autopilot proposal. Activation happens only
        # via the user's approval path — never here.
        try:
            from . import autopilot_pack  # noqa: PLC0415
        except ImportError:
            return {"error": ("self.upgrade: autopilot_pack unavailable — "
                              "upgrade drafted and tested but NOT queued"),
                    "upgrade_id": upgrade_id, "target": target_key,
                    "version": version, "path": str(upgrade_path)}
        goal = (f"upgrade ready for {target_key}: {changes_text}. "
                "Approve to activate?")
        pres = autopilot_pack.propose_handler({
            "goal": goal,
            "steps": [{
                "tool": "notes.add",
                "args": {"text":
                         f"ACTIVATION RECORD — upgrade {upgrade_id} for "
                         f"{target_key} (v{version}) approved by the user. "
                         f"To activate, the parent loads {upgrade_path} and "
                         f"registers its run() as the replacement handler; "
                         f"revert any time with self.rollback "
                         f'{{"upgrade_id": "{upgrade_id}"}}.'},
                "why": ("audit trail: written only when the user approves "
                        "this upgrade"),
            }],
        })
        if "error" in pres:
            return {"error": ("self.upgrade: upgrade drafted and tested but "
                              f"the proposal failed: {pres['error']}"),
                    "upgrade_id": upgrade_id, "target": target_key,
                    "version": version, "path": str(upgrade_path)}
        pid = pres.get("id")
        meta["proposal_id"] = pid
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        with _LOCK:
            conn = _connect()
            try:
                test_report["proposal_id"] = pid
                conn.execute(
                    "UPDATE self_upgrades SET test_report=? WHERE id=?",
                    (json.dumps(test_report), upgrade_id))
                conn.commit()
            finally:
                conn.close()
        return {
            "ok": True,
            "upgrade_id": upgrade_id,
            "target": target_key,
            "version": version,
            "path": str(upgrade_path),
            "original_preserved": str(preserved),
            "sandbox": "pass",
            "proposal_id": pid,
            "changes": changes_text,
            "activation": ("NOT applied — approve proposal "
                           f"{pid} to activate; the parent applies the handler "
                           "swap only after user approval, never here"),
        }
    except Exception as exc:  # never raise
        return {"error": f"self.upgrade failed: {exc}"}


# ---------------------------------------------------------------------------
# self.rollback — one-tap revert
# ---------------------------------------------------------------------------
def _get_upgrade(uid: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM self_upgrades WHERE id=?", (uid,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def _mark_reverted(uid: str, note: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT test_report FROM self_upgrades WHERE id=?",
                (uid,)).fetchone()
            try:
                report = json.loads(row["test_report"]) if row else {}
            except (TypeError, ValueError):
                report = {}
            report["reverted_at"] = _now()
            report["revert_note"] = note
            conn.execute(
                "UPDATE self_upgrades SET status='reverted', test_report=? "
                "WHERE id=?", (json.dumps(report), uid))
            conn.commit()
        finally:
            conn.close()


def _restore_live_handler(tool_name: str, meta: dict) -> tuple[bool, str]:
    """Re-register the original handler from the preserved copy."""
    if _AGENT is None:
        return False, ("live registry not bound (server.py must call "
                       "self_pack.bind_agent(agent)) — recorded as reverted; "
                       "a restart restores the original since upgrade files "
                       "never overwrite originals")
    registry = getattr(_AGENT, "registry", None)
    tools = getattr(registry, "tools", None)
    if tools is None or tool_name not in tools:
        return False, (f"tool '{tool_name}' is not in the live registry — "
                       "recorded as reverted; restart restores the original")
    original_path = meta.get("original_path")
    func = meta.get("func")
    if not original_path or not func:
        return False, ("upgrade metadata is missing the original path/function "
                       "— recorded as reverted")
    try:
        spec = importlib.util.spec_from_file_location(
            "jarvis_self_rollback_original", original_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        original_fn = getattr(mod, func, None)
        if not callable(original_fn):
            return False, (f"preserved copy has no callable '{func}' — "
                           "recorded as reverted")
        tools[tool_name].handler = original_fn
        return True, (f"live registry restored: '{tool_name}' handler "
                      "re-registered from the preserved original")
    except Exception as e:
        return False, (f"could not load the preserved original "
                       f"({type(e).__name__}: {e}) — recorded as reverted")


def self_rollback_handler(args: dict) -> dict:
    """self.rollback {upgrade_id} — one-tap revert of an upgrade."""
    try:
        a = args or {}
        uid = a.get("upgrade_id")
        if not isinstance(uid, str) or not uid.strip():
            return {"error": "upgrade_id is required (e.g. 'upg_tools_builtin_fs_tools__read_v1')"}
        uid = uid.strip()
        row = _get_upgrade(uid)
        if row is None:
            return {"error": (f"unknown upgrade id '{uid}' — no such self-upgrade "
                              "(ids look like 'upg_<target>_v<n>')")}
        status = row["status"]
        if status == "reverted":
            return {"error": f"upgrade '{uid}' was already reverted — nothing to do"}
        meta_path = Path(row["path"]).with_name(
            Path(row["path"]).stem + ".meta.json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        live_change = "none needed — the upgrade was never activated"
        if status == "active":
            tool_name = meta.get("tool")
            if not tool_name:
                live_change = ("no live tool mapping was recorded for this "
                               "upgrade — recorded as reverted; restart "
                               "restores the original")
            else:
                restored, note = _restore_live_handler(tool_name, meta)
                live_change = note
        elif status not in ("proposed", "test-failed"):
            live_change = (f"upgrade was in unexpected status '{status}' — "
                           "recorded as reverted without live changes")
        _mark_reverted(uid, live_change)
        return {"ok": True, "upgrade_id": uid, "status": "reverted",
                "was": status, "live_change": live_change}
    except Exception as exc:  # never raise
        return {"error": f"self.rollback failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "self.diagnose",
     "description": ("Mine the audit log and return a ranked problem list with "
                     "evidence counts: which tools error most (name, error "
                     "count, sample error), which tools hit confirmation gates "
                     "or policy denials most (friction), and which tool+args "
                     "patterns repeat 3+ times (re-asked questions). Says so "
                     "honestly when nothing is problematic — no fake findings."),
     "handler": self_diagnose_handler, "risk": "low", "needs_network": False,
     "schema": {"days?": "int"}},
    {"name": "self.upgrade",
     "description": ("Draft a hardened replacement for one tool handler "
                     "(target: 'tools/builtin/fs_tools.py::read' or a "
                     "registered tool name like 'fs.read'). The drafter is "
                     "mechanical: it preserves the original bytes verbatim and "
                     "generates a hardening wrapper (args validation, "
                     "exception containment, non-dict normalization). "
                     "FORBIDDEN targets are refused: anything under agent/ or "
                     "security/, anything named policy/permissions_pack/"
                     "autonomy_pack/self_pack. The new version is written ONLY "
                     "to tools/forged/upgrades/<slug>_v<version>.py (the "
                     "original file is never touched), sandbox-tested with the "
                     "tools.test_forged technique, and queued as an "
                     "autopilot.propose ('upgrade ready for <target>'). "
                     "Activation happens only via the user's approval path, "
                     "never here. HIGH risk — needs confirmation."),
     "handler": self_upgrade_handler, "risk": "high", "needs_network": False,
     "schema": {"target": "string", "example_input?": "dict",
                "tool?": "string", "changes?": "string"}},
    {"name": "self.rollback",
     "description": ("One-tap revert of an upgrade: restores the original "
                     "handler registration from the preserved copy and marks "
                     "the upgrade reverted in sqlite. Honest errors for "
                     "unknown ids and already-reverted upgrades."),
     "handler": self_rollback_handler, "risk": "medium", "needs_network": False,
     "schema": {"upgrade_id": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(self_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "self.diagnose": ("low", False),
    "self.upgrade": ("high", False),
    "self.rollback": ("medium", False),
}


def register(reg) -> None:
    """Wire the three Self pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
