"""Unit tests for the Phase 6 / Pack 1 Comms Tool Pack. All offline:
smtplib, imaplib, and urllib are mocked; contacts/calendar use tmp_path
sqlite databases. Handlers must never raise — they return {"error": ...}.
"""
import json
import smtplib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "sidecar")

from jarvis.tools.base import Registry  # noqa: E402
from jarvis.tools.builtin import comms  # noqa: E402
from jarvis.tools.builtin import creator as _creator_mod  # noqa: E402


# ------------------------------------------------------------------ helpers
def _cfg(monkeypatch, **sections):
    cfg = {"tools": {}}
    cfg.update(sections)
    fake = lambda: cfg  # noqa: E731
    # Patch both: comms._load_config (used directly) and creator._load_config
    # (used inside creator._enabled, which reads the creator module's name).
    monkeypatch.setattr(comms, "_load_config", fake)
    monkeypatch.setattr(_creator_mod, "_load_config", fake)
    return cfg


def _smtp_cfg(**kw):
    base = {"enabled": True, "host": "smtp.gmail.com", "port": 587,
            "username": "me@example.com", "password": "app-pass",
            "use_tls": True, "from_addr": "me@example.com"}
    base.update(kw)
    return base


def _imap_cfg(**kw):
    base = {"enabled": True, "host": "imap.gmail.com", "port": 993,
            "username": "me@example.com", "password": "app-pass",
            "mailbox": "INBOX"}
    base.update(kw)
    return base


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.tls = True

    def login(self, user, pw):
        self.login_args = (user, pw)

    def send_message(self, msg):
        self.sent = msg


class FakeIMAP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.search_criteria = None
        FakeIMAP.instances.append(self)

    def login(self, user, pw):
        self.login_args = (user, pw)
        return ("OK", [b"ok"])

    def select(self, mailbox, readonly=False):
        self.mailbox, self.readonly = mailbox, readonly
        return ("OK", [b"3"])

    def search(self, charset, *criteria):
        self.search_criteria = criteria
        return ("OK", [b"1 2 3"])

    def fetch(self, num, parts):
        raw = (b"From: Alice <alice@example.com>\r\n"
               b"Subject: Hello there\r\n"
               b"Date: Sat, 26 Sep 2026 10:00:00 +0530\r\n"
               b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
               b"Hi there, this is the message body.")
        return ("OK", [(b"%s (RFC822)" % num, raw)])

    def close(self):
        return ("OK", [b""])

    def logout(self):
        return ("OK", [b""])


class FakeResp:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": true}'


# ----------------------------------------------------------------- mail.send
def test_mail_send_not_configured(monkeypatch):
    _cfg(monkeypatch)
    r = comms.mail_send({"to": "a@b.c", "subject": "x", "body": "y"})
    assert r == {"error": "not configured: fill in the smtp: section of tools_config.yaml"}


def test_mail_send_disabled(monkeypatch):
    _cfg(monkeypatch, tools={"mail.send": {"enabled": False}},
         smtp=_smtp_cfg())
    r = comms.mail_send({"to": "a@b.c", "subject": "x", "body": "y"})
    assert "disabled" in r["error"]


def test_mail_send_missing_args(monkeypatch):
    _cfg(monkeypatch, smtp=_smtp_cfg())
    assert comms.mail_send({})["error"] == "to is required"
    assert comms.mail_send({"to": "a@b.c"})["error"] == "subject is required"
    assert comms.mail_send({"to": "a@b.c", "subject": "s"})["error"] == "body is required"


def test_mail_send_egress_blocked(monkeypatch):
    _cfg(monkeypatch, smtp=_smtp_cfg(host="smtp.evil.example"))
    r = comms.mail_send({"to": "a@b.c", "subject": "x", "body": "y"})
    assert r == {"error": "egress to smtp.evil.example blocked by policy"}


def test_mail_send_success(monkeypatch):
    _cfg(monkeypatch, smtp=_smtp_cfg())
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    r = comms.mail_send({"to": "boss@example.com", "subject": "Report",
                         "body": "Done.", "cc": ["cc@example.com"]})
    assert r == {"sent": True, "to": "boss@example.com"}
    inst = FakeSMTP.instances[-1]
    assert (inst.host, inst.port) == ("smtp.gmail.com", 587)
    assert inst.tls is True
    assert inst.login_args == ("me@example.com", "app-pass")
    assert inst.sent["To"] == "boss@example.com"
    assert inst.sent["Subject"] == "Report"
    assert inst.sent["Cc"] == "cc@example.com"


