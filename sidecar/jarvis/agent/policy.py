"""Policy engine: the safety layer. NOT trainable, NOT part of the model.

Decides allow / confirm / deny for every tool call, and scans untrusted
content (web pages, emails, tool outputs) for injected instructions.
"""
from __future__ import annotations
import re

# risk table: tool -> (risk, needs_network)
RISK_TABLE = {
    "fs.read": ("low", False),
    "fs.list": ("low", False),
    "fs.search": ("low", False),
    "fs.write": ("medium", False),
    "fs.delete": ("high", False),
    "shell.exec": ("medium", False),   # upgraded to high for destructive cmds
    "reminders.add": ("low", False),
    "reminders.list": ("low", False),
    "timers.set": ("low", False),
    "notes.add": ("low", False),
    "notes.search": ("low", False),
    "calc.eval": ("low", False),
    "clipboard.read": ("medium", False),
    "mail.read": ("medium", True),
    "mail.send": ("high", True),
    "mail.search": ("medium", True),
    "sms.send": ("high", True),
    "contacts.add": ("low", False),
    "contacts.search": ("low", False),
    "contacts.list": ("low", False),
    "calendar.read": ("low", False),   # Phase 6: local sqlite calendar, offline
    "calendar.create": ("medium", False),
    "browser.task": ("high", True),
    "web.search": ("medium", True),
    "web.fetch": ("low", True),
    "web.summarize": ("low", False),
    "price.check": ("medium", True),
    "rss.read": ("low", True),
    "bookmarks.save": ("low", False),
    "bookmarks.list": ("low", False),
    "link.unshorten": ("low", True),
    "webshot.capture": ("low", False),
    "camera.describe": ("medium", False),
    "camera.watch": ("medium", False),
    "screen.describe": ("medium", False),
    "memory.remember": ("low", False),
    "memory.forget": ("medium", False),
    "training.start": ("medium", False),
    "models.rollback": ("low", False),
    "code.run": ("medium", False),
    "website.build": ("low", False),
    "media.image": ("low", True),
    "video.compose": ("low", False),
    "social.post_instagram": ("high", True),
    "notify.send": ("low", False),
    "screen.capture": ("low", False),
    "clipboard.write": ("medium", False),
    "battery.status": ("low", False),
    "disk.usage": ("low", False),
    "process.top": ("low", False),
    "system.uptime": ("low", False),
    "home.call": ("high", True),
    "mqtt.publish": ("high", True),
    "devices.register": ("low", False),
    "devices.list": ("low", False),
    "scenes.run": ("medium", False),
    "csv.read": ("low", False),
    "csv.filter": ("low", False),
    "csv.stats": ("low", False),
    "json.query": ("low", False),
    "db.query": ("medium", False),
    "expenses.add": ("low", False),
    "expenses.report": ("low", False),
    "habits.checkin": ("low", False),
    "habits.report": ("low", False),
    "units.convert": ("low", False),
    "git.status": ("low", False),
    "git.log": ("low", False),
    "git.diff": ("low", False),
    "project.scaffold": ("medium", False),
    "code.lint": ("low", False),
    "deps.list": ("low", False),
    "log.tail": ("low", False),
    "port.check": ("low", False),
    "env.doctor": ("low", False),
    "tts.speak": ("low", False),
    "audio.transcribe": ("low", False),
    "image.resize": ("low", False),
    "image.convert": ("low", False),
    "video.trim": ("low", False),
    "video.gif": ("low", False),
    "video.contact_sheet": ("low", False),
    "file.hash": ("low", False),
    "archive.zip": ("medium", False),
    "archive.unzip": ("medium", False),
    "tasks.delegate": ("medium", False),
    # --- Phase 8: capability frontier ---
    "gui.screenshot": ("low", False),
    "gui.click": ("high", False),
    "gui.type": ("high", False),
    "gui.hotkey": ("high", False),
    "gui.windows_list": ("low", False),
    "gui.window_focus": ("medium", False),
    "gui.mouse_position": ("low", False),
    "browser.open": ("low", True),
    "browser.read_text": ("low", True),
    "browser.fill": ("medium", True),
    "browser.click": ("medium", True),
    "browser.shot": ("low", True),
    "browser.close": ("low", True),
    "pc.winget_search": ("low", True),
    "pc.winget_install": ("high", True),
    "pc.winget_list": ("low", False),
    "pc.duplicates": ("low", False),
    "pc.big_files": ("low", False),
    "pc.disk_report": ("low", False),
    "pc.startup_list": ("low", False),
    "telegram.me": ("low", True),
    "telegram.send": ("high", True),
    "telegram.updates": ("low", True),
    "telegram.register_chat": ("low", True),
    "gmail.auth_url": ("low", True),
    "gmail.search": ("low", True),
    "gmail.read": ("low", True),
    "gmail.send": ("high", True),
    "gmail.labels": ("low", True),
    "studio.podcast": ("low", False),
    "studio.voiced_reel": ("low", False),
    "studio.transcribe_notes": ("low", False),
    "studio.mix": ("low", False),
    "system.doctor": ("low", False),
    "system.capabilities": ("low", False),
    "plugins.list": ("low", False),
    "plugins.reload": ("low", False),
}

