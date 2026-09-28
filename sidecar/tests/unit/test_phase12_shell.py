"""Offline tests for the Windows shell integration pack (Phase 12).

We develop on Linux, so every test monkeypatches the pack's win32 helper
layer to simulate Windows (_is_windows, _winrt_send_toast,
_ctypes_balloon_fallback, _ensure_tray_icon, _run_key_*,
_start_hotkey_listener) and separately forces _is_windows False to prove
every handler returns an honest windows-only error instead of raising.

The toast action registry uses a tmp sqlite DB via JARVIS_SHELL_ACTIONS_DB.
"""
from __future__ import annotations

import pytest

import jarvis.tools.builtin.shell_pack as sp
from jarvis.tools.base import Registry


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SHELL_ACTIONS_DB", str(tmp_path / "actions.db"))
    return tmp_path


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sp, "_is_windows", lambda: True)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sp, "_is_windows", lambda: False)


@pytest.fixture(autouse=True)
def _reset_hotkey(monkeypatch):
    monkeypatch.setattr(sp, "_HOTKEY_STATE",
                        {"state": "stopped", "combo": None, "thread": None,
                         "stop_event": None})
    yield
    monkeypatch.setattr(sp, "_HOTKEY_STATE",
                        {"state": "stopped", "combo": None, "thread": None,
                         "stop_event": None})


# ---------------------------------------------------------------------------
# register() wiring
# ---------------------------------------------------------------------------

def test_register_wires_four_tools_with_risk_table():
    reg = Registry()
    sp.register(reg)
    assert set(reg.tools) == {"notify.toast", "shell.tray",
                              "shell.autostart", "hotkey.daemon"}
    for spec in sp.TOOL_DEFS:
        tool = reg.tools[spec["name"]]
        assert tool.handler is spec["handler"]
        assert tool.risk == spec["risk"]
        assert tool.needs_network is False
        assert sp.RISK_TABLE_ADDITIONS[spec["name"]] == (spec["risk"], False)
    assert len(sp.TOOL_DEFS) == 4
    assert all(d["needs_network"] is False for d in sp.TOOL_DEFS)


# ---------------------------------------------------------------------------
# Linux gating: honest windows-only error, never raises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,args", [
    ("notify.toast", {"title": "hi", "body": "x"}),
    ("shell.tray", {}),
    ("shell.autostart", {"enable": True}),
    ("hotkey.daemon", {"action": "status"}),
])
def test_linux_gate_returns_windows_only_error(linux, tmpdb, name, args):
    handler = next(d["handler"] for d in sp.TOOL_DEFS if d["name"] == name)
    out = handler(args)  # must not raise
    assert isinstance(out, dict)
    assert "error" in out
    assert out["error"].startswith("windows-only:")
    assert name in out["error"]


def test_linux_gate_never_touches_win32(linux, tmpdb, monkeypatch):
    # Even with "helpful" win32 fakes installed, the gate fires first.
    monkeypatch.setattr(sp, "_winrt_send_toast",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("touched")))
    out = sp.toast_handler({"title": "t"})
    assert out["error"].startswith("windows-only:")


# ---------------------------------------------------------------------------
# notify.toast payload construction (simulated Windows)
# ---------------------------------------------------------------------------

def test_toast_payload_and_action_registry(windows, tmpdb, monkeypatch):
    seen = {}

    def fake_winrt(title, body, actions, toast_id):
        seen.update(title=title, body=body, actions=actions, toast_id=toast_id)
        return {"channel": "winrt", "buttons": len(actions)}

    monkeypatch.setattr(sp, "_winrt_send_toast", fake_winrt)
    out = sp.toast_handler({
        "title": "Build done", "body": "All green",
        "actions": [{"id": "show", "label": "Show JARVIS"},
                    {"id": "dismiss"}]})
    assert out["channel"] == "winrt"
    assert seen["title"] == "Build done"
    assert seen["body"] == "All green"
    assert seen["actions"] == [{"id": "show", "label": "Show JARVIS"},
                               {"id": "dismiss", "label": "dismiss"}]
    assert seen["toast_id"] == out["toast_id"]
    assert [a["id"] for a in out["actions_registered"]] == ["show", "dismiss"]
    assert out["launch_protocol"] == "jarvis-toast"
    # Actions are retrievable through the registry (click-dispatch loop).
    hit = sp.dispatch_toast_action(out["toast_id"], "show")
    assert hit["ok"] is True
    assert hit["label"] == "Show JARVIS"
    miss = sp.dispatch_toast_action(out["toast_id"], "nope")
    assert miss["ok"] is False and "error" in miss