def test_mail_send_smtp_failure_returns_error(monkeypatch):
    _cfg(monkeypatch, smtp=_smtp_cfg())

    class BoomSMTP(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPException("relay denied")

    monkeypatch.setattr(smtplib, "SMTP", BoomSMTP)
    r = comms.mail_send({"to": "a@b.c", "subject": "x", "body": "y"})
    assert r["error"].startswith("smtp send failed:")
    assert "app-pass" not in r["error"]  # never leak the password


# ----------------------------------------------------------------- mail.read
def test_mail_read_not_configured(monkeypatch):
    _cfg(monkeypatch)
    r = comms.mail_read({})
    assert r == {"error": "not configured: fill in the imap: section of tools_config.yaml"}


def test_mail_read_success(monkeypatch):
    _cfg(monkeypatch, imap=_imap_cfg())
    FakeIMAP.instances.clear()
    import imaplib
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    r = comms.mail_read({"limit": 2})
    assert r["count"] == 2
    assert r["unread_only"] is False
    m = r["messages"][0]
    assert "alice@example.com" in m["from"]
    assert m["subject"] == "Hello there"
    assert "this is the message body" in m["snippet"]
    inst = FakeIMAP.instances[-1]
    assert inst.login_args == ("me@example.com", "app-pass")
    assert inst.mailbox == "INBOX"
    assert inst.search_criteria == ("ALL",)


def test_mail_read_unread_only(monkeypatch):
    _cfg(monkeypatch, imap=_imap_cfg())
    FakeIMAP.instances.clear()
    import imaplib
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    r = comms.mail_read({"unread_only": True})
    assert r["unread_only"] is True
    assert FakeIMAP.instances[-1].search_criteria == ("UNSEEN",)


# --------------------------------------------------------------- mail.search
def test_mail_search_requires_query(monkeypatch):
    _cfg(monkeypatch, imap=_imap_cfg())
    assert comms.mail_search({})["error"] == "query is required"


def test_mail_search_success(monkeypatch):
    _cfg(monkeypatch, imap=_imap_cfg())
    FakeIMAP.instances.clear()
    import imaplib
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    r = comms.mail_search({"query": "invoice"})
    assert r["count"] == 3
    assert r["query"] == "invoice"
    crit = FakeIMAP.instances[-1].search_criteria
    assert crit[0] == "TEXT" and "invoice" in crit[1]


# ------------------------------------------------------------------ sms.send
def test_sms_send_not_configured(monkeypatch):
    _cfg(monkeypatch)
    r = comms.sms_send({"to": "+911234567890", "message": "hi"})
    assert r == {"error": "not configured: fill in the sms: section of tools_config.yaml"}


def test_sms_send_egress_blocked(monkeypatch):
    _cfg(monkeypatch, sms={"enabled": True, "provider": "webhook",
                           "webhook_url": "https://sms.example.com/hook"})
    r = comms.sms_send({"to": "+911234567890", "message": "hi"})
    assert r == {"error": "egress to sms.example.com blocked by policy"}


def test_sms_send_success(monkeypatch):
    _cfg(monkeypatch, sms={"enabled": True, "provider": "webhook",
                           "webhook_url": "https://sms.example.com/hook",
                           "api_key": "sekret"})
    monkeypatch.setattr(comms.egress, "check", lambda tool, host: True)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode())
        captured["headers"] = dict(req.header_items())
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = comms.sms_send({"to": "+911234567890", "message": "hello"})
    assert r["sent"] is True and r["to"] == "+911234567890"
    assert captured["url"] == "https://sms.example.com/hook"
    assert captured["data"] == {"to": "+911234567890", "message": "hello"}
    assert captured["headers"]["Authorization"] == "Bearer sekret"


def test_sms_send_missing_args(monkeypatch):
    _cfg(monkeypatch, sms={"enabled": True, "provider": "webhook",
                           "webhook_url": "https://sms.example.com/hook"})
    assert comms.sms_send({})["error"] == "to is required"
    assert comms.sms_send({"to": "x"})["error"] == "message is required"