DESTRUCTIVE_SHELL = re.compile(
    r"(\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+(~|/)(?!\s)"  # rm -rf against home or root
    r"|mkfs|dd\s+.*of=/dev|:(){:|:&};:|shutdown|reboot"
    r"|chmod\s+-R\s+777\s+/|>\s*/dev/sd)",
    re.IGNORECASE,
)

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+|previous\s+|prior\s+)?instructions",
        r"disregard\s+(all\s+|previous\s+)?(instructions|rules)",
        r"you\s+must\s+now\s+",
        r"^system\s*:",
        r"new\s+instructions\s*:",
        r"override\s+your\s+(safety|system)\s+",
        r"reveal\s+your\s+(system\s+)?prompt",
        r"send\s+(your|the)\s+passwords?\s+to\s+",
        r"delete\s+all\s+files",
    ]
]

# Policy rules are user-owned config. Nothing in the trainer may touch these.
PROTECTED_RULES = frozenset({"confirm_before_irreversible", "default_deny_egress"})


class PolicyEngine:
    def decide(self, tool: str, args: dict | None = None) -> dict:
        """Return {action: allow|confirm|deny, risk, reason}."""
        args = args or {}
        if tool not in RISK_TABLE:
            return {"action": "deny", "risk": "high",
                    "reason": f"unknown tool '{tool}' — default deny"}
        risk, needs_net = RISK_TABLE[tool]
        if tool == "shell.exec" and DESTRUCTIVE_SHELL.search(args.get("cmd", "")):
            risk = "high"
        # --- Phase 11: permission grants (fail-closed store) ---
        # get_level() -> 'allow' | 'ask' | 'deny' | None (None = no grant: normal policy).
        # permissions_pack is stdlib-only; lazy import keeps agent.policy cycle-free.
        try:
            from ..tools.builtin import permissions_pack as _perm_store
            _grant = _perm_store.get_level(tool)
        except Exception:
            _grant = None
        if _grant == "deny":
            return {"action": "deny", "risk": risk,
                    "reason": f"permission grant denies '{tool}'"}
        if _grant == "allow":
            return {"action": "allow", "risk": risk,
                    "reason": f"permission grant allows '{tool}' (explicit user grant)"}
        if _grant == "ask":
            return {"action": "confirm", "risk": risk,
                    "reason": f"permission grant requires confirmation for '{tool}'"}
        if risk == "low":
            return {"action": "allow", "risk": risk, "reason": "low risk"}
        if risk == "medium":
            return {"action": "allow", "risk": risk, "reason": "medium risk — logged prominently"}
        return {"action": "confirm", "risk": risk,
                "reason": f"high-risk tool '{tool}' needs explicit user confirmation"}

    def scan_untrusted(self, text: str) -> dict:
        """Flag prompt-injection attempts inside untrusted content."""
        hits = [p.pattern for p in INJECTION_PATTERNS if p.search(text)]
        return {"contains_instruction": bool(hits), "patterns": hits}

    def confirm_text(self, tool: str, args: dict) -> str:
        return (f"I'm about to run {tool} with {self._summarize(args)}. "
                f"Say 'yes' to confirm, or 'no' to cancel.")

    @staticmethod
    def _summarize(args: dict) -> str:
        items = [f"{k}={str(v)[:80]}" for k, v in args.items()]
        return ", ".join(items) or "no arguments"
