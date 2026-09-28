"""Network + perception tools. Real shapes; backends wire up on user machines
(OAuth tokens live in the OS keychain, never in files)."""
from __future__ import annotations

from ...security import egress


def mail_read(args: dict) -> dict:
    return {"note": "connect an email account in Settings first", "messages": []}


def mail_send(args: dict) -> dict:
    host = "smtp.gmail.com"
    if not egress.check("mail.send", host):
        return {"error": f"egress to {host} blocked by policy"}
    return {"note": "dry-run: no account connected", "to": args.get("to")}


def calendar_read(args: dict) -> dict:
    return {"note": "connect a calendar in Settings first", "events": []}


def calendar_create(args: dict) -> dict:
  return {"note": "dry-run: no account connected", "event": args}


def browser_task(args: dict) -> dict:
    # Playwright-driven local Chromium in production. Domain allowlist per task.
    domains = args.get("allow_domains", [])
    return {"note": "browser automation wires up in Phase 2 on the target machine",
            "task": args.get("task"), "allow_domains": domains}


def web_search(args: dict) -> dict:
    if not egress.check("web.search", "search.local"):
        return {"error": "web search is disabled — enable it in Settings first"}
    return {"results": []}


def camera_describe(args: dict) -> dict:
    from ...vision.pipeline import FakeVision
    frame = b"fake-frame"  # shell streams real frames over WS in production
    desc = FakeVision().describe(frame, args.get("prompt", "What do you see?"))
    return {"description": desc, "saved": bool(args.get("save"))}


def screen_describe(args: dict) -> dict:
    return {"description": "screen capture wires up in the shell (Phase 3)"}
