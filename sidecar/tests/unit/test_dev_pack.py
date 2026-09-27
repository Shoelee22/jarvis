"""Unit tests for the Phase 6 DEV Tool Pack. Fully offline: subprocess and
shutil.which are monkeypatched; git/* and env.doctor never touch the real
shell. scaffold / lint / deps.list / log.tail / port.check run for real in
tmp dirs against a monkeypatched repos_dir.
"""
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import creator  # noqa: E402
from jarvis.tools.builtin import dev_pack  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _cfg(monkeypatch, tmp_path):
    """Point dev.repos_dir at tmp_path via the shared config loader."""
    cfg = json.loads(json.dumps(creator._DEFAULTS))
    cfg["dev"] = {"repos_dir": str(tmp_path)}
    monkeypatch.setattr(creator, "_load_config", lambda: cfg)
    return cfg


def _run_ok(monkeypatch, stdout="", returncode=0, stderr=""):
    calls = []

    def fake(cmd, timeout=300):
        calls.append(cmd)
        return _Proc(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(creator, "_run", fake)
    return calls


# ------------------------------------------------------------- git.status
def test_git_status_dirty(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    _run_ok(monkeypatch, " M app.py\n?? new.txt\n")
    r = dev_pack.git_status({"repo": "."})
    assert r["clean"] is False
    assert {"status": "M", "path": "app.py"} in r["changes"]
    assert {"status": "??", "path": "new.txt"} in r["changes"]
    assert r["repo"] == str(tmp_path.resolve())


def test_git_status_failure(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    _run_ok(monkeypatch, returncode=128,
            stderr="fatal: not a git repository (or any of the parent directories)")
    r = dev_pack.git_status({"repo": "."})
    assert "error" in r and "not a git repository" in r["error"]


def test_git_status_jail_rejects_escape(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = dev_pack.git_status({"repo": "../../etc"})
    assert "error" in r and "outside the allowed root" in r["error"]


# ---------------------------------------------------------------- git.log
def test_git_log_parses_commits(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    out = ("a" * 40 + "\x1fJane\x1f2026-09-27 10:00:00 +0530\x1ffix bug\n"
           + "b" * 40 + "\x1fRaj\x1f2026-09-26 09:00:00 +0530\x1fadd feature\n")
    _run_ok(monkeypatch, out)
    r = dev_pack.git_log({"repo": ".", "n": 2})
    assert len(r["commits"]) == 2
    c = r["commits"][0]
    assert c == {"hash": "a" * 40, "author": "Jane",
                 "date": "2026-09-27 10:00:00 +0530", "subject": "fix bug"}
    assert r["commits"][1]["author"] == "Raj"


def test_git_log_failure(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    _run_ok(monkeypatch, returncode=128, stderr="fatal: not a git repository")
    r = dev_pack.git_log({})
    assert "error" in r


# --------------------------------------------------------------- git.diff
def test_git_diff_caps_output(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    calls = _run_ok(monkeypatch, "x" * 25000)
    r = dev_pack.git_diff({})
    assert r["truncated"] is True
    assert len(r["diff"]) == 20000
    assert r["staged"] is False


def test_git_diff_staged_flag(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    calls = _run_ok(monkeypatch, "diff --git a/x b/x")
    r = dev_pack.git_diff({"staged": True})
    assert r["staged"] is True
    assert r["truncated"] is False
    assert "--staged" in calls[0]


# ------------------------------------------------------- project.scaffold
def test_scaffold_python(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = dev_pack.project_scaffold({"kind": "python", "name": "demo-app"})
    assert "error" not in r, r
    root = tmp_path / "demo-app"
    for f in ["pyproject.toml", "demo_app/__init__.py", "tests/test_demo_app.py",
              "README.md"]:
        assert (root / f).is_file(), f
    # twice refuses
    r2 = dev_pack.project_scaffold({"kind": "python", "name": "demo-app"})
    assert "error" in r2 and "existing" in r2["error"]


def test_scaffold_static_site(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = dev_pack.project_scaffold({"kind": "static-site", "name": "mysite"})
    assert "error" not in r, r
    root = tmp_path / "mysite"
    assert (root / "index.html").read_text().find("<title>mysite</title>") >= 0
    assert (root / "styles.css").is_file()
    assert (root / "app.js").is_file()


def test_scaffold_bad_kind_and_name(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    assert "error" in dev_pack.project_scaffold({"kind": "rust", "name": "x"})
    assert "error" in dev_pack.project_scaffold({"kind": "python", "name": "../evil"})
    assert "error" in dev_pack.project_scaffold({"kind": "python", "name": ""})


# ---------------------------------------------------------------- code.lint
def test_code_lint_ok_and_syntax_error(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    good = tmp_path / "good.py"
    good.write_text("x = 1\n")
    r = dev_pack.code_lint({"path": "good.py"})
    assert r == {"path": str(good.resolve()), "ok": True, "errors": ""}
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n")
    r = dev_pack.code_lint({"path": "bad.py"})
    assert r["ok"] is False
    assert "SyntaxError" in r["errors"]


def test_code_lint_missing_file(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = dev_pack.code_lint({"path": "nope.py"})
    assert "error" in r


# --------------------------------------------------------------- deps.list
def test_deps_list_requirements(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    (tmp_path / "requirements.txt").write_text(
        "# comment\nrequests>=2.0\n\nflask==3.0\n")
    r = dev_pack.deps_list({})
    assert r["manifest"] == "requirements.txt"
    assert r["declared"] == ["requests>=2.0", "flask==3.0"]
    assert "declared" in r["note"]


def test_deps_list_pyproject(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["httpx>=0.27", "pydantic"]\n')
    r = dev_pack.deps_list({})
    assert r["manifest"] == "pyproject.toml"
    assert r["declared"] == ["httpx>=0.27", "pydantic"]


def test_deps_list_no_manifest(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = dev_pack.deps_list({})
    assert r == {"error": "no manifest found (looked for requirements.txt, "
                          "pyproject.toml)"}


# ----------------------------------------------------------------- log.tail
def test_log_tail_last_lines(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "app.log"
    f.write_text("\n".join(f"line{i}" for i in range(1, 101)))
    r = dev_pack.log_tail({"path": "app.log", "lines": 5})
    assert r["lines"] == ["line96", "line97", "line98", "line99", "line100"]
    assert r["shown"] == 5 and r["total_lines"] == 100


def test_log_tail_cap_and_binary(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "big.log"
    f.write_text("\n".join("x" for _ in range(500)))
    r = dev_pack.log_tail({"path": "big.log", "lines": 999})
    assert r["shown"] == 200  # capped
    b = tmp_path / "bin.log"
    b.write_bytes(b"ok\x00binary\n")
    r = dev_pack.log_tail({"path": "bin.log"})
    assert "error" in r and "binary" in r["error"]


def test_log_tail_jail(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = dev_pack.log_tail({"path": "/etc/passwd"})
    assert "error" in r


# --------------------------------------------------------------- port.check
def test_port_check_open_and_closed():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    ready = threading.Event()
    threading.Thread(target=lambda: (ready.set(), srv.accept()),
                     daemon=True).start()
    ready.wait(timeout=5)
    try:
        r = dev_pack.port_check({"host": "127.0.0.1", "port": port})
        assert r == {"host": "127.0.0.1", "port": port, "open": True}
    finally:
        # shutdown() wakes the thread blocked in accept(); close() alone
        # does not reliably stop a listening socket on Linux.
        try:
            srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        srv.close()
    # the kernel may lag teardown by a moment; poll until it refuses
    deadline = time.time() + 3.0
    while True:
        r = dev_pack.port_check({"host": "127.0.0.1", "port": port})
        if r["open"] is False:
            break
        assert time.time() < deadline, "port stayed open after close"
        time.sleep(0.05)
    assert r == {"host": "127.0.0.1", "port": port, "open": False}


def test_port_check_bad_port():
    assert "error" in dev_pack.port_check({})
    assert "error" in dev_pack.port_check({"port": 99999})


# --------------------------------------------------------------- env.doctor
def test_env_doctor_success_and_missing(monkeypatch):
    real_which = {"python3": "/usr/bin/python3", "git": "/usr/bin/git"}

    monkeypatch.setattr("shutil.which", lambda b: real_which.get(b))

    def fake_run(cmd, timeout=300):
        return _Proc(returncode=0, stdout="v9.9.9\n")

    monkeypatch.setattr(creator, "_run", fake_run)
    r = dev_pack.env_doctor({})
    assert r["tools"]["python3"] == {"found": True, "binary": "/usr/bin/python3",
                                    "version": "v9.9.9"}
    assert r["tools"]["git"]["found"] is True
    assert r["tools"]["node"] == {"found": False, "error": "not on PATH"}
    assert r["tools"]["rustc"]["found"] is False


def test_env_doctor_version_probe_fails(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: f"/bin/{b}")

    def fake_run(cmd, timeout=300):
        raise RuntimeError("boom")

    monkeypatch.setattr(creator, "_run", fake_run)
    r = dev_pack.env_doctor({})
    assert r["tools"]["npm"]["found"] is True
    assert "error" in r["tools"]["npm"]
    assert r["tools"]["npm"]["binary"] == "/bin/npm"


# ------------------------------------------------------------- misc wiring
def test_tools_metadata_sane():
    names = [t["name"] for t in dev_pack.TOOLS]
    assert names == ["git.status", "git.log", "git.diff", "project.scaffold",
                     "code.lint", "deps.list", "log.tail", "port.check",
                     "env.doctor"]
    risks = {t["name"]: t["risk"] for t in dev_pack.TOOLS}
    assert risks["project.scaffold"] == "medium"
    assert all(r == "low" for n, r in risks.items() if n != "project.scaffold")
    assert all(t["needs_network"] is False for t in dev_pack.TOOLS)


def test_handlers_never_raise(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    for t in dev_pack.TOOLS:
        out = t["handler"]({"repo": None, "path": None, "port": "zzz",
                            "kind": "python", "name": "", "lines": "zzz",
                            "n": "zzz", "staged": False, "host": None})
        assert isinstance(out, dict), t["name"]
