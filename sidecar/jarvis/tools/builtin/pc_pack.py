"""PC Management Tool Pack (Phase 8): Windows winget package management,
SHA256-based duplicate finder, largest-file scanner, disk reports, and
startup-item listing.

Every handler takes a dict and returns a dict, NEVER raises. Platform-only
tools return an honest {"error": ...} when the machine cannot do them —
no fake data.

Risk notes: pc.winget_install is "high" (modifies the machine by design);
everything else here is read-only ("low"). winget_search / winget_install
need network access to package sources.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

OSNAME = platform.system()

# ------------------------------------------------------------ helpers

_WINGET_NOT_WINDOWS = {"error": "winget is Windows-only"}

_PKG_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_MAX_WALK_FILES = 50000          # duplicate scan file cap
_HASH_CHUNK = 1024 * 1024        # 1 MiB read chunks for hashing
_OUTPUT_LINE_CAP = 40            # winget output lines returned
_OUTPUT_CHAR_CAP = 8000          # winget output chars returned


def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _winget_bin() -> str | None:
    return shutil.which("winget")


def _run_winget(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _capped_tail(text: str, lines: int = _OUTPUT_LINE_CAP,
                 chars: int = _OUTPUT_CHAR_CAP) -> str:
    kept = "\n".join(text.splitlines()[-lines:])
    return kept[-chars:]


# --------------------------------------------------- pc.winget_search
def winget_search(args: dict) -> dict:
    """Search winget packages (Windows only)."""
    try:
        if OSNAME != "Windows":
            return dict(_WINGET_NOT_WINDOWS)
        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}
        if shutil.which("winget") is None:
            return {"error": "winget not found on PATH"}
        r = _run_winget(["winget", "search", query], timeout=60)
        if r.returncode != 0 and not r.stdout.strip():
            return {"error": f"winget search failed (exit {r.returncode}): "
                             f"{_capped_tail(r.stderr or '', lines=10, chars=500)}"}
        return {"query": query, "exit_code": r.returncode,
                "results": _capped_tail(r.stdout)}
    except Exception as e:
        return _fail("pc.winget_search", e)


# --------------------------------------------------- pc.winget_install
def winget_install(args: dict) -> dict:
    """Install a winget package silently (Windows only). HIGH risk."""
    try:
        if OSNAME != "Windows":
            return dict(_WINGET_NOT_WINDOWS)
        package_id = str(args.get("package_id", "")).strip()
        if not package_id:
            return {"error": "package_id is required"}
        if not _PKG_ID_RE.match(package_id):
            return {"error": "invalid package_id: must match "
                             r"^[A-Za-z0-9._-]+$"}
        if shutil.which("winget") is None:
            return {"error": "winget not found on PATH"}
        r = _run_winget([
            "winget", "install", package_id,
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ], timeout=1800)
        return {"package_id": package_id, "exit_code": r.returncode,
                "success": r.returncode == 0,
                "output_tail": _capped_tail(r.stdout + "\n" + r.stderr)}
    except Exception as e:
        return _fail("pc.winget_install", e)


# ------------------------------------------------------ pc.winget_list
def winget_list(args: dict) -> dict:
    """List installed winget packages (Windows only): count + sample."""
    try:
        if OSNAME != "Windows":
            return dict(_WINGET_NOT_WINDOWS)
        if shutil.which("winget") is None:
            return {"error": "winget not found on PATH"}
        r = _run_winget(["winget", "list"], timeout=60)
        if r.returncode != 0 and not r.stdout.strip():
            return {"error": f"winget list failed (exit {r.returncode}): "
                             f"{_capped_tail(r.stderr or '', lines=10, chars=500)}"}
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        # Skip the winget header block (first two non-empty lines: header + dashes).
        data = lines[2:] if len(lines) > 2 else []
        return {"count": len(data), "exit_code": r.returncode,
                "sample": data[:_OUTPUT_LINE_CAP]}
    except Exception as e:
        return _fail("pc.winget_list", e)


# ---------------------------------------------------- jail for scanners
def _scan_root(value: str | None) -> Path:
    """Resolve a user-supplied scan dir; refuse nonexistent paths and
    absurdly broad roots ("/" on Linux, drive root on Windows)."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("dir is required")
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"dir does not exist or is not a directory: {raw}")
    if OSNAME == "Windows":
        if p == Path(p.anchor):
            raise ValueError("refusing to scan a drive root directly — "
                             "pick a real subdirectory")
    elif p == Path("/"):
        raise ValueError('refusing to scan "/" directly — '
                         "pick a real subdirectory")
    return p


