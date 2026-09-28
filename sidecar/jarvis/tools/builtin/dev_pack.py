"""DEV Tool Pack (Phase 6 / Pack 4): git inspection, project scaffolding,
linting, dependency inventory, log tailing, port probing, and an offline
environment doctor.

All handlers are dict in -> dict out and NEVER raise. Every {path}/{repo}
argument is jailed: it must resolve inside dev.repos_dir (default
~/workspace) or the call is rejected with {"error": ...}.

Read-only git tools only (status/log/diff) — nothing here bypasses the
destructive-command guardrails that govern shell.exec.

Registration note: this module exposes a TOOLS list (name, description,
handler, risk, needs_network, schema) so the builtin __init__.py wire-up
can register each handler without extra glue.
"""
from __future__ import annotations

import re
import shutil
import socket
import subprocess
from pathlib import Path

from . import creator  # REUSE: _load_config(), _enabled(), _run()

HOME = Path.home()
DEFAULT_REPOS_DIR = HOME / "workspace"

# tool probes for env.doctor: key -> candidate binaries, in order
_DOCTOR_TOOLS = {
    "python3": ["python3", "python"],
    "node": ["node"],
    "npm": ["npm"],
    "rustc": ["rustc"],
    "cargo": ["cargo"],
    "git": ["git"],
    "ffmpeg": ["ffmpeg"],
    "chrome": ["google-chrome", "chrome", "chrome-headless-shell"],
    "chromium": ["chromium", "chromium-browser"],
}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DIFF_CAP = 20000
_TAIL_CAP = 200


# ------------------------------------------------------------ jail helpers
def _repos_dir() -> Path:
    """Jail root from tools_config.yaml dev.repos_dir (default ~/workspace)."""
    raw = creator._load_config().get("dev", {}).get("repos_dir", str(DEFAULT_REPOS_DIR))
    return Path(str(raw)).expanduser().resolve()


def _jailed(value: str | None, *, allow_home: bool = False,
            default: Path | None = None) -> Path:
    """Resolve a user-supplied path inside the jail.

    Relative values are resolved against repos_dir; absolute values are
    accepted only if they resolve inside repos_dir (or, when allow_home,
    anywhere under $HOME). Raises ValueError on jail violation so callers
    can turn it into an {"error"} dict.
    """
    root = _repos_dir()
    if not value:
        if default is None:
            raise ValueError("path is required")
        return default
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()
    if p.is_relative_to(root):
        return p
    if allow_home and p.is_relative_to(HOME):
        return p
    raise ValueError(f"path '{value}' is outside the allowed root {root}")


def _jail(value: str | None, **kw) -> tuple[Path | None, dict | None]:
    try:
        return _jailed(value, **kw), None
    except ValueError as e:
        return None, {"error": str(e)}
    except Exception as e:
        return None, {"error": f"invalid path: {type(e).__name__}: {e}"}


def _git(args: dict) -> tuple[Path | None, dict | None]:
    """Resolve the repo arg and run a git command there. Returns (path, err)."""
    path, err = _jail(args.get("repo"), default=_repos_dir())
    if err:
        return None, err
    try:
        if not path.is_dir():
            return None, {"error": f"repo path is not a directory: {path}"}
    except Exception as e:
        return None, {"error": f"cannot stat repo path: {type(e).__name__}: {e}"}
    return path, None


