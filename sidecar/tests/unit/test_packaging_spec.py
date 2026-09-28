"""Regression test: PyInstaller spec must explicitly list every builtin tool pack.

v0.3.1 shipped a Windows bundle missing jarvis.tools.builtin.analyst_pack.
PyInstaller's static analysis did not reliably discover it from the long
`from . import (...)` in jarvis/tools/builtin/__init__.py, and the resulting
startup failure masquerades as a circular import:

    ImportError: cannot import name 'analyst_pack' from partially initialized
    module 'jarvis.tools.builtin' (most likely due to a circular import)

(The fromlist handler silently skips the missing submodule, then the
subsequent IMPORT_FROM raises the misleading message.)

This test fails if any builtin pack is not listed in the spec's
hiddenimports, so a missing pack can never again slip into a release.
"""
from __future__ import annotations

import re
from pathlib import Path

SIDECAR_DIR = Path(__file__).resolve().parents[2]
SPEC_PATH = SIDECAR_DIR.parent / "packaging" / "sidecar.spec"
BUILTIN_DIR = SIDECAR_DIR / "jarvis" / "tools" / "builtin"


def _builtin_packs() -> list[str]:
    return sorted(
        p.stem
        for p in BUILTIN_DIR.glob("*.py")
        if p.name != "__init__.py"
    )


def _spec_hiddenimports() -> set[str]:
    text = SPEC_PATH.read_text(encoding="utf-8")
    m = re.search(r"hiddenimports=\[(.*?)\],\s*\n\s*hookspath", text, re.S)
    assert m, "hiddenimports block not found in sidecar.spec"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_spec_lists_every_builtin_pack():
    packs = _builtin_packs()
    assert packs, "no builtin packs found"
    hidden = _spec_hiddenimports()
    missing = [p for p in packs if f"jarvis.tools.builtin.{p}" not in hidden]
    assert not missing, (
        "sidecar.spec hiddenimports is missing builtin packs: "
        + ", ".join(missing)
        + " (the frozen bundle would crash at startup)"
    )


def test_fromlist_names_exist_on_disk():
    """Every name in the __init__ fromlist must exist as a module on disk.

    A fromlist name with no matching file is exactly what produced the
    v0.3.1 "circular import" crash (the missing submodule is silently
    skipped, then IMPORT_FROM raises the misleading ImportError).
    """
    init_text = (BUILTIN_DIR / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r"^from \. import (.+)$", init_text, re.M)
    assert m, "from . import ... not found in builtin __init__.py"
    listed = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", m.group(1)))
    packs = set(_builtin_packs())
    missing = sorted(listed - packs)
    assert not missing, (
        "builtin __init__.py imports names with no module on disk: "
        + ", ".join(missing)
    )
