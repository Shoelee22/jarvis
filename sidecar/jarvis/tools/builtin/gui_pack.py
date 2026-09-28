"""GUI Tool Pack (Phase 8): screenshot, mouse, keyboard, windows.

Every handler takes a dict and returns a dict, NEVER raises. All backends
are resolved lazily inside the handlers (imports inside functions wrapped in
try/except) so this module loads on any OS with zero new dependencies.

Backend preference order per tool:
  - pyautogui (if importable) -> Windows ctypes -> Linux CLI tools
    (scrot / ImageMagick `import` / gnome-screenshot, xdotool, wmctrl).
Headless or missing backends -> honest {"error": "no GUI backend available ..."},
never a crash and never fake data.

Risk: anything that ACTS on the machine (click/type/hotkey) is "high".
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time

_MAX_TYPE_LEN = 2000

_MODIFIERS = {"ctrl", "alt", "shift", "win", "meta", "super", "command", "option"}
_NAMED_KEYS = {
    "enter", "tab", "esc", "space", "delete", "backspace",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
}
_SINGLE_KEY = re.compile(r"^(?:[a-z0-9]|f(?:[1-9]|1[0-2]))$")


# ------------------------------------------------------------------ helpers

def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _pyautogui():
    """Return the pyautogui module, or None if it cannot be imported."""
    try:
        import pyautogui  # noqa: F401
        return pyautogui
    except Exception:
        return None


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _run(cmd: list[str], timeout: int = 15, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


def _linux_gui_ready(cli_tool: str | None = None) -> dict | None:
    """Upfront headless/missing-tool check for Linux GUI actions.

    Returns an honest error dict when this Linux box cannot drive a GUI,
    or None when it is fine to proceed."""
    if not _has_display():
        return {"error": "no GUI backend available on Linux: no DISPLAY/WAYLAND_DISPLAY set (headless)"}
    if cli_tool and not _which(cli_tool):
        return {"error": f"no GUI backend available on Linux: '{cli_tool}' not installed"}
    return None


def _coord(value, name: str):
    """Validate a screen coordinate: real int, >= 0. Returns (ok, int|error)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, {"error": f"'{name}' must be an integer, got {type(value).__name__}"}
    if value < 0:
        return None, {"error": f"'{name}' must be >= 0, got {value}"}
    return value, None


# ---------------------------------------------------------- gui.screenshot

def _screenshot_pyautogui(path: str, pag) -> str:
    pag.screenshot(path)
    return path


