"""Offline unit tests for the Gmail tool pack (Phase 8).

All tests run without network, without Google credentials, and without the
real google-api-python-client installed: the Google libraries are faked via
sys.modules and the pack's config loader / token reader are monkeypatched.
"""
import base64
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jarvis.tools.builtin import gmail_pack  # noqa: E402


# ---------------------------------------------------------------- fakes

class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


_PLAIN = base64.urlsafe_b64encode(b"Hello world body").decode()


class _FakeMessages:
    def list(self, **kw):
        return _FakeExec(
            {
                "messages": [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t1"}],
                "resultSizeEstimate": 2,
            }
        )

    def get(self, **kw):
        headers = [
            {"name": "Subject", "value": "Hi there"},
            {"name": "From", "value": "boss@example.com"},
            {"name": "Date", "value": "Sun, 27 Sep 2026 10:00:00 +0530"},
        ]
        if kw.get("format") == "metadata":
            return _FakeExec(
                {"id": kw["id"], "threadId": "t1", "snippet": "hello snippet",
                 "payload": {"headers": headers}}
            )
        return _FakeExec(
            {
                "id": kw["id"],
                "threadId": "t1",
                "snippet": "hello snippet",
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": headers,
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _PLAIN}},
                        {"mimeType": "text/html",
                         "body": {"data": base64.urlsafe_b64encode(b"<b>hi</b>").decode()}},
                    ],
                },
            }
        )

    def send(self, **kw):
        return _FakeExec({"id": "sent123", "threadId": "t9", "labelIds": ["SENT"]})


class _FakeLabels:
    def list(self, **kw):
        return _FakeExec({"labels": [{"name": "INBOX"}, {"name": "SENT"}]})


class _FakeUsers:
    def messages(self):
        return _FakeMessages()

    def labels(self):
        return _FakeLabels()


class _FakeService:
    def users(self):
        return _FakeUsers()


def _install_google_fakes(monkeypatch):
    """Fake googleapiclient.discovery.build + google_auth_oauthlib.flow imports."""
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *a, **k: _FakeService()
    gapi = types.ModuleType("googleapiclient")
    gapi.discovery = discovery
    flow_mod = types.ModuleType("google_auth_oauthlib.flow")
    oauthlib = types.ModuleType("google_auth_oauthlib")
    oauthlib.flow = flow_mod
    monkeypatch.setitem(sys.modules, "googleapiclient", gapi)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", oauthlib)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_mod)


def _authed(monkeypatch):
    """Put the pack in an authenticated state: fake config creds + fake google libs + stubbed token."""
    _install_google_fakes(monkeypatch)
    monkeypatch.setattr(
        gmail_pack, "_load_config",
        lambda: {"gmail": {"client_id": "cid", "client_secret": "csec"}},
    )
    monkeypatch.setattr(
        gmail_pack, "_read_token",
        lambda: types.SimpleNamespace(expired=False, refresh_token=None),
    )


# ---------------------------------------------------------------- tests

def test_search_no_creds_returns_setup_steps(monkeypatch):
    monkeypatch.setattr(gmail_pack, "_load_config", lambda: {})
    r = gmail_pack.gmail_search({"q": "invoice", "max": 5})
    assert "error" in r
    assert "setup" in r
    assert any("console.cloud.google.com" in s for s in r["setup"])


def test_search_parses_messages(monkeypatch):
    _authed(monkeypatch)
    r = gmail_pack.gmail_search({"q": "invoice", "max": 5})
    assert "error" not in r, r
    assert len(r["messages"]) == 2
    m = r["messages"][0]
    assert m["id"] == "m1"
    assert m["subject"] == "Hi there"
    assert m["from"] == "boss@example.com"
    assert "hello snippet" in m["snippet"]


def test_read_extracts_plain_body(monkeypatch):
    _authed(monkeypatch)
    r = gmail_pack.gmail_read({"id": "m1"})
    assert "error" not in r, r
    assert r["subject"] == "Hi there"
    assert r["body"] == "Hello world body"
    assert r["truncated"] is False


def test_send_roundtrip(monkeypatch):
    _authed(monkeypatch)
    r = gmail_pack.gmail_send({"to": "friend@example.com", "subject": "Hi", "body": "yo"})
    assert r == {"sent": True, "id": "sent123", "threadId": "t9"}


def test_send_validates_bad_email(monkeypatch):
    for bad in ("not-an-email", "a@b", "@x.com", "", "two @x.com"):
        r = gmail_pack.gmail_send({"to": bad, "subject": "s", "body": "b"})
        assert "error" in r and "invalid" in r["error"].lower(), bad


def test_labels_list(monkeypatch):
    _authed(monkeypatch)
    r = gmail_pack.gmail_labels({})
    assert r == {"labels": ["INBOX", "SENT"]}


def test_tool_defs_shape():
    names = {d["name"] for d in gmail_pack.TOOL_DEFS}
    assert names == {"gmail.auth_url", "gmail.search", "gmail.read", "gmail.send", "gmail.labels"}
    by_name = {d["name"]: d for d in gmail_pack.TOOL_DEFS}
    assert by_name["gmail.send"]["risk"] == "high"
    for d in gmail_pack.TOOL_DEFS:
        assert d["needs_network"] is True
        assert callable(d["handler"])
        assert d["risk"] in ("low", "high")
        assert isinstance(d["description"], str) and d["description"]
