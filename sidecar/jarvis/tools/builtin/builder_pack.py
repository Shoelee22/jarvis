"""Builder Pack (Phase 15, workstream C): scaffold -> iterate -> review.

Three tools that turn "build me X" into real files on disk, then close the
loop by running the project's own tests until they pass:

    build.scaffold  medium  Create a REAL multi-file project under the
                            projects root (default ~/workspace/jarvis-projects,
                            override with JARVIS_BUILD_ROOT): main module,
                            tests/, README.md — from hand-written honest
                            templates per project kind ("cli", "web", "lib"),
                            never from an LLM. Everything stays inside the
                            project dir (realpath containment; escapes refused).
    build.iterate   medium  The loop: (1) run the project's tests via
                            code.run through the live registry (subprocess
                            fallback with a strict timeout when no agent is
                            bound), (2) on failure apply a MECHANICAL patch
                            (typo-style identifier fix, missing-colon syntax
                            fix, or a bounded integer-literal +/-1 search),
                            (3) re-run. Bounded by max_rounds. STOPS and
                            reports honestly when stuck: "tried N rounds,
                            still failing with <error>, here's what I
                            changed". Never installs packages: a missing
                            third-party import stops the loop and is reported
                            as a confirmation-gated next step (installing is
                            high risk and NOT this tool's job).
    build.review    low     Static checks only, no execution: compile-check
                            every .py, count tests, TODO/FIXME scan, short
                            issue list.

HONESTY CONTRACT: patches are mechanical. Every report labels them as such
("mechanical typo-style correction", "mechanical literal search") and the
tool never claims to understand the code. The test suite is the oracle: a
patch is kept only if the suite goes green.

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    1. Register + risk table (same as every pack)::

           from jarvis.tools.builtin import builder_pack
           builder_pack.register(registry)
           from jarvis.agent.policy import RISK_TABLE
           RISK_TABLE.update(builder_pack.RISK_TABLE_ADDITIONS)

       i.e. in sidecar/jarvis/tools/builtin/__init__.py: add builder_pack to
       the `from . import (...)` list and include it in the pack loop, e.g.::

           for _pack in (brain_pack, knowledge_pack, episodic_pack, builder_pack):

    2. Bind the agent (build.iterate calls code.run — and best-effort
       brain.plan bookkeeping — through the live registry). In
       sidecar/jarvis/ipc/server.py, next to the other bind_agent calls::

           builder_pack.bind_agent(agent)

    3. No config additions needed. The projects root defaults to
       ~/workspace/jarvis-projects and is overridable with the
       JARVIS_BUILD_ROOT env var (unit tests point it at a tmp dir).

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - Project names are sanitized to [A-Za-z0-9_-]{1,64}; anything else
      (slashes, "..", spaces, shell metacharacters) is refused before
      touching disk.
    - Every file op goes through _safe_path(): the resolved path must stay
      inside the project dir, which itself must stay inside the projects
      root. Symlink escapes are caught because paths are resolved first.
    - build.iterate never runs pip / installs packages, never writes outside
      the project dir, and every test run is time-bounded.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tokenize
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/sleep_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# Projects root + path safety
# ---------------------------------------------------------------------------
DEFAULT_BUILD_ROOT = Path.home() / "workspace" / "jarvis-projects"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_ROUNDS_CAP = 10
_TEST_TIMEOUT = 60
_MAX_PATCH_CANDIDATES = 12


def _build_root() -> Path:
    override = os.environ.get("JARVIS_BUILD_ROOT")
    root = Path(override).expanduser() if override else DEFAULT_BUILD_ROOT
    return root


def _sanitize_project(name) -> str | None:
    """Return the name if safe, else None. Refuses '..', slashes, etc."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not _NAME_RE.match(name):
        return None
    return name


def _is_within(base: Path, path: Path) -> bool:
    try:
        b = base.resolve()
        p = path.resolve()
    except Exception:
        return False
    return p == b or b in p.parents


def _safe_path(base: Path, *parts: str) -> Path:
    """Join parts onto base; raise ValueError if the result escapes base."""
    p = base.joinpath(*parts)
    if not _is_within(base, p):
        raise ValueError(f"refused: path escapes its container: {parts!r}")
    return p


def _project_dir(name: str) -> Path | None:
    """Resolved, containment-checked project dir for a sanitized name."""
    root = _build_root()
    pdir = root / name
    # root itself must be absolute-ish; containment of pdir under root:
    if not _is_within(root, pdir) and pdir.resolve() != root.resolve():
        return None
    return pdir


# ---------------------------------------------------------------------------
# Risk-gated registry calls (same shape as sleep_pack._regcall, but the
# builder pack is allowed to call low AND medium risk collaborators —
# code.run is medium. High/unknown risk still fails closed, and
# confirmed=True is never passed.
# ---------------------------------------------------------------------------
def _tool_risk(reg, name: str) -> str:
    try:
        tool = getattr(reg, "tools", {}).get(name)
    except Exception:
        tool = None
    if tool is not None:
        return getattr(tool, "risk", None) or "unknown"
    try:
        from ...agent.policy import RISK_TABLE  # noqa: PLC0415 (lazy: no cycles)
        entry = RISK_TABLE.get(name)
        if entry:
            return entry[0]
    except Exception:
        pass
    return "unknown"


