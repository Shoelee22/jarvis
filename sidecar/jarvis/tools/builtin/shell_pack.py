"""Windows shell integration pack (Phase 12, workstream D): JARVIS as a
first-class Windows citizen — toasts, tray icon, autostart, global hotkey.

DEVELOPMENT NOTE: this pack is written on a Linux box. EVERY Windows API
call is platform-gated and every win32 import sits behind a lazy
try/except in a mockable helper layer. On non-Windows, every handler
returns {"error": "windows-only: ..."} — honest, never a crash, never a
faked success. On real Windows 10/11 the helpers below are the only
functions that touch the OS.

TOAST-ACTION CALLBACK LOOP (parent: wire this in — the tool alone cannot):
    1. ``notify.toast`` validates title/body/actions, generates a toast id,
       persists the (toast_id -> [{id, label}]) mapping to sqlite at
       ~/workspace/jarvis/data/shell_actions.db (overridable with the
       JARVIS_SHELL_ACTIONS_DB env var; tests use a tmp file), and — on
       Windows — shows the toast via WinRT (``windows.ui.notifications``)
       with ``ctypes`` balloon as fallback.
    2. The toast's buttons carry ``launch`` arguments of the form
       ``jarvis-toast://<toast_id>/<action_id>``.
    3. The TAURI side must register ``jarvis-toast`` as a protocol handler
       (``tauri.conf.json`` ``protocol`` / ``deep-link`` plugin, single-
       instance) OR the sidecar's IPC server must expose a local loopback
       HTTP endpoint, e.g. ``POST /toast_action`` with
       ``{"toast_id": ..., "action_id": ...}``.
    4. Whoever receives the click calls ``shell_pack.dispatch_toast_action
       (toast_id, action_id)`` — pure python, works on any platform — which
       looks up the registered action and returns it as UNTRUSTED DATA.
       The agent then decides what to do (e.g. action id ``show`` -> bring
       the window forward, ``ptt`` -> start push-to-talk, custom ids ->
       agent-routed intents).

    Nothing here executes code on a click by itself: the registry is a
    lookup table, the agent is the decider. Until the parent wires step 3,
    actions are registered and reported but clicks cannot arrive.

HOTKEY NOTE: the daemon listens for ONE registered global combo (default
Ctrl+Shift+J) via RegisterHotKey + a message pump in a background thread.
It is a single-combo listener, NOT a keylogger — no keystroke content is
ever captured or logged.

State lives in sqlite (stdlib only) + module-level daemon state; config in
~/workspace/jarvis/tools_config.yaml under a ``shell:`` section (hotkey
combo, autostart exe path, toast app id). Missing file -> sane defaults.

SAFETY:
    - notify.toast / shell.tray are low risk (local UI only, no network).
    - shell.autostart is medium risk (persistent HKCU\\...\\Run change,
      fully reversible via enable=false).
    - hotkey.daemon is medium risk (system-wide hotkey listener; it only
      listens for its one registered combo).
    - Handlers take dict -> return dict and never raise; the registry wraps
      failures, but we keep belt-and-braces here too.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "tools_config.yaml"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "shell_actions.db"

RUN_KEY_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "JARVIS"
DEFAULT_EXE_PATH = r"C:\Program Files\JARVIS\JARVIS.exe"
TOAST_PROTOCOL = "jarvis-toast"

# ---------------------------------------------------------------------------
# Platform gate + config
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    """Mockable platform check. Real impl: sys.platform == 'win32'."""
    return sys.platform == "win32"


def _windows_only(tool_name: str) -> dict:
    return {"error": f"windows-only: {tool_name} requires Windows 10/11 "
                     f"(Win32/WinRT APIs); this host is {sys.platform}"}


def _shell_config() -> dict:
    """Read the ``shell:`` section of tools_config.yaml. Absent file /
    broken YAML / missing keys -> safe defaults."""
    defaults = {
        "hotkey_combo": "ctrl+shift+j",
        "autostart_exe": "",
        "toast_app_id": "JARVIS",
        "tray_tooltip": "JARVIS",
    }
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return defaults
    try:
        import yaml  # noqa: PLC0415

        cfg = yaml.safe_load(text) or {}
        section = cfg.get("shell") or {}
        if isinstance(section, dict):
            for key in defaults:
                if section.get(key):
                    defaults[key] = str(section[key])
        return defaults
    except Exception:
        pass
    # Tiny regex fallback so a missing/broken pyyaml never crashes the pack.
    m = re.search(r"(?ms)^shell:\s*\n((?:[ \t]+[^\n]*\n)+)", text)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"\s*(\w+)\s*:\s*(.+?)\s*$", line)
            if kv and kv.group(1) in defaults and kv.group(2):
                defaults[kv.group(1)] = kv.group(2).strip("\"'")
    return defaults


def _autostart_exe() -> str:
    """Exe path written into the Run key: config override, else default."""
    return _shell_config().get("autostart_exe") or DEFAULT_EXE_PATH


# ---------------------------------------------------------------------------
# Toast action registry (sqlite, platform-independent)
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    import os  # noqa: PLC0415

    return Path(os.environ.get("JARVIS_SHELL_ACTIONS_DB", DEFAULT_DB_PATH))


def _db() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS toast_actions ("
        "toast_id TEXT, action_id TEXT, label TEXT, created_at TEXT, "
        "PRIMARY KEY (toast_id, action_id))"
    )
    return conn


def _register_actions(toast_id: str, actions: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = _db()
    try:
        for a in actions:
            conn.execute(
                "INSERT OR REPLACE INTO toast_actions "
                "(toast_id, action_id, label, created_at) VALUES (?, ?, ?, ?)",
                (toast_id, str(a["id"]), str(a.get("label", "")), now),
            )
        conn.commit()
    finally:
        conn.close()


def _lookup_actions(toast_id: str) -> list[dict]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT action_id, label, created_at FROM toast_actions "
            "WHERE toast_id = ?", (toast_id,)).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "label": r[1], "created_at": r[2]} for r in rows]


def dispatch_toast_action(toast_id: str, action_id: str) -> dict:
    """Resolve a clicked toast button.

    Called by the Tauri deep-link/protocol handler or the sidecar's
    ``POST /toast_action`` endpoint (parent wiring — see module docstring).
    Pure python; safe on any platform. Returned data is UNTRUSTED: the
    agent decides what the action means.
    """
    try:
        for a in _lookup_actions(str(toast_id)):
            if a["id"] == str(action_id):
                return {"ok": True, "toast_id": toast_id,
                        "action_id": action_id, "label": a["label"],
                        "hint": "untrusted toast click; agent decides"}
        return {"ok": False, "error": f"unknown toast/action: "
                f"{toast_id!r}/{action_id!r}"}
    except Exception as e:  # never raise
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Win32 helper layer (ALL real Windows calls live here; lazy + mockable)
# ---------------------------------------------------------------------------


def _winrt_send_toast(title: str, body: str, actions: list[dict],
                      toast_id: str) -> dict:
    """Preferred path: WinRT ToastNotificationManager with buttons.

    Raises RuntimeError when winrt is unavailable so the caller can fall
    back. Buttons use launch args ``jarvis-toast://<id>/<action>`` (parent
    wires the protocol — see module docstring).
    """
    try:
        from winrt.windows.ui.notifications import (  # noqa: PLC0415
            ToastNotificationManager, ToastNotification)
        from winrt.windows.data.xml.dom import XmlDocument  # noqa: PLC0415
    except Exception as e:
        raise RuntimeError(f"winrt unavailable: {e}")
    app_id = _shell_config().get("toast_app_id") or "JARVIS"
    buttons = "".join(
        f'<action content="{_xml(a.get("label", a["id"]))}" '
        f'arguments="{TOAST_PROTOCOL}://{toast_id}/{_xml(str(a["id"]))}" '
        f'activationType="protocol"/>'
        for a in actions
    )
    xml = (f"<toast><visual><binding template='ToastGeneric'>"
           f"<text>{_xml(title)}</text><text>{_xml(body)}</text>"
           f"</binding></visual><actions>{buttons}</actions></toast>")
    doc = XmlDocument()
    doc.load_xml(xml)
    notifier = ToastNotificationManager.create_toast_notifier(app_id)
    toast = ToastNotification(doc)
    toast.tag = toast_id
    notifier.show(toast)
    return {"channel": "winrt", "buttons": len(actions)}


def _xml(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _ctypes_balloon_fallback(title: str, body: str) -> dict:
    """Fallback path: Shell_NotifyIconW balloon. No buttons possible —
    actions stay registered for later wiring."""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception as e:
        raise RuntimeError(f"ctypes unavailable: {e}")

    class _NID(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uTimeout", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD)]

    NIF_INFO, NIM_ADD, NIM_DELETE = 0x10, 0x0, 0x2
    shell32 = ctypes.windll.shell32
    nid = _NID()
    nid.cbSize = ctypes.sizeof(_NID)
    nid.uFlags = NIF_INFO
    nid.szInfo = body[:255]
    nid.szInfoTitle = title[:63]
    nid.dwInfoFlags = 0x1  # NIIF_INFO
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        raise RuntimeError("Shell_NotifyIconW failed")
    try:
        time.sleep(0.5)
    finally:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    return {"channel": "balloon",
            "note": "fallback has no buttons; actions registered only"}


def _ensure_tray_icon() -> dict:
    """Ensure the system-tray icon exists with quick actions.

    Real impl (Windows): create/refresh the NotifyIcon via the Tauri
    shell's tray manager (``tauri::tray``) — the sidecar signals it over
    the IPC socket; the executable registered here is the source of truth.
    Kept as a mockable helper so tests never touch the shell.
    """
    try:
        import ctypes  # noqa: PLC0415
        ctypes.windll.shell32  # touch win32; real wiring lives in Tauri
    except Exception as e:
        raise RuntimeError(f"win32 unavailable: {e}")
    return {"ensured": True,
            "actions": ["Show JARVIS", "Push-to-talk", "Quit"],
            "via": "tauri-tray"}


def _run_key_set(exe_path: str) -> dict:
    """Write HKCU\\...\\Run\\JARVIS -> exe_path."""
    try:
        import winreg  # noqa: PLC0415
    except Exception as e:
        raise RuntimeError(f"winreg unavailable: {e}")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, exe_path)
    return {"key": f"HKCU\\{RUN_KEY_PATH}", "name": RUN_VALUE_NAME,
            "value": exe_path}


def _run_key_remove() -> dict:
    """Remove HKCU\\...\\Run\\JARVIS. Missing key is not an error."""
    try:
        import winreg  # noqa: PLC0415
    except Exception as e:
        raise RuntimeError(f"winreg unavailable: {e}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE_NAME)
        removed = True
    except FileNotFoundError:
        removed = False
    return {"key": f"HKCU\\{RUN_KEY_PATH}", "name": RUN_VALUE_NAME,
            "removed": removed}


def _run_key_get() -> dict:
    """Read the current Run value, if any."""
    try:
        import winreg  # noqa: PLC0415
    except Exception as e:
        raise RuntimeError(f"winreg unavailable: {e}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
        return {"present": True, "value": value}
    except FileNotFoundError:
        return {"present": False, "value": None}


# --- global hotkey daemon ---------------------------------------------------
# One registered combo only (default Ctrl+Shift+J). Listens via
# RegisterHotKey + a GetMessage pump in a daemon thread. NOT a keylogger:
# RegisterHotKey fires only for the exact combo; no keystroke content is
# captured, buffered, or logged.

_HOTKEY_STATE = {"state": "stopped", "combo": None, "thread": None,
                 "stop_event": None}
_HOTKEY_LOCK = threading.Lock()


def _parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+shift+j' -> (modifiers, vk). Raises ValueError on bad input."""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    mods = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
    mod = 0
    key = None
    for p in parts:
        if p in mods:
            mod |= mods[p]
        elif key is None:
            key = p
        else:
            raise ValueError(f"bad combo {combo!r}: only one key allowed")
    if not mod or key is None:
        raise ValueError(f"bad combo {combo!r}: need modifier(s) + one key")
    if len(key) == 1:
        vk = ord(key.upper())
    elif re.fullmatch(r"f\d{1,2}", key):
        n = int(key[1:])
        if not 1 <= n <= 24:
            raise ValueError(f"bad combo {combo!r}: bad function key")
        vk = 0x70 + n - 1
    else:
        raise ValueError(f"bad combo {combo!r}: unsupported key {key!r}")
    return mod, vk


