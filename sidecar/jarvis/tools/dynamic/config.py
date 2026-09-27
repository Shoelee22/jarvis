"""Dynamic-tool config: tools_config.dynamic.yaml, mtime-cached.

Mirrors the approach in builtin/creator.py: try `import yaml`, deep-merge a
section over caller-supplied defaults, tolerate a missing or broken file by
falling back (here: to the defaults / empty dict).
"""
from __future__ import annotations

from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "tools_config.dynamic.yaml"

_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dynamic_config() -> dict:
    """Read tools_config.dynamic.yaml next to this module; mtime-cached.

    Never raises: a missing/unparseable file yields {}.
    """
    global _cfg_cache, _cfg_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        import yaml  # pyyaml ships with the sidecar env
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        cfg = dict(raw) if isinstance(raw, dict) else {}
    except Exception:
        cfg = {}
    _cfg_cache = cfg
    _cfg_mtime = mtime
    return cfg


def section(name: str, defaults: dict | None = None) -> dict:
    """Config section `name` deep-merged over `defaults`.

    Missing/broken sections yield the defaults unchanged. Never raises.
    """
    merged_defaults = dict(defaults) if defaults else {}
    sec = load_dynamic_config().get(name, {})
    if not isinstance(sec, dict):
        return merged_defaults
    return _deep_merge(merged_defaults, sec)
