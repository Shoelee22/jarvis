"""System Tool Pack (Phase 6): desktop OS integrations — notifications,
screenshots, clipboard, battery, disk, processes, uptime.

Every handler takes a dict and returns a dict, NEVER raises. Platform-only
tools try the real platform mechanism for the current OS and return an
honest {"error": ...} when the machine cannot do it — no fake data.

Toggles and output dir come from ~/workspace/jarvis/tools_config.yaml via
creator's config loader (system_pack.yaml fragment holds the tunables).
Do NOT duplicate clipboard.read (lives in misc.py).
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
from pathlib import Path

from .creator import _load_config, _enabled, _run, _output_dir

# ------------------------------------------------------------------ helpers

_TIMEOUTS = {}


def _timeout(key: str, default: int) -> int:
    cfg = _load_config().get("system_pack", {})
    try:
        return max(1, int(cfg.get(key, default)))
    except (TypeError, ValueError):
        return default


def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _ps(powershell_script: str, env: dict | None = None, timeout: int = 15):
    """Run a PowerShell script; prefer powershell.exe, fall back to pwsh."""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise FileNotFoundError("no powershell/pwsh on PATH")
    merged = dict(os.environ)
    if env:
        merged.update(env)
    import subprocess
    return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command",
                           powershell_script],
                          capture_output=True, text=True, timeout=timeout,
                          env=merged)


def _human_seconds(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    if m or h or d:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# -------------------------------------------------------------- notify.send
_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName("text")
$texts[0].AppendChild($xml.CreateTextNode($env:JARVIS_NOTIFY_TITLE)) | Out-Null
$texts[1].AppendChild($xml.CreateTextNode($env:JARVIS_NOTIFY_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Jarvis").Show($toast)
"""


def notify_send(args: dict) -> dict:
    """Best-effort desktop notification. Returns {sent, via}."""
    try:
        blocked = _enabled("notify.send")
        if blocked:
            return blocked
        title = str(args.get("title", "Jarvis"))
        body = str(args.get("body", ""))
        osname = platform.system()
        timeout = _timeout("notify_timeout", 10)
        if osname == "Windows":
            r = _ps(_TOAST_PS,
                    env={"JARVIS_NOTIFY_TITLE": title, "JARVIS_NOTIFY_BODY": body},
                    timeout=timeout)
            if r.returncode != 0:
                return {"error": f"windows toast failed: {r.stderr[-300:]}"}
            return {"sent": True, "via": "winrt-toast"}
        if osname == "Darwin":
            if not shutil.which("osascript"):
                return {"error": "no notification mechanism on this machine"}
            r = _run(["osascript", "-e",
                      f'display notification {json.dumps(body)} with title {json.dumps(title)}'],
                     timeout=timeout)
            if r.returncode != 0:
                return {"error": f"osascript notification failed: {r.stderr[-300:]}"}
            return {"sent": True, "via": "osascript"}
        # Linux
        if not shutil.which("notify-send"):
            return {"error": "no notification mechanism on this machine"}
        r = _run(["notify-send", title, body], timeout=timeout)
        if r.returncode != 0:
            return {"error": f"notify-send failed: {r.stderr[-300:]}"}
        return {"sent": True, "via": "notify-send"}
    except Exception as e:
        return _fail("notify.send", e)


