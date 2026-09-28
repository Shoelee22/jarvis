"""Plugin SDK loader (Phase 8): discovers plugin folders, validates their
``plugin.yaml`` manifests, imports handler modules via importlib, and returns
registry-ready TOOL_DEF dicts namespaced as ``<plugin>.<tool>``.

Scans two directories:
  - user plugins:   ~/workspace/jarvis/plugins/
  - shipped examples: sidecar/jarvis/tools/plugins/examples/

Rules:
  - Any failure (bad yaml, missing module, missing handler, bad signature,
    duplicate tool name) skips just that plugin/tool with a logged warning.
  - The loader NEVER crashes and NEVER raises out of ``load_plugins``.
  - Plugin handlers are wrapped so they NEVER raise either: exceptions become
    {"error": ...} dicts, matching the sidecar handler contract.
"""
from __future__ import annotations

import functools
import importlib.util
import inspect
import logging
import sys
from pathlib import Path

import yaml

log = logging.getLogger("jarvis.plugins")

_VALID_RISKS = ("low", "medium", "high")
_DEFAULT_RISK = "medium"

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_EXAMPLES_DIR = _PACKAGE_DIR / "examples"


def default_user_dir() -> Path:
    """User-owned plugin directory (created on first scan if missing)."""
    return Path.home() / "workspace" / "jarvis" / "plugins"


# ---------------------------------------------------------------------------
# Internal state (populated by load_plugins; read by the plugins.list/reload
# tool handlers).
# ---------------------------------------------------------------------------
_STATE: dict = {"plugins": [], "warnings": []}
_RELOAD_COUNTER = {"n": 0}


def load_warnings() -> list[str]:
    """Warnings collected by the most recent load_plugins() call."""
    return list(_STATE["warnings"])


def _warn(warnings: list[str], msg: str) -> None:
    log.warning(msg)
    warnings.append(msg)