def _iter_files(root: Path) -> "list[Path]":
    """Recursive file walk, skipping symlinks, capped at _MAX_WALK_FILES."""
    found: list[Path] = []
    stack = [root]
    while stack and len(found) < _MAX_WALK_FILES:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            found.append(Path(entry.path))
                            if len(found) >= _MAX_WALK_FILES:
                                break
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _sha256(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


# ----------------------------------------------------- pc.duplicates
def duplicates(args: dict) -> dict:
    """REAL SHA256 duplicate finder. Read-only: never modifies files."""
    try:
        root = _scan_root(args.get("dir"))
    except ValueError as e:
        return {"error": str(e)}
    try:
        try:
            min_size_kb = int(args.get("min_size_kb", 100))
        except (TypeError, ValueError):
            return {"error": "min_size_kb must be an integer"}
        min_size = max(0, min_size_kb) * 1024

        files = _iter_files(root)

        # Pass 1: group by size (cheap), ignore tiny files.
        by_size: dict[int, list[Path]] = {}
        for f in files:
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size < min_size:
                continue
            by_size.setdefault(size, []).append(f)

        # Pass 2: full SHA256 of size-collision candidates only.
        groups: list[dict] = []
        wasted = 0
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            by_hash: dict[str, list[Path]] = {}
            for p in paths:
                digest = _sha256(p)
                if digest is None:
                    continue
                by_hash.setdefault(digest, []).append(p)
            for digest, dupes in by_hash.items():
                if len(dupes) < 2:
                    continue
                groups.append({
                    "sha256": digest,
                    "size_bytes": size,
                    "paths": sorted(str(p) for p in dupes),
                    "wasted_bytes": size * (len(dupes) - 1),
                })
                wasted += size * (len(dupes) - 1)

        groups.sort(key=lambda g: g["wasted_bytes"], reverse=True)
        return {"dir": str(root),
                "files_scanned": len(files),
                "min_size_kb": min_size_kb,
                "truncated": len(files) >= _MAX_WALK_FILES,
                "duplicate_groups": groups,
                "group_count": len(groups),
                "wasted_bytes": wasted,
                "note": "read-only scan; nothing was deleted"}
    except Exception as e:
        return _fail("pc.duplicates", e)


# ------------------------------------------------------ pc.big_files
def big_files(args: dict) -> dict:
    """Top-N largest files under dir, sorted desc."""
    try:
        root = _scan_root(args.get("dir"))
    except ValueError as e:
        return {"error": str(e)}
    try:
        try:
            n = int(args.get("n", 20))
        except (TypeError, ValueError):
            return {"error": "n must be an integer"}
        n = max(1, min(n, 200))

        files = _iter_files(root)
        sized: list[tuple[int, str]] = []
        for f in files:
            try:
                sized.append((f.stat().st_size, str(f)))
            except OSError:
                continue
        sized.sort(key=lambda t: t[0], reverse=True)
        top = [{"path": p, "size_bytes": s, "size_mb": round(s / 1048576, 2)}
               for s, p in sized[:n]]
        return {"dir": str(root), "files_scanned": len(files),
                "truncated": len(files) >= _MAX_WALK_FILES,
                "top": top, "n": n}
    except Exception as e:
        return _fail("pc.big_files", e)


# ---------------------------------------------------- pc.disk_report
def disk_report(args: dict) -> dict:
    """Disk usage for home + every drive/mount found, human-readable GB."""
    try:
        targets: list[str] = []
        seen: set[str] = set()

        def add(label: str, path: str):
            rp = str(Path(path).resolve())
            if rp.lower() not in seen:
                seen.add(rp.lower())
                targets.append((label, path))

        add("home", str(Path.home()))
        if OSNAME == "Windows":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    add(f"drive {letter}:", drive)
        elif OSNAME == "Darwin":
            vol = Path("/Volumes")
            if vol.is_dir():
                for child in vol.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        add(f"volume {child.name}", str(child))
        else:  # Linux
            try:
                with open("/proc/mounts") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) < 3:
                            continue
                        dev, mnt, fstype = parts[0], parts[1], parts[2]
                        if fstype in {"tmpfs", "devtmpfs", "proc", "sysfs",
                                      "cgroup", "cgroup2", "overlay",
                                      "squashfs", "debugfs", "tracefs",
                                      "fusectl", "securityfs", "devpts",
                                      "mqueue", "shm", "hugetlbfs",
                                      "pstore", "configfs", "autofs"}:
                            continue
                        if not dev.startswith("/") and not mnt.startswith("/"):
                            continue
                        add(f"mount {mnt}", mnt)
            except OSError:
                pass

        disks = []
        for label, path in targets:
            try:
                total, used, free = shutil.disk_usage(path)
                if total <= 0:
                    continue  # pseudo-mount with no size (e.g. some fuse mounts)
                disks.append({"label": label, "path": path,
                              "total_gb": round(total / 1073741824, 2),
                              "used_gb": round(used / 1073741824, 2),
                              "free_gb": round(free / 1073741824, 2),
                              "used_pct": round(used / total * 100, 1)
                              if total else 0.0})
            except OSError as e:
                disks.append({"label": label, "path": path,
                              "error": str(e)})
        return {"os": OSNAME, "disks": disks}
    except Exception as e:
        return _fail("pc.disk_report", e)


