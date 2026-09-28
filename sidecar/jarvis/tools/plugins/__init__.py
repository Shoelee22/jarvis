"""Plugin SDK (Phase 8): third-party tool packs for JARVIS.

A plugin is a folder containing:
    plugin.yaml        manifest (name, version, description, tools)
    <module>.py        the handler module referenced as "module:function" in the yaml

Tools load as registry-ready TOOL_DEF dicts, namespaced ``<plugin>.<tool>``.
Broken plugins are skipped with a logged warning — the loader NEVER crashes.
"""
from __future__ import annotations

from .loader import (
    TOOL_DEFS,
    DEFAULT_EXAMPLES_DIR,
    all_tool_defs,
    default_user_dir,
    load_plugins,
    load_warnings,
    register,
    risk_table_entries,
)

__all__ = [
    "TOOL_DEFS",
    "DEFAULT_EXAMPLES_DIR",
    "all_tool_defs",
    "default_user_dir",
    "load_plugins",
    "load_warnings",
    "register",
    "risk_table_entries",
]