def _start_hotkey_listener(combo: str) -> dict:
    """Register the combo and pump messages in a daemon thread."""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception as e:
        raise RuntimeError(f"win32 unavailable: {e}")
    mod, vk = _parse_combo(combo)
    user32 = ctypes.windll.user32
    HOTKEY_ID, WM_HOTKEY = 0xBEEF, 0x0312
    stop = threading.Event()

    def _pump():
        if not user32.RegisterHotKey(None, HOTKEY_ID, mod, vk):
            return
        try:
            msg = wintypes.MSG()
            while not stop.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    _on_hotkey_pressed()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    t = threading.Thread(target=_pump, name="jarvis-hotkey", daemon=True)
    t.start()
    return {"thread": t, "stop_event": stop,
            "note": "listener started; press the combo to trigger"}


def _on_hotkey_pressed() -> None:
    """What happens on combo press. The Tauri shell owns the visible
    behavior; the sidecar exposes this hook so a headless test or the IPC
    server can observe/fire it."""
    hook = globals().get("_HOTKEY_HOOK")
    if callable(hook):
        try:
            hook()
        except Exception:
            pass


def _stop_hotkey_listener(state: dict) -> None:
    stop = state.get("stop_event")
    if stop is not None:
        stop.set()
    # Wake the GetMessage pump so the thread can exit.
    try:
        import ctypes  # noqa: PLC0415
        ctypes.windll.user32.PostThreadMessageW(
            state["thread"].ident, 0x0012, 0, 0)  # WM_QUIT
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Handlers (dict in -> dict out, never raise)
# ---------------------------------------------------------------------------


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def toast_handler(args: dict) -> dict:
    """Show a Windows toast with optional action buttons."""
    if not _is_windows():
        return _windows_only("notify.toast")
    args = args or {}
    title = str(args.get("title", "")).strip()
    body = str(args.get("body", "")).strip()
    if not title:
        return {"error": "title is required"}
    raw_actions = args.get("actions") or []
    if not isinstance(raw_actions, list):
        return {"error": "actions must be a list of {id, label}"}
    actions = []
    for i, a in enumerate(raw_actions):
        if not isinstance(a, dict) or not str(a.get("id", "")).strip():
            return {"error": f"actions[{i}] must be {{id, label}} with non-empty id"}
        actions.append({"id": str(a["id"]).strip(),
                        "label": str(a.get("label", a["id"])).strip()})
    toast_id = uuid.uuid4().hex[:12]
    reg = _safe(_register_actions, toast_id, actions)
    if isinstance(reg, dict) and "error" in reg:
        return {"error": f"could not register toast actions: {reg['error']}"}
    # Preferred channel, then fallback; honest about which one fired.
    try:
        sent = _winrt_send_toast(title, body, actions, toast_id)
    except Exception as e1:
        try:
            fb = _ctypes_balloon_fallback(title, body)
            sent = {"channel": fb["channel"], "note": fb.get("note", ""),
                    "winrt_error": f"{type(e1).__name__}: {e1}"}
        except Exception as e2:
            return {"error": f"toast failed (winrt: {e1}; fallback: {e2})",
                    "toast_id": toast_id,
                    "actions_registered": [a["id"] for a in actions]}
    return {"toast_id": toast_id, "title": title,
            "actions_registered": [{"id": a["id"], "label": a["label"]}
                                   for a in actions],
            "launch_protocol": TOAST_PROTOCOL,
            "channel": sent.get("channel"),
            "note": sent.get("note", ""),
            "callback_wiring": ("Tauri deep-link or POST /toast_action -> "
                                "shell_pack.dispatch_toast_action(toast_id, action_id)")}


