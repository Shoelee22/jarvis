"""Phase 13 (workstream B) Self pack tests: self.diagnose / self.upgrade / self.rollback.

All state is isolated via env overrides so the real dirs are never touched:
  JARVIS_FORGED_DIR -> tmp (upgrade files land under forged/upgrades/)
  JARVIS_SELF_DB    -> tmp (self_upgrades sqlite)
  JARVIS_AUDIT_DB   -> tmp (synthetic audit rows)
  JARVIS_SELF_PKGROOT -> tmp (fake target modules)
  JARVIS_AUTOPILOT_DB -> tmp (upgrade proposals)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent.audit import AuditLog
from jarvis.tools.base import Registry, Tool
from jarvis.tools.builtin import autopilot_pack, self_pack


FAKE_MODULE = '''"""Fake target module for self-upgrade tests (self-contained)."""

def flaky(args):
    if args.get("boom"):
        raise RuntimeError("boom happened")
    return {"echo": args.get("echo", "ok")}

def another(args):
    return {"another": True}
'''


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_FORGED_DIR", str(tmp_path / "forged"))
    monkeypatch.setenv("JARVIS_SELF_DB", str(tmp_path / "self.db"))
    monkeypatch.setenv("JARVIS_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JARVIS_AUTOPILOT_DB", str(tmp_path / "autopilot.db"))
    pkgroot = tmp_path / "pkgroot" / "tools" / "builtin"
    pkgroot.mkdir(parents=True)
    (pkgroot / "fake_tool.py").write_text(FAKE_MODULE, encoding="utf-8")
    monkeypatch.setenv("JARVIS_SELF_PKGROOT", str(tmp_path / "pkgroot"))
    self_pack.bind_agent(None)
    yield
    self_pack.bind_agent(None)


def _seed_audit(db_path: str) -> AuditLog:
    return AuditLog(db_path)


# ---------------------------------------------------------------------------
# self.diagnose
# ---------------------------------------------------------------------------

def test_diagnose_ranks_errors(tmp_path):
    import os
    audit = _seed_audit(os.environ["JARVIS_AUDIT_DB"])
    for _ in range(5):
        audit.record("agent", "tool_x", {}, "{'error': 'ValueError: bad thing'}", "low")
    audit.record("agent", "tool_y", {}, "{'error': 'KeyError: missing'}", "low")
    for _ in range(3):
        audit.record("agent", "tool_x", {}, "{'ok': True, 'n': 1}", "low")

    out = self_pack.self_diagnose_handler({"days": 30})
    assert "error" not in out
    rank = out["error_rank"]
    assert rank[0]["tool"] == "tool_x" and rank[0]["errors"] == 5
    assert rank[0]["calls"] == 8
    assert rank[1]["tool"] == "tool_y" and rank[1]["errors"] == 1
    assert "ValueError: bad thing" in rank[0]["sample_error"]
    assert out["all_clear"] is False


def test_diagnose_friction_and_repeats(tmp_path):
    import os
    audit = _seed_audit(os.environ["JARVIS_AUDIT_DB"])
    for _ in range(2):
        audit.record("agent", "mail.send", {"to": "a@b.c"},
                     "awaiting confirmation", "high")
    audit.record("agent", "nope.tool", {}, "unknown tool 'nope.tool' — default deny",
                 "high")
    for _ in range(4):
        audit.record("agent", "web.search", {"q": "same question"},
                     "{'ok': True}", "medium")

    out = self_pack.self_diagnose_handler({"days": 30})
    friction = {f["tool"]: f for f in out["confirmation_friction"]}
    assert friction["mail.send"]["confirmation_gates"] == 2
    assert friction["nope.tool"]["denials"] == 1
    rep = out["repeated_calls"]
    assert len(rep) == 1
    assert rep[0]["tool"] == "web.search" and rep[0]["times"] == 4
    assert rep[0]["sample_args"]["q"] == "same question"


def test_diagnose_all_clear_is_honest(tmp_path):
    import os
    audit = _seed_audit(os.environ["JARVIS_AUDIT_DB"])
    audit.record("agent", "notes.add", {"text": "hi"}, "{'ok': True}", "low")
    out = self_pack.self_diagnose_handler({"days": 30})
    assert out["all_clear"] is True
    assert out["error_rank"] == [] and out["confirmation_friction"] == []
    assert out["repeated_calls"] == []
    assert "No problems found" in out["summary"]


def test_diagnose_missing_db_is_honest(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("JARVIS_AUDIT_DB", str(tmp_path / "does-not-exist.db"))
    out = self_pack.self_diagnose_handler({"days": 7})
    assert out["all_clear"] is True and out["rows_scanned"] == 0


# ---------------------------------------------------------------------------
# self.upgrade — forbidden targets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target,rule", [
    ("agent/policy.py", "FORBIDDEN-DIR"),
    ("agent/core.py", "FORBIDDEN-DIR"),
    ("security/egress.py", "FORBIDDEN-DIR"),
    ("tools/builtin/permissions_pack.py", "FORBIDDEN-NAME"),
    ("tools/builtin/autonomy_pack.py", "FORBIDDEN-NAME"),
    ("tools/builtin/self_pack.py", "FORBIDDEN-NAME"),
    ("tools/builtin/policy.py", "FORBIDDEN-NAME"),
    ("permissions_pack", "FORBIDDEN-NAME"),
    ("self_pack", "FORBIDDEN-NAME"),
    ("../evil.py", "PATH-JAIL"),
    ("/etc/passwd.py", "PATH-JAIL"),
])
def test_upgrade_refuses_forbidden_targets(target, rule):
    out = self_pack.self_upgrade_handler({"target": target})
    assert "error" in out, target
    assert rule in out["error"], (target, out["error"])
    assert "ok" not in out


def test_upgrade_ambiguous_target_lists_candidates():
    out = self_pack.self_upgrade_handler({"target": "tools/builtin/fake_tool.py"})
    assert "error" in out
    assert "flaky" in out["error"] and "another" in out["error"]


def test_upgrade_unknown_target_is_honest():
    out = self_pack.self_upgrade_handler({"target": "tools/builtin/nope.py::f"})
    assert "error" in out and "no such module" in out["error"]


# ---------------------------------------------------------------------------
# self.upgrade — happy path: writes only under forged/upgrades
# ---------------------------------------------------------------------------

def test_upgrade_drafts_tests_and_proposes(tmp_path, monkeypatch):
    import os
    pkgroot = tmp_path / "pkgroot"
    original = pkgroot / "tools" / "builtin" / "fake_tool.py"
    before = original.read_bytes()

    out = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky",
        "example_input": {"echo": "hi"},
        "tool": "fake.tool",
    })
    assert "error" not in out, out
    assert out["ok"] is True
    assert out["target"] == "tools/builtin/fake_tool.py::flaky"
    assert out["version"] == 1
    assert out["sandbox"] == "pass"
    assert out["proposal_id"]

    forged = Path(os.environ["JARVIS_FORGED_DIR"])
    upath = Path(out["path"])
    # writes ONLY under forged/upgrades, never the original path
    assert upath.is_relative_to(forged / "upgrades")
    assert upath != original
    assert original.read_bytes() == before, "original file must be untouched"
    src = upath.read_text(encoding="utf-8")
    assert "def run(args)" in src
    assert "exec(" not in src and "eval(" not in src

    # original preserved alongside
    preserved = forged / "upgrades" / "_originals" / (
        upath.stem.replace("_v1", "") + "_orig.py")
    assert preserved.is_file()
    assert preserved.read_bytes() == before

    # sqlite row recorded as proposed
    conn = sqlite3.connect(os.environ["JARVIS_SELF_DB"])
    try:
        row = conn.execute("SELECT * FROM self_upgrades WHERE id=?",
                           (out["upgrade_id"],)).fetchone()
        assert row is not None
        status = conn.execute("SELECT status FROM self_upgrades WHERE id=?",
                              (out["upgrade_id"],)).fetchone()[0]
        report = json.loads(conn.execute(
            "SELECT test_report FROM self_upgrades WHERE id=?",
            (out["upgrade_id"],)).fetchone()[0])
    finally:
        conn.close()
    assert status == "proposed"
    assert report["sandbox"] == "pass"
    assert report["proposal_id"] == out["proposal_id"]

    # proposal queued in autopilot
    queued = autopilot_pack.queue_handler({})
    ids = [p["id"] for p in queued["pending"]]
    assert out["proposal_id"] in ids
    assert "upgrade ready for tools/builtin/fake_tool.py::flaky" in \
        queued["pending"][ids.index(out["proposal_id"])]["goal"]

    # versioning: a second upgrade of the same target becomes v2
    out2 = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky",
        "example_input": {},
    })
    assert out2["version"] == 2
    assert out2["upgrade_id"] != out["upgrade_id"]


def test_upgrade_sandbox_failure_proposes_nothing(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(
        self_pack, "_sandbox_test_upgrade",
        lambda path, ei: (False, "synthetic failure", None))
    out = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky",
        "example_input": {},
    })
    assert "error" in out
    assert "NOT proposed" in out["error"] or "not proposed" in out["error"].lower()
    conn = sqlite3.connect(os.environ["JARVIS_SELF_DB"])
    try:
        n = conn.execute("SELECT COUNT(*) FROM self_upgrades").fetchone()[0]
    finally:
        conn.close()
    assert n == 0
    queued = autopilot_pack.queue_handler({})
    assert queued["count"] == 0


def test_upgrade_wrapper_contains_original_errors():
    import importlib.util
    import os
    out = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky",
        "example_input": {"boom": True},  # original raises RuntimeError
    })
    assert out.get("sandbox") == "pass", out
    spec = importlib.util.spec_from_file_location("probe_mod", out["path"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.run({"boom": True})
    assert res["error"].startswith("RuntimeError: boom happened")
    assert mod.run({}) == {"echo": "ok"}
    assert mod.run("not-a-dict")["error"].startswith("self-upgrade v1")


# ---------------------------------------------------------------------------
# self.rollback
# ---------------------------------------------------------------------------

def test_rollback_unknown_and_double_revert():
    out = self_pack.self_rollback_handler({"upgrade_id": "upg_nope_v9"})
    assert "error" in out and "unknown upgrade id" in out["error"]

    created = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky", "example_input": {}})
    uid = created["upgrade_id"]
    first = self_pack.self_rollback_handler({"upgrade_id": uid})
    assert first["ok"] is True and first["status"] == "reverted"
    assert first["was"] == "proposed"
    second = self_pack.self_rollback_handler({"upgrade_id": uid})
    assert "error" in second and "already reverted" in second["error"]


def test_rollback_restores_live_handler_from_preserved_copy(tmp_path):
    import importlib.util
    import os
    created = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky",
        "example_input": {},
        "tool": "fake.tool",
    })
    uid = created["upgrade_id"]

    # fake live agent with the upgrade "activated"
    reg = Registry()
    spec = importlib.util.spec_from_file_location("upg_mod", created["path"])
    upg_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upg_mod)
    reg.register(Tool("fake.tool", "d", {}, upg_mod.run, "low", False))
    self_pack.bind_agent(SimpleNamespace(registry=reg))
    conn = sqlite3.connect(os.environ["JARVIS_SELF_DB"])
    try:
        conn.execute("UPDATE self_upgrades SET status='active' WHERE id=?",
                     (uid,))
        conn.commit()
    finally:
        conn.close()

    out = self_pack.self_rollback_handler({"upgrade_id": uid})
    assert out["ok"] is True
    assert "restored" in out["live_change"]
    restored = reg.tools["fake.tool"].handler
    assert restored.__name__ == "flaky"  # the ORIGINAL function, not run()
    assert restored({}) == {"echo": "ok"}
    conn = sqlite3.connect(os.environ["JARVIS_SELF_DB"])
    try:
        status = conn.execute("SELECT status FROM self_upgrades WHERE id=?",
                              (uid,)).fetchone()[0]
    finally:
        conn.close()
    assert status == "reverted"


def test_rollback_unbound_agent_is_honest(tmp_path):
    import os
    created = self_pack.self_upgrade_handler({
        "target": "tools/builtin/fake_tool.py::flaky",
        "example_input": {},
        "tool": "fake.tool",
    })
    uid = created["upgrade_id"]
    conn = sqlite3.connect(os.environ["JARVIS_SELF_DB"])
    try:
        conn.execute("UPDATE self_upgrades SET status='active' WHERE id=?",
                     (uid,))
        conn.commit()
    finally:
        conn.close()
    out = self_pack.self_rollback_handler({"upgrade_id": uid})
    assert out["ok"] is True and out["status"] == "reverted"
    assert "not bound" in out["live_change"]


# ---------------------------------------------------------------------------
# registration contract
# ---------------------------------------------------------------------------

def test_registration_contract():
    reg = Registry()
    self_pack.register(reg)
    names = {t.name: t for t in reg.tools.values()}
    assert names["self.diagnose"].risk == "low"
    assert names["self.upgrade"].risk == "high"
    assert names["self.rollback"].risk == "medium"
    assert self_pack.RISK_TABLE_ADDITIONS == {
        "self.diagnose": ("low", False),
        "self.upgrade": ("high", False),
        "self.rollback": ("medium", False),
    }
    assert [d["name"] for d in self_pack.TOOL_DEFS] == [
        "self.diagnose", "self.upgrade", "self.rollback"]
