"""DevOps Tool Pack (Phase 10): docker container listing, git status.

Read-only, low risk. Presence-gated: missing docker CLI or git returns an
honest {"error": ...} — nothing is faked. Subprocess calls use argv lists
only (no shell), so paths cannot inject commands.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


# ------------------------------------------------------------ helpers
def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


# --------------------------------------------------- devops.docker_ps
def docker_ps(args: dict) -> dict:
    """List running docker containers (read-only)."""
    try:
        if shutil.which("docker") is None:
            return {"error": "docker CLI not found on PATH. Install Docker "
                             "(https://docs.docker.com/engine/install/) and "
                             "ensure 'docker' is on PATH."}
        r = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"error": f"docker ps failed (exit {r.returncode}): "
                             f"{(r.stderr or '').strip()[-300:]}"}
        containers = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        slim = [{"id": c.get("ID", "")[:12], "image": c.get("Image", ""),
                 "name": c.get("Names", ""), "status": c.get("Status", ""),
                 "ports": c.get("Ports", "")} for c in containers]
        return {"containers": slim, "count": len(slim)}
    except Exception as e:
        return _fail("devops.docker_ps", e)


# ------------------------------------------------- devops.git_status
def git_status(args: dict) -> dict:
    """git status --porcelain for a repo path (read-only)."""
    try:
        repo = str(args.get("repo", "")).strip()
        if not repo:
            return {"error": "repo is required (path to a git repository)"}
        p = Path(repo).expanduser().resolve()
        if not p.is_dir():
            return {"error": f"repo does not exist: {repo}"}
        if shutil.which("git") is None:
            return {"error": "git not found on PATH. Install git "
                             "(https://git-scm.com/downloads)."}
        r = subprocess.run(
            ["git", "-C", str(p), "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"error": f"git status failed (exit {r.returncode}): "
                             f"{(r.stderr or '').strip()[-300:]} "
                             f"(is this a git repository?)"}
        branch = None
        files = []
        for line in r.stdout.splitlines():
            if line.startswith("## "):
                branch = line[3:].split("...", 1)[0]
                continue
            if len(line) >= 4:
                files.append({"x": line[0], "y": line[1],
                              "path": line[3:].strip()})
        counts = {"staged": 0, "unstaged": 0, "untracked": 0}
        for f in files:
            if f["x"] == "?" and f["y"] == "?":
                counts["untracked"] += 1
            else:
                if f["x"] not in (" ", "?"):
                    counts["staged"] += 1
                if f["y"] not in (" ", "?"):
                    counts["unstaged"] += 1
        return {"repo": str(p), "branch": branch, "clean": not files,
                "counts": counts, "files": files[:100],
                "truncated": len(files) > 100}
    except Exception as e:
        return _fail("devops.git_status", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "devops.docker_ps",
     "description": "List running docker containers (read-only).",
     "handler": docker_ps, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "devops.git_status",
     "description": "git status --porcelain for a repo path (read-only).",
     "handler": git_status, "risk": "low", "needs_network": False,
     "schema": {"repo": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(devops_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "devops.docker_ps": ("low", False),
    "devops.git_status": ("low", False),
}


def register(reg) -> None:
    """Wire the two devops tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "docker_ps", "git_status"]