def _safe_callable(fn):
    """Wrap a plugin handler so it never raises: returns {"error": ...}."""
    @functools.wraps(fn)
    def _wrapped(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # plugin code must not crash the loop
            return {"error": f"plugin tool failed: {type(e).__name__}: {e}"}
    return _wrapped


def _signature_ok(fn, label: str, warnings: list[str]) -> bool:
    """Loosely validate that fn can be called as handler(dict)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        # Builtins/odd callables: accept if plain callable.
        if callable(fn):
            return True
        _warn(warnings, f"{label}: handler is not callable; skipping tool")
        return False
    params = list(sig.parameters.values())
    kinds = {p.kind for p in params}
    if (inspect.Parameter.VAR_POSITIONAL in kinds
            or inspect.Parameter.VAR_KEYWORD in kinds):
        return True  # *args/**kwargs can always take the dict
    positional = [p for p in params
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    required = [p for p in positional
                if p.default is inspect.Parameter.empty]
    if len(required) > 1:
        _warn(warnings,
              f"{label}: handler takes {len(required)} required positional "
              f"args, expected 1 (a dict); skipping tool")
        return False
    if len(positional) == 0 and len(required) == 0:
        _warn(warnings,
              f"{label}: handler takes no arguments, expected 1 (a dict); "
              f"skipping tool")
        return False
    return True


def _import_module(plugin_name: str, folder: Path, module_name: str,
                   warnings: list[str]):
    """Import <module_name>.py from the plugin folder via importlib."""
    module_file = folder / f"{module_name}.py"
    if not module_file.is_file():
        _warn(warnings,
              f"plugin '{plugin_name}': module file '{module_name}.py' not "
              f"found in {folder}; skipping tool")
        return None
    # Unique module name per load so re-scans (plugins.reload) pick up edits
    # instead of serving the stale sys.modules entry.
    _RELOAD_COUNTER["n"] += 1
    qualname = (f"jarvis_plugin_{_RELOAD_COUNTER['n']}"
                f"_{plugin_name}_{module_name}")
    try:
        spec = importlib.util.spec_from_file_location(qualname, module_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {module_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualname] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        sys.modules.pop(qualname, None)
        _warn(warnings,
              f"plugin '{plugin_name}': failed to import '{module_name}.py': "
              f"{type(e).__name__}: {e}; skipping tool")
        return None


def _valid_risk(raw, label: str, warnings: list[str]) -> str:
    risk = str(raw or _DEFAULT_RISK).strip().lower()
    if risk not in _VALID_RISKS:
        _warn(warnings,
              f"{label}: invalid risk '{raw}', using '{_DEFAULT_RISK}'")
        return _DEFAULT_RISK
    return risk


def _load_plugin_folder(folder: Path, warnings: list[str]):
    """Load one plugin folder. Returns (info, tool_defs) or (None, [])."""
    manifest_path = folder / "plugin.yaml"
    if not manifest_path.is_file():
        return None, []  # not a plugin folder; silently ignore
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        _warn(warnings,
              f"{folder.name}: bad plugin.yaml ({type(e).__name__}: {e}); "
              f"skipping plugin")
        return None, []
    if not isinstance(manifest, dict):
        _warn(warnings, f"{folder.name}: plugin.yaml is not a mapping; "
                        f"skipping plugin")
        return None, []
    plugin_name = manifest.get("name")
    if not isinstance(plugin_name, str) or not plugin_name.strip():
        _warn(warnings, f"{folder.name}: plugin.yaml missing 'name'; "
                        f"skipping plugin")
        return None, []
    plugin_name = plugin_name.strip()
    info = {
        "name": plugin_name,
        "version": str(manifest.get("version", "0.1.0")),
        "description": str(manifest.get("description", "")),
        "folder": str(folder),
        "tools": [],
    }
    raw_tools = manifest.get("tools", [])
    if not isinstance(raw_tools, list):
        _warn(warnings,
              f"plugin '{plugin_name}': 'tools' is not a list; skipping plugin")
        return None, []
    defs = []
    for i, entry in enumerate(raw_tools):
        label = f"plugin '{plugin_name}' tool #{i}"
        if not isinstance(entry, dict):
            _warn(warnings, f"{label}: not a mapping; skipping tool")
            continue
        tool_name = entry.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            _warn(warnings, f"{label}: missing 'name'; skipping tool")
            continue
        tool_name = tool_name.strip()
        label = f"plugin '{plugin_name}' tool '{tool_name}'"
        ref = entry.get("handler", "")
        if not isinstance(ref, str) or ref.count(":") != 1:
            _warn(warnings,
                  f"{label}: bad handler ref {ref!r}, expected "
                  f"'module:function'; skipping tool")
            continue
        module_name, func_name = (p.strip() for p in ref.split(":"))
        if not module_name or not func_name:
            _warn(warnings, f"{label}: bad handler ref {ref!r}; skipping tool")
            continue
        module = _import_module(plugin_name, folder, module_name, warnings)
        if module is None:
            continue
        fn = getattr(module, func_name, None)
        if not callable(fn):
            _warn(warnings,
                  f"{label}: '{func_name}' not found/callable in "
                  f"'{module_name}.py'; skipping tool")
            continue
        if not _signature_ok(fn, label, warnings):
            continue
        schema = entry.get("schema") or {}
        if not isinstance(schema, dict):
            _warn(warnings, f"{label}: schema is not a mapping; using {{}}")
            schema = {}
        defs.append({
            "name": tool_name,  # namespaced below
            "description": str(entry.get("description", "")),
            "handler": _safe_callable(fn),
            "risk": _valid_risk(entry.get("risk"), label, warnings),
            "needs_network": bool(entry.get("needs_network", False)),
            "schema": schema,
        })
        info["tools"].append(tool_name)
    if not defs:
        _warn(warnings,
              f"plugin '{plugin_name}': no usable tools; skipping plugin")
        return None, []
    return info, defs


def _scan_dir(plugin_dir: Path, warnings: list[str]):
    """Scan one plugin directory. Returns (infos, defs)."""
    infos, defs = [], []
    if not plugin_dir.is_dir():
        return infos, defs
    for folder in sorted(plugin_dir.iterdir()):
        if not folder.is_dir() or folder.name.startswith((".", "__")):
            continue
        try:
            info, folder_defs = _load_plugin_folder(folder, warnings)
        except Exception as e:  # belt and braces: never crash the scan
            _warn(warnings,
                  f"{folder.name}: unexpected loader failure "
                  f"({type(e).__name__}: {e}); skipping plugin")
            continue
        if info is not None:
            infos.append(info)
            defs.extend(folder_defs)
    return infos, defs


def _namespace(defs: list[dict], infos: list[dict], warnings: list[str]):
    """Rename each tool def to <plugin>.<tool>; resolve duplicates."""
    seen: set[str] = set()
    namespaced = []
    info_by_index = {}
    # Rebuild the plugin->defs association by order (defs were appended in
    # plugin order in _scan_dir).
    idx = 0
    for info in infos:
        for tool_name in info["tools"]:
            info_by_index[idx] = info
            idx += 1
    for i, d in enumerate(defs):
        info = info_by_index.get(i, {})
        plugin = info.get("name", "unknown")
        base = f"{plugin}.{d['name']}"
        candidate = base
        suffix = 2
        while candidate in seen:
            candidate = f"{plugin}_{suffix}.{d['name']}"
            suffix += 1
        if candidate != base:
            _warn(warnings,
                  f"duplicate tool name '{base}' — registered as "
                  f"'{candidate}'")
        seen.add(candidate)
        namespaced.append({**d, "name": candidate})
    return namespaced


def load_plugins(user_dir: str | Path | None = None,
                 examples_dir: str | Path | None = None) -> list[dict]:
    """Scan both plugin dirs and return registry-ready TOOL_DEF dicts.

    Never raises: broken plugins are skipped with warnings.
    """
    warnings: list[str] = []
    udir = Path(user_dir) if user_dir is not None else default_user_dir()
    edir = Path(examples_dir) if examples_dir is not None else DEFAULT_EXAMPLES_DIR
    try:
        udir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _warn(warnings, f"cannot create user plugin dir {udir}: {e}")
    infos, defs = [], []
    for d in (udir, edir):
        try:
            i, dd = _scan_dir(d, warnings)
            infos.extend(i)
            defs.extend(dd)
        except Exception as e:  # never let a scan crash the loader
            _warn(warnings, f"scan of {d} failed ({type(e).__name__}: {e})")
    defs = _namespace(defs, infos, warnings)
    # Record the surviving (namespaced) tool names per plugin for plugins.list.
    cursor = 0
    for info in infos:
        n = len(info["tools"])
        info["tools"] = [d["name"] for d in defs[cursor:cursor + n]]
        cursor += n
    _STATE["plugins"] = infos
    _STATE["warnings"] = warnings
    return defs


# ---------------------------------------------------------------------------
# Management tools: plugins.list / plugins.reload
# ---------------------------------------------------------------------------
def _plugins_list(args: dict) -> dict:
    plugins = _STATE["plugins"]
    return {
        "count": len(plugins),
        "plugins": [
            {"name": p["name"], "version": p["version"],
             "description": p["description"], "tool_count": len(p["tools"]),
             "tools": list(p["tools"])}
            for p in plugins
        ],
    }


def _plugins_reload(args: dict) -> dict:
    defs = load_plugins()
    return {
        "plugins": len(_STATE["plugins"]),
        "tools": len(defs),
        "warnings": list(_STATE["warnings"]),
    }


TOOL_DEFS = [
    {"name": "plugins.list",
     "description": "List loaded JARVIS plugins and their tool counts.",
     "handler": _plugins_list, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "plugins.reload",
     "description": "Re-scan the plugin directories and reload all plugins.",
     "handler": _plugins_reload, "risk": "low", "needs_network": False,
     "schema": {}},
]


def all_tool_defs() -> list[dict]:
    """Management tools + every currently loaded plugin tool."""
    return list(TOOL_DEFS) + load_plugins()


def risk_table_entries() -> dict[str, tuple[str, bool]]:
    """{tool_name: (risk, needs_network)} for all plugin-pack tools.

    Merge into ``PolicyEngine.RISK_TABLE`` when registering plugin tools,
    e.g. ``policy.RISK_TABLE.update(risk_table_entries())`` — otherwise the
    policy default-denies any tool it has not seen before.
    """
    entries = {d["name"]: (d["risk"], d["needs_network"]) for d in TOOL_DEFS}
    for d in load_plugins():
        entries[d["name"]] = (d["risk"], d["needs_network"])
    return entries


def register(reg) -> None:
    """Register plugins.list / plugins.reload plus all discovered plugin tools."""
    from ..base import Tool
    for d in all_tool_defs():
        reg.register(Tool(d["name"], d["description"], d.get("schema", {}),
                          d["handler"], d.get("risk", _DEFAULT_RISK),
                          d.get("needs_network", False)))