def _run_git(path: Path, *git_args: str, timeout: int = 30):
    """Run git -C <path> ... via the shared _run helper. Returns (proc, err)."""
    try:
        proc = creator._run(["git", "-C", str(path), *git_args], timeout=timeout)
    except Exception as e:
        return None, {"error": f"git failed to run: {type(e).__name__}: {e}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = detail[0] if detail else f"git exited {proc.returncode}"
        return None, {"error": f"git error: {msg[:300]}"}
    return proc, None


# ---------------------------------------------------------------- git.status
def git_status(args: dict) -> dict:
    """Porcelain status of a repo. Low risk."""
    blocked = creator._enabled("git.status")
    if blocked:
        return blocked
    path, err = _git(args)
    if err:
        return err
    proc, err = _run_git(path, "status", "--porcelain")
    if err:
        return err
    changes = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        xy, name = line[:2], line[3:]
        if " -> " in name:  # renames show as "old -> new"
            name = name.split(" -> ", 1)[1]
        changes.append({"status": xy.strip(), "path": name})
    return {"repo": str(path), "clean": not changes, "changes": changes}


# ------------------------------------------------------------------- git.log
def git_log(args: dict) -> dict:
    """Recent commits as [{hash, author, date, subject}]. Low risk."""
    blocked = creator._enabled("git.log")
    if blocked:
        return blocked
    path, err = _git(args)
    if err:
        return err
    try:
        n = max(1, min(100, int(args.get("n", 10))))
    except (TypeError, ValueError):
        n = 10
    proc, err = _run_git(
        path, "log", f"-n{n}",
        "--pretty=format:%H%x1f%an%x1f%ad%x1f%s", "--date=iso")
    if err:
        return err
    commits = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        h, author, date, subject = parts
        commits.append({"hash": h, "author": author, "date": date,
                        "subject": subject})
    return {"repo": str(path), "commits": commits}


# ------------------------------------------------------------------ git.diff
def git_diff(args: dict) -> dict:
    """Working-tree (or --staged) diff, capped at 20k chars. Low risk."""
    blocked = creator._enabled("git.diff")
    if blocked:
        return blocked
    path, err = _git(args)
    if err:
        return err
    cmd = ["diff", "--staged"] if args.get("staged") else ["diff"]
    proc, err = _run_git(path, *cmd, timeout=30)
    if err:
        return err
    text = proc.stdout
    truncated = len(text) > _DIFF_CAP
    return {"repo": str(path), "staged": bool(args.get("staged")),
            "diff": text[:_DIFF_CAP], "truncated": truncated}


# ------------------------------------------------------- project.scaffold
def _python_tree(root: Path, name: str) -> None:
    pkg = name.lower().replace("-", "_")
    (root / pkg).mkdir(parents=True)
    (root / pkg / "__init__.py").write_text(
        f'"""Top-level package for {name}."""\n\n__version__ = "0.1.0"\n')
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / f"test_{pkg}.py").write_text(
        f'"""Smoke tests for {name}."""\n\n\ndef test_import():\n'
        f'    import {pkg}\n    assert {pkg}.__version__ == "0.1.0"\n')
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        f'description = ""\nrequires-python = ">=3.11"\ndependencies = []\n\n'
        f'[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n')
    (root / "README.md").write_text(
        f"# {name}\n\nScaffolded by Jarvis dev pack.\n\n"
        f"```sh\npip install -e .\npytest\n```\n")


