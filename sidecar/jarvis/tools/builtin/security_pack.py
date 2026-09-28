"""Security self-audit pack (Phase 18): JARVIS owns its posture.

Read-only audit of the installation's own security hygiene, plus an
explicit opt-in harden step. Nothing here phones home; everything is local.

Tools:
    security.audit    low     Run the self-audit. Returns findings
                            [{check, status: pass|warn|fail, detail,
                            remediation}]. Checks: data-dir file
                            permissions, world-writable files, plaintext
                            secret-looking values in tools_config.yaml
                            (KEY NAMES ONLY — values are never read out),
                            egress firewall state, kill-switch state,
                            audit-log presence/recency, ~/.ssh key
                            permissions.
    security.harden     medium  Apply permission fixes for audit findings
                            {dry_run?}. chmod 700 on the data dir, 600 on
                            files inside it. dry_run=true (default) only
                            reports what would change.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - security.audit is strictly read-only. security.harden only touches
      permission bits under the JARVIS data dir and ~/.ssh keys — never
      file contents, never anything outside those roots.
    - Secret detection reports the config KEY name and line number only;
      the value is never included, logged, or returned.
"""
from __future__ import annotations

import os
import re
import sqlite3
import stat
import time
from datetime import datetime
from pathlib import Path

def _config_path() -> Path:
    # Derived from _data_dir() at CALL time so the JARVIS_DATA test override
    # (and any runtime relocation) applies. A module-level constant computed
    # at import would pin the real home dir and break hermetic tests.
    return _data_dir().parent / "tools_config.yaml"

_SECRET_KEY_RE = re.compile(
    r"(?i)^\s*([a-z0-9_.\-]*"
    r"(api[_-]?key|secret|password|passwd|token|private[_-]?key|"
    r"client[_-]?secret|auth[_-]?key|bearer|webhook[_-]?url)"
    r"[a-z0-9_.\-]*)\s*:")


def _data_dir() -> Path:
    override = os.environ.get("JARVIS_DATA")
    if override:
        return Path(override)
    from ...config import DATA_DIR
    return DATA_DIR