# --------------------------------------------------- pc.startup_list
def startup_list(args: dict) -> dict:
    """List OS startup/autostart entries. Read-only on every platform."""
    try:
        if OSNAME == "Windows":
            import winreg
            entries = []
            keys = [
                (winreg.HKEY_CURRENT_USER,
                 r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"Software\Microsoft\Windows\CurrentVersion\Run"),
            ]
            for hive, subkey in keys:
                hive_name = "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM"
                try:
                    with winreg.OpenKey(hive, subkey) as k:
                        i = 0
                        while True:
                            try:
                                name, value, _ = winreg.EnumValue(k, i)
                            except OSError:
                                break
                            entries.append({"hive": hive_name, "name": name,
                                            "command": str(value)})
                            i += 1
                except OSError:
                    continue
            return {"os": OSNAME, "entries": entries,
                    "count": len(entries), "via": "winreg Run keys"}
        if OSNAME == "Linux":
            autostart = Path.home() / ".config" / "autostart"
            entries = []
            if autostart.is_dir():
                for desktop in sorted(autostart.glob("*.desktop")):
                    exec_line = ""
                    name = desktop.stem
                    try:
                        text = desktop.read_text(errors="replace")
                        for line in text.splitlines():
                            if line.startswith("Exec="):
                                exec_line = line[5:].strip()
                            elif line.startswith("Name=") and name == desktop.stem:
                                name = line[5:].strip()
                    except OSError:
                        continue
                    entries.append({"name": name, "file": str(desktop),
                                    "exec": exec_line})
            return {"os": OSNAME, "entries": entries, "count": len(entries),
                    "via": "~/.config/autostart/*.desktop"}
        if OSNAME == "Darwin":
            agents = Path.home() / "Library" / "LaunchAgents"
            entries = []
            if agents.is_dir():
                for plist in sorted(agents.glob("*.plist")):
                    entries.append({"name": plist.name, "path": str(plist)})
            return {"os": OSNAME, "entries": entries, "count": len(entries),
                    "via": "~/Library/LaunchAgents"}
        return {"error": f"startup listing not supported on {OSNAME}"}
    except Exception as e:
        return _fail("pc.startup_list", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "pc.winget_search",
     "description": "Search for a package with winget (Windows only).",
     "handler": winget_search, "risk": "low", "needs_network": True,
     "schema": {"query": "string"}},
    {"name": "pc.winget_install",
     "description": "Silently install a winget package. MODIFIES THE MACHINE.",
     "handler": winget_install, "risk": "high", "needs_network": True,
     "schema": {"package_id": "string"}},
    {"name": "pc.winget_list",
     "description": "Installed winget packages: count + sample (Windows only).",
     "handler": winget_list, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "pc.duplicates",
     "description": "SHA256 duplicate-file finder under dir (read-only): "
                    "groups + wasted bytes.",
     "handler": duplicates, "risk": "low", "needs_network": False,
     "schema": {"dir": "string", "min_size_kb": "int?"}},
    {"name": "pc.big_files",
     "description": "Top-N largest files under dir, sorted desc.",
     "handler": big_files, "risk": "low", "needs_network": False,
     "schema": {"dir": "string", "n": "int?"}},
    {"name": "pc.disk_report",
     "description": "Disk usage for home + every drive/mount, in GB.",
     "handler": disk_report, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "pc.startup_list",
     "description": "List OS startup/autostart entries (read-only).",
     "handler": startup_list, "risk": "low", "needs_network": False,
     "schema": {}},
]