def _static_tree(root: Path, name: str) -> None:
    (root / "index.html").write_text(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<main>
  <h1>{name}</h1>
  <p>Built with the Jarvis dev pack. Edit me.</p>
</main>
<script src="app.js"></script>
</body>
</html>
""")
    (root / "styles.css").write_text(
        "*,*::before,*::after{box-sizing:border-box;margin:0}\n"
        "body{font-family:system-ui,sans-serif;line-height:1.6;padding:2rem}\n"
        "main{max-width:40rem;margin:0 auto}\n")
    (root / "app.js").write_text(
        "// Entry point — wire up your UI here.\n"
        "document.addEventListener('DOMContentLoaded', () => {\n"
        "  console.log('ready');\n});\n")


_SCAFFOLDERS = {"python": _python_tree, "static-site": _static_tree}


def project_scaffold(args: dict) -> dict:
    """Create a real starter project under repos_dir/<name>. Medium risk."""
    blocked = creator._enabled("project.scaffold")
    if blocked:
        return blocked
    kind = args.get("kind", "")
    name = (args.get("name") or "").strip()
    if kind not in _SCAFFOLDERS:
        return {"error": f"kind must be one of {sorted(_SCAFFOLDERS)}"}
    if not _NAME_RE.match(name):
        return {"error": ("name must be a simple directory name "
                          "(letters, digits, '-', '_')")}
    root = _repos_dir() / name
    try:
        if root.exists():
            return {"error": f"refusing to overwrite existing directory: {root}"}
        root.mkdir(parents=True)
        _SCAFFOLDERS[kind](root, name)
    except Exception as e:
        return {"error": f"scaffold failed: {type(e).__name__}: {e}"}
    files = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    return {"kind": kind, "name": name, "path": str(root), "files": files}


# ----------------------------------------------------------------- code.lint
def code_lint(args: dict) -> dict:
    """Syntax-check a Python file with py_compile. Low risk."""
    blocked = creator._enabled("code.lint")
    if blocked:
        return blocked
    path, err = _jail(args.get("path"))
    if err:
        return err
    try:
        if not path.is_file():
            return {"error": f"not a file: {path}"}
        proc = creator._run(["python3", "-m", "py_compile", str(path)],
                            timeout=30)
    except Exception as e:
        return {"error": f"lint failed to run: {type(e).__name__}: {e}"}
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"path": str(path), "ok": proc.returncode == 0, "errors": out}


# ----------------------------------------------------------------- deps.list
def _parse_requirements(text: str) -> list[str]:
    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        deps.append(line)
    return deps


def _parse_pyproject(text: str) -> list[str]:
    try:  # tomllib ships with Python 3.11+
        import tomllib
        deps = tomllib.loads(text).get("project", {}).get("dependencies", [])
        return [str(d) for d in deps]
    except Exception:
        pass
    # fallback: grab quoted strings from a dependencies = [ ... ] block
    m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
    if not m:
        return []
    pairs = re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1))
    return [a or b for a, b in pairs]


def deps_list(args: dict) -> dict:
    """List DECLARED deps from requirements.txt/pyproject.toml (not installed)."""
    blocked = creator._enabled("deps.list")
    if blocked:
        return blocked
    path, err = _jail(args.get("path"), default=_repos_dir())
    if err:
        return err
    try:
        if not path.is_dir():
            return {"error": f"not a directory: {path}"}
        req = path / "requirements.txt"
        toml = path / "pyproject.toml"
        if req.is_file():
            declared = _parse_requirements(req.read_text(errors="replace"))
            manifest = "requirements.txt"
        elif toml.is_file():
            declared = _parse_pyproject(toml.read_text(errors="replace"))
            manifest = "pyproject.toml"
        else:
            return {"error": "no manifest found (looked for requirements.txt, "
                             "pyproject.toml)"}
    except Exception as e:
        return {"error": f"deps.list failed: {type(e).__name__}: {e}"}
    return {"path": str(path), "manifest": manifest, "declared": declared,
            "note": "declared in the manifest, not necessarily installed"}


# ------------------------------------------------------------------ log.tail
def log_tail(args: dict) -> dict:
    """Return the last N lines of a text log. Rejects binary files."""
    blocked = creator._enabled("log.tail")
    if blocked:
        return blocked
    path, err = _jail(args.get("path"), allow_home=True)
    if err:
        return err
    try:
        lines = int(args.get("lines", 50))
    except (TypeError, ValueError):
        lines = 50
    lines = max(1, min(_TAIL_CAP, lines))
    try:
        if not path.is_file():
            return {"error": f"not a file: {path}"}
        raw = path.read_bytes()
    except Exception as e:
        return {"error": f"cannot read file: {type(e).__name__}: {e}"}
    if b"\x00" in raw[:8192]:
        return {"error": "binary file detected (null bytes) — refusing to tail"}
    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    tail = all_lines[-lines:]
    return {"path": str(path), "lines": tail, "shown": len(tail),
            "total_lines": len(all_lines)}


# ----------------------------------------------------------------- port.check
def port_check(args: dict) -> dict:
    """TCP connect probe with a 3s timeout. Low risk, local only by default."""
    blocked = creator._enabled("port.check")
    if blocked:
        return blocked
    host = str(args.get("host", "127.0.0.1") or "127.0.0.1")
    try:
        port = int(args["port"])
        if not 1 <= port <= 65535:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"error": "port is required and must be an integer 1-65535"}
    try:
        with socket.create_connection((host, port), timeout=3):
            open_ = True
    except (socket.timeout, ConnectionRefusedError, OSError):
        open_ = False
    except Exception as e:
        return {"error": f"port check failed: {type(e).__name__}: {e}"}
    return {"host": host, "port": port, "open": open_}


# ----------------------------------------------------------------- env.doctor
def _probe_version(binary: str) -> str | None:
    """Return the first line of `<binary> --version`, or None."""
    try:
        proc = creator._run([binary, "--version"], timeout=10)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    line = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return line[0][:200] if line else None


def env_doctor(args: dict) -> dict:
    """Offline probe: which + --version for common dev tools. Low risk."""
    blocked = creator._enabled("env.doctor")
    if blocked:
        return blocked
    tools: dict[str, dict] = {}
    for name, candidates in _DOCTOR_TOOLS.items():
        binary = next((w for c in candidates if (w := shutil.which(c))), None)
        if binary is None:
            tools[name] = {"found": False, "error": "not on PATH"}
            continue
        version = _probe_version(binary)
        if version is None:
            tools[name] = {"found": True, "binary": binary,
                           "error": "--version probe failed"}
        else:
            tools[name] = {"found": True, "binary": binary, "version": version}
    return {"tools": tools}


# ------------------------------------------------- registry wiring metadata
TOOLS = [
    {"name": "git.status", "description": "Porcelain status of a git repo (jailed).",
     "handler": git_status, "risk": "low", "needs_network": False,
     "schema": {"repo": "string?"}},
    {"name": "git.log", "description": "Recent commits [{hash, author, date, subject}].",
     "handler": git_log, "risk": "low", "needs_network": False,
     "schema": {"repo": "string?", "n": "int?"}},
    {"name": "git.diff", "description": "Working-tree or --staged diff, capped at 20k chars.",
     "handler": git_diff, "risk": "low", "needs_network": False,
     "schema": {"repo": "string?", "staged": "bool?"}},
    {"name": "project.scaffold", "description": "Create a starter python/static-site project.",
     "handler": project_scaffold, "risk": "medium", "needs_network": False,
     "schema": {"kind": "string", "name": "string"}},
    {"name": "code.lint", "description": "Syntax-check a Python file via py_compile.",
     "handler": code_lint, "risk": "low", "needs_network": False,
     "schema": {"path": "string"}},
    {"name": "deps.list", "description": "List DECLARED deps from requirements.txt/pyproject.toml.",
     "handler": deps_list, "risk": "low", "needs_network": False,
     "schema": {"path": "string?"}},
    {"name": "log.tail", "description": "Last N lines of a text log (binary rejected).",
     "handler": log_tail, "risk": "low", "needs_network": False,
     "schema": {"path": "string", "lines": "int?"}},
    {"name": "port.check", "description": "TCP connect probe (3s timeout).",
     "handler": port_check, "risk": "low", "needs_network": False,
     "schema": {"host": "string?", "port": "int"}},
    {"name": "env.doctor", "description": "Offline which + --version probe of dev tools.",
     "handler": env_doctor, "risk": "low", "needs_network": False,
     "schema": {}},
]

__all__ = ["TOOLS", "git_status", "git_log", "git_diff", "project_scaffold",
           "code_lint", "deps_list", "log_tail", "port_check", "env_doctor"]
