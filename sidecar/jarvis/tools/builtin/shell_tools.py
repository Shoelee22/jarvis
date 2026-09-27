"""Guardrailed shell. Destructive commands are escalated to high risk by the policy engine."""
from __future__ import annotations
import subprocess

TIMEOUT = 60


def exec_cmd(args: dict) -> dict:
    cmd = args["cmd"]
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=args.get("timeout", TIMEOUT))
    out = (proc.stdout + proc.stderr)[-8000:]
    return {"cmd": cmd, "returncode": proc.returncode, "output": out}
