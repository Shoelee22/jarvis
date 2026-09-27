"""Unit tests for the Phase 8 Telegram Tool Pack. All offline:
urllib.request.urlopen is monkeypatched with canned Bot API responses.
"""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "sidecar")

import pytest  # noqa: E402

from jarvis.security import egress  # noqa: E402
from jarvis.tools.base import Registry  # noqa: E402
from jarvis.tools.builtin import telegram_pack  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    # egress default-deny would block api.telegram.org in tests.
    egress.set_enabled(False)
    yield
    egress.set_enabled(True)


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_factory(responses, captured, raise_for=None):
    """responses: method -> payload or Exception factory."""
    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8")) if req.data else {}
        captured["timeout"] = timeout
        method = req.full_url.rsplit("/", 1)[-1]
        if raise_for and method == raise_for:
            body = json.dumps({"ok": False, "description": "Unauthorized",
                               "error_code": 401}).encode()
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                         {}, io.BytesIO(body))
        return FakeResp(responses[method])
    return fake


def _use_cfg(monkeypatch, **tg):
    monkeypatch.setattr(telegram_pack, "_load_config", lambda: dict(tg))


def _use_urlopen(monkeypatch, responses, captured, raise_for=None):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _fake_urlopen_factory(responses, captured, raise_for))


ME = {"ok": True, "result": {"id": 123456789, "is_bot": True,
                             "first_name": "Jarvis", "username": "jarvis_bot"}}
SEND = {"ok": True, "result": {"message_id": 42,
                               "chat": {"id": 987, "type": "private"}}}
UPDATES = {"ok": True, "result": [
    {"update_id": 100, "message": {"message_id": 1,
                                   "from": {"id": 987, "first_name": "Raj",
                                            "username": "raj"},
                                   "chat": {"id": 987}, "text": "hello jarvis"}},
    {"update_id": 101, "message": {"message_id": 2,
                                   "from": {"id": 555, "first_name": "Asha"},
                                   "chat": {"id": 555},
                                   "photo": [], "caption": "look at this"}},
]}


# ----------------------------------------------------------- TOOL_DEFS

def test_tool_defs_shape():
    defs = telegram_pack.TOOL_DEFS
    assert len(defs) == 4
    by_name = {d["name"]: d for d in defs}
    assert set(by_name) == {"telegram.me", "telegram.send",
                            "telegram.updates", "telegram.register_chat"}
    assert by_name["telegram.send"]["risk"] == "high"
    for d in defs:
        assert d["needs_network"] is True
        for key in ("name", "description", "handler", "risk", "needs_network", "schema"):
            assert key in d
        assert callable(d["handler"])


def test_register_into_registry():
    reg = Registry()
    telegram_pack.register(reg)
    assert set(reg.tools) >= {"telegram.me", "telegram.send",
                              "telegram.updates", "telegram.register_chat"}
    assert reg.tools["telegram.send"].risk == "high"


# -------------------------------------------------------------- telegram.me

def test_me_ok(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN")
    captured = {}
    _use_urlopen(monkeypatch, {"getMe": ME}, captured)
    out = telegram_pack.telegram_me({})
    assert out == {"ok": True, "id": 123456789, "username": "jarvis_bot",
                   "first_name": "Jarvis"}
    assert captured["url"] == "https://api.telegram.org/botTOKEN/getMe"
    assert captured["timeout"] == 20


def test_me_bad_token_surfaces_telegram_error(monkeypatch):
    _use_cfg(monkeypatch, bot_token="BAD")
    _use_urlopen(monkeypatch, {}, {}, raise_for="getMe")
    out = telegram_pack.telegram_me({})
    assert "error" in out
    assert "Unauthorized" in out["error"]
    assert out["http_status"] == 401


def test_me_no_token_honest_config_error(monkeypatch):
    _use_cfg(monkeypatch)  # no bot_token
    out = telegram_pack.telegram_me({})
    assert out["error"] == telegram_pack.CONFIG_ERROR


# ------------------------------------------------------------ telegram.send

def test_send_ok(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN", default_chat_id=987)
    captured = {}
    _use_urlopen(monkeypatch, {"sendMessage": SEND}, captured)
    out = telegram_pack.telegram_send({"text": "sir, systems nominal"})
    assert out == {"message_id": 42, "chat_id": 987}
    assert captured["payload"] == {"chat_id": 987,
                                   "text": "sir, systems nominal"}


def test_send_explicit_chat_id_beats_default(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN", default_chat_id=987)
    captured = {}
    _use_urlopen(monkeypatch, {"sendMessage": SEND}, captured)
    telegram_pack.telegram_send({"chat_id": 111, "text": "hi"})
    assert captured["payload"]["chat_id"] == 111


def test_send_truncates_at_4000(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN", default_chat_id=987)
    captured = {}
    _use_urlopen(monkeypatch, {"sendMessage": SEND}, captured)
    out = telegram_pack.telegram_send({"text": "x" * 5000})
    assert len(captured["payload"]["text"]) == 4000
    assert out["note"] == "text truncated to 4000 chars"


def test_send_no_chat_id_and_no_default_errors(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN")
    captured = {}
    _use_urlopen(monkeypatch, {"sendMessage": SEND}, captured)
    out = telegram_pack.telegram_send({"text": "hi"})
    assert "error" in out
    assert "default_chat_id" in out["error"]
    assert captured == {}  # no HTTP call made


def test_send_no_token_honest_config_error(monkeypatch):
    _use_cfg(monkeypatch, default_chat_id=987)
    out = telegram_pack.telegram_send({"text": "hi"})
    assert out["error"] == telegram_pack.CONFIG_ERROR


def test_send_telegram_failure_surfaces_description(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN", default_chat_id=987)

    def fake(req, timeout=None):
        body = json.dumps({"ok": False, "error_code": 400,
                           "description": "Bad Request: chat not found"}).encode()
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                     {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = telegram_pack.telegram_send({"text": "hi"})
    assert out["error"] == "telegram API error: Bad Request: chat not found"


# --------------------------------------------------------- telegram.updates

def test_updates_simplified(monkeypatch):
    _use_cfg(monkeypatch, bot_token="TOKEN")
    captured = {}
    _use_urlopen(monkeypatch, {"getUpdates": UPDATES}, captured)
    out = telegram_pack.telegram_updates({"limit": 10})
    assert out == {"updates": [
        {"update_id": 100, "from": "raj", "text": "hello jarvis"},
        {"update_id": 101, "from": "Asha", "text": "look at this"},
    ]}
    assert captured["payload"]["limit"] == 10


# ----------------------------------------------------- telegram.register_chat

def test_register_chat_int_returns_snippet(monkeypatch):
    out = telegram_pack.telegram_register_chat({"chat_id": 12345})
    assert out["ok"] is True
    assert out["add_to_config"] == "telegram:\n  default_chat_id: 12345"


def test_register_chat_channel_string_returns_snippet(monkeypatch):
    out = telegram_pack.telegram_register_chat({"chat_id": "@jarvis_alerts"})
    assert out["ok"] is True
    assert out["add_to_config"] == "telegram:\n  default_chat_id: @jarvis_alerts"


def test_register_chat_numeric_string_normalized(monkeypatch):
    out = telegram_pack.telegram_register_chat({"chat_id": "12345"})
    assert out["add_to_config"] == "telegram:\n  default_chat_id: 12345"


def test_register_chat_invalid_rejected(monkeypatch):
    for bad in ("not a chat", "", "@", None, 12.5, True):
        out = telegram_pack.telegram_register_chat({"chat_id": bad})
        assert "error" in out, bad