def _safe(fn):
    def wrapper(args: dict):
        try:
            return fn(args or {})
        except Exception as exc:  # never raise out of a tool handler
            return {"error": f"{fn.__name__}: {type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    return wrapper


def _finding(check: str, status: str, detail: str, remediation: str) -> dict:
    return {"check": check, "status": status, "detail": detail,
            "remediation": remediation}


def _check_data_dir_permissions() -> list[dict]:
    d = _data_dir()
    if not d.exists():
        return [_finding("data_dir_permissions", "warn",
                         f"data dir {d} does not exist yet",
                         "it is created on first run; re-run the audit then")]
    st = d.stat()
    findings = []
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        findings.append(_finding(
            "data_dir_permissions", "fail",
            f"{d} is group/other accessible (mode {oct(st.st_mode & 0o777)})",
            "run security.harden to chmod 700 the data dir"))
    exposed = []
    for p in d.rglob("*"):
        try:
            if p.is_symlink() or not p.is_file():
                continue
            m = p.stat().st_mode
            if m & (stat.S_IRGRP | stat.S_IROTH):
                exposed.append(str(p.relative_to(d)))
                if len(exposed) >= 10:
                    break
        except OSError:
            continue
    if exposed:
        findings.append(_finding(
            "data_file_permissions", "fail",
            f"{len(exposed)}+ data files readable by group/other "
            f"(e.g. {exposed[0]})",
            "run security.harden to chmod 600 data files"))
    if not findings:
        findings.append(_finding("data_dir_permissions", "pass",
                                 f"{d} is owner-only (mode "
                                 f"{oct(st.st_mode & 0o777)})",
                                 "none"))
    return findings


def _check_world_writable() -> list[dict]:
    d = _data_dir()
    bad = []
    if d.exists():
        for p in d.rglob("*"):
            try:
                if p.is_symlink():
                    continue
                if p.stat().st_mode & stat.S_IWOTH:
                    bad.append(str(p.relative_to(d)))
                    if len(bad) >= 10:
                        break
            except OSError:
                continue
    if bad:
        return [_finding("world_writable", "fail",
                         f"world-writable paths under data dir: {bad[0]}...",
                         "run security.harden or chmod o-w the paths")]
    return [_finding("world_writable", "pass",
                     "no world-writable files under the data dir", "none")]


def _check_config_secrets() -> list[dict]:
    cfg = _config_path()
    if not cfg.exists():
        return [_finding("config_secrets", "warn",
                         f"{cfg} not found",
                         "audit skipped — check the install layout")]
    hits = []
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                m = _SECRET_KEY_RE.match(line)
                if not m:
                    continue
                key = m.group(1)
                # value redaction: only report that a value is present
                has_value = bool(line.split(":", 1)[1].strip().strip("'\""))
                if has_value and "your-" not in line.lower() \
                        and "changeme" not in line.lower() \
                        and "example" not in line.lower():
                    hits.append(f"line {lineno}: key '{key}'")
    except OSError as exc:
        return [_finding("config_secrets", "warn",
                         f"could not read config: {exc}", "check file permissions")]
    if hits:
        return [_finding(
            "config_secrets", "warn",
            f"{len(hits)} secret-looking keys with values in tools_config.yaml "
            f"({hits[0]}...). Values are NOT shown.",
            "move real secrets to the Secure Vault / env vars; keep only "
            "placeholders in the yaml")]
    return [_finding("config_secrets", "pass",
                     "no secret-looking values in tools_config.yaml", "none")]


def _check_egress() -> list[dict]:
    try:
        from ...security import egress  # noqa: PLC0415
    except ImportError:
        return [_finding("egress_firewall", "warn",
                         "egress module not importable",
                         "check the sidecar install")]
    enabled = bool(getattr(egress, "_enabled", False))
    violations = egress.violations() if hasattr(egress, "violations") else []
    if not enabled:
        return [_finding("egress_firewall", "fail",
                         "default-deny egress is DISABLED — outbound "
                         "connections are not allowlisted",
                         "re-enable via jarvis.security.egress.set_enabled(True)")]
    detail = "default-deny egress is ON"
    if violations:
        detail += f"; {len(violations)} violation(s) logged"
        return [_finding("egress_firewall", "warn", detail,
                         "review egress.violations() for unexpected hosts")]
    return [_finding("egress_firewall", "pass", detail, "none")]


def _check_kill_switch() -> list[dict]:
    try:
        from . import autonomy_pack  # noqa: PLC0415
    except ImportError:
        return [_finding("kill_switch", "warn",
                         "autonomy_pack not installed — kill switch unknown",
                         "install autonomy_pack for the kill switch")]
    try:
        killed = bool(autonomy_pack.is_killed())
    except Exception:
        return [_finding("kill_switch", "warn",
                         "kill-switch state unreadable", "check autonomy.db")]
    if killed:
        return [_finding("kill_switch", "warn",
                         "kill switch is ENGAGED — autonomous execution halted",
                         "run autonomy.on (high-risk, needs confirmation) to resume")]
    return [_finding("kill_switch", "pass",
                     "kill switch present and disengaged", "none")]


def _check_audit_log() -> list[dict]:
    override = os.environ.get("JARVIS_LOOPS_AUDIT_DB")
    path = Path(override) if override else _data_dir() / "jarvis.db"
    if not path.exists():
        return [_finding("audit_log", "warn",
                         f"audit db {path} not found",
                         "audit logging starts with the sidecar loop")]
    try:
        conn = sqlite3.connect(path)
        try:
            tables = {r[0] for r in
                      conn.execute("SELECT name FROM sqlite_master"
                                   " WHERE type='table'").fetchall()}
            if not tables:
                raise sqlite3.Error("no tables")
            # best-effort recency probe over common audit tables
            recent = False
            for t in ("audit_log", "tool_calls", "events"):
                if t in tables:
                    try:
                        row = conn.execute(
                            f"SELECT MAX(created_at) FROM {t}").fetchone()
                        if row and row[0]:
                            recent = True
                            break
                    except sqlite3.Error:
                        continue
            return [_finding("audit_log", "pass",
                             f"audit db present ({path.name})",
                             "none" if recent else
                             "no recent rows found — verify the sidecar is logging")]
        finally:
            conn.close()
    except Exception as exc:
        return [_finding("audit_log", "warn",
                         f"audit db unreadable: {type(exc).__name__}",
                         "check db permissions")]


def _check_ssh_keys() -> list[dict]:
    ssh = Path.home() / ".ssh"
    if not ssh.exists():
        return [_finding("ssh_keys", "pass", "no ~/.ssh directory", "none")]
    bad = []
    for p in ssh.iterdir():
        try:
            if not p.is_file() or p.is_symlink():
                continue
            m = p.stat().st_mode & 0o777
            is_priv = not p.suffix == ".pub" and "known_hosts" not in p.name \
                and "config" not in p.name
            if is_priv and (m & 0o077):
                bad.append(f"{p.name} (mode {oct(m)})")
            elif not is_priv and (m & 0o002):
                bad.append(f"{p.name} (mode {oct(m)})")
        except OSError:
            continue
    if bad:
        return [_finding("ssh_keys", "fail",
                         f"loose key permissions: {', '.join(bad)}",
                         "chmod 600 private keys, 644 public keys")]
    return [_finding("ssh_keys", "pass", "~/.ssh key permissions look sane",
                     "none")]


@_safe
def audit_handler(args: dict) -> dict:
    findings: list[dict] = []
    for check in (_check_data_dir_permissions, _check_world_writable,
                  _check_config_secrets, _check_egress, _check_kill_switch,
                  _check_audit_log, _check_ssh_keys):
        try:
            findings.extend(check())
        except Exception as exc:  # one bad check must not kill the audit
            findings.append(_finding("internal", "warn",
                                     f"check {check.__name__} errored: "
                                     f"{type(exc).__name__}",
                                     "re-run; report if persistent"))
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for f in findings:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return {"ok": True, "findings": findings, "summary": counts,
            "scanned_at": datetime.now().astimezone().isoformat(timespec="seconds")}


@_safe
def harden_handler(args: dict) -> dict:
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        return {"error": "security.harden: dry_run must be a boolean"}
    d = _data_dir()
    changes: list[str] = []
    if d.exists():
        dm = d.stat().st_mode & 0o777
        if dm != 0o700:
            changes.append(f"{d}: {oct(dm)} -> 0o700")
            if not dry_run:
                os.chmod(d, 0o700)
        for p in d.rglob("*"):
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                m = p.stat().st_mode & 0o777
                want = 0o700 if (m & 0o111) else 0o600
                if m != want:
                    changes.append(f"{p.relative_to(d)}: {oct(m)} -> {oct(want)}")
                    if not dry_run:
                        os.chmod(p, want)
            except OSError:
                continue
    ssh = Path.home() / ".ssh"
    if ssh.exists():
        for p in ssh.iterdir():
            try:
                if not p.is_file() or p.is_symlink():
                    continue
                m = p.stat().st_mode & 0o777
                is_priv = p.suffix != ".pub" and p.name not in (
                    "known_hosts", "config", "authorized_keys")
                want = 0o600 if is_priv else 0o644
                if m != want:
                    changes.append(f"~/.ssh/{p.name}: {oct(m)} -> {oct(want)}")
                    if not dry_run:
                        os.chmod(p, want)
            except OSError:
                continue
    return {"ok": True, "dry_run": dry_run, "changes": changes,
            "changed": 0 if dry_run else len(changes),
            "note": ("dry run — no changes applied; pass dry_run=false "
                     "to apply") if dry_run else "permission fixes applied"}


TOOL_DEFS = [
    {"name": "security.audit",
     "description": ("Read-only self-audit of JARVIS security hygiene: data-dir "
                     "permissions, world-writable files, secret-looking config "
                     "values (key names only), egress firewall, kill switch, "
                     "audit log, ssh key permissions."),
     "handler": audit_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "security.harden",
     "description": ("Apply permission fixes for audit findings "
                     "{dry_run?}. dry_run=true (default) only reports."),
     "handler": harden_handler, "risk": "medium", "needs_network": False,
     "schema": {"dry_run": "bool?"}},
]

RISK_TABLE_ADDITIONS = {
    "security.audit": ("low", False),
    "security.harden": ("medium", False),
}


def register(reg) -> None:
    """Wire the two security pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