# ------------------------------------------------------------ screen.capture
_SCREEN_PS = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save($env:JARVIS_SCREEN_OUT, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"""


def screen_capture(args: dict) -> dict:
    """Screenshot the primary display to a PNG in the output dir."""
    try:
        blocked = _enabled("screen.capture")
        if blocked:
            return blocked
        out_arg = str(args.get("out", "")).strip()
        out = Path(out_arg).expanduser() if out_arg else \
            _output_dir() / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
        if out.suffix.lower() != ".png":
            out = out.with_suffix(".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        osname = platform.system()
        timeout = _timeout("capture_timeout", 30)
        if osname == "Windows":
            r = _ps(_SCREEN_PS, env={"JARVIS_SCREEN_OUT": str(out)}, timeout=timeout)
            if r.returncode != 0:
                return {"error": f"windows screenshot failed: {r.stderr[-300:]}"}
        elif osname == "Darwin":
            if not shutil.which("screencapture"):
                return {"error": "no screenshot mechanism on this machine"}
            r = _run(["screencapture", "-x", str(out)], timeout=timeout)
            if r.returncode != 0:
                return {"error": f"screencapture failed: {r.stderr[-300:]}"}
        else:  # Linux: ImageMagick import, then scrot, then gnome-screenshot
            tried = []
            done = False
            candidates = [
                ("import", ["import", "-window", "root", str(out)]),
                ("scrot", ["scrot", str(out)]),
                ("gnome-screenshot", ["gnome-screenshot", "-f", str(out)]),
            ]
            for name, cmd in candidates:
                if not shutil.which(name):
                    tried.append(f"{name}: not installed")
                    continue
                r = _run(cmd, timeout=timeout)
                if r.returncode == 0 and out.is_file():
                    done = True
                    via = name
                    break
                tried.append(f"{name}: exit {r.returncode}")
            if not done:
                return {"error": "no screenshot mechanism on this machine: " +
                                 "; ".join(tried)}
            return {"saved": str(out), "via": via}
        if not out.is_file():
            return {"error": "screenshot command ran but produced no file"}
        return {"saved": str(out), "via": "windows-forms" if osname == "Windows" else "screencapture"}
    except Exception as e:
        return _fail("screen.capture", e)


# ----------------------------------------------------------- clipboard.write
def clipboard_write(args: dict) -> dict:
    """Write text to the OS clipboard."""
    try:
        blocked = _enabled("clipboard.write")
        if blocked:
            return blocked
        text = args.get("text")
        if text is None:
            return {"error": "text is required"}
        text = str(text)
        osname = platform.system()
        timeout = _timeout("clipboard_timeout", 10)
        if osname == "Windows":
            r = _ps("Set-Clipboard -Value $env:JARVIS_CLIP_TEXT",
                    env={"JARVIS_CLIP_TEXT": text}, timeout=timeout)
            if r.returncode != 0:
                return {"error": f"Set-Clipboard failed: {r.stderr[-300:]}"}
            return {"written": True, "chars": len(text), "via": "powershell"}
        if osname == "Darwin":
            if not shutil.which("pbcopy"):
                return {"error": "no clipboard mechanism on this machine"}
            import subprocess
            r = subprocess.run(["pbcopy"], input=text.encode("utf-8"),
                               capture_output=True, timeout=timeout)
            if r.returncode != 0:
                return {"error": f"pbcopy failed: {r.stderr.decode()[-300:]}"}
            return {"written": True, "chars": len(text), "via": "pbcopy"}
        # Linux: xclip, xsel, or wl-copy — whichever exists
        import subprocess
        tried = []
        for name, cmd in (("xclip", ["xclip", "-selection", "clipboard"]),
                          ("xsel", ["xsel", "--clipboard", "--input"]),
                          ("wl-copy", ["wl-copy"])):
            if not shutil.which(name):
                tried.append(f"{name}: not installed")
                continue
            r = subprocess.run(cmd, input=text.encode("utf-8"),
                               capture_output=True, timeout=timeout)
            if r.returncode == 0:
                return {"written": True, "chars": len(text), "via": name}
            tried.append(f"{name}: exit {r.returncode}")
        return {"error": "no clipboard mechanism on this machine: " + "; ".join(tried)}
    except Exception as e:
        return _fail("clipboard.write", e)


# ------------------------------------------------------------ battery.status
_BATTERY_PS = ("Get-WmiObject Win32_Battery | "
               "Select-Object EstimatedChargeRemaining, BatteryStatus | "
               "ConvertTo-Json")


def battery_status(args: dict) -> dict:
    """Battery percent + charging state. Honest error on desktops/VMs."""
    try:
        blocked = _enabled("battery.status")
        if blocked:
            return blocked
        osname = platform.system()
        if osname == "Linux":
            bases = sorted(Path("/sys/class/power_supply").glob("BAT*"))
            if not bases:
                return {"error": "no battery info on this machine"}
            bat = bases[0]
            try:
                percent = int((bat / "capacity").read_text().strip())
            except OSError:
                percent = None
            try:
                state = (bat / "status").read_text().strip().lower()
            except OSError:
                state = "unknown"
            charging = {"charging": True, "discharging": False,
                        "full": False, "not charging": False}.get(state, None)
            return {"percent": percent, "status": state, "charging": charging,
                    "via": "sysfs"}
        if osname == "Windows":
            r = _ps(_BATTERY_PS, timeout=15)
            if r.returncode != 0 or not r.stdout.strip():
                return {"error": "no battery info on this machine"}
            data = json.loads(r.stdout)
            if isinstance(data, list):
                data = data[0] if data else {}
            percent = data.get("EstimatedChargeRemaining")
            status_code = data.get("BatteryStatus")
            charging = status_code == 2 if status_code is not None else None
            return {"percent": percent, "battery_status_code": status_code,
                    "charging": charging, "via": "wmi"}
        if osname == "Darwin":
            if not shutil.which("pmset"):
                return {"error": "no battery info on this machine"}
            r = _run(["pmset", "-g", "batt"], timeout=15)
            if r.returncode != 0 or not r.stdout.strip():
                return {"error": "no battery info on this machine"}
            m = re.search(r"(\d+)%;\s*([a-zA-Z]+);", r.stdout)
            if not m:
                return {"error": "no battery info on this machine"}
            state = m.group(2).lower()
            return {"percent": int(m.group(1)), "status": state,
                    "charging": state == "charging", "via": "pmset"}
        return {"error": "no battery info on this machine"}
    except Exception as e:
        return _fail("battery.status", e)


# ---------------------------------------------------------------- disk.usage
def disk_usage(args: dict) -> dict:
    """Disk usage for a path (default: home). Cross-platform via shutil."""
    try:
        blocked = _enabled("disk.usage")
        if blocked:
            return blocked
        path = str(args.get("path", "")).strip() or str(Path.home())
        p = Path(path).expanduser()
        if not p.exists():
            return {"error": f"path does not exist: {p}"}
        total, used, free = shutil.disk_usage(p)
        gb = 1024 ** 3
        return {
            "path": str(p),
            "total_gb": round(total / gb, 2),
            "used_gb": round(used / gb, 2),
            "free_gb": round(free / gb, 2),
            "percent": round(used / total * 100, 1) if total else 0.0,
        }
    except Exception as e:
        return _fail("disk.usage", e)


# --------------------------------------------------------------- process.top
def process_top(args: dict) -> dict:
    """Top N processes by CPU. Returns a list of {pid, cpu, mem, name}."""
    try:
        blocked = _enabled("process.top")
        if blocked:
            return blocked
        try:
            n = max(1, min(100, int(args.get("n", 10))))
        except (TypeError, ValueError):
            n = 10
        osname = platform.system()
        if osname == "Windows":
            ps = (f"Get-Process | Sort-Object CPU -Descending | "
                  f"Select-Object -First {n} Id, ProcessName, CPU, "
                  "@{n='MemMB';e={[math]::Round($_.WorkingSet/1MB,1)}} | "
                  "ConvertTo-Json")
            r = _ps(ps, timeout=20)
            if r.returncode != 0 or not r.stdout.strip():
                return {"error": f"Get-Process failed: {r.stderr[-300:]}"}
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                data = [data]
            procs = [{"pid": int(d.get("Id", 0)),
                      "cpu": float(d.get("CPU") or 0.0),
                      "mem": float(d.get("MemMB") or 0.0),
                      "name": str(d.get("ProcessName", ""))} for d in data]
            return {"processes": procs, "via": "get-process"}
        if osname in ("Linux", "Darwin"):
            if not shutil.which("ps"):
                return {"error": "ps not available on this machine"}
            r = _run(["ps", "-eo", "pid,pcpu,pmem,comm", "--sort=-pcpu"],
                     timeout=20)
            if r.returncode != 0:
                return {"error": f"ps failed: {r.stderr[-300:]}"}
            procs = []
            for line in r.stdout.splitlines()[1:n + 1]:
                parts = line.split(None, 3)
                if len(parts) != 4:
                    continue
                try:
                    procs.append({"pid": int(parts[0]), "cpu": float(parts[1]),
                                  "mem": float(parts[2]), "name": parts[3]})
                except ValueError:
                    continue
            return {"processes": procs, "via": "ps"}
        return {"error": "process listing not supported on this OS"}
    except Exception as e:
        return _fail("process.top", e)


# -------------------------------------------------------------- system.uptime
_UPTIME_PS = ("[math]::Round(((Get-Date) - "
              "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalSeconds)")


def system_uptime(args: dict) -> dict:
    """System uptime as {uptime_human, seconds}."""
    try:
        blocked = _enabled("system.uptime")
        if blocked:
            return blocked
        osname = platform.system()
        seconds: float | None = None
        via = ""
        if osname == "Linux":
            try:
                seconds = float(Path("/proc/uptime").read_text().split()[0])
                via = "procfs"
            except OSError:
                pass
        elif osname == "Darwin":
            if shutil.which("sysctl"):
                r = _run(["sysctl", "-n", "kern.boottime"], timeout=10)
                m = re.search(r"sec\s*=\s*(\d+)", r.stdout or "")
                if r.returncode == 0 and m:
                    seconds = time.time() - int(m.group(1))
                    via = "sysctl"
        elif osname == "Windows":
            r = _ps(_UPTIME_PS, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                seconds = float(r.stdout.strip())
                via = "cim"
        if seconds is None:
            return {"error": "could not determine uptime on this machine"}
        return {"uptime_human": _human_seconds(seconds),
                "seconds": int(seconds), "via": via}
    except Exception as e:
        return _fail("system.uptime", e)
