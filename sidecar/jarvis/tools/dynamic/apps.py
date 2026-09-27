"""App launcher provider (Phase 7 / Worker 5): real installed-application
discovery, scanned lazily and cached with a 60s TTL.

Namespaces:
  app.open.<slug>   launch the application            (risk: medium)
  app.focus.<slug>  best-effort foreground focus       (risk: medium)

Platforms:
  Windows: %APPDATA%/Microsoft/Windows/Start Menu and
           %PROGRAMDATA%/Microsoft/Windows/Start Menu, recursive *.lnk
           (name from filename; launch via os.startfile).
  Linux:   /usr/share/applications + ~/.local/share/applications *.desktop
           (parse Name= and Exec=, strip % field codes; launch via
           subprocess.Popen(shlex.split(exec))).
  macOS:   /Applications/*.app (+ ~/Applications); launch via `open -a`.

Slugs: lowercase, runs of non-alnum become "_", deduplicated with a numeric
suffix on collision. Every handler takes a dict and returns a dict; handlers
never raise -- failures come back as {"error": ...}.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ..base import Tool
from .providers import Provider

try:  # parallel worker contract
    from .config import section
except ImportError:  # pragma: no cover - defensive; config.py ships alongside
    def section(name, defaults=None):  # type: ignore[no-redef]
        return dict(defaults) if defaults else {}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "app"


# ------------------------------------------------------------ scanning
def _scan_windows() -> list[dict]:
    apps: list[dict] = []
    for base in (os.environ.get("APPDATA", ""), os.environ.get("PROGRAMDATA", "")):
        if not base:
            continue
        root = Path(base) / "Microsoft" / "Windows" / "Start Menu"
        try:
            if not root.is_dir():
                continue
            for lnk in root.rglob("*.lnk"):
                apps.append({"name": lnk.stem, "kind": "lnk", "path": str(lnk)})
        except OSError:
            continue
    return apps


_FIELD_CODE_RE = re.compile(r"%[a-zA-Z]")


def _parse_desktop(path: Path) -> dict | None:
    """Parse Name= / Exec= out of a .desktop file; None when unusable.

    Never raises; tolerant of weird lines (bad encoding, missing keys,
    NoDisplay/Hidden entries, non-Application types).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    name: str | None = None
    exec_line: str | None = None
    dtype: str | None = None
    in_entry = False
    try:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                in_entry = line == "[Desktop Entry]"
                continue
            if not in_entry or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key == "Name" and name is None:
                name = val
            elif key == "Exec" and exec_line is None:
                exec_line = val
            elif key == "Type" and dtype is None:
                dtype = val
            elif key in ("NoDisplay", "Hidden") and val.lower() == "true":
                return None
    except Exception:
        return None
    if dtype is not None and dtype != "Application":
        return None
    if not name or not exec_line:
        return None
    exec_clean = _FIELD_CODE_RE.sub("", exec_line).strip()
    if not exec_clean:
        return None
    return {"name": name, "kind": "desktop", "exec": exec_clean, "path": str(path)}


def _scan_linux() -> list[dict]:
    apps: list[dict] = []
    dirs = [Path("/usr/share/applications"),
            Path.home() / ".local" / "share" / "applications"]
    for d in dirs:
        try:
            if not d.is_dir():
                continue
            for desktop in sorted(d.glob("*.desktop")):
                entry = _parse_desktop(desktop)
                if entry:
                    apps.append(entry)
        except OSError:
            continue
    return apps


def _scan_macos() -> list[dict]:
    apps: list[dict] = []
    for base in (Path("/Applications"), Path.home() / "Applications"):
        try:
            if not base.is_dir():
                continue
            for bundle in sorted(base.glob("*.app")):
                apps.append({"name": bundle.stem, "kind": "app", "path": str(bundle)})
        except OSError:
            continue
    return apps


def _scan_all() -> list[dict]:
    """Scan this machine; slugify + dedupe. Never raises."""
    try:
        if sys.platform == "win32":
            raw = _scan_windows()
        elif sys.platform == "darwin":
            raw = _scan_macos()
        else:
            raw = _scan_linux()
    except Exception:
        raw = []
    seen: set[str] = set()
    out: list[dict] = []
    for entry in raw:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        base_slug = _slugify(name)
        slug = base_slug
        i = 2
        while slug in seen:
            slug = f"{base_slug}_{i}"
            i += 1
        seen.add(slug)
        entry["name"] = name
        entry["slug"] = slug
        out.append(entry)
    return out


