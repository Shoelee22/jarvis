"""Offline unit tests for the Phase-8 doctor pack."""
import importlib
import sys
import types

import pytest

from jarvis.tools.builtin import doctor_pack as doc

NEW_PACKS = {"gui", "browser", "pc", "telegram", "gmail", "studio", "plugins"}
PREEXISTING = {"comms", "web_pack", "data_pack", "dev_pack", "media_pack",
               "system_pack", "home_pack", "creator"}
STATUSES = {"live", "needs_config", "needs_package", "unavailable"}


def test_tool_defs_shape():
    assert len(doc.TOOL_DEFS) == 2
    names = [t["name"] for t in doc.TOOL_DEFS]
    assert names == ["system.doctor", "system.capabilities"]
    for t in doc.TOOL_DEFS:
        assert t["risk"] == "low"
        assert t["needs_network"] is False
        assert callable(t["handler"])
        assert isinstance(t["schema"], dict)


def test_doctor_report_structure_against_reality():
    out = doc.doctor({})
    assert isinstance(out, dict)
    report = out["report"]
    assert isinstance(report, list)
    packs = {r["pack"] for r in report}
    assert NEW_PACKS <= packs
    assert PREEXISTING <= packs
    for r in report:
        assert set(r) >= {"pack", "status", "tools", "detail"}
        assert r["status"] in STATUSES
        assert r["tools"] is None or isinstance(r["tools"], int)
        assert isinstance(r["detail"], str) and r["detail"]
    s = out["summary"]
    assert sum(s[k] for k in STATUSES) == len(report)


def _fake_pack_module(tool_defs_count):
    m = types.ModuleType("fake_pack")
    m.TOOL_DEFS = [{"name": f"t{i}"} for i in range(tool_defs_count)]
    return m


def test_doctor_simulated_present_packs(monkeypatch):
    """All new packs importable with TOOL_DEFS; imports of optional libs blocked."""
    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "jarvis.tools.builtin.gui_pack":
            return _fake_pack_module(3)
        if name == "jarvis.tools.builtin.browser_pack":
            return _fake_pack_module(4)
        if name == "jarvis.tools.builtin.pc_pack":
            return _fake_pack_module(2)
        if name == "jarvis.tools.builtin.telegram_pack":
            return _fake_pack_module(5)
        if name == "jarvis.tools.builtin.gmail_pack":
            return _fake_pack_module(6)
        if name == "jarvis.tools.builtin.studio_pack":
            return _fake_pack_module(7)
        if name == "jarvis.tools.plugins.loader":
            m = types.ModuleType("loader")
            m.list_plugins = lambda: ["p1", "p2"]
            return m
        if name in ("pyautogui", "piper", "faster_whisper"):
            raise ImportError("blocked")
        if name == "playwright":
            raise ImportError("blocked")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = doc.doctor({})
    by_pack = {r["pack"]: r for r in out["report"]}
    assert by_pack["gui"]["tools"] == 3
    assert by_pack["browser"]["tools"] == 4
    assert by_pack["telegram"]["tools"] == 5
    assert by_pack["gmail"]["tools"] == 6
    assert by_pack["studio"]["tools"] == 7
    assert by_pack["plugins"]["tools"] == 2
    # browser needs_package without playwright installed
    assert by_pack["browser"]["status"] == "needs_package"
    # pre-existing packs still probed through the real import path
    assert by_pack["creator"]["status"] == "live"


def test_doctor_simulated_all_missing(monkeypatch):
    """Every optional import blocked: nothing raises, all marked unavailable/not installed."""
    def fake_import(name, *a, **kw):
        raise ImportError("blocked")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = doc.doctor({})  # must not raise
    report = out["report"]
    assert len(report) == len(NEW_PACKS) + len(PREEXISTING)
    for r in report:
        assert r["status"] == "unavailable"
        assert "not installed" in r["detail"]


def test_doctor_survives_broken_pack(monkeypatch):
    """A pack raising RuntimeError on import becomes unavailable, not a crash."""
    real_import = importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "jarvis.tools.builtin.gui_pack":
            raise RuntimeError("boom")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    out = doc.doctor({})
    by_pack = {r["pack"]: r for r in out["report"]}
    assert by_pack["gui"]["status"] == "unavailable"
    assert "boom" in by_pack["gui"]["detail"]


def test_capabilities_summary_nonempty():
    out = doc.capabilities({})
    assert isinstance(out["summary"], str)
    assert len(out["summary"]) > 200
    for domain in doc.DOMAINS:
        assert domain in out["summary"]


def test_capabilities_domain_filter():
    out = doc.capabilities({"domain": "Create"})
    assert out["domain"] == "Create"
    assert "website.build" in out["summary"]
    assert "Telegram" not in out["summary"]


def test_capabilities_domain_filter_case_insensitive():
    out = doc.capabilities({"domain": "media studio"})
    assert out["domain"] == "Media Studio"
    assert "ffmpeg" in out["summary"]


def test_capabilities_unknown_domain():
    out = doc.capabilities({"domain": "Nonexistent"})
    assert "Available:" in out["summary"]
    assert set(out["available_domains"]) == set(doc.DOMAINS)


def test_capabilities_never_raises_with_all_imports_blocked(monkeypatch):
    monkeypatch.setattr(importlib, "import_module",
                        lambda *a, **kw: (_ for _ in ()).throw(ImportError("blocked")))
    out = doc.capabilities({})
    assert isinstance(out["summary"], str) and out["summary"]
    out2 = doc.capabilities({"domain": "Automate"})
    assert "Automate" in out2["summary"]
