"""Offline tests for the Phase 18 security pack."""
from __future__ import annotations

import os
import stat
import sys

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.security_pack as sec
from jarvis.tools.base import Registry


@pytest.fixture
def tmpdata(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("JARVIS_DATA", str(data))
    # Deterministic config where _config_path() resolves (data dir's parent),
    # carrying one real-looking secret so the no-leak invariant is actually
    # exercised instead of passing vacuously on "config not found".
    cfg = tmp_path / "tools_config.yaml"
    cfg.write_text('sidecar:\n  api_key: "sk-test-12345"\n')
    os.chmod(cfg, 0o644)
    yield data


class TestAudit:
    def test_audit_returns_findings(self, tmpdata):
        (tmpdata / "brain.db").write_text("x")
        out = sec.audit_handler({})
        assert out["ok"] is True
        assert out["findings"]
        checks = {f["check"] for f in out["findings"]}
        assert {"data_dir_permissions", "world_writable", "config_secrets",
                "egress_firewall", "kill_switch", "audit_log",
                "ssh_keys"} <= checks
        for f in out["findings"]:
            assert f["status"] in ("pass", "warn", "fail")
            assert f["detail"] and f["remediation"]
        total = sum(out["summary"].values())
        assert total == len(out["findings"])

    def test_audit_never_leaks_values(self, tmpdata):
        # Structural invariant: secret findings carry key names + line
        # numbers only — never values.
        import re
        out = sec.audit_handler({})
        for f in out["findings"]:
            if f["check"] == "config_secrets" and f["status"] == "warn":
                assert re.search(r"line \d+: key '[^']+'", f["detail"])
                assert "=" not in f["detail"].split("key")[0]

    def test_world_writable_detected(self, tmpdata):
        p = tmpdata / "loose.txt"
        p.write_text("x")
        os.chmod(p, 0o666)
        out = sec.audit_handler({})
        ww = [f for f in out["findings"] if f["check"] == "world_writable"]
        assert ww and ww[0]["status"] == "fail"

    def test_egress_state_reported(self, tmpdata):
        out = sec.audit_handler({})
        eg = [f for f in out["findings"] if f["check"] == "egress_firewall"]
        assert eg
        assert eg[0]["status"] in ("pass", "fail", "warn")

    def test_kill_switch_reported(self, tmpdata):
        out = sec.audit_handler({})
        ks = [f for f in out["findings"] if f["check"] == "kill_switch"]
        assert ks and ks[0]["status"] in ("pass", "warn")


class TestHarden:
    def test_dry_run_reports_only(self, tmpdata):
        p = tmpdata / "f.db"
        p.write_text("x")
        os.chmod(p, 0o644)
        before = p.stat().st_mode & 0o777
        out = sec.harden_handler({"dry_run": True})
        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["changed"] == 0
        assert out["changes"]
        assert (p.stat().st_mode & 0o777) == before  # untouched

    def test_apply_fixes(self, tmpdata):
        p = tmpdata / "g.db"
        p.write_text("x")
        os.chmod(p, 0o644)
        os.chmod(tmpdata, 0o755)
        out = sec.harden_handler({"dry_run": False})
        assert out["ok"] is True
        assert out["changed"] >= 2
        assert (p.stat().st_mode & 0o777) == 0o600
        assert (tmpdata.stat().st_mode & 0o777) == 0o700

    def test_harden_bad_arg(self, tmpdata):
        assert "error" in sec.harden_handler({"dry_run": "yes"})


class TestRegistration:
    def test_register_both(self):
        reg = Registry()
        sec.register(reg)
        assert "security.audit" in reg.tools
        assert "security.harden" in reg.tools
        assert reg.tools["security.audit"].risk == "low"
        assert reg.tools["security.harden"].risk == "medium"