# ------------------------------------------------------------ actions
def _launch(entry: dict) -> dict:
    """Launch an app entry. Never raises."""
    try:
        kind = entry.get("kind")
        if kind == "lnk":
            if sys.platform != "win32":
                return {"error": "lnk launch is only supported on Windows"}
            os.startfile(entry["path"])  # noqa: SLF -- Windows only, guarded
        elif kind == "desktop":
            exec_line = entry.get("exec") or ""
            try:
                argv = shlex.split(exec_line)
            except ValueError:
                argv = exec_line.split()
            if not argv:
                return {"error": f"could not parse Exec line for '{entry.get('name')}'"}
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        elif kind == "app":
            subprocess.Popen(["open", "-a", entry["path"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return {"error": f"unknown app kind '{kind}'"}
        return {"opened": entry.get("name")}
    except Exception as e:
        return {"error": f"launch failed: {type(e).__name__}: {e}"}


def _focus_windows(entry: dict) -> dict:
    """Best-effort foreground focus on Windows via SetForegroundWindow."""
    stem = re.sub(r"[^a-z0-9]", "", (entry.get("name") or "").lower()) or "app"
    script = (
        "Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;"
        "public class W32{[DllImport(\"user32.dll\")]"
        "public static extern bool SetForegroundWindow(IntPtr hWnd);}}';"
        f"$p=Get-Process|Where{{$_.MainWindowHandle -ne [IntPtr]::Zero "
        f"-and $_.ProcessName -like '*{stem}*'}}|Select-Object -First 1;"
        "if($p){[W32]::SetForegroundWindow($p.MainWindowHandle)|Out-Null;"
        "'focused:'+$p.ProcessName}else{'not-found'}"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15)
        if (proc.stdout or "").strip().lower().startswith("focused"):
            return {"focused": entry.get("name")}
        return {"error": "focus not supported on this setup"}
    except Exception:
        return {"error": "focus not supported on this setup"}


def _focus(entry: dict) -> dict:
    """Best-effort focus. Never raises; honest when unsupported."""
    try:
        if sys.platform == "win32":
            return _focus_windows(entry)
        return {"error": f"focus not implemented on {sys.platform}; use app.open"}
    except Exception as e:
        return {"error": f"focus failed: {type(e).__name__}: {e}"}


# ------------------------------------------------------------ provider
class AppsProvider(Provider):
    """Discovers installed applications and exposes open/focus tools."""

    namespace = "app"

    def __init__(self, data_dir=None):
        super().__init__(data_dir)
        self._apps: list[dict] | None = None
        self._scanned_at = 0.0

    def _ttl(self) -> float:
        try:
            return max(5.0, float(section("apps", {}).get("scan_ttl", 60.0)))
        except Exception:
            return 60.0

    def _apps_list(self) -> list[dict]:
        now = time.monotonic()
        if self._apps is None or (now - self._scanned_at) > self._ttl():
            self._apps = _scan_all()
            self._scanned_at = now
        return self._apps

    def _by_slug(self) -> dict[str, dict]:
        return {a["slug"]: a for a in self._apps_list()}

    # -- Provider contract -------------------------------------------
    def expand(self) -> int:
        """2 x app_count (open + focus per app). Cheap: uses the TTL cache."""
        return 2 * len(self._apps_list())

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        parts = name.split(".")
        if len(parts) != 3 or parts[1] not in ("open", "focus"):
            return None
        entry = self._by_slug().get(parts[2])
        if entry is None:
            return None
        if parts[1] == "open":
            def _open_handler(args: dict, _e=entry) -> dict:
                return _launch(_e)
            return Tool(
                name=name,
                description=f"Launch the '{entry['name']}' application.",
                schema={},
                handler=_open_handler,
                risk="medium",
                needs_network=False,
            )
        def _focus_handler(args: dict, _e=entry) -> dict:
            return _focus(_e)
        return Tool(
            name=name,
            description=(f"Bring the '{entry['name']}' window to the foreground "
                         f"(best effort; falls back to app.open)."),
            schema={},
            handler=_focus_handler,
            risk="medium",
            needs_network=False,
        )

    def sample_names(self, n: int = 5) -> list[str]:
        slugs = sorted(a["slug"] for a in self._apps_list())
        return [f"app.open.{s}" for s in slugs[: max(0, n)]]