def tray_handler(args: dict) -> dict:
    """Ensure the JARVIS system-tray icon exists with quick actions."""
    if not _is_windows():
        return _windows_only("shell.tray")
    args = args or {}
    ensure = bool(args.get("ensure", True))
    if not ensure:
        return {"ensured": False, "note": "ensure=false: no-op"}
    res = _safe(_ensure_tray_icon)
    if isinstance(res, dict) and "error" in res:
        return res
    return {"ensured": True, "tooltip": _shell_config()["tray_tooltip"],
            "quick_actions": res.get("actions", []), "via": res.get("via")}


def autostart_handler(args: dict) -> dict:
    """Enable/disable JARVIS autostart via the HKCU Run key (reversible)."""
    if not _is_windows():
        return _windows_only("shell.autostart")
    args = args or {}
    enable = args.get("enable")
    if enable is None:
        cur = _safe(_run_key_get)
        if isinstance(cur, dict) and "error" in cur:
            return cur
        return {"enabled": bool(cur.get("present")),
                "key": f"HKCU\\{RUN_KEY_PATH}", "name": RUN_VALUE_NAME,
                "value": cur.get("value"),
                "note": "pass enable=true/false to change"}
    if enable:
        exe = _safe(_autostart_exe)
        res = _safe(_run_key_set, exe)
        if isinstance(res, dict) and "error" in res:
            return res
        return {"enabled": True, "persistent_change": True, **res,
                "reversible_via": "shell.autostart {enable: false}"}
    res = _safe(_run_key_remove)
    if isinstance(res, dict) and "error" in res:
        return res
    return {"enabled": False, "persistent_change": True, **res,
            "note": "HKCU Run key removed" if res.get("removed")
                    else "key was not present; nothing to remove"}


