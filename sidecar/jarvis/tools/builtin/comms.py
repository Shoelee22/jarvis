"""Comms Tool Pack (Phase 6 / Pack 1): email, SMS, contacts, and a local
offline-first calendar. Dict in -> dict out; handlers NEVER raise, they
return {"error": ...} on failure.

Config comes from ~/workspace/jarvis/tools_config.yaml (reusing the
creator pack's mtime-cached _load_config/_enabled). SMTP/IMAP/SMS sections
are optional: missing or disabled config returns an honest not-configured
error instead of failing silently.

Wire-up: the parent agent adds `from . import comms` and calls
`comms.register_tools(reg)` inside build_registry() (__init__.py left
untouched by design).
"""
from __future__ import annotations

import datetime as _dt
import email
import email.policy
import json
import smtplib
import sqlite3
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from ..base import Tool
from ...config import DATA_DIR
from ...security import egress
from .creator import _load_config, _enabled

_NOT_CONFIGURED = "not configured: fill in the {section}: section of tools_config.yaml"

_SMTP_DEFAULTS = {
    "enabled": False, "host": "", "port": 587, "username": "",
    "password": "", "use_tls": True, "from_addr": "",
}
_IMAP_DEFAULTS = {
    "enabled": False, "host": "", "port": 993, "username": "",
    "password": "", "mailbox": "INBOX",
}
_SMS_DEFAULTS = {
    "enabled": False, "provider": "webhook", "webhook_url": "", "api_key": "",
}


def _section(name: str, defaults: dict) -> dict:
    raw = _load_config().get(name, {}) or {}
    return {**defaults, **raw}


def _smtp_config() -> dict:
    return _section("smtp", _SMTP_DEFAULTS)


def _imap_config() -> dict:
    return _section("imap", _IMAP_DEFAULTS)


def _sms_config() -> dict:
    return _section("sms", _SMS_DEFAULTS)