def _regcall(name: str, args: dict | None, actor: str = "build") -> dict:
    """Call a tool through the bound agent's registry.

    Low and medium risk collaborators are allowed (build.iterate needs
    code.run, which is medium). High risk or unknown risk is REFUSED.
    confirmed=True is never passed.
    """
    if _AGENT is None:
        return {"skipped": True, "tool": name,
                "reason": "builder pack is not bound to a live agent "
                          "(bind_agent was not called)"}
    reg = getattr(_AGENT, "registry", None)
    if reg is None:
        return {"skipped": True, "tool": name,
                "reason": "bound agent exposes no .registry"}
    risk = _tool_risk(reg, name)
    if risk not in ("low", "medium"):
        return {"skipped": True, "tool": name, "risk": risk,
                "reason": "builder pack refuses high/unknown-risk collaborators"}
    try:
        return reg.call(name, args or {}, actor=actor)
    except Exception as exc:  # never let a collaborator crash the build
        return {"ok": False, "error": f"registry.call raised: {exc}"}


def _unwrap(res: dict | None) -> dict | None:
    """Unwrap a Registry.call result envelope -> handler output (or None)."""
    if isinstance(res, dict) and res.get("ok"):
        inner = res.get("result")
        return inner if isinstance(inner, dict) else None
    return None


# ---------------------------------------------------------------------------
# Honest hand-written templates (NOT LLM output)
# ---------------------------------------------------------------------------
_KINDS = ("cli", "web", "lib")


def _module_name(project: str) -> str:
    mod = project.replace("-", "_")
    if mod and mod[0].isdigit():
        mod = "m_" + mod
    return mod


def _readme(name: str, kind: str, desc: str, mod: str) -> str:
    usage = {
        "cli": "python3 main.py Ada",
        "web": "python3 app.py   # then open http://127.0.0.1:8000",
        "lib": f'python3 -c "import {mod}; print({mod}.add(2, 3))"',
    }[kind]
    return (
        f"# {name}\n\n{desc}\n\n"
        f"Scaffolded by JARVIS `build.scaffold` (`{kind}` template — "
        "hand-written, no LLM involved).\n\n"
        "## Run\n\n"
        f"    {usage}\n\n"
        "## Test\n\n"
        "    python3 -m unittest discover -s tests -t .\n"
    )


def _tpl_lib(name: str, desc: str, mod: str) -> dict:
    module = '''"""__NAME__: __DESC__

Generated by build.scaffold (hand-written template, not LLM output).
Stdlib only, so `python -m unittest` just works.
"""


def add(a, b):
    """Return a + b."""
    return a + b


def total(items):
    """Return the sum of items."""
    s = 0
    for x in items:
        s = s + x
    return s


def first_n(n):
    """Return [0, 1, ..., n-1]."""
    return list(range(n))
'''.replace("__NAME__", name).replace("__DESC__", desc)
    test = '''"""Tests for __MOD__ (scaffolded with the project)."""
import unittest

import __MOD__


class TestLib(unittest.TestCase):
    def test_add(self):
        self.assertEqual(__MOD__.add(2, 3), 5)

    def test_total(self):
        self.assertEqual(__MOD__.total([1, 2, 3, 4]), 10)

    def test_first_n(self):
        self.assertEqual(__MOD__.first_n(3), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
'''.replace("__MOD__", mod)
    return {f"{mod}.py": module,
            "tests/__init__.py": "",
            f"tests/test_{mod}.py": test,
            "README.md": _readme(name, "lib", desc, mod)}


def _tpl_cli(name: str, desc: str, mod: str) -> dict:
    mod = ""  # the cli template always uses main.py
    module = '''"""__NAME__: __DESC__

Generated by build.scaffold (hand-written template, not LLM output).
Stdlib only, so `python -m unittest` just works.
"""
import argparse


def greet(who):
    """Return a greeting for who."""
    return "Hello, " + str(who) + "!"


def main(argv=None):
    """CLI entry point. Returns an exit code."""
    parser = argparse.ArgumentParser(
        prog="__NAME__", description="__DESC__")
    parser.add_argument("name", nargs="?", default="world",
                        help="who to greet")
    args = parser.parse_args(argv)
    print(greet(args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.replace("__NAME__", name).replace("__DESC__", desc)
    test = '''"""Tests for the __NAME__ CLI (scaffolded with the project)."""
import unittest

import main


