"""Tool Forging pack (Phase 9): Jarvis writes new tools from safe primitives.

The honest path to "infinite tools": instead of letting the agent exec/eval
arbitrary code, tools.forge composes a small set of audited, stdlib-only
primitives (HTTP fetch, regex extract, JSON path, jailed SQLite SELECT,
desktop notify, jailed file head) based on keyword intent matching, and
generates a source file that may ONLY call those primitives with fixed args
derived from the intent. Forged tools are born INACTIVE and must pass
tools.test_forged before activation.

Config: none required. Works with no config file; if the parent wants knobs
later (e.g. forge.allow_network), they can be merged here defensively.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DATA_DIR = JARVIS_DIR / "data"

# ---------------------------------------------------------------------------
# Paths (JARVIS_FORGED_DIR env override exists so tests never touch the real dir)
# ---------------------------------------------------------------------------

def _forged_dir() -> Path:
    default = Path(__file__).resolve().parent.parent / "forged"
    return Path(os.environ.get("JARVIS_FORGED_DIR", str(default)))


def _manifest_path() -> Path:
    return _forged_dir() / "manifest.json"


def _load_manifest() -> list:
    try:
        p = _manifest_path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_manifest(manifest: list) -> None:
    d = _forged_dir()
    d.mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Safe primitives (real implementations, stdlib only). dict -> dict, never raise.
# ---------------------------------------------------------------------------

def p_http_get(args: dict) -> dict:
    """Fetch a URL (15s timeout, 256KB cap). Args: {"url": str}."""
    try:
        url = ((args or {}).get("url") or "")
        if not isinstance(url, str) or not url.strip():
            return {"error": "url required"}
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return {"error": "only http(s) urls allowed"}
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-forge/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(262144 + 1)
        truncated = len(raw) > 262144
        raw = raw[:262144]
        return {"ok": True, "url": url, "text": raw.decode("utf-8", errors="replace"),
                "bytes": len(raw), "truncated": truncated}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def p_regex_extract(args: dict) -> dict:
    """Regex findall (pattern cap 200 chars, text cap 1MB, matches cap 500).
    Args: {"text": str, "pattern": str, "max_matches": int?}."""
    try:
        a = args or {}
        text = a.get("text", "")
        pattern = a.get("pattern", "")
        max_matches = a.get("max_matches", 100)
        if not isinstance(text, str):
            text = str(text)
        if not isinstance(pattern, str) or not pattern:
            return {"error": "pattern required"}
        if len(pattern) > 200:
            return {"error": "pattern too long (max 200 chars)"}
        text = text[:1_000_000]
        try:
            rx = re.compile(pattern, re.DOTALL)
        except re.error as e:
            return {"error": f"bad pattern: {e}"}
        matches: list[str] = []
        limit = max(1, min(int(max_matches or 100), 500))
        for m in rx.finditer(text):
            matches.append(m.group(1) if m.groups() else m.group(0))
            if len(matches) >= limit:
                break
        return {"ok": True, "matches": matches, "count": len(matches)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def p_json_path(args: dict) -> dict:
    """Dot-path lookup into JSON/dict. Args: {"data": str|dict, "path": str}."""
    try:
        a = args or {}
        data = a.get("data")
        path = a.get("path", "")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as e:
                return {"error": f"data is not valid JSON: {e}"}
        cur = data
        if path:
            for part in str(path).split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                    cur = cur[int(part)]
                else:
                    return {"error": f"path not found: {path}"}
        return {"ok": True, "value": cur}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


_SELECT_ONLY = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|"
    r"transaction|commit|rollback|grant|revoke)\b", re.IGNORECASE)


def p_sqlite_select(args: dict) -> dict:
    """SELECT-only query against a db jailed under ~/workspace/jarvis/data/.
    Args: {"db": "jarvis.db", "query": "SELECT ..."}."""
    try:
        a = args or {}
        db = a.get("db", "jarvis.db")
        query = a.get("query", "")
        if not isinstance(db, str) or not db or "/" in db or "\\" in db or db.startswith("."):
            return {"error": "db must be a plain filename inside the jarvis data dir"}
        if not isinstance(query, str) or not query.strip():
            return {"error": "query required"}
        q = query.strip().rstrip(";").strip()
        if ";" in q:
            return {"error": "only a single statement allowed"}
        if not q.lower().startswith("select"):
            return {"error": "only SELECT queries allowed"}
        if _SELECT_ONLY.search(q):
            return {"error": "only SELECT queries allowed"}
        db_path = DATA_DIR / db
        if not db_path.is_file():
            return {"error": f"db not found in data dir: {db}"}
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(q)
            rows = [dict(r) for r in cur.fetchmany(200)]
            cols = [d[0] for d in cur.description] if cur.description else []
        finally:
            conn.close()
        return {"ok": True, "rows": rows, "columns": cols, "count": len(rows)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def p_notify(args: dict) -> dict:
    """Best-effort desktop notification. Never claims delivery.
    Args: {"text": str}."""
    try:
        text = str(((args or {}).get("text")) or "")[:500]
        if not text:
            return {"error": "text required"}
        import platform
        system = platform.system()
        attempts: list[tuple[str, list[str]]] = []
        if system == "Linux":
            attempts = [("notify-send", ["notify-send", "JARVIS", text])]
        elif system == "Darwin":
            attempts = [("osascript", ["osascript", "-e",
                                       f'display notification "{text}" with title "JARVIS"'])]
        for name, cmd in attempts:
            try:
                r = subprocess.run(cmd, timeout=5, capture_output=True)
                if r.returncode == 0:
                    return {"ok": True, "delivered": True, "backend": name,
                            "note": "exit code 0; delivery not independently verified"}
                return {"ok": True, "delivered": False, "backend": name,
                        "warn": f"{name} exited with code {r.returncode}"}
            except FileNotFoundError:
                continue
            except Exception as e:
                return {"ok": True, "delivered": False, "backend": name,
                        "warn": f"{type(e).__name__}: {e}"}
        return {"ok": True, "delivered": False,
                "warn": "no notifier available on this system"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def p_file_head(args: dict) -> dict:
    """Read first N lines of a file jailed under ~/workspace/jarvis/data/.
    Args: {"path": str, "n": int?}."""
    try:
        a = args or {}
        path = a.get("path", "")
        n = a.get("n", 50)
        if not isinstance(path, str) or not path:
            return {"error": "path required"}
        p = Path(path)
        if not p.is_absolute():
            p = DATA_DIR / p
        try:
            resolved = p.resolve()
        except Exception:
            return {"error": "bad path"}
        data_root = DATA_DIR.resolve()
        if resolved != data_root and data_root not in resolved.parents:
            return {"error": "path must be under the jarvis data dir"}
        n = max(1, min(int(n or 50), 500))
        if not resolved.is_file():
            return {"error": f"file not found: {path}"}
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()[:n]
        return {"ok": True, "path": str(resolved), "lines": lines,
                "line_count": len(lines)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


PRIMITIVES = {
    "p_http_get": p_http_get,
    "p_regex_extract": p_regex_extract,
    "p_json_path": p_json_path,
    "p_sqlite_select": p_sqlite_select,
    "p_notify": p_notify,
    "p_file_head": p_file_head,
}

# ---------------------------------------------------------------------------
# Intent matching: keywords -> 1-3 primitives, canonical pipeline order
# ---------------------------------------------------------------------------

_INTENT_MAP = [
    ({"fetch", "url", "website", "http", "https", "scrape", "web", "page",
      "download"}, "p_http_get"),
    ({"file", "read", "log", "logs", "tail"}, "p_file_head"),
    ({"database", "sqlite", "query", "sql", "db", "table", "rows"}, "p_sqlite_select"),
    ({"json", "api", "rest", "endpoint"}, "p_json_path"),
    ({"extract", "regex", "title", "titles", "pattern", "patterns", "match",
      "matches", "links", "link", "emails", "email", "href"}, "p_regex_extract"),
    ({"notify", "alert", "notification", "remind", "reminder"}, "p_notify"),
]
# Canonical dataflow order: sources first, transforms next, notify last.
_PRIM_ORDER = ["p_http_get", "p_file_head", "p_sqlite_select",
               "p_json_path", "p_regex_extract", "p_notify"]

SUPPORTED_PRIMITIVES = ", ".join(_PRIM_ORDER)


def match_intent(intent: str) -> list[str]:
    """Return 1-3 primitive names matching the intent, in pipeline order."""
    words = set(re.findall(r"[a-z0-9]+", (intent or "").lower()))
    picked = {prim for keys, prim in _INTENT_MAP if words & keys}
    return sorted(picked, key=_PRIM_ORDER.index)[:3]


# ---------------------------------------------------------------------------
# Source generation: fixed template only. The generated module may ONLY call
# the selected primitives with fixed args derived from the intent.
# No exec/eval of user code anywhere in this pipeline.
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")

# Best-effort known registry names (from builtin/__init__.py) to avoid collisions.
_KNOWN_TOOL_NAMES = frozenset({
    "fs.read", "fs.write", "fs.delete", "fs.list", "fs.search",
    "shell.exec", "reminders.add", "reminders.list", "timers.set",
    "notes.add", "notes.search", "calc.eval", "clipboard.read",
    "notify.send", "screen.capture", "clipboard.write", "battery.status",
    "disk.usage", "process.top", "system.uptime",
    "code.run", "website.build", "media.image", "video.compose",
    "social.post_instagram", "tasks.delegate", "browser.task",
    "camera.describe", "screen.describe",
    "tts.speak", "audio.transcribe", "image.resize", "image.convert",
    "video.trim", "video.gif", "video.contact_sheet", "file.hash",
    "archive.zip", "archive.unzip",
    "tools.forge", "tools.test_forged", "tools.list_forged", "tools.retire_forged",
})


def _first_url(intent: str) -> str:
    m = re.search(r"https?://\S+", intent or "")
    return m.group(0).rstrip(".,);:'\"") if m else ""


def _guess_pattern(intent: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", (intent or "").lower()))
    if "title" in words or "titles" in words:
        return r"<title[^>]*>(.*?)</title>"
    if "email" in words or "emails" in words:
        return r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
    if "link" in words or "links" in words or "href" in words:
        return r'href="([^"]+)"'
    if "price" in words or "prices" in words:
        return r"\$\s?\d[\d,]*(?:\.\d+)?"
    return r"\S+"


def _guess_json_path(intent: str) -> str:
    m = re.search(r"path\s+([A-Za-z0-9_.]+)", intent or "", re.IGNORECASE)
    return m.group(1) if m else ""


def _step_block(prim: str, intent: str, default_url: str) -> list[str]:
    I = "        "  # noqa: E741
    fail = [
        I + 'if "error" in _r:',
        I + '    return {"ok": False, "step": "%s", "error": _r["error"]}' % prim,
    ]
    if prim == "p_http_get":
        return [
            I + 'ctx["url"] = args.get("url", %r)' % default_url,
            I + '_r = _fp.p_http_get({"url": ctx["url"]})',
            *fail,
            I + 'ctx["text"] = _r.get("text", "")',
            I + 'ctx["bytes"] = _r.get("bytes", 0)',
        ]
    if prim == "p_file_head":
        return [
            I + 'ctx["path"] = args.get("path", "")',
            I + '_r = _fp.p_file_head({"path": ctx["path"], "n": args.get("n", 50)})',
            *fail,
            I + 'ctx["text"] = "\\n".join(_r.get("lines", []))',
        ]
    if prim == "p_sqlite_select":
        return [
            I + 'ctx["query"] = args.get("query", "SELECT 1")',
            I + '_r = _fp.p_sqlite_select({"db": args.get("db", "jarvis.db"),'
               ' "query": ctx["query"]})',
            *fail,
            I + 'ctx["rows"] = _r.get("rows", [])',
            I + 'ctx["columns"] = _r.get("columns", [])',
        ]
    if prim == "p_json_path":
        return [
            I + '_data = ctx.get("text") or args.get("data", "")',
            I + '_r = _fp.p_json_path({"data": _data, "path": args.get("path", %r)})'
            % _guess_json_path(intent),
            *fail,
            I + 'ctx["value"] = _r.get("value")',
        ]
    if prim == "p_regex_extract":
        return [
            I + '_r = _fp.p_regex_extract({"text": ctx.get("text", ""),'
               ' "pattern": args.get("pattern", %r), "max_matches": 100})'
            % _guess_pattern(intent),
            *fail,
            I + 'ctx["matches"] = _r.get("matches", [])',
            I + 'ctx["count"] = _r.get("count", 0)',
        ]
    if prim == "p_notify":
        default_text = (intent or "").strip()[:120] or "forged tool notification"
        return [
            I + 'ctx["notify"] = _fp.p_notify({"text": args.get("text", %r)})'
            % default_text,
        ]
    return []


def _render(name: str, description: str, intent: str, prims: list[str]) -> str:
    lines = [
        '"""Forged tool: %s' % name,
        "Intent: " + intent.strip().replace('"""', "'''"),
        "Primitives: " + ", ".join(prims),
        "Generated by tools.forge: calls only forge_pack primitives; no exec/eval.",
        '"""',
        "from jarvis.tools.builtin import forge_pack as _fp",
        "",
        "DESCRIPTION = %r" % description.strip(),
        "PRIMITIVES = %r" % (prims,),
        "",
        "def run(args):",
        '    """Run the forged pipeline. args: dict -> dict. Never raises."""',
        "    try:",
        "        args = args if isinstance(args, dict) else {}",
        "        ctx = {}",
    ]
    default_url = _first_url(intent)
    for prim in prims:
        lines.extend(_step_block(prim, intent, default_url))
    lines += [
        '        return {"ok": True, "result": ctx}',
        "    except Exception as e:  # defensive: run never raises",
        '        return {"error": "%s: %s" % (type(e).__name__, e)}',
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Pack tool handlers (dict -> dict, never raise)
# ---------------------------------------------------------------------------

def forge(args: dict) -> dict:
    """Forge a new tool from safe primitives (born inactive)."""
    try:
        a = args or {}
        name = a.get("name", "")
        description = a.get("description", "")
        intent = a.get("intent", "")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return {"error": "name must match ^[a-z][a-z0-9_]{2,30}$"}
        if not isinstance(description, str) or not description.strip():
            return {"error": "description required"}
        if not isinstance(intent, str) or not intent.strip():
            return {"error": "intent required"}
        if len(description) > 500 or len(intent) > 2000:
            return {"error": "description/intent too long (500/2000 chars max)"}
        manifest = _load_manifest()
        if any(e.get("name") == name for e in manifest):
            return {"error": f"a forged tool named '{name}' already exists"}
        if name in _KNOWN_TOOL_NAMES:
            return {"error": f"name '{name}' collides with an existing tool"}
        target = _forged_dir() / f"{name}.py"
        if target.exists():
            return {"error": f"name '{name}' collides with an existing module file"}
        prims = match_intent(intent)
        if not prims:
            return {"error": "intent didn't match any safe primitive; supported: "
                             + SUPPORTED_PRIMITIVES}
        source = _render(name, description, intent, prims)
        d = _forged_dir()
        d.mkdir(parents=True, exist_ok=True)
        init = d / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
        target.write_text(source, encoding="utf-8")
        manifest.append({
            "name": name,
            "description": description.strip(),
            "primitives": prims,
            "intent": intent.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": False,
        })
        _save_manifest(manifest)
        return {"name": name, "primitives": prims, "path": str(target),
                "note": "forged tool is INACTIVE until tools.test_forged passes"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def test_forged(args: dict) -> dict:
    """Test a forged tool with example input; optionally activate on success."""
    try:
        a = args or {}
        name = a.get("name", "")
        example_input = a.get("example_input") or {}
        activate = bool(a.get("activate", False))
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return {"error": "valid forged tool name required"}
        if not isinstance(example_input, dict):
            return {"error": "example_input must be a dict"}
        manifest = _load_manifest()
        entry = next((e for e in manifest if e.get("name") == name), None)
        if entry is None:
            return {"error": f"no forged tool named '{name}'"}
        path = _forged_dir() / f"{name}.py"
        if not path.is_file():
            return {"error": f"module file missing: {path}"}
        try:
            spec = importlib.util.spec_from_file_location(
                f"jarvis_forged_{name}", str(path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            return {"ok": False, "error": f"import failed: {type(e).__name__}: {e}"}
        if not callable(getattr(mod, "run", None)):
            return {"ok": False, "error": "module has no run(args) function"}
        try:
            out = mod.run(dict(example_input))
        except Exception as e:
            return {"ok": False, "error": f"run raised: {type(e).__name__}: {e}"}
        if not isinstance(out, dict):
            return {"ok": False, "error": "run did not return a dict"}
        ok = out.get("ok") is True
        result: dict = {"ok": ok, "output": out}
        if ok and activate:
            entry["active"] = True
            _save_manifest(manifest)
            result["note"] = f"forged tool '{name}' is now ACTIVE"
        elif ok:
            result["note"] = (f"test passed; '{name}' stays inactive "
                              "until test_forged with activate=true")
        else:
            result["note"] = f"test failed; '{name}' stays inactive"
        return result
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def list_forged(args: dict) -> dict:
    """List forged tools and their status."""
    try:
        manifest = _load_manifest()
        return {"ok": True, "tools": manifest, "count": len(manifest)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def retire_forged(args: dict) -> dict:
    """Delete a forged tool's file and remove it from the manifest."""
    try:
        a = args or {}
        name = a.get("name", "")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return {"error": "valid forged tool name required"}
        manifest = _load_manifest()
        entry = next((e for e in manifest if e.get("name") == name), None)
        if entry is None:
            return {"error": f"no forged tool named '{name}'"}
        path = _forged_dir() / f"{name}.py"
        try:
            if path.is_file():
                path.unlink()
        except Exception as e:
            return {"error": f"could not delete module file: {e}"}
        manifest = [e for e in manifest if e.get("name") != name]
        _save_manifest(manifest)
        return {"ok": True, "retired": name}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Registration contract (applied by the parent)
# ---------------------------------------------------------------------------

def register(reg):
    from ..base import Tool
    reg.register(Tool("tools.forge", "Forge a new tool from safe primitives (inactive until tested).", {"name": "string", "description": "string", "intent": "string"}, forge, "medium", False))
    reg.register(Tool("tools.test_forged", "Test a forged tool with example input; optionally activate it.", {"name": "string", "example_input": "dict", "activate": "bool?"}, test_forged, "medium", False))
    reg.register(Tool("tools.list_forged", "List forged tools and their active status.", {}, list_forged, "low", False))
    reg.register(Tool("tools.retire_forged", "Delete a forged tool and remove it from the manifest.", {"name": "string"}, retire_forged, "medium", False))


RISK_TABLE_ADDITIONS = {
    "tools.forge": ("medium", False),
    "tools.test_forged": ("medium", False),
    "tools.list_forged": ("low", False),
    "tools.retire_forged": ("medium", False),
}