def _screenshot_windows_ctypes(path: str) -> str:
    """Capture the primary display via BitBlt and write a 24-bit BMP."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    width = user32.GetSystemMetrics(0)   # SM_CXSCREEN
    height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
    if width <= 0 or height <= 0:
        raise RuntimeError("could not determine screen size")

    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise RuntimeError("GetDC failed")
    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            raise RuntimeError("CreateCompatibleDC failed")
        try:
            hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not hbmp:
                raise RuntimeError("CreateCompatibleBitmap failed")
            try:
                gdi32.SelectObject(hdc_mem, hbmp)
                SRCCOPY = 0x00CC0020
                if not gdi32.BitBlt(hdc_mem, 0, 0, width, height,
                                    hdc_screen, 0, 0, SRCCOPY):
                    raise RuntimeError("BitBlt failed")

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", wintypes.DWORD),
                        ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD),
                    ]

                stride = ((width * 3 + 3) // 4) * 4
                buf = (ctypes.c_ubyte * (stride * height))()
                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = width
                bmi.biHeight = -height  # top-down
                bmi.biPlanes = 1
                bmi.biBitCount = 24
                bmi.biCompression = 0  # BI_RGB
                DIB_RGB_COLORS = 0
                if not gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf,
                                       ctypes.byref(bmi), DIB_RGB_COLORS):
                    raise RuntimeError("GetDIBits failed")

                pixel_bytes = bytes(buf)
                file_size = 14 + 40 + len(pixel_bytes)
                with open(path, "wb") as f:
                    # BITMAPFILEHEADER
                    f.write(b"BM")
                    f.write(file_size.to_bytes(4, "little"))
                    f.write((0).to_bytes(2, "little"))
                    f.write((0).to_bytes(2, "little"))
                    f.write((54).to_bytes(4, "little"))
                    # BITMAPINFOHEADER
                    f.write(bytes(bmi))
            finally:
                gdi32.DeleteObject(hbmp)
        finally:
            gdi32.DeleteDC(hdc_mem)
    finally:
        user32.ReleaseDC(None, hdc_screen)
    return path


def _screenshot_linux(path: str) -> str:
    if not _has_display():
        raise RuntimeError("no DISPLAY/WAYLAND_DISPLAY set (headless)")
    if _which("scrot"):
        r = _run(["scrot", "-z", path])
    elif _which("import"):  # ImageMagick
        r = _run(["import", "-window", "root", path])
    elif _which("gnome-screenshot"):
        r = _run(["gnome-screenshot", "-f", path])
    else:
        raise RuntimeError("no GUI backend available on Linux: no screenshot tool found (need scrot, ImageMagick 'import', or gnome-screenshot)")
    if r.returncode != 0:
        raise RuntimeError(f"screenshot command failed: {r.stderr.strip()[:200]}")
    if not os.path.exists(path):
        raise RuntimeError("screenshot command produced no file")
    return path


def gui_screenshot(args: dict) -> dict:
    """Capture the screen to a PNG file. {path?}."""
    try:
        path = str(args.get("path") or
                   f"/tmp/jarvis-screenshot-{int(time.time())}.png")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        pag = _pyautogui()
        if pag is not None:
            return {"path": _screenshot_pyautogui(path, pag), "via": "pyautogui"}
        if _is_windows():
            bmp = path if path.lower().endswith(".bmp") else re.sub(r"\.[a-zA-Z0-9]+$", "", path) + ".bmp"
            return {"path": _screenshot_windows_ctypes(bmp), "via": "ctypes-bitblt", "format": "bmp"}
        if _is_linux():
            ready = _linux_gui_ready()
            if ready:
                return ready
            return {"path": _screenshot_linux(path), "via": "linux-cli"}
        return {"error": f"no GUI backend available on {platform.system()} (no pyautogui, no supported OS mechanism)"}
    except Exception as e:
        return _fail("gui.screenshot", e)


# --------------------------------------------------------------- gui.click

def _click_windows(x: int, y: int, button: str) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    user32.SetCursorPos(x, y)
    if button == "right":
        down, up = 0x0008, 0x0010
    else:
        down, up = 0x0002, 0x0004
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)


def _click_linux(x: int, y: int, button: str) -> None:
    if not _which("xdotool"):
        raise RuntimeError("xdotool not installed")
    if not _has_display():
        raise RuntimeError("no DISPLAY set (headless)")
    btn = "3" if button == "right" else "1"
    r = _run(["xdotool", "mousemove", str(x), str(y), "click", btn])
    if r.returncode != 0:
        raise RuntimeError(f"xdotool failed: {r.stderr.strip()[:200]}")


def gui_click(args: dict) -> dict:
    """Click at screen coordinates. {x, y, button?: 'left'|'right'}."""
    try:
        x, err = _coord(args.get("x"), "x")
        if err:
            return err
        y, err = _coord(args.get("y"), "y")
        if err:
            return err
        button = str(args.get("button") or "left").lower()
        if button not in ("left", "right"):
            return {"error": f"'button' must be 'left' or 'right', got '{button}'"}

        pag = _pyautogui()
        if pag is not None:
            pag.click(x, y, button=button)
            return {"clicked": True, "x": x, "y": y, "button": button, "via": "pyautogui"}
        if _is_windows():
            _click_windows(x, y, button)
            return {"clicked": True, "x": x, "y": y, "button": button, "via": "ctypes"}
        if _is_linux():
            ready = _linux_gui_ready("xdotool")
            if ready:
                return ready
            _click_linux(x, y, button)
            return {"clicked": True, "x": x, "y": y, "button": button, "via": "xdotool"}
        return {"error": f"no GUI backend available on {platform.system()} (no pyautogui, no supported OS mechanism)"}
    except Exception as e:
        return _fail("gui.click", e)


# ---------------------------------------------------------------- gui.type

def _type_windows(text: str) -> None:
    import ctypes
    from ctypes import wintypes

    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD),
                    ("ki", KEYBDINPUT)]

    user32 = ctypes.windll.user32
    n = len(text)
    inputs = (INPUT * (n * 2))()
    for i, ch in enumerate(text):
        code = ord(ch)
        inputs[i * 2].type = 1  # INPUT_KEYBOARD
        inputs[i * 2].ki.wScan = code
        inputs[i * 2].ki.dwFlags = KEYEVENTF_UNICODE
        inputs[i * 2 + 1].type = 1
        inputs[i * 2 + 1].ki.wScan = code
        inputs[i * 2 + 1].ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    sent = user32.SendInput(n * 2, inputs, ctypes.sizeof(INPUT))
    if sent != n * 2:
        raise RuntimeError(f"SendInput sent {sent}/{n * 2} events")


def _type_linux(text: str) -> None:
    if not _which("xdotool"):
        raise RuntimeError("xdotool not installed")
    if not _has_display():
        raise RuntimeError("no DISPLAY set (headless)")
    r = _run(["xdotool", "type", "--", text])
    if r.returncode != 0:
        raise RuntimeError(f"xdotool failed: {r.stderr.strip()[:200]}")


def gui_type(args: dict) -> dict:
    """Type text at the focused control. {text} (capped at 2000 chars)."""
    try:
        text = args.get("text")
        if not isinstance(text, str):
            return {"error": f"'text' must be a string, got {type(text).__name__}"}
        if len(text) > _MAX_TYPE_LEN:
            return {"error": f"'text' too long ({len(text)} chars); max is {_MAX_TYPE_LEN}"}

        pag = _pyautogui()
        if pag is not None:
            pag.typewrite(text)
            return {"typed": True, "chars": len(text), "via": "pyautogui"}
        if _is_windows():
            _type_windows(text)
            return {"typed": True, "chars": len(text), "via": "ctypes-sendinput"}
        if _is_linux():
            ready = _linux_gui_ready("xdotool")
            if ready:
                return ready
            _type_linux(text)
            return {"typed": True, "chars": len(text), "via": "xdotool"}
        return {"error": f"no GUI backend available on {platform.system()} (no pyautogui, no supported OS mechanism)"}
    except Exception as e:
        return _fail("gui.type", e)


# -------------------------------------------------------------- gui.hotkey

def _parse_hotkey(keys: str):
    """Split 'ctrl+shift+j' and validate every part against an allowlist.
    Returns (parts, None) or (None, error_dict)."""
    if not isinstance(keys, str) or not keys.strip():
        return None, {"error": "'keys' must be a non-empty string like 'ctrl+shift+j'"}
    raw_parts = keys.strip().lower().split("+")
    parts = []
    for part in raw_parts:
        part = part.strip()
        if part in _MODIFIERS or part in _NAMED_KEYS or _SINGLE_KEY.match(part):
            parts.append(part)
        else:
            return None, {"error": f"invalid hotkey part '{part}': only modifiers (ctrl/alt/shift/win/meta) and alphanumeric or named keys are allowed"}
    if not parts:
        return None, {"error": "'keys' must contain at least one key"}
    return parts, None


_VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20,
    "delete": 0x2E, "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
}
for _m in ("win", "meta", "super", "command"):
    _VK[_m] = 0x5B  # VK_LWIN
_VK["option"] = 0x12  # mac-style alt
for _i in range(1, 13):
    _VK[f"f{_i}"] = 0x6F + _i


def _vk(part: str) -> int:
    if len(part) == 1:
        return ord(part.upper())
    code = _VK.get(part)
    if code is None:
        raise ValueError(f"no virtual-key mapping for '{part}'")
    return code


def _hotkey_windows(parts: list[str]) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    codes = [_vk(p) for p in parts]
    for c in codes:  # key down, in order
        user32.keybd_event(c, 0, 0, 0)
    for c in reversed(codes):  # key up, reverse order
        user32.keybd_event(c, 0, 0x0002, 0)  # KEYEVENTF_KEYUP


_XDOTOOL_KEYMAP = {
    "win": "super", "meta": "super", "command": "super",
    "esc": "Escape", "enter": "Return", "delete": "Delete",
    "backspace": "BackSpace", "tab": "Tab", "space": "space",
}


def _hotkey_linux(parts: list[str]) -> None:
    if not _which("xdotool"):
        raise RuntimeError("xdotool not installed")
    if not _has_display():
        raise RuntimeError("no DISPLAY set (headless)")
    mapped = [_XDOTOOL_KEYMAP.get(p, p) for p in parts]
    r = _run(["xdotool", "key", "+".join(mapped)])
    if r.returncode != 0:
        raise RuntimeError(f"xdotool failed: {r.stderr.strip()[:200]}")


def gui_hotkey(args: dict) -> dict:
    """Press a key combination. {keys} e.g. 'ctrl+shift+j'."""
    try:
        parts, err = _parse_hotkey(args.get("keys"))
        if err:
            return err

        pag = _pyautogui()
        if pag is not None:
            pag.hotkey(*parts)
            return {"pressed": True, "keys": "+".join(parts), "via": "pyautogui"}
        if _is_windows():
            _hotkey_windows(parts)
            return {"pressed": True, "keys": "+".join(parts), "via": "ctypes"}
        if _is_linux():
            ready = _linux_gui_ready("xdotool")
            if ready:
                return ready
            _hotkey_linux(parts)
            return {"pressed": True, "keys": "+".join(parts), "via": "xdotool"}
        return {"error": f"no GUI backend available on {platform.system()} (no pyautogui, no supported OS mechanism)"}
    except Exception as e:
        return _fail("gui.hotkey", e)


# --------------------------------------------------------- gui.windows_list

def _windows_list_windows() -> list[dict]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title:
                    titles.append({"hwnd": int(hwnd), "title": title})
        return True

    if not user32.EnumWindows(_enum_proc, 0):
        raise RuntimeError("EnumWindows failed")
    return titles


def _windows_list_linux() -> list[dict]:
    if not _which("wmctrl"):
        raise RuntimeError("wmctrl not installed")
    if not _has_display():
        raise RuntimeError("no DISPLAY set (headless)")
    r = _run(["wmctrl", "-l"])
    if r.returncode != 0:
        raise RuntimeError(f"wmctrl failed: {r.stderr.strip()[:200]}")
    windows = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 4:
            windows.append({"id": parts[0], "desktop": parts[1],
                            "host": parts[2], "title": parts[3]})
    return windows


def gui_windows_list(args: dict) -> dict:
    """List visible window titles. {}."""
    try:
        if _is_windows():
            windows = _windows_list_windows()
            return {"windows": windows, "count": len(windows), "via": "enumwindows"}
        if _is_linux():
            ready = _linux_gui_ready("wmctrl")
            if ready:
                return ready
            windows = _windows_list_linux()
            return {"windows": windows, "count": len(windows), "via": "wmctrl"}
        return {"error": f"no GUI backend available on {platform.system()} for window listing"}
    except Exception as e:
        return _fail("gui.windows_list", e)


# --------------------------------------------------------- gui.window_focus

def _window_focus_windows(title: str) -> dict:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    needle = title.lower()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                t = buf.value.strip()
                if t and needle in t.lower():
                    found.append((hwnd, t))
        return True

    if not user32.EnumWindows(_enum_proc, 0):
        raise RuntimeError("EnumWindows failed")
    if not found:
        return {"error": f"no visible window matches '{title}'"}
    hwnd, matched = found[0]
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    if not user32.SetForegroundWindow(hwnd):
        return {"error": f"found '{matched}' but SetForegroundWindow was denied"}
    return {"focused": True, "title": matched}


def _window_focus_linux(title: str) -> dict:
    if not _which("wmctrl"):
        raise RuntimeError("wmctrl not installed")
    if not _has_display():
        raise RuntimeError("no DISPLAY set (headless)")
    r = _run(["wmctrl", "-a", title])
    if r.returncode != 0:
        return {"error": f"no window matching '{title}' (wmctrl: {r.stderr.strip()[:200]})"}
    return {"focused": True, "title": title}


def gui_window_focus(args: dict) -> dict:
    """Bring a window to the front by (partial) title. {title}."""
    try:
        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            return {"error": "'title' must be a non-empty string"}
        title = title.strip()
        if _is_windows():
            return _window_focus_windows(title)
        if _is_linux():
            ready = _linux_gui_ready("wmctrl")
            if ready:
                return ready
            return _window_focus_linux(title)
        return {"error": f"no GUI backend available on {platform.system()} for window focus"}
    except Exception as e:
        return _fail("gui.window_focus", e)


# ------------------------------------------------------- gui.mouse_position

def _mouse_position_windows() -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    pt = POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
        raise RuntimeError("GetCursorPos failed")
    return pt.x, pt.y


def gui_mouse_position(args: dict) -> dict:
    """Report the current mouse cursor position. {}."""
    try:
        pag = _pyautogui()
        if pag is not None:
            pos = pag.position()
            return {"x": int(pos[0]), "y": int(pos[1]), "via": "pyautogui"}
        if _is_windows():
            x, y = _mouse_position_windows()
            return {"x": x, "y": y, "via": "ctypes"}
        return {"error": f"no GUI backend available on {platform.system()} (no pyautogui, Windows-only ctypes fallback)"}
    except Exception as e:
        return _fail("gui.mouse_position", e)


# ------------------------------------------------------------------ wiring

TOOL_DEFS = [
    {"name": "gui.screenshot",
     "description": "Capture the screen to a PNG file. Optional 'path'; defaults to /tmp/jarvis-screenshot-<ts>.png. Honest error when headless or no backend.",
     "handler": gui_screenshot, "risk": "low", "needs_network": False,
     "schema": {"path?": "string"}},
    {"name": "gui.click",
     "description": "Click at screen coordinates x, y (integers >= 0). Optional button 'left' (default) or 'right'.",
     "handler": gui_click, "risk": "high", "needs_network": False,
     "schema": {"x": "int", "y": "int", "button?": "left|right"}},
    {"name": "gui.type",
     "description": "Type text at the focused control. Text is capped at 2000 characters to avoid runaway input.",
     "handler": gui_type, "risk": "high", "needs_network": False,
     "schema": {"text": "string"}},
    {"name": "gui.hotkey",
     "description": "Press a key combination like 'ctrl+shift+j'. Parts are allowlist-validated (modifiers, alphanumerics, named keys only).",
     "handler": gui_hotkey, "risk": "high", "needs_network": False,
     "schema": {"keys": "string"}},
    {"name": "gui.windows_list",
     "description": "List visible window titles (Windows: EnumWindows; Linux: wmctrl -l). Honest error when unavailable.",
     "handler": gui_windows_list, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "gui.window_focus",
     "description": "Bring a window to the front by matching (part of) its title. Honest error when the window or backend is missing.",
     "handler": gui_window_focus, "risk": "medium", "needs_network": False,
     "schema": {"title": "string"}},
    {"name": "gui.mouse_position",
     "description": "Report the current mouse cursor position as {x, y}.",
     "handler": gui_mouse_position, "risk": "low", "needs_network": False,
     "schema": {}},
]


def register(reg) -> None:
    """Wire the seven GUI pack tools into a Registry (call from builtin.__init__)."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