class TestCli(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(main.greet("Ada"), "Hello, Ada!")

    def test_main_returns_zero(self):
        self.assertEqual(main.main(["Ada"]), 0)

    def test_main_default(self):
        self.assertEqual(main.main([]), 0)


if __name__ == "__main__":
    unittest.main()
'''.replace("__NAME__", name)
    return {"main.py": module,
            "tests/__init__.py": "",
            "tests/test_main.py": test,
            "README.md": _readme(name, "cli", desc, mod)}


def _tpl_web(name: str, desc: str, mod: str) -> dict:
    mod = ""  # the web template always uses app.py
    module = '''"""__NAME__: __DESC__

Generated by build.scaffold (hand-written template, not LLM output).
A tiny stdlib-only web app (http.server). Tests cover the pure parts;
the server itself is only started by run().
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import html

TITLE = "__NAME__"


def render_home(title=TITLE):
    """Render the home page HTML (pure function — safe to test)."""
    safe = html.escape(title)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>" + safe + "</title></head>"
        "<body><h1>" + safe + "</h1>"
        "<p>It works.</p></body></html>"
    )


class Handler(BaseHTTPRequestHandler):
    """Serve render_home() on every GET."""

    def do_GET(self):
        body = render_home().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output quiet
        pass


def run(host="127.0.0.1", port=8000):
    """Start the server (blocking)."""
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    run()
'''.replace("__NAME__", name).replace("__DESC__", desc)
    test = '''"""Tests for the __NAME__ web app (scaffolded with the project)."""
import unittest
from http.server import BaseHTTPRequestHandler

import app


class TestWeb(unittest.TestCase):
    def test_render_home_contains_title(self):
        page = app.render_home()
        self.assertIn(app.TITLE, page)

    def test_render_home_says_it_works(self):
        self.assertIn("It works.", app.render_home())

    def test_handler_is_request_handler(self):
        self.assertTrue(issubclass(app.Handler, BaseHTTPRequestHandler))


if __name__ == "__main__":
    unittest.main()
'''.replace("__NAME__", name)
    return {"app.py": module,
            "tests/__init__.py": "",
            "tests/test_app.py": test,
            "README.md": _readme(name, "web", desc, mod)}


_TPL = {"lib": _tpl_lib, "cli": _tpl_cli, "web": _tpl_web}


# ---------------------------------------------------------------------------
# build.scaffold
# ---------------------------------------------------------------------------
def _scaffold(args: dict) -> dict:
    spec = args.get("spec") if isinstance(args.get("spec"), dict) else args
    raw_name = spec.get("name", "")
    kind = str(spec.get("kind", "lib") or "lib").lower()
    desc = str(spec.get("description") or f"A {kind} project called {raw_name}.").strip()

    name = _sanitize_project(raw_name)
    if name is None:
        return {"ok": False, "error": (
            f"refused: project name {raw_name!r} is not allowed — use 1-64 "
            "chars of A-Z a-z 0-9 _ - only (no slashes, no '..', no spaces)")}
    if kind not in _KINDS:
        return {"ok": False, "error": (
            f"unknown kind {kind!r}; choose one of {', '.join(_KINDS)}")}

    root = _build_root()
    pdir = _project_dir(name)
    if pdir is None:
        return {"ok": False, "error": "refused: project path escapes the projects root"}
    if pdir.exists():
        return {"ok": False, "error": (
            f"refused: {pdir} already exists — pick another name "
            "(scaffold never overwrites)")}

    mod = _module_name(name)
    files = _TPL[kind](name, desc, mod)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if not _is_within(root, pdir):
            return {"ok": False, "error": "refused: path escapes projects root"}
        pdir.mkdir(parents=False, exist_ok=False)
        written = []
        for rel, content in files.items():
            dest = _safe_path(pdir, *rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written.append(rel)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": f"could not write project files: {exc}"}

    return {"ok": True, "project": name, "kind": kind, "path": str(pdir),
            "files": sorted(written),
            "note": ("scaffolded from a hand-written honest template "
                     "(not LLM-generated); run build.iterate to verify "
                     "the test suite passes")}


def build_scaffold_handler(args: dict) -> dict:
    try:
        return _scaffold(args or {})
    except Exception as exc:  # never raise
        return {"ok": False,
                "error": f"build.scaffold failed: {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Test running: code.run through the live registry, subprocess fallback
# ---------------------------------------------------------------------------
_FRAME_RE = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\S+)', re.M)


def _test_snippet(pdir: Path) -> str:
    """A self-contained unittest runner for the project's tests dir.

    code.run executes this in its own jailed sandbox dir, so the snippet
    inserts the project dir onto sys.path and discovers tests itself.
    """
    proj_json = json.dumps(str(pdir))
    return (
        "import io, json, os, sys, traceback, unittest\n"
        f"proj = {proj_json}\n"
        "sys.path.insert(0, proj)\n"
        "buf = io.StringIO()\n"
        "try:\n"
        "    loader = unittest.TestLoader()\n"
        "    suite = loader.discover(os.path.join(proj, 'tests'), top_level_dir=proj)\n"
        "    runner = unittest.TextTestRunner(stream=buf, verbosity=1)\n"
        "    result = runner.run(suite)\n"
        "    payload = {'ok': bool(result.wasSuccessful()),\n"
        "               'tests_run': result.testsRun,\n"
        "               'failures': len(result.failures),\n"
        "               'errors': len(result.errors)}\n"
        "    code = 0 if result.wasSuccessful() else 1\n"
        "except Exception:\n"
        "    payload = {'ok': False, 'discovery_error': traceback.format_exc(limit=5)}\n"
        "    code = 2\n"
        "print('BUILD_JSON:' + json.dumps(payload))\n"
        "print(buf.getvalue())\n"
        "sys.exit(code)\n"
    )


def _parse_snippet_output(inner: dict) -> dict | None:
    if inner.get("timed_out"):
        return {"ok": False, "timed_out": True, "tests_run": 0,
                "output": str(inner.get("stdout", ""))[-4000:]}
    if inner.get("error"):
        return None  # let the caller fall back to subprocess
    stdout = str(inner.get("stdout", ""))
    payload = None
    for line in stdout.splitlines():
        if line.startswith("BUILD_JSON:"):
            try:
                payload = json.loads(line[len("BUILD_JSON:"):])
            except Exception:
                payload = None
            break
    if payload is None:
        return None
    payload["output"] = stdout[-8000:]
    payload["exit_code"] = inner.get("exit_code")
    return payload


def _run_tests_subprocess(pdir: Path, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", "tests", "-t", "."],
            cwd=str(pdir), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return {"ok": False, "timed_out": True, "tests_run": 0,
                "output": out[-4000:], "via": "subprocess"}
    except Exception as exc:
        return {"ok": False, "tests_run": 0, "via": "subprocess",
                "output": "", "error": f"could not run tests: {exc}"}
    combined = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"Ran (\d+) tests?", combined)
    tests_run = int(m.group(1)) if m else 0
    return {"ok": proc.returncode == 0, "tests_run": tests_run,
            "output": combined[-8000:], "via": "subprocess",
            "exit_code": proc.returncode}


def _run_tests(pdir: Path, timeout: int = _TEST_TIMEOUT) -> dict:
    """Run the project's unittest suite. Prefers code.run via the live
    registry; falls back to a contained subprocess when no agent is bound
    (or code.run is unavailable) — the fallback is noted honestly."""
    # Clear stale bytecode first: rapid successive patch writes can land
    # within one mtime tick at identical file size, which would make a
    # fresh interpreter trust a stale .pyc. Contained to the project dir.
    try:
        for cache in pdir.rglob("__pycache__"):
            if _is_within(pdir, cache):
                shutil.rmtree(cache, ignore_errors=True)
    except Exception:
        pass
    if _AGENT is not None:
        res = _regcall("code.run",
                       {"code": _test_snippet(pdir), "timeout": timeout},
                       actor="build")
        inner = _unwrap(res)
        if inner is not None:
            parsed = _parse_snippet_output(inner)
            if parsed is not None:
                parsed["via"] = "code.run"
                return parsed
        note = ("code.run unavailable "
                f"({inner.get('error') if inner else res.get('reason', 'no result')}); "
                "fell back to contained subprocess")
    else:
        note = "no agent bound; used contained subprocess"
    out = _run_tests_subprocess(pdir, timeout)
    out["fallback_note"] = note
    return out


# ---------------------------------------------------------------------------
# Mechanical patch strategies. Each returns ("patched", desc) | ("stop",
# next_step) | (None, None). "Mechanical" is not a disclaimer — it is the
# design: no strategy claims to understand the code.
# ---------------------------------------------------------------------------
def _project_source_frames(output: str, pdir: Path) -> list:
    """Traceback frames inside the project dir, excluding tests/."""
    base = pdir.resolve()
    frames = []
    for m in _FRAME_RE.finditer(output or ""):
        try:
            fr = Path(m.group(1)).resolve()
        except Exception:
            continue
        if fr == base or base in fr.parents:
            try:
                rel = fr.relative_to(base)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == "tests":
                continue
            frames.append((fr, int(m.group(2))))
    return frames


def _identifiers(src: str) -> set:
    return set(re.findall(r"[A-Za-z_]\w*", src))


def _strategy_missing_module(output: str, pdir: Path, run) -> tuple:
    """ModuleNotFoundError -> never install; stop with a gated next step."""
    m = re.search(r"ModuleNotFoundError: No module named '([\w.]+)'", output or "")
    if not m:
        return (None, None)
    mod = m.group(1).split(".")[0]
    if mod in sys.stdlib_module_names:
        return ("stop", {
            "suggested_next_step": "check the Python environment",
            "confirmation_required": False,
            "note": (f"the failing import is stdlib module {mod!r}, which "
                     "should always exist — this looks like a broken "
                     "environment, not something a patch can fix")})
    # A same-project module that failed to resolve is a path quirk, not a
    # missing package — but unittest already runs with the project on
    # sys.path, so report it honestly rather than guessing.
    local = pdir / f"{mod}.py"
    if local.exists():
        return ("stop", {
            "suggested_next_step": f"inspect why {mod}.py is not importable",
            "confirmation_required": False,
            "note": (f"{mod}.py exists in the project but still failed to "
                     "import — beyond mechanical patching")})
    return ("stop", {
        "suggested_next_step": f"install package '{mod}'",
        "confirmation_required": True,
        "note": ("installing packages is high-risk and is NOT this tool's "
                 "job — approve it as a separate, confirmation-gated step")})


def _strategy_syntax(output: str, pdir: Path, run) -> tuple:
    """SyntaxError -> only the most mechanical fix: a missing ':'."""
    m = re.search(r'File "([^"]+)", line (\d+)\s*\n\s*(\S[^\n]*)\n\s*\^+\s*\nSyntaxError: ([^\n]+)',
                  output or "")
    if not m:
        # simpler shape: unittest prints the SyntaxError line without caret
        m2 = re.search(r'SyntaxError: ([^\n]+)', output or "")
        frames = _project_source_frames(output, pdir)
        if not (m2 and frames):
            return (None, None)
        path, lineno = frames[-1]
        msg = m2.group(1)
    else:
        try:
            path = Path(m.group(1)).resolve()
        except Exception:
            return (None, None)
        if not _is_within(pdir, path):
            return (None, None)
        lineno = int(m.group(2))
        msg = m.group(4)
    if "expected ':'" not in msg:
        return (None, None)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return (None, None)
    if not (1 <= lineno <= len(lines)):
        return (None, None)
    line = lines[lineno - 1]
    if not (line.rstrip().endswith(")")
            and re.match(r"\s*(def|class|if|elif|else|for|while|try|except|finally|with)\b", line)):
        return (None, None)
    lines[lineno - 1] = line.rstrip() + ":"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = run()
    if res.get("ok"):
        return ("patched",
                f"mechanical syntax fix: appended missing ':' at "
                f"{path.name}:{lineno} (no understanding claimed)")
    # revert — the guess did not help
    lines[lineno - 1] = line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (None, None)


def _strategy_name_error(output: str, pdir: Path, run) -> tuple:
    """NameError / bad import -> difflib closest-identifier rename (mechanical)."""
    import difflib

    m = re.search(r"NameError: name '(\w+)' is not defined", output or "")
    imp = re.search(r"cannot import name '(\w+)' from '([\w.]+)'", output or "")
    attr = re.search(r"AttributeError: module '([\w.]+)' has no attribute '(\w+)'",
                     output or "")
    if m:
        wrong = m.group(1)
        frames = _project_source_frames(output, pdir)
        if not frames:
            return (None, None)
        path, lineno = frames[-1]
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return (None, None)
        if not (1 <= lineno <= len(lines)):
            return (None, None)
        ids = _identifiers("\n".join(lines))
        ids.discard(wrong)
        close = difflib.get_close_matches(wrong, sorted(ids), n=1, cutoff=0.8)
        if not close:
            return (None, None)
        right = close[0]
        # Two mechanical attempts, in order: (1) the close match may be a
        # typo'd *definition* (rename it to the name the code asked for);
        # (2) otherwise replace the wrong name at the failing line. The
        # test suite is the oracle for both.
        def_lines = [i for i, ln in enumerate(lines)
                     if re.search(rf"\b{re.escape(right)}\b", ln)
                     and re.match(r"\s*(def|class)\s+", ln)]
        attempts = []
        if def_lines:
            attempts.append(
                ([(i, right, wrong) for i in def_lines],
                 f"mechanical typo-style correction: renamed definition "
                 f"{right!r} to {wrong!r} in {path.name} (difflib closest "
                 f"match; NameError site was {path.name}:{lineno})"))
        attempts.append(
            ([(lineno - 1, wrong, right)],
             f"mechanical typo-style correction: replaced {wrong!r} with "
             f"{right!r} (difflib closest match) at {path.name}:{lineno}"))
        for subs, desc in attempts:
            originals = {i: lines[i] for i, _, _ in subs}
            for i, pat, rep in subs:
                lines[i] = re.sub(rf"\b{re.escape(pat)}\b", rep, lines[i])
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            res = run()
            if res.get("ok"):
                return ("patched", desc)
            for i, orig in originals.items():  # revert; try the next attempt
                lines[i] = orig
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return (None, None)
    elif imp or attr:
        if imp:
            wrong, from_mod = imp.group(1), imp.group(2).split(".")[0]
        else:
            from_mod, wrong = attr.group(1).split(".")[-1], attr.group(2)
        path = pdir / f"{from_mod}.py"
        if not (path.exists() and _is_within(pdir, path)):
            return (None, None)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return (None, None)
        ids = _identifiers("\n".join(lines))
        ids.discard(wrong)
        close = difflib.get_close_matches(wrong, sorted(ids), n=1, cutoff=0.8)
        if not close:
            return (None, None)
        right = close[0]
        target_lines = [i for i, ln in enumerate(lines)
                        if re.search(rf"\b{re.escape(right)}\b", ln)
                        and re.match(r"\s*(def|class)\s+", ln)]
        if not target_lines:
            return (None, None)
        desc = (f"mechanical typo-style correction: renamed definition "
                f"{right!r} to {wrong!r} in {path.name} (difflib closest match)")
    else:
        return (None, None)

    originals = {i: lines[i] for i in target_lines}
    for i in target_lines:
        lines[i] = re.sub(rf"\b{re.escape(wrong if m else right)}\b",
                          right if m else wrong, lines[i])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = run()
    if res.get("ok"):
        return ("patched", desc)
    for i, orig in originals.items():  # revert — the guess did not help
        lines[i] = orig
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (None, None)


def _number_spans(src: str) -> list:
    """(lineno0, col_start, col_end, value) for plain int literals via tokenize."""
    spans = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type == tokenize.NUMBER and tok.string.isdigit():
                spans.append((tok.start[0] - 1, tok.start[1], tok.end[1],
                              int(tok.string)))
    except Exception:
        return []
    return spans


def _strategy_literal_search(output: str, pdir: Path, run) -> tuple:
    """Assertion failures -> bounded integer-literal +/-1 search (mechanical).

    Fixes seeded off-by-one style bugs like range(n) vs range(n+1). Each
    candidate is kept only if the whole suite goes green.
    """
    text = output or ""
    if not re.search(r"(FAIL|AssertionError|Error)", text):
        return (None, None)
    if re.search(r"(ModuleNotFoundError|ImportError|SyntaxError|NameError)", text):
        return (None, None)  # other strategies own those
    files = sorted(p for p in pdir.glob("*.py")
                   if p.is_file() and _is_within(pdir, p))
    tried = 0
    for path in files:
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        spans = _number_spans(src)
        if not spans:
            continue
        lines = src.splitlines()
        for (ln0, c0, c1, val) in spans:
            for delta in (-1, 1):
                if tried >= _MAX_PATCH_CANDIDATES:
                    return (None, None)
                new_val = val + delta
                if new_val < 0:
                    continue
                tried += 1
                orig_line = lines[ln0]
                lines[ln0] = orig_line[:c0] + str(new_val) + orig_line[c1:]
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                res = run()
                if res.get("ok"):
                    return ("patched",
                            f"mechanical literal search: changed {val} to "
                            f"{new_val} at {path.name}:{ln0 + 1}; suite is "
                            "green (no understanding claimed)")
                lines[ln0] = orig_line  # revert, on disk too, before trying
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (None, None)


_STRATEGIES = (
    _strategy_missing_module,
    _strategy_syntax,
    _strategy_name_error,
    _strategy_literal_search,
)


# ---------------------------------------------------------------------------
# build.iterate
# ---------------------------------------------------------------------------
def _plan_for_goal(goal: str, project: str) -> dict:
    """Best-effort brain.plan bookkeeping through the live registry."""
    if _AGENT is None:
        return {"plan": "skipped — no agent bound"}
    res = _regcall("brain.plan",
                   {"goal": goal,
                    "steps": [{"id": "s1", "tool": "build.iterate",
                               "args": {"project": project},
                               "depends_on": []}]},
                   actor="build")
    inner = _unwrap(res)
    if inner and inner.get("plan_id"):
        return {"plan_id": inner["plan_id"]}
    reason = res.get("reason") if isinstance(res, dict) else None
    return {"plan": f"skipped — brain.plan unavailable ({reason or 'no result'})"}


def _tail(text: str, n: int = 1500) -> str:
    text = text or ""
    return text[-n:] if len(text) > n else text


def _iterate(args: dict) -> dict:
    name = _sanitize_project(args.get("project", ""))
    if name is None:
        return {"ok": False, "error": (
            f"refused: project name {args.get('project')!r} is not allowed")}
    pdir = _project_dir(name)
    if pdir is None or not pdir.exists():
        return {"ok": False, "error": f"no such project: {name!r}"}
    goal = str(args.get("goal") or "make the test suite pass")
    try:
        max_rounds = max(1, min(_MAX_ROUNDS_CAP, int(args.get("max_rounds", 5))))
    except (TypeError, ValueError):
        max_rounds = 5

    plan_note = _plan_for_goal(goal, name)
    changes: list = []
    rounds_tried = 0
    last: dict = {}

    def run():
        return _run_tests(pdir)

    for _ in range(max_rounds):
        rounds_tried += 1
        last = run()
        if last.get("ok"):
            return {
                "ok": True, "project": name, "goal": goal,
                "rounds_tried": rounds_tried,
                "tests_run": last.get("tests_run", 0),
                "via": last.get("via"),
                "changes": changes,
                "plan": plan_note,
                "note": ("all patches applied were mechanical "
                         "(typo-style rename, missing-colon fix, or bounded "
                         "literal search) — kept only because the suite went "
                         "green; no understanding of the code is claimed."),
            }
        if last.get("timed_out"):
            break  # no patch fixes a hang; report honestly below
        acted = False
        for strat in _STRATEGIES:
            kind, payload = strat(last.get("output", ""), pdir, run)
            if kind == "patched":
                changes.append({"round": rounds_tried,
                                "strategy": strat.__name__,
                                "description": payload})
                acted = True
                break
            if kind == "stop":
                return {
                    "ok": False, "stuck": True, "project": name, "goal": goal,
                    "rounds_tried": rounds_tried,
                    "last_error": _tail(last.get("output", "")),
                    "changes": changes,
                    "next_step": payload,
                    "plan": plan_note,
                    "note": ("tried {n} round(s), still failing — stopped "
                             "instead of guessing").format(n=rounds_tried),
                }
        if not acted:
            break  # no strategy applied: honestly stuck

    err = "test run timed out" if last.get("timed_out") else _tail(last.get("output", ""))
    return {
        "ok": False, "stuck": True, "project": name, "goal": goal,
        "rounds_tried": rounds_tried,
        "last_error": err,
        "changes": changes,
        "plan": plan_note,
        "note": (f"tried {rounds_tried} round(s), still failing with the "
                 "error above; here is what I changed: "
                 + (json.dumps(changes) if changes else "(nothing — no "
                    "mechanical patch applied cleanly)")),
    }


def build_iterate_handler(args: dict) -> dict:
    try:
        return _iterate(args or {})
    except Exception as exc:  # never raise
        return {"ok": False,
                "error": f"build.iterate failed: {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# build.review (low risk, read-only, no execution)
# ---------------------------------------------------------------------------
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b\s*:?\s*(.*)")
_TEST_DEF_RE = re.compile(r"^\s*def\s+(test_\w+)", re.M)


def _review(args: dict) -> dict:
    name = _sanitize_project(args.get("project", ""))
    if name is None:
        return {"ok": False, "error": (
            f"refused: project name {args.get('project')!r} is not allowed")}
    pdir = _project_dir(name)
    if pdir is None or not pdir.exists():
        return {"ok": False, "error": f"no such project: {name!r}"}

    base = pdir.resolve()
    py_files = sorted(p for p in pdir.rglob("*.py")
                      if p.is_file() and _is_within(base, p))
    issues, todos = [], []
    test_defs = 0
    test_files = 0
    checked = 0
    test_sources = []

    for path in py_files:
        try:
            rel = str(path.resolve().relative_to(base))
        except ValueError:
            continue
        checked += 1
        try:
            src = path.read_text(encoding="utf-8")
        except OSError as exc:
            issues.append({"type": "unreadable", "file": rel,
                           "message": str(exc)})
            continue
        try:
            compile(src, rel, "exec")  # parse only — never executes
        except SyntaxError as exc:
            issues.append({"type": "syntax_error", "file": rel,
                           "line": exc.lineno, "message": exc.msg})
        is_test = len(path.relative_to(base).parts) > 1 and \
            path.relative_to(base).parts[0] == "tests" and \
            path.name.startswith("test")  # unittest's default pattern
        if is_test:
            test_files += 1
            test_defs += len(_TEST_DEF_RE.findall(src))
            test_sources.append(src)
        for i, line in enumerate(src.splitlines(), 1):
            m = _TODO_RE.search(line)
            if m:
                todos.append({"file": rel, "line": i, "tag": m.group(1),
                              "text": m.group(2).strip()[:120]})

    # heuristic: top-level modules with no test file mentioning them
    top_level = {p.stem for p in py_files
                 if len(p.resolve().relative_to(base).parts) == 1}
    blob = "\n".join(test_sources)
    for stem in sorted(top_level):
        if stem not in blob:
            issues.append({"type": "no_tests_reference", "file": f"{stem}.py",
                           "message": "no test file mentions this module"})

    verdict = "clean" if not issues else f"{len(issues)} issue(s) found"
    return {
        "ok": True, "project": name, "verdict": verdict,
        "files_checked": checked,
        "test_files": test_files, "tests_found": test_defs,
        "todos": todos, "issues": issues,
        "note": "static checks only — no code was executed",
    }


def build_review_handler(args: dict) -> dict:
    try:
        return _review(args or {})
    except Exception as exc:  # never raise
        return {"ok": False,
                "error": f"build.review failed: {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract (same shape as sleep_pack.py / teach_pack.py)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 38: persistent multi-day coding workspace.
#
# The project files already persist, but *context* evaporates between
# days: what was I doing, what's left, what did I decide. build.note
# appends timestamped entries to <project>/.session.md (plain markdown,
# human-readable, survives restarts and reinstalls). build.resume reads
# it back together with fresh mechanical state (recently modified files,
# open TODOs from build.review) so work can pick up where it stopped.
# ---------------------------------------------------------------------------

_SESSION_FILE = ".session.md"
_RESUME_WINDOW_DAYS = 7


def _session_path(name: str) -> Path | None:
    pdir = _project_dir(name)
    return pdir / _SESSION_FILE if pdir is not None else None


def build_note_handler(args: dict) -> dict:
    """build.note {project, text} — append a dated session note."""
    try:
        a = args or {}
        name = _sanitize_project(a.get("project", ""))
        if name is None:
            return {"ok": False,
                    "error": f"refused: project name {a.get('project')!r} "
                             "is not allowed"}
        text = str(a.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "build.note: 'text' is required"}
        pdir = _project_dir(name)
        if pdir is None or not pdir.exists():
            return {"ok": False, "error": f"no such project: {name!r}"}
        spath = pdir / _SESSION_FILE
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        first = not spath.exists()
        with spath.open("a", encoding="utf-8") as f:
            if first:
                f.write(f"# Session log — {name}\n\n")
            f.write(f"## {stamp}\n{text[:2000]}\n\n")
        return {"ok": True, "project": name, "at": stamp,
                "read": "Note appended to the project's session log."}
    except Exception as exc:  # never raise
        return {"ok": False, "error": f"build.note failed: {exc}"}


def build_resume_handler(args: dict) -> dict:
    """build.resume {project} — pick up where work stopped."""
    try:
        a = args or {}
        name = _sanitize_project(a.get("project", ""))
        if name is None:
            return {"ok": False,
                    "error": f"refused: project name {a.get('project')!r} "
                             "is not allowed"}
        pdir = _project_dir(name)
        if pdir is None or not pdir.exists():
            return {"ok": False, "error": f"no such project: {name!r}"}
        base = pdir.resolve()
        cutoff = time.time() - _RESUME_WINDOW_DAYS * 86400
        recent = []
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name == _SESSION_FILE:
                continue
            try:
                rel = str(p.resolve().relative_to(base))
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                recent.append({"file": rel,
                               "modified": datetime.fromtimestamp(
                                   mtime).strftime("%Y-%m-%d %H:%M")})
        recent.sort(key=lambda r: r["modified"], reverse=True)
        review = _review({"project": name})
        notes: list[str] = []
        spath = base / _SESSION_FILE
        if spath.exists():
            try:
                blocks = [b.strip() for b in
                          spath.read_text(encoding="utf-8").split("## ")[1:]]
                notes = blocks[-10:]
            except OSError:
                notes = []
        todos = (review.get("todos") or [])[:10]
        issues = (review.get("issues") or [])[:10]
        next_step = None
        if issues:
            i = issues[0]
            next_step = (f"Fix {i.get('type')}: {i.get('file')}"
                         f"{':' + str(i['line']) if i.get('line') else ''}")
        elif todos:
            t = todos[0]
            next_step = (f"Address {t.get('tag')}: {t.get('file')}:"
                         f"{t.get('line')}")
        elif notes:
            next_step = "Last session note recorded — check it for the plan."
        return {"ok": True, "project": name,
                "recently_modified": recent[:20],
                "open_todos": todos, "open_issues": issues,
                "recent_notes": notes,
                "next_suggested_step": next_step,
                "read": ("Mechanical resume: recently touched files, open "
                         "TODOs/issues from a static review, and the last "
                         "session notes. The suggested step is the first "
                         "open item — not a plan, just a pointer.")}
    except Exception as exc:  # never raise
        return {"ok": False, "error": f"build.resume failed: {exc}"}


TOOL_DEFS = [
    {"name": "build.note",
     "description": (
         "Append a dated note to a project's session log "
         "(<project>/.session.md) — what was done, decided, or left open. "
         "The log survives restarts; build.resume reads it back."),
     "handler": build_note_handler, "risk": "low", "needs_network": False,
     "schema": {"project": "string", "text": "string"}},
    {"name": "build.resume",
     "description": (
         "Pick up where coding work stopped: files modified in the last 7 "
         "days, open TODOs/issues from a static review, and the last "
         "session notes. Points at the first open item as the suggested "
         "next step — a pointer, not a plan."),
     "handler": build_resume_handler, "risk": "low", "needs_network": False,
     "schema": {"project": "string"}},
    {"name": "build.scaffold",
     "description": (
         "Create a REAL multi-file project under the projects root "
         "(~/workspace/jarvis-projects, JARVIS_BUILD_ROOT override): main "
         "module, tests/, README.md — from hand-written honest templates "
         "per kind (cli | web | lib), never from an LLM. Wraps in a "
         "containment jail: names are sanitized, '..' escapes refused, "
         "existing projects never overwritten."),
     "handler": build_scaffold_handler, "risk": "medium", "needs_network": False,
     "schema": {"spec": "object"}},
    {"name": "build.iterate",
     "description": (
         "Test-driven fix loop for a scaffolded project: run its unittest "
         "suite (via code.run through the live registry, subprocess fallback "
         "when unbound), apply a MECHANICAL patch on failure (typo-style "
         "identifier rename, missing-colon syntax fix, or bounded "
         "integer-literal +/-1 search), re-run — bounded by max_rounds. "
         "Stops and reports honestly when stuck; never installs packages "
         "(a missing third-party import becomes a confirmation-gated next "
         "step). Patches never claim understanding."),
     "handler": build_iterate_handler, "risk": "medium", "needs_network": False,
     "schema": {"project": "string", "goal": "string?", "max_rounds": "int?"}},
    {"name": "build.review",
     "description": (
         "Read-only static review of a project: compile-check every .py "
         "(parse only, never executes), count test functions, scan for "
         "TODO/FIXME markers, and flag top-level modules no test mentions. "
         "Returns a short issue list."),
     "handler": build_review_handler, "risk": "low", "needs_network": False,
     "schema": {"project": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
RISK_TABLE_ADDITIONS = {
    "build.note": ("low", False),
    "build.resume": ("low", False),
    "build.scaffold": ("medium", False),
    "build.iterate": ("medium", False),
    "build.review": ("low", False),
}


def register(reg) -> None:
    """Wire the Builder pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