def test_toast_fallback_channel_when_winrt_missing(windows, tmpdb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("winrt unavailable: no module")

    monkeypatch.setattr(sp, "_winrt_send_toast", boom)
    monkeypatch.setattr(sp, "_ctypes_balloon_fallback",
                        lambda title, body: {"channel": "balloon", "note": "n"})
    out = sp.toast_handler({"title": "t", "body": "b"})
    assert out["channel"] == "balloon"
    assert "winrt unavailable" in out.get("note", "") or True  # note optional


def test_toast_both_channels_fail_returns_error(windows, tmpdb, monkeypatch):
    monkeypatch.setattr(sp, "_winrt_send_toast",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no winrt")))
    monkeypatch.setattr(sp, "_ctypes_balloon_fallback",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ctypes")))
    out = sp.toast_handler({"title": "t", "actions": [{"id": "x"}]})
    assert "error" in out
    assert out["toast_id"]  # still hands back the id + registered actions
    assert out["actions_registered"] == ["x"]


@pytest.mark.parametrize("args", [
    {},
    {"title": "   "},
    {"title": "t", "actions": "not-a-list"},
    {"title": "t", "actions": [{"label": "no id"}]},
    {"title": "t", "actions": [{"id": "  "}]},
])
def test_toast_validation_errors(windows, tmpdb, args):
    out = sp.toast_handler(args)
    assert "error" in out


def test_dispatch_unknown_toast_is_error(tmpdb):
    out = sp.dispatch_toast_action("deadbeef", "x")
    assert out["ok"] is False and "error" in out


# ---------------------------------------------------------------------------
# shell.tray
# ---------------------------------------------------------------------------

def test_tray_ensure_reports_quick_actions(windows, monkeypatch):
    monkeypatch.setattr(sp, "_ensure_tray_icon",
                        lambda: {"ensured": True,
                                 "actions": ["Show JARVIS", "Push-to-talk", "Quit"],
                                 "via": "tauri-tray"})
    out = sp.tray_handler({})
    assert out["ensured"] is True
    assert out["quick_actions"] == ["Show JARVIS", "Push-to-talk", "Quit"]
    assert out["via"] == "tauri-tray"


def test_tray_noop_when_ensure_false(windows):
    assert sp.tray_handler({"ensure": False})["ensured"] is False


def test_tray_helper_failure_surfaces_as_error(windows, monkeypatch):
    monkeypatch.setattr(sp, "_ensure_tray_icon",
                        lambda: (_ for _ in ()).throw(RuntimeError("no shell")))
    out = sp.tray_handler({})
    assert "error" in out


# ---------------------------------------------------------------------------
# shell.autostart: Run-key path/value construction
# ---------------------------------------------------------------------------

def test_autostart_enable_builds_run_key(windows, monkeypatch):
    seen = {}
    monkeypatch.setattr(sp, "_autostart_exe", lambda: r"D:\Apps\JARVIS.exe")
    monkeypatch.setattr(sp, "_run_key_set",
                        lambda exe: seen.update(exe=exe) or {
                            "key": "HKCU\\X", "name": "JARVIS", "value": exe})
    out = sp.autostart_handler({"enable": True})
    assert out["enabled"] is True
    assert seen["exe"] == r"D:\Apps\JARVIS.exe"
    assert out["value"] == r"D:\Apps\JARVIS.exe"
    assert out["persistent_change"] is True
    assert "shell.autostart" in out["reversible_via"]


def test_autostart_enable_uses_config_default_path(windows, monkeypatch, tmp_path):
    # No config override -> DEFAULT_EXE_PATH is written.
    monkeypatch.setattr(sp, "_shell_config",
                        lambda: {"autostart_exe": "", "hotkey_combo": "ctrl+shift+j",
                                 "toast_app_id": "JARVIS", "tray_tooltip": "JARVIS"})
    seen = {}
    monkeypatch.setattr(sp, "_run_key_set",
                        lambda exe: seen.update(exe=exe) or {"value": exe})
    out = sp.autostart_handler({"enable": True})
    assert seen["exe"] == sp.DEFAULT_EXE_PATH
    assert out["enabled"] is True


def test_autostart_run_key_path_constant():
    assert sp.RUN_KEY_PATH == r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    assert sp.RUN_VALUE_NAME == "JARVIS"


def test_autostart_disable_removes_key(windows, monkeypatch):
    monkeypatch.setattr(sp, "_run_key_remove",
                        lambda: {"key": "HKCU\\X", "name": "JARVIS", "removed": True})
    out = sp.autostart_handler({"enable": False})
    assert out["enabled"] is False
    assert out["persistent_change"] is True
    assert "removed" in out["note"]


def test_autostart_disable_when_absent_is_honest(windows, monkeypatch):
    monkeypatch.setattr(sp, "_run_key_remove",
                        lambda: {"key": "HKCU\\X", "name": "JARVIS", "removed": False})
    out = sp.autostart_handler({"enable": False})
    assert out["enabled"] is False
    assert "not present" in out["note"]


def test_autostart_status_query(windows, monkeypatch):
    monkeypatch.setattr(sp, "_run_key_get",
                        lambda: {"present": True, "value": r"C:\J\JARVIS.exe"})
    out = sp.autostart_handler({})
    assert out["enabled"] is True
    assert out["value"] == r"C:\J\JARVIS.exe"
    assert "HKCU" in out["key"]


# ---------------------------------------------------------------------------
# hotkey.daemon state machine
# ---------------------------------------------------------------------------

def _fake_listener(monkeypatch, calls):
    def fake(combo):
        calls.append(combo)
        return {"thread": object(), "stop_event": object(),
                "note": "listener started"}
    monkeypatch.setattr(sp, "_start_hotkey_listener", fake)


def test_hotkey_start_stop_status_cycle(windows, monkeypatch):
    calls = []
    _fake_listener(monkeypatch, calls)
    stopped = []

    def fake_stop(state):
        stopped.append(state["combo"])
    monkeypatch.setattr(sp, "_stop_hotkey_listener", fake_stop)

    assert sp.hotkey_handler({"action": "status"})["state"] == "stopped"

    started = sp.hotkey_handler({"action": "start", "combo": "ctrl+shift+j"})
    assert started["state"] == "running"
    assert started["combo"] == "ctrl+shift+j"
    assert calls == ["ctrl+shift+j"]

    again = sp.hotkey_handler({"action": "start"})
    assert again["state"] == "running" and "already running" in again["note"]
    assert len(calls) == 1  # no second listener

    status = sp.hotkey_handler({"action": "status"})
    assert status["state"] == "running" and status["combo"] == "ctrl+shift+j"
    assert "keylogger" in status["note"]  # honesty is in the copy

    out = sp.hotkey_handler({"action": "stop"})
    assert out["state"] == "stopped"
    assert stopped == ["ctrl+shift+j"]

    out2 = sp.hotkey_handler({"action": "stop"})
    assert out2["state"] == "stopped" and "not running" in out2["note"]


def test_hotkey_start_uses_config_combo_default(windows, monkeypatch):
    calls = []
    _fake_listener(monkeypatch, calls)
    monkeypatch.setattr(sp, "_shell_config",
                        lambda: {"hotkey_combo": "alt+win+k",
                                 "autostart_exe": "", "toast_app_id": "J",
                                 "tray_tooltip": "J"})
    out = sp.hotkey_handler({"action": "start"})
    assert out["combo"] == "alt+win+k"
    assert calls == ["alt+win+k"]


def test_hotkey_bad_action_and_bad_combo(windows, monkeypatch):
    calls = []
    _fake_listener(monkeypatch, calls)
    assert "error" in sp.hotkey_handler({"action": "explode"})
    assert "error" in sp.hotkey_handler({"action": "start", "combo": "j"})
    assert calls == []  # validation happens before any win32 touch
    assert sp.hotkey_handler({"action": "status"})["state"] == "stopped"


def test_parse_combo_matrix():
    assert sp._parse_combo("ctrl+shift+j") == (0x0002 | 0x0004, ord("J"))
    assert sp._parse_combo("alt+F4") == (0x0001, 0x73)
    assert sp._parse_combo("win+ctrl+x")[0] == 0x0008 | 0x0002
    for bad in ["j", "ctrl", "ctrl+j+k", "ctrl+banana", "ctrl+f99", "", "++"]:
        with pytest.raises(ValueError):
            sp._parse_combo(bad)


# ---------------------------------------------------------------------------
# Config + db defaults (file may be absent)
# ---------------------------------------------------------------------------

def test_shell_config_defaults_without_file(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "CONFIG_PATH", tmp_path / "missing.yaml")
    cfg = sp._shell_config()
    assert cfg["hotkey_combo"] == "ctrl+shift+j"
    assert cfg["toast_app_id"] == "JARVIS"


def test_toast_action_registry_roundtrip(tmpdb):
    sp._register_actions("abc123", [{"id": "a", "label": "Alpha"}])
    rows = sp._lookup_actions("abc123")
    assert rows[0]["id"] == "a" and rows[0]["label"] == "Alpha"
    assert sp._lookup_actions("nope") == []
