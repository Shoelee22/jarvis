"""Unit tests for the Phase 8 GUI Tool Pack. All offline: pyautogui is a fake
module injected into sys.modules, platform/shutil/os.environ are monkeypatched
to exercise every branch on this Linux box."""
import os
import sys
import types

import pytest

sys.path.insert(0, "sidecar")  # noqa: E402

from jarvis.tools.builtin import gui_pack as gp  # noqa: E402


# ------------------------------------------------------------------ fakes

class FakePyAutoGUI:
    """Records calls; mimics the pyautogui API surface we use."""

    def __init__(self):
        self.calls = []

    def screenshot(self, path):
        self.calls.append(("screenshot", path))
        open(path, "wb").write(b"PNG")

    def click(self, x, y, button="left"):
        self.calls.append(("click", x, y, button))

    def typewrite(self, text):
        self.calls.append(("typewrite", text))

    def hotkey(self, *parts):
        self.calls.append(("hotkey", parts))

    def position(self):
        self.calls.append(("position",))
        return (321, 654)


@pytest.fixture
def fake_pag(monkeypatch):
    fake = FakePyAutoGUI()
    mod = types.ModuleType("pyautogui")
    for name in ("screenshot", "click", "typewrite", "hotkey", "position"):
        setattr(mod, name, getattr(fake, name))
    monkeypatch.setitem(sys.modules, "pyautogui", mod)
    return fake


def _no_pyautogui(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", None)  # import -> ImportError


def _platform(monkeypatch, name):
    monkeypatch.setattr(gp.platform, "system", lambda: name)


def _no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def _no_cli_tools(monkeypatch):
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)


# ------------------------------------------------------------------ wiring

def test_tool_defs_count_and_risk():
    assert len(gp.TOOL_DEFS) == 7
    risks = {d["name"]: d["risk"] for d in gp.TOOL_DEFS}
    assert risks == {
        "gui.screenshot": "low",
        "gui.click": "high",
        "gui.type": "high",
        "gui.hotkey": "high",
        "gui.windows_list": "low",
        "gui.window_focus": "medium",
        "gui.mouse_position": "low",
    }
    for d in gp.TOOL_DEFS:
        assert d["needs_network"] is False
        assert callable(d["handler"])
        assert isinstance(d["schema"], dict)


def test_register_wires_all_tools():
    from jarvis.tools.base import Registry
    reg = Registry()
    gp.register(reg)
    assert set(reg.tools) == {d["name"] for d in gp.TOOL_DEFS}
    assert reg.tools["gui.click"].risk == "high"


# ------------------------------------------------------------------ gui.click

def test_click_validates_negative_coords():
    r = gp.gui_click({"x": -5, "y": 10})
    assert "error" in r and ">= 0" in r["error"]
    r = gp.gui_click({"x": 10, "y": -1})
    assert "error" in r and ">= 0" in r["error"]


def test_click_validates_types():
    assert "error" in gp.gui_click({"x": "10", "y": 5})
    assert "error" in gp.gui_click({"x": 10.5, "y": 5})
    assert "error" in gp.gui_click({"x": True, "y": 5})  # bool is not a coord
    assert "error" in gp.gui_click({"x": 10})            # missing y
    assert "error" in gp.gui_click({"x": 1, "y": 2, "button": "middle"})


def test_click_via_fake_pyautogui(fake_pag):
    r = gp.gui_click({"x": 100, "y": 200, "button": "right"})
    assert r == {"clicked": True, "x": 100, "y": 200,
                 "button": "right", "via": "pyautogui"}
    assert fake_pag.calls == [("click", 100, 200, "right")]


def test_click_no_backend_honest_error(monkeypatch):
    _no_pyautogui(monkeypatch)
    _platform(monkeypatch, "Linux")
    _no_display(monkeypatch)
    _no_cli_tools(monkeypatch)
    r = gp.gui_click({"x": 1, "y": 2})
    assert "error" in r
    assert "no GUI backend available" in r["error"]


# ------------------------------------------------------------------ gui.type

def test_type_caps_long_text():
    r = gp.gui_type({"text": "a" * 2001})
    assert "error" in r and "max is 2000" in r["error"]


def test_type_boundary_and_nonstring():
    r = gp.gui_type({"text": "a" * 2000})  # exactly at cap: passes validation
    assert "error" not in r or "too long" not in r["error"]
    assert "error" in gp.gui_type({"text": 123})
    assert "error" in gp.gui_type({})


def test_type_via_fake_pyautogui(fake_pag):
    r = gp.gui_type({"text": "hello sir"})
    assert r == {"typed": True, "chars": 9, "via": "pyautogui"}
    assert fake_pag.calls == [("typewrite", "hello sir")]


def test_type_no_backend_honest_error(monkeypatch):
    _no_pyautogui(monkeypatch)
    _platform(monkeypatch, "Linux")
    _no_display(monkeypatch)
    _no_cli_tools(monkeypatch)
    r = gp.gui_type({"text": "hi"})
    assert "error" in r and "no GUI backend available" in r["error"]