# ------------------------------------------------------------------ contacts
def test_contacts_add_search_list(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(comms, "DATA_DIR", tmp_path)
    r = comms.contacts_add({"name": "Arjun Mehta", "phone": "+919876543210",
                            "email": "arjun@example.com", "notes": "gym owner"})
    assert r["name"] == "Arjun Mehta" and isinstance(r["id"], int)
    r2 = comms.contacts_add({"name": "Priya Sharma", "email": "priya@example.com"})
    assert r2["id"] != r["id"]

    s = comms.contacts_search({"query": "arjun"})
    assert s["count"] == 1 and s["contacts"][0]["phone"] == "+919876543210"

    s = comms.contacts_search({"query": "example.com"})
    assert s["count"] == 2  # matches on email for both

    s = comms.contacts_search({"query": "9876"})
    assert s["count"] == 1 and s["contacts"][0]["name"] == "Arjun Mehta"

    lst = comms.contacts_list({})
    assert lst["count"] == 2
    assert [c["name"] for c in lst["contacts"]] == ["Arjun Mehta", "Priya Sharma"]


def test_contacts_add_requires_name(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(comms, "DATA_DIR", tmp_path)
    assert comms.contacts_add({})["error"] == "name is required"
    assert comms.contacts_search({})["error"] == "query is required"


def test_contacts_search_escapes_wildcards(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(comms, "DATA_DIR", tmp_path)
    comms.contacts_add({"name": "100% Real"})
    s = comms.contacts_search({"query": "%"})
    assert s["count"] == 1  # literal %, not a wildcard matching everything
    s = comms.contacts_search({"query": "zzz-no-match"})
    assert s["count"] == 0


# ------------------------------------------------------------------ calendar
def test_calendar_create_and_read(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(comms, "DATA_DIR", tmp_path)
    c = comms.calendar_create({"title": "Leg day", "start_iso": "2026-09-28T07:00:00",
                               "end_iso": "2026-09-28T08:00:00", "notes": "squats"})
    assert isinstance(c["id"], int) and c["title"] == "Leg day"

    r = comms.calendar_read({"date": "2026-09-28"})
    assert r["count"] == 1
    assert r["events"][0]["title"] == "Leg day"
    assert r["events"][0]["notes"] == "squats"

    r = comms.calendar_read({"date": "2026-09-28", "days": 3})
    assert r["count"] == 1  # still in range

    r = comms.calendar_read({"date": "2026-10-01"})
    assert r["count"] == 0  # out of range

    # default date = today
    today = comms.calendar_create({"title": "Today thing",
                                   "start_iso": "2026-09-27T12:00:00"})
    assert "id" in today


def test_calendar_create_bad_input(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(comms, "DATA_DIR", tmp_path)
    assert comms.calendar_create({})["error"] == "title is required"
    assert comms.calendar_create({"title": "x"})["error"] == "start_iso is required"
    r = comms.calendar_create({"title": "x", "start_iso": "not-a-date"})
    assert "start_iso must be an ISO datetime" in r["error"]
    r = comms.calendar_create({"title": "x", "start_iso": "2026-09-28T10:00:00",
                               "end_iso": "2026-09-28T09:00:00"})
    assert r["error"] == "end_iso must not be before start_iso"
    r = comms.calendar_read({"date": "yesterday-ish"})
    assert "date must be ISO" in r["error"]


# ---------------------------------------------------------- registry wiring
def test_register_tools_registers_all_nine():
    reg = Registry()
    comms.register_tools(reg)
    expected = {
        "mail.send": ("high", True),
        "mail.read": ("medium", True),
        "mail.search": ("medium", True),
        "sms.send": ("high", True),
        "contacts.add": ("low", False),
        "contacts.search": ("low", False),
        "contacts.list": ("low", False),
        "calendar.read": ("low", False),
        "calendar.create": ("medium", False),
    }
    assert set(reg.tools) == set(expected)
    for name, (risk, net) in expected.items():
        t = reg.tools[name]
        assert (t.risk, t.needs_network) == (risk, net), name


def test_disabled_tool_returns_error_not_raise(monkeypatch, tmp_path):
    _cfg(monkeypatch, tools={"calendar.create": {"enabled": False}})
    monkeypatch.setattr(comms, "DATA_DIR", tmp_path)
    r = comms.calendar_create({"title": "x", "start_iso": "2026-09-28T10:00:00"})
    assert "disabled" in r["error"]
