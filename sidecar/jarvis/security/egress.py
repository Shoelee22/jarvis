"""Default-deny egress: every outbound connection checked against the allowlist."""
from __future__ import annotations
import socket

ALLOWLIST = {
    # tool -> allowed host suffixes
    "mail.send": ["smtp.gmail.com", "smtp.office365.com"],
    "mail.read": ["imap.gmail.com", "outlook.office365.com"],
    "mail.search": ["imap.gmail.com", "outlook.office365.com"],
    "sms.send": [],  # user allowlists their webhook host here
    "home.call": [],  # user allowlists their HA host here
    "mqtt.publish": [],  # user allowlists their broker host here
    "web.search": ["lite.duckduckgo.com"],
    "browser.task": [],  # populated from user-approved domains per task
    "browser.open": [],  # populated from user-approved domains per task
    # Phase 8 packs (fixed API hosts — safe to allowlist):
    "telegram.me": ["api.telegram.org"],
    "telegram.send": ["api.telegram.org"],
    "telegram.updates": ["api.telegram.org"],
    "gmail.search": ["gmail.googleapis.com", "oauth2.googleapis.com", "accounts.google.com"],
    "gmail.read": ["gmail.googleapis.com", "oauth2.googleapis.com", "accounts.google.com"],
    "gmail.send": ["gmail.googleapis.com", "oauth2.googleapis.com", "accounts.google.com"],
    "gmail.labels": ["gmail.googleapis.com", "oauth2.googleapis.com", "accounts.google.com"],
    "gmail.auth_url": ["oauth2.googleapis.com", "accounts.google.com"],
    # Phase 9: Twilio voice (fixed API host — safe to allowlist):
    "phone.call_and_talk": ["api.twilio.com"],
    "phone.hangup": ["api.twilio.com"],
    # Phase 10 (fixed hosts):
    "weather.now": ["api.open-meteo.com"],
    "weather.forecast": ["api.open-meteo.com"],
    "news.headlines": ["feeds.bbci.co.uk", "hnrss.org", "www.theguardian.com"],
    "news.digest": ["feeds.bbci.co.uk", "hnrss.org", "www.theguardian.com"],
    "travel.flight_search": ["api.duffel.com"],
}

_violations: list[dict] = []
_enabled = True


def check(tool: str, host: str) -> bool:
    """Return True if allowed. Log + surface violations."""
    if not _enabled:
        return True
    allowed = ALLOWLIST.get(tool, [])
    ok = any(host.endswith(suffix) for suffix in allowed) if allowed else False
    if not ok:
        _violations.append({"tool": tool, "host": host})
    return ok


def violations() -> list[dict]:
    return list(_violations)


def set_enabled(on: bool):
    global _enabled
    _enabled = on