# ------------------------------------------------------------------ gui.hotkey

def test_hotkey_rejects_junk():
    for junk in ("rm -rf /", "ctrl;ls", "ctrl+ ", "", "   ",
                 "ctrl+$(whoami)", "alt+f4;reboot", "ctrl++j", "+"):
        r = gp.gui_hotkey({"keys": junk})
        assert "error" in r, f"expected error for {junk!r}, got {r}"


def test_hotkey_accepts_valid_combos(fake_pag):
    r = gp.gui_hotkey({"keys": "ctrl+shift+j"})
    assert r == {"pressed": True, "keys": "ctrl+shift+j", "via": "pyautogui"}
    assert fake_pag.calls == [("hotkey", ("ctrl", "shift", "j"))]
    r = gp.gui_hotkey({"keys": "Alt+F4"})
    assert r["pressed"] is True and r["keys"] == "alt+f4"


def test_hotkey_no_backend_honest_error(monkeypatch):
    _no_pyautogui(monkeypatch)
    _platform(monkeypatch, "Linux")
    _no_display(monkeypatch)
    _no_cli_tools(monkeypatch)
    r = gp.gui_hotkey({"keys": "ctrl+c"})
    assert "error" in r and "no GUI backend available" in r["error"]


# ------------------------------------------------------------------ gui.screenshot

def test_screenshot_via_fake_pyautogui(fake_pag, tmp_path):
    path = str(tmp_path / "shot.png")
    r = gp.gui_screenshot({"path": path})
    assert r == {"path": path, "via": "pyautogui"}
    assert os.path.exists(path)
    assert fake_pag.calls == [("screenshot", path)]


def test_screenshot_no_backend_headless_honest_error(monkeypatch):
    _no_pyautogui(monkeypatch)
    _platform(monkeypatch, "Linux")
    _no_display(monkeypatch)
    _no_cli_tools(monkeypatch)
    r = gp.gui_screenshot({})
    assert "error" in r
    assert "no DISPLAY" in r["error"] or "no GUI backend" in r["error"]
    assert "error" in r and not isinstance(r, Exception)


def test_screenshot_unknown_os_honest_error(monkeypatch):
    _no_pyautogui(monkeypatch)
    _platform(monkeypatch, "Plan9")
    r = gp.gui_screenshot({})
    assert "error" in r and "no GUI backend available" in r["error"]


# ------------------------------------------------------------------ gui.windows_list

def test_windows_list_linux_wmctrl(monkeypatch):
    _platform(monkeypatch, "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(gp.shutil, "which", lambda n: "/usr/bin/wmctrl" if n == "wmctrl" else None)

    class R:
        returncode = 0
        stdout = ("0x03a00007  0  host  Terminal\n"
                  "0x03a0000a  1  host  Firefox\n")
        stderr = ""

    monkeypatch.setattr(gp.subprocess, "run", lambda *a, **k: R())
    r = gp.gui_windows_list({})
    assert r["count"] == 2
    assert r["windows"][0]["title"] == "Terminal"
    assert r["via"] == "wmctrl"


def test_windows_list_no_wmctrl_honest_error(monkeypatch):
    _platform(monkeypatch, "Linux")
    _no_display(monkeypatch)
    _no_cli_tools(monkeypatch)
    r = gp.gui_windows_list({})
    assert "error" in r


# ------------------------------------------------------------------ gui.window_focus

def test_window_focus_validates_title():
    assert "error" in gp.gui_window_focus({})
    assert "error" in gp.gui_window_focus({"title": "   "})
    assert "error" in gp.gui_window_focus({"title": 123})


def test_window_focus_linux_no_backend_honest_error(monkeypatch):
    _platform(monkeypatch, "Linux")
    _no_display(monkeypatch)
    _no_cli_tools(monkeypatch)
    r = gp.gui_window_focus({"title": "Terminal"})
    assert "error" in r


def test_window_focus_linux_wmctrl_success(monkeypatch):
    _platform(monkeypatch, "Linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(gp.shutil, "which", lambda n: "/usr/bin/wmctrl" if n == "wmctrl" else None)

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(gp.subprocess, "run", lambda *a, **k: R())
    r = gp.gui_window_focus({"title": "Terminal"})
    assert r == {"focused": True, "title": "Terminal"}


# ------------------------------------------------------------------ gui.mouse_position

def test_mouse_position_via_fake_pyautogui(fake_pag):
    r = gp.gui_mouse_position({})
    assert r == {"x": 321, "y": 654, "via": "pyautogui"}
    assert fake_pag.calls == [("position",)]


def test_mouse_position_no_backend_honest_error(monkeypatch):
    _no_pyautogui(monkeypatch)
    _platform(monkeypatch, "Linux")
    r = gp.gui_mouse_position({})
    assert "error" in r and "no GUI backend available" in r["error"]
