"""Unit tests for the Phase 6 System Tool Pack. All offline: platform.system,
shutil.which, subprocess.run and the _run/_ps helpers are monkeypatched to
exercise every OS branch on this Linux box; disk.usage / ps / /proc/uptime
are exercised for real."""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "sidecar")  # noqa: E402

from jarvis.tools.builtin import system_pack as sp  # noqa: E402


class R:
    """Fake subprocess result."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _platform(monkeypatch, name):
    monkeypatch.setattr(sp.platform, "system", lambda: name)


def _which(monkeypatch, mapping):
    monkeypatch.setattr(sp.shutil, "which",
                        lambda name: mapping.get(name))


def _run(monkeypatch, fake):
    monkeypatch.setattr(sp, "_run", fake)


def _ps(monkeypatch, fake):
    monkeypatch.setattr(sp, "_ps", fake)


def _enabled_ok(monkeypatch):
    monkeypatch.setattr(sp, "_enabled", lambda name: None)


# -------------------------------------------------------------- notify.send
def test_notify_send_linux_notify_send(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {"notify-send": "/usr/bin/notify-send"})
    _run(monkeypatch, lambda cmd, timeout=10: R(0))
    _enabled_ok(monkeypatch)
    r = sp.notify_send({"title": "hi", "body": "hello"})
    assert r == {"sent": True, "via": "notify-send"}


def test_notify_send_linux_no_mechanism(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {})
    _enabled_ok(monkeypatch)
    r = sp.notify_send({"title": "hi"})
    assert r["error"] == "no notification mechanism on this machine"


def test_notify_send_windows_winrt(monkeypatch):
    _platform(monkeypatch, "Windows")
    _ps(monkeypatch, lambda *a, **k: R(0))
    _enabled_ok(monkeypatch)
    r = sp.notify_send({"title": "hi", "body": "yo"})
    assert r == {"sent": True, "via": "winrt-toast"}


def test_notify_send_macos_osascript(monkeypatch):
    _platform(monkeypatch, "Darwin")
    _which(monkeypatch, {"osascript": "/usr/bin/osascript"})
    _run(monkeypatch, lambda cmd, timeout=10: R(0))
    _enabled_ok(monkeypatch)
    r = sp.notify_send({"title": "hi"})
    assert r == {"sent": True, "via": "osascript"}


def test_notify_send_disabled(monkeypatch):
    monkeypatch.setattr(sp, "_enabled", lambda name: {"error": "disabled"})
    r = sp.notify_send({"title": "hi"})
    assert r == {"error": "disabled"}


# ------------------------------------------------------------ screen.capture
def test_screen_capture_linux_scrot(monkeypatch, tmp_path):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {"scrot": "/usr/bin/scrot"})  # import + gnome missing

    def fake_run(cmd, timeout=30):
        Path(cmd[-1]).touch()
        return R(0)
    _run(monkeypatch, fake_run)
    _enabled_ok(monkeypatch)
    out = str(tmp_path / "shot.png")
    r = sp.screen_capture({"out": out})
    assert r["saved"] == out and r["via"] == "scrot"
    assert Path(out).is_file()


def test_screen_capture_linux_import_first(monkeypatch, tmp_path):
    # ImageMagick 'import' is tried before scrot.
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {"import": "/usr/bin/import", "scrot": "/usr/bin/scrot"})
    seen = []

    def fake_run(cmd, timeout=30):
        seen.append(cmd[0])
        Path(cmd[-1]).touch()
        return R(0)
    _run(monkeypatch, fake_run)
    _enabled_ok(monkeypatch)
    r = sp.screen_capture({"out": str(tmp_path / "s.png")})
    assert seen == ["import"] and r["via"] == "import"


def test_screen_capture_linux_no_mechanism(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {})
    _enabled_ok(monkeypatch)
    r = sp.screen_capture({})
    assert "no screenshot mechanism" in r["error"]


def test_screen_capture_windows(monkeypatch, tmp_path):
    _platform(monkeypatch, "Windows")

    def fake_ps(script, env=None, timeout=30):
        Path(env["JARVIS_SCREEN_OUT"]).touch()
        return R(0)
    _ps(monkeypatch, fake_ps)
    _enabled_ok(monkeypatch)
    out = str(tmp_path / "w.png")
    r = sp.screen_capture({"out": out})
    assert r["saved"] == out and r["via"] == "windows-forms"


def test_screen_capture_macos(monkeypatch, tmp_path):
    _platform(monkeypatch, "Darwin")
    _which(monkeypatch, {"screencapture": "/usr/sbin/screencapture"})

    def fake_run(cmd, timeout=30):
        Path(cmd[-1]).touch()
        return R(0)
    _run(monkeypatch, fake_run)
    _enabled_ok(monkeypatch)
    out = str(tmp_path / "m.png")
    r = sp.screen_capture({"out": out})
    assert r["saved"] == out and r["via"] == "screencapture"


# ----------------------------------------------------------- clipboard.write
def test_clipboard_write_linux_xclip(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {"xclip": "/usr/bin/xclip"})
    seen = {}

    def fake_run(cmd, input=None, capture_output=False, timeout=10):
        seen["cmd"] = cmd
        seen["input"] = input
        return R(0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    _enabled_ok(monkeypatch)
    r = sp.clipboard_write({"text": "hello clipboard"})
    assert r["written"] is True and r["via"] == "xclip"
    assert seen["input"] == b"hello clipboard"


def test_clipboard_write_linux_falls_back_to_xsel(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {"xsel": "/usr/bin/xsel"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(0))
    _enabled_ok(monkeypatch)
    r = sp.clipboard_write({"text": "x"})
    assert r["via"] == "xsel"


def test_clipboard_write_linux_no_mechanism(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {})
    _enabled_ok(monkeypatch)
    r = sp.clipboard_write({"text": "x"})
    assert "no clipboard mechanism" in r["error"]


def test_clipboard_write_macos_pbcopy(monkeypatch):
    _platform(monkeypatch, "Darwin")
    _which(monkeypatch, {"pbcopy": "/usr/bin/pbcopy"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(0))
    _enabled_ok(monkeypatch)
    r = sp.clipboard_write({"text": "hi"})
    assert r == {"written": True, "chars": 2, "via": "pbcopy"}


def test_clipboard_write_windows(monkeypatch):
    _platform(monkeypatch, "Windows")
    _ps(monkeypatch, lambda *a, **k: R(0))
    _enabled_ok(monkeypatch)
    r = sp.clipboard_write({"text": "hi"})
    assert r["via"] == "powershell" and r["chars"] == 2


def test_clipboard_write_missing_text():
    r = sp.clipboard_write({})
    assert r["error"] == "text is required"


# ------------------------------------------------------------ battery.status
def test_battery_status_linux_no_battery(monkeypatch):
    # This VM has no BAT* entries: the honest-error path runs for real.
    _platform(monkeypatch, "Linux")
    _enabled_ok(monkeypatch)
    assert list(Path("/sys/class/power_supply").glob("BAT*")) == []
    r = sp.battery_status({})
    assert r["error"] == "no battery info on this machine"


def test_battery_status_windows(monkeypatch):
    _platform(monkeypatch, "Windows")
    _ps(monkeypatch, lambda *a, **k: R(0, stdout='{"EstimatedChargeRemaining": 87, "BatteryStatus": 2}'))
    _enabled_ok(monkeypatch)
    r = sp.battery_status({})
    assert r["percent"] == 87 and r["charging"] is True and r["via"] == "wmi"


def test_battery_status_windows_absent(monkeypatch):
    _platform(monkeypatch, "Windows")
    _ps(monkeypatch, lambda *a, **k: R(0, stdout=""))
    _enabled_ok(monkeypatch)
    r = sp.battery_status({})
    assert r["error"] == "no battery info on this machine"


def test_battery_status_macos_pmset(monkeypatch):
    _platform(monkeypatch, "Darwin")
    _which(monkeypatch, {"pmset": "/usr/bin/pmset"})
    _run(monkeypatch, lambda cmd, timeout=15: R(0, stdout=" -InternalBattery-0  87%; discharging; 4:12 remaining\n"))
    _enabled_ok(monkeypatch)
    r = sp.battery_status({})
    assert r["percent"] == 87 and r["charging"] is False and r["via"] == "pmset"


# ---------------------------------------------------------------- disk.usage
def test_disk_usage_real_home():
    r = sp.disk_usage({})
    assert set(r) == {"path", "total_gb", "used_gb", "free_gb", "percent"}
    assert r["total_gb"] > 0 and r["free_gb"] >= 0
    assert 0 <= r["percent"] <= 100
    assert r["path"] == str(Path.home())


def test_disk_usage_bad_path():
    r = sp.disk_usage({"path": "/no/such/dir/xyz"})
    assert "does not exist" in r["error"]


# --------------------------------------------------------------- process.top
def test_process_top_real_ps():
    # Real ps on this Linux box; offline and fast.
    r = sp.process_top({"n": 5})
    assert "processes" in r and len(r["processes"]) <= 5
    p = r["processes"][0]
    assert set(p) == {"pid", "cpu", "mem", "name"}
    assert isinstance(p["pid"], int) and isinstance(p["name"], str)


def test_process_top_windows(monkeypatch):
    _platform(monkeypatch, "Windows")
    _ps(monkeypatch, lambda *a, **k: R(0, stdout='[{"Id": 1234, "ProcessName": "python", "CPU": 12.5, "MemMB": 100.2}]'))
    _enabled_ok(monkeypatch)
    r = sp.process_top({"n": 3})
    assert r["processes"] == [{"pid": 1234, "cpu": 12.5, "mem": 100.2, "name": "python"}]


def test_process_top_ps_failure(monkeypatch):
    _platform(monkeypatch, "Linux")
    _which(monkeypatch, {"ps": "/usr/bin/ps"})
    _run(monkeypatch, lambda cmd, timeout=20: R(1, stderr="boom"))
    _enabled_ok(monkeypatch)
    r = sp.process_top({})
    assert "ps failed" in r["error"]


# -------------------------------------------------------------- system.uptime
def test_system_uptime_real_linux():
    # Real /proc/uptime on this box.
    r = sp.system_uptime({})
    assert r["seconds"] > 0 and r["via"] == "procfs"
    assert "s" in r["uptime_human"]


def test_system_uptime_windows(monkeypatch):
    _platform(monkeypatch, "Windows")
    _ps(monkeypatch, lambda *a, **k: R(0, stdout="3661"))
    _enabled_ok(monkeypatch)
    r = sp.system_uptime({})
    assert r["seconds"] == 3661 and r["uptime_human"] == "1h 1m 1s"


def test_system_uptime_macos(monkeypatch):
    _platform(monkeypatch, "Darwin")
    _which(monkeypatch, {"sysctl": "/usr/sbin/sysctl"})
    boot = int(time.time()) - 7200
    _run(monkeypatch, lambda cmd, timeout=10: R(0, stdout=f"kern.boottime: {{ sec = {boot}, usec = 0 }} something"))
    _enabled_ok(monkeypatch)
    r = sp.system_uptime({})
    assert 7195 <= r["seconds"] <= 7205 and r["via"] == "sysctl"


def test_system_uptime_unknown_os(monkeypatch):
    _platform(monkeypatch, "SunOS")
    _enabled_ok(monkeypatch)
    r = sp.system_uptime({})
    assert r["error"] == "could not determine uptime on this machine"