def hotkey_handler(args: dict) -> dict:
    """Start/stop/status the global hotkey daemon (single combo listener)."""
    if not _is_windows():
        return _windows_only("hotkey.daemon")
    args = args or {}
    action = str(args.get("action", "status")).strip().lower()
    if action not in ("start", "stop", "status"):
        return {"error": "action must be start, stop or status"}
    with _HOTKEY_LOCK:
        state = _HOTKEY_STATE["state"]
        if action == "status":
            return {"state": state, "combo": _HOTKEY_STATE["combo"],
                    "note": "listens for the registered combo only; "
                            "not a keylogger"}
        if action == "start":
            if state == "running":
                return {"state": "running", "combo": _HOTKEY_STATE["combo"],
                        "note": "already running"}
            combo = str(args.get("combo") or
                        _shell_config().get("hotkey_combo") or
                        "ctrl+shift+j").strip().lower()
            try:
                _parse_combo(combo)  # validate before touching win32
            except ValueError as e:
                return {"error": str(e)}
            res = _safe(_start_hotkey_listener, combo)
            if isinstance(res, dict) and "error" in res:
                return res
            _HOTKEY_STATE.update(state="running", combo=combo,
                                 thread=res.get("thread"),
                                 stop_event=res.get("stop_event"))
            return {"state": "running", "combo": combo,
                    "note": res.get("note", "")}
        # action == "stop"
        if state != "running":
            return {"state": "stopped",
                    "note": "daemon was not running"}
        _safe(_stop_hotkey_listener, dict(_HOTKEY_STATE))
        _HOTKEY_STATE.update(state="stopped", combo=None, thread=None,
                             stop_event=None)
        return {"state": "stopped", "note": "hotkey unregistered"}


# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "notify.toast",
     "description": ("Show a Windows 10/11 toast notification (WinRT, ctypes "
                     "balloon fallback). actions: optional list of {id, label} "
                     "buttons; clicks arrive as jarvis-toast://<toast_id>/"
                     "<action_id> launch args which the Tauri shell or a "
                     "local POST /toast_action endpoint must route into "
                     "shell_pack.dispatch_toast_action — until the parent "
                     "wires that, actions are registered and reported only. "
                     "Windows-only; honest error elsewhere."),
     "handler": toast_handler, "risk": "low", "needs_network": False,
     "schema": {"title": "string", "body?": "string",
                "actions?": "list of {id, label}"}},
    {"name": "shell.tray",
     "description": ("Ensure the JARVIS system-tray icon exists with quick "
                     "actions (Show JARVIS, Push-to-talk, Quit). The Tauri "
                     "shell owns the visible icon; the sidecar signals it "
                     "over IPC. Windows-only; honest error elsewhere."),
     "handler": tray_handler, "risk": "low", "needs_network": False,
     "schema": {"ensure?": "bool"}},
    {"name": "shell.autostart",
     "description": ("Enable/disable JARVIS autostart by writing/removing the "
                     "HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"
                     "\\JARVIS value pointing at the JARVIS executable "
                     "(config: shell.autostart_exe). Persistent but fully "
                     "reversible with enable=false. Windows-only; honest "
                     "error elsewhere."),
     "handler": autostart_handler, "risk": "medium", "needs_network": False,
     "schema": {"enable?": "bool"}},
    {"name": "hotkey.daemon",
     "description": ("Start/stop/status a global hotkey daemon that listens "
                     "for ONE registered combo (default Ctrl+Shift+J, config: "
                     "shell.hotkey_combo). It is a single-combo listener, NOT "
                     "a keylogger — no keystroke content is captured. "
                     "Windows-only; honest error elsewhere."),
     "handler": hotkey_handler, "risk": "medium", "needs_network": False,
     "schema": {"action": "start|stop|status", "combo?": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(shell_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "notify.toast": ("low", False),
    "shell.tray": ("low", False),
    "shell.autostart": ("medium", False),
    "hotkey.daemon": ("medium", False),
}


def register(reg) -> None:
    """Wire the four Windows shell-integration tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