# ---------------------------------------------------------------- mail.send
def mail_send(args: dict) -> dict:
    """Send an email via real SMTP (smtplib). HIGH risk, needs network."""
    blocked = _enabled("mail.send")
    if blocked:
        return blocked
    cfg = _smtp_config()
    if not cfg.get("enabled") or not cfg.get("host"):
        return {"error": _NOT_CONFIGURED.format(section="smtp")}
    to = str(args.get("to", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    if not to:
        return {"error": "to is required"}
    if not subject:
        return {"error": "subject is required"}
    if not body:
        return {"error": "body is required"}
    host = cfg["host"]
    if not egress.check("mail.send", host):
        return {"error": f"egress to {host} blocked by policy"}
    try:
        port = int(cfg.get("port", 587))
    except (TypeError, ValueError):
        return {"error": "smtp port must be a number"}
    try:
        msg = EmailMessage()
        msg["From"] = cfg.get("from_addr") or cfg.get("username") or ""
        msg["To"] = to
        cc = args.get("cc")
        if cc:
            msg["Cc"] = ", ".join(cc) if isinstance(cc, (list, tuple)) else str(cc)
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if cfg.get("use_tls", True):
                smtp.starttls()
            if cfg.get("username"):
                smtp.login(cfg["username"], cfg.get("password", ""))
            smtp.send_message(msg)
        return {"sent": True, "to": to}
    except Exception as e:  # never leak the password in the error
        return {"error": f"smtp send failed: {type(e).__name__}: {e}"}


# -------------------------------------------------------------- mail helpers
def _parse_message(raw: bytes) -> dict:
    """Parse a raw RFC822 message into {from, subject, date, snippet}."""
    try:
        m = email.message_from_bytes(raw, policy=email.policy.default)
        snippet = ""
        if m.is_multipart():
            for part in m.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    try:
                        snippet = part.get_content()
                    except Exception:
                        payload = part.get_payload(decode=True) or b""
                        snippet = payload.decode("utf-8", errors="replace")
                    break
        else:
            try:
                snippet = m.get_content()
            except Exception:
                payload = m.get_payload(decode=True) or b""
                snippet = payload.decode("utf-8", errors="replace")
        return {
            "from": str(m.get("From", "")),
            "subject": str(m.get("Subject", "")),
            "date": str(m.get("Date", "")),
            "snippet": str(snippet).strip().replace("\r\n", "\n")[:300],
        }
    except Exception as e:
        return {"from": "", "subject": "", "date": "",
                "snippet": f"(could not parse message: {type(e).__name__})"}


def _imap_session(cfg: dict):
    """Open + login an IMAP4_SSL session. Returns (session, error_dict)."""
    if not cfg.get("enabled") or not cfg.get("host"):
        return None, {"error": _NOT_CONFIGURED.format(section="imap")}
    host = cfg["host"]
    if not egress.check("mail.read", host):
        return None, {"error": f"egress to {host} blocked by policy"}
    try:
        port = int(cfg.get("port", 993))
    except (TypeError, ValueError):
        return None, {"error": "imap port must be a number"}
    try:
        import imaplib  # local import so tests can monkeypatch imaplib.IMAP4_SSL
        M = imaplib.IMAP4_SSL(host, port)
        M.login(cfg.get("username", ""), cfg.get("password", ""))
        typ, _ = M.select(cfg.get("mailbox") or "INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError(f"mailbox select failed: {typ}")
        return M, None
    except Exception as e:
        return None, {"error": f"imap connection failed: {type(e).__name__}: {e}"}


def _fetch_ids(M, ids: list[bytes], limit: int) -> list[dict]:
    out = []
    for num in reversed(ids[-limit:]):
        try:
            typ, fdata = M.fetch(num, "(RFC822)")
            if typ != "OK" or not fdata or not fdata[0]:
                continue
            raw = fdata[0][1]
            if isinstance(raw, (bytes, bytearray)):
                out.append(_parse_message(bytes(raw)))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- mail.read
def mail_read(args: dict) -> dict:
    """Read recent emails via real IMAP. Medium risk, needs network."""
    blocked = _enabled("mail.read")
    if blocked:
        return blocked
    try:
        limit = max(1, min(100, int(args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    unread_only = bool(args.get("unread_only", False))
    M, err = _imap_session(_imap_config())
    if err:
        return err
    try:
        typ, data = M.search(None, "UNSEEN" if unread_only else "ALL")
        if typ != "OK":
            return {"error": f"imap search failed: {typ}"}
        ids = (data[0] or b"").split()
        messages = _fetch_ids(M, ids, limit)
        return {"messages": messages, "count": len(messages),
                "unread_only": unread_only}
    except Exception as e:
        return {"error": f"imap read failed: {type(e).__name__}: {e}"}
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass


# --------------------------------------------------------------- mail.search
def mail_search(args: dict) -> dict:
    """IMAP SEARCH over the configured mailbox. Medium risk, needs network."""
    blocked = _enabled("mail.search")
    if blocked:
        return blocked
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "query is required"}
    try:
        limit = max(1, min(100, int(args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    M, err = _imap_session(_imap_config())
    if err:
        return err
    try:
        # TEXT search; the query is quoted so spaces are safe.
        crit = '"%s"' % query.replace('"', "")
        typ, data = M.search(None, "TEXT", crit)
        if typ != "OK":
            return {"error": f"imap search failed: {typ}"}
        ids = (data[0] or b"").split()
        messages = _fetch_ids(M, ids, limit)
        return {"messages": messages, "count": len(messages), "query": query}
    except Exception as e:
        return {"error": f"imap search failed: {type(e).__name__}: {e}"}
    finally:
        try:
            M.close()
        except Exception:
            pass
        try:
            M.logout()
        except Exception:
            pass


# ----------------------------------------------------------------- sms.send
def sms_send(args: dict) -> dict:
    """Send an SMS via a config-gated HTTP webhook. HIGH risk, needs network."""
    blocked = _enabled("sms.send")
    if blocked:
        return blocked
    cfg = _sms_config()
    if not cfg.get("enabled") or not cfg.get("webhook_url"):
        return {"error": _NOT_CONFIGURED.format(section="sms")}
    to = str(args.get("to", "")).strip()
    message = str(args.get("message", "")).strip()
    if not to:
        return {"error": "to is required"}
    if not message:
        return {"error": "message is required"}
    url = cfg["webhook_url"]
    host = urllib.parse.urlparse(url).hostname or ""
    if not egress.check("sms.send", host):
        return {"error": f"egress to {host} blocked by policy"}
    try:
        payload = json.dumps({"to": to, "message": message}).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "User-Agent": "jarvis-sidecar/1.0"}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read()
        return {"sent": True, "to": to, "status": status,
                "response": body[:500].decode("utf-8", errors="replace")}
    except Exception as e:
        return {"error": f"sms send failed: {type(e).__name__}: {e}"}


# ------------------------------------------------------------ contacts (sqlite)
def _db_path(kind: str) -> Path:
    """Db path: tools_config.yaml '{kind}: {db_path: ...}' wins, else DATA_DIR."""
    override = (_load_config().get(kind, {}) or {}).get("db_path")
    if override:
        return Path(str(override)).expanduser()
    return DATA_DIR / f"{kind}.db"


def _contacts_conn() -> sqlite3.Connection:
    path = _db_path("contacts")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contacts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, phone TEXT, email TEXT, notes TEXT, "
        "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')))")
    return conn


def _like_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


def contacts_add(args: dict) -> dict:
    """Add a contact. Low risk, local sqlite."""
    blocked = _enabled("contacts.add")
    if blocked:
        return blocked
    name = str(args.get("name", "")).strip()
    if not name:
        return {"error": "name is required"}
    try:
        with _contacts_conn() as conn:
            cur = conn.execute(
                "INSERT INTO contacts (name, phone, email, notes) VALUES (?, ?, ?, ?)",
                (name, args.get("phone"), args.get("email"), args.get("notes")))
            conn.commit()
            return {"id": cur.lastrowid, "name": name}
    except Exception as e:
        return {"error": f"contacts.add failed: {type(e).__name__}: {e}"}


def contacts_search(args: dict) -> dict:
    """LIKE search over name/phone/email. Low risk, local sqlite."""
    blocked = _enabled("contacts.search")
    if blocked:
        return blocked
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "query is required"}
    try:
        pat = f"%{_like_escape(query)}%"
        with _contacts_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, phone, email, notes FROM contacts "
                "WHERE name LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\' "
                "OR email LIKE ? ESCAPE '\\' ORDER BY name LIMIT 100",
                (pat, pat, pat)).fetchall()
        return {"contacts": [
            {"id": r[0], "name": r[1], "phone": r[2], "email": r[3], "notes": r[4]}
            for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": f"contacts.search failed: {type(e).__name__}: {e}"}


def contacts_list(args: dict) -> dict:
    """List all contacts. Low risk, local sqlite."""
    blocked = _enabled("contacts.list")
    if blocked:
        return blocked
    try:
        limit = max(1, min(500, int(args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    try:
        with _contacts_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, phone, email, notes FROM contacts "
                "ORDER BY name LIMIT ?", (limit,)).fetchall()
        return {"contacts": [
            {"id": r[0], "name": r[1], "phone": r[2], "email": r[3], "notes": r[4]}
            for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": f"contacts.list failed: {type(e).__name__}: {e}"}


# --------------------------------------------- calendar (local sqlite, offline)
def _calendar_conn() -> sqlite3.Connection:
    path = _db_path("calendar")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "title TEXT NOT NULL, start_iso TEXT NOT NULL, "
        "end_iso TEXT, notes TEXT)")
    return conn


def _parse_iso(value: str, field: str):
    try:
        return _dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an ISO datetime "
                         f"(e.g. 2026-09-28T10:00:00), got {value!r}")


def calendar_create(args: dict) -> dict:
    """Insert a calendar event. Medium risk, local sqlite."""
    blocked = _enabled("calendar.create")
    if blocked:
        return blocked
    title = str(args.get("title", "")).strip()
    start_raw = args.get("start_iso")
    if not title:
        return {"error": "title is required"}
    if not start_raw:
        return {"error": "start_iso is required"}
    try:
        start = _parse_iso(start_raw, "start_iso")
    except ValueError as e:
        return {"error": str(e)}
    end_raw = args.get("end_iso")
    if end_raw:
        try:
            end = _parse_iso(end_raw, "end_iso")
        except ValueError as e:
            return {"error": str(e)}
        if end < start:
            return {"error": "end_iso must not be before start_iso"}
    try:
        with _calendar_conn() as conn:
            cur = conn.execute(
                "INSERT INTO events (title, start_iso, end_iso, notes) "
                "VALUES (?, ?, ?, ?)",
                (title, start.isoformat(), end.isoformat() if end_raw else None,
                 args.get("notes")))
            conn.commit()
            return {"id": cur.lastrowid, "title": title,
                    "start_iso": start.isoformat()}
    except Exception as e:
        return {"error": f"calendar.create failed: {type(e).__name__}: {e}"}


def calendar_read(args: dict) -> dict:
    """Read events in [date 00:00, date+days 00:00). Low risk, local sqlite."""
    blocked = _enabled("calendar.read")
    if blocked:
        return blocked
    date_raw = args.get("date")
    if date_raw:
        try:
            day = _dt.datetime.fromisoformat(str(date_raw)).date()
        except (TypeError, ValueError):
            return {"error": f"date must be ISO (e.g. 2026-09-28), got {date_raw!r}"}
    else:
        day = _dt.date.today()
    try:
        days = max(1, min(365, int(args.get("days", 1))))
    except (TypeError, ValueError):
        days = 1
    start = _dt.datetime.combine(day, _dt.time.min)
    end = start + _dt.timedelta(days=days)
    try:
        with _calendar_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, start_iso, end_iso, notes FROM events "
                "WHERE start_iso >= ? AND start_iso < ? ORDER BY start_iso",
                (start.isoformat(), end.isoformat())).fetchall()
        return {"events": [
            {"id": r[0], "title": r[1], "start_iso": r[2],
             "end_iso": r[3], "notes": r[4]} for r in rows],
                "count": len(rows),
                "from": start.isoformat(), "to": end.isoformat()}
    except Exception as e:
        return {"error": f"calendar.read failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------- registry wiring
def register_tools(reg) -> None:
    """Register the 9 comms tools. Called from build_registry() by the parent."""
    def T(name, desc, handler, risk="low", needs_network=False, schema=None):
        reg.register(Tool(name, desc, schema or {}, handler, risk, needs_network))

    T("mail.send", "Send an email via SMTP. IRREVERSIBLE — needs confirmation.",
      mail_send, risk="high", needs_network=True,
      schema={"to": "string", "subject": "string", "body": "string", "cc": "string|list?"})
    T("mail.read", "Read recent emails via IMAP.",
      mail_read, risk="medium", needs_network=True,
      schema={"limit": "int?", "unread_only": "bool?"})
    T("mail.search", "Search emails via IMAP SEARCH.",
      mail_search, risk="medium", needs_network=True,
      schema={"query": "string", "limit": "int?"})
    T("sms.send", "Send an SMS via a configured webhook. IRREVERSIBLE — needs confirmation.",
      sms_send, risk="high", needs_network=True,
      schema={"to": "string", "message": "string"})
    T("contacts.add", "Add a contact to the local address book.",
      contacts_add, risk="low", schema={"name": "string", "phone": "string?",
                                        "email": "string?", "notes": "string?"})
    T("contacts.search", "Search contacts by name, phone, or email.",
      contacts_search, risk="low", schema={"query": "string"})
    T("contacts.list", "List contacts.",
      contacts_list, risk="low", schema={"limit": "int?"})
    T("calendar.read", "Read local calendar events for a date range.",
      calendar_read, risk="low", schema={"date": "string?", "days": "int?"})
    T("calendar.create", "Create a local calendar event.",
      calendar_create, risk="medium",
      schema={"title": "string", "start_iso": "string", "end_iso": "string?",
              "notes": "string?"})
