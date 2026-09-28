import sys, tempfile
sys.path.insert(0, "sidecar")
from pathlib import Path
from jarvis.tools.builtin import build_registry
from jarvis.agent.audit import AuditLog

def _reg():
    tmp = tempfile.mkdtemp()
    return build_registry(tmp), AuditLog(Path(tmp) / "a.db")

def test_fs_write_read(tmp_path):
    reg, audit = _reg()
    p = tmp_path / "note.txt"
    # jail is home; use a home-relative path instead
    home_file = str(Path.home() / ".jarvis-test-note.txt")
    try:
        r = reg.call("fs.write", {"path": home_file, "content": "hello"}, audit=audit)
        assert r["ok"]
        r = reg.call("fs.read", {"path": home_file}, audit=audit)
        assert r["ok"] and "hello" in str(r["result"])
    finally:
        Path(home_file).unlink(missing_ok=True)

def test_fs_jail_blocks_escape():
    reg, audit = _reg()
    r = reg.call("fs.read", {"path": "/etc/shadow"}, audit=audit)
    # /etc is outside home jail → PermissionError → ok False
    assert not r["ok"]

def test_shell_allow_and_confirm():
    reg, audit = _reg()
    r = reg.call("shell.exec", {"cmd": "echo hi"}, audit=audit)
    assert r["ok"] and "hi" in str(r["result"])
    r = reg.call("shell.exec", {"cmd": "rm -rf /"}, audit=audit)
    assert r.get("needs_confirmation")

def test_reminder_roundtrip():
    reg, audit = _reg()
    r = reg.call("reminders.add", {"text": "check sales", "cron": "0 9 * * 1-5"}, audit=audit)
    assert r["ok"]
    r = reg.call("reminders.list", {}, audit=audit)
    assert any("check sales" in x["text"] for x in r["result"]["reminders"])

def test_calc():
    reg, audit = _reg()
    r = reg.call("calc.eval", {"expression": "2*(3+4)"}, audit=audit)
    assert r["ok"] and r["result"]["value"] == 14

def test_audit_records():
    reg, audit = _reg()
    reg.call("calc.eval", {"expression": "1+1"}, audit=audit)
    assert any(e["tool"] == "calc.eval" for e in audit.today())
