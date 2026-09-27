"""Offline unit tests for the Universal Inbox pack (Phase 9).

Channel packs are faked via sys.modules (fake gmail_pack / telegram_pack /
comms modules). No network, no credentials, no real Google/Telegram/IMAP.
"""
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jarvis.tools.builtin import inbox_pack  # noqa: E402
from jarvis.tools.base import Tool  # noqa: E402

NOW = time.time()

_FAKE_KEYS = ("jarvis.tools.builtin.gmail_pack",
              "jarvis.tools.builtin.telegram_pack",
              "jarvis.tools.builtin.comms")


def _make_module(name, **attrs):
    mod = types.ModuleType(f"jarvis.tools.builtin.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture
def fake_channels():
    """Inject working fake channel packs; restore sys.modules afterwards."""
    calls = {"gmail_send": [], "gmail_read": []}
    gmail_msgs = [
        {"id": "m1",
         "subject": "URGENT: invoice payment due Friday",
         "from": "Boss Man <boss@corp.com>",
         "date": NOW - 600,  # 10 min ago
         "snippet": "Please pay the invoice asap. The deadline is Friday."},
        {"id": "m2",
         "subject": "weekly newsletter",
         "from": "news@example.com",
         "date": NOW - 90000,  # 25 h ago
         "snippet": "Here is your weekly newsletter with fun stories."},
    ]

    def fake_gmail_search(args):
        return {"messages": list(gmail_msgs)}

    def fake_gmail_read(args):
        calls["gmail_read"].append(args)
        return {"id": args.get("id"), "from": "Boss Man <boss@corp.com>",
                "subject": "URGENT: invoice payment due Friday",
                "body": "Please pay the invoice asap. Deadline is Friday."}

    def fake_gmail_send(args):
        calls["gmail_send"].append(args)
        return {"sent": True, "id": "s1"}

    def fake_telegram_updates(args):
        return {"updates": [
            {"update_id": 7, "from": "alice",
             "text": "call me when you land, need to talk"}]}

    def fake_mail_read(args):
        assert args.get("unread_only") is True
        return {"messages": [
            {"from": "friend@example.org", "subject": "hi",
             "date": NOW - 7200,  # 2 h ago
             "snippet": "just saying hi, no rush"}],
                "count": 1, "unread_only": True}

    def fake_contacts_list(args):
        return {"contacts": [
            {"id": 1, "name": "Boss Man", "phone": "+911234567890",
             "email": "boss@corp.com", "notes": ""}]}

    fakes = {
        "gmail_pack": _make_module(
            "gmail_pack", gmail_search=fake_gmail_search,
            gmail_read=fake_gmail_read, gmail_send=fake_gmail_send),
        "telegram_pack": _make_module(
            "telegram_pack", telegram_updates=fake_telegram_updates),
        "comms": _make_module(
            "comms", mail_read=fake_mail_read, contacts_list=fake_contacts_list),
    }
    saved = {}
    for short, key in zip(("gmail_pack", "telegram_pack", "comms"), _FAKE_KEYS):
        saved[key] = sys.modules.get(key)
        sys.modules[key] = fakes[short]
    yield calls
    for key in _FAKE_KEYS:
        if saved[key] is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = saved[key]


@pytest.fixture
def registry():
    class FakeRegistry:
        def __init__(self):
            self.tools = {}

        def register(self, tool):
            self.tools[tool.name] = tool

    reg = FakeRegistry()
    inbox_pack.register(reg)
    return reg


def _call(registry, name, args):
    return registry.tools[name].handler(args)


# ------------------------------------------------------------ triage

def test_triage_merges_and_sorts(fake_channels, registry):
    out = _call(registry, "inbox.triage", {"limit": 30})
    assert "error" not in out
    ids = [m["id"] for m in out["messages"]]
    # m1: in-contacts (+2) + urgent/invoice/asap/deadline (+2) + recent (+1) = 5
    # telegram: keyword "call me" (+2) + recent (+1) = 3
    # imap: nothing = 0 ; m2: nothing = 0, older ts so last
    assert ids == ["gmail:m1", "telegram:7", "imap:0", "gmail:m2"]
    scores = [m["urgency"] for m in out["messages"]]
    assert scores == [5, 3, 0, 0]
    assert out["channels"]["gmail"] == "ok (2)"
    assert out["channels"]["telegram"] == "ok (1)"
    assert out["channels"]["imap"] == "ok (1)"
    assert out["channels"]["sms"].startswith("unavailable:")
    assert isinstance(out["generated_at"], int)
    # normalized shape
    m = out["messages"][0]
    assert set(("id", "channel", "sender", "subject_or_first_line", "ts",
                "snippet", "reply_to", "urgency")) <= set(m.keys())
    assert m["reply_to"] == "boss@corp.com"


def test_triage_limit_trims(fake_channels, registry):
    out = _call(registry, "inbox.triage", {"limit": 2})
    assert len(out["messages"]) == 2
    assert out["messages"][0]["id"] == "gmail:m1"


def test_channel_exception_recorded_not_crashed(registry):
    def boom(args):
        raise RuntimeError("connection exploded")

    saved = {}
    fakes = {
        "gmail_pack": _make_module("gmail_pack", gmail_search=boom),
        "telegram_pack": _make_module(
            "telegram_pack",
            telegram_updates=lambda args: {"updates": []}),
        "comms": _make_module(
            "comms",
            mail_read=lambda args: {"messages": [], "count": 0},
            contacts_list=lambda args: {"contacts": []}),
    }
    for short, key in zip(("gmail_pack", "telegram_pack", "comms"), _FAKE_KEYS):
        saved[key] = sys.modules.get(key)
        sys.modules[key] = fakes[short]
    try:
        out = _call(registry, "inbox.triage", {})
    finally:
        for key in _FAKE_KEYS:
            if saved[key] is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = saved[key]
    assert out["channels"]["gmail"].startswith("unavailable:")
    assert "exploded" in out["channels"]["gmail"]
    assert out["channels"]["telegram"] == "ok (0)"
    assert out["channels"]["imap"] == "ok (0)"
    assert out["messages"] == []


def test_gmail_pack_without_list_function(registry):
    saved = {}
    fakes = {
        "gmail_pack": _make_module("gmail_pack"),  # no gmail_search at all
        "telegram_pack": _make_module(
            "telegram_pack", telegram_updates=lambda args: {"updates": []}),
        "comms": _make_module(
            "comms", mail_read=lambda args: {"messages": [], "count": 0},
            contacts_list=lambda args: {"contacts": []}),
    }
    for short, key in zip(("gmail_pack", "telegram_pack", "comms"), _FAKE_KEYS):
        saved[key] = sys.modules.get(key)
        sys.modules[key] = fakes[short]
    try:
        out = _call(registry, "inbox.triage", {})
    finally:
        for key in _FAKE_KEYS:
            if saved[key] is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = saved[key]
    assert out["channels"]["gmail"] == \
        "unavailable: gmail_pack has no list function"


def test_all_channels_missing(registry, monkeypatch):
    for ch in inbox_pack.CHANNELS:
        monkeypatch.setitem(inbox_pack._CHANNEL_MODULES, ch,
                            "definitely_missing_module_xyz")
    out = _call(registry, "inbox.triage", {})
    assert out["messages"] == []
    assert len(out["channels"]) == 4
    for ch in inbox_pack.CHANNELS:
        assert out["channels"][ch].startswith("unavailable:"), ch


def test_empty_everything_honest(fake_channels, registry, monkeypatch):
    # fakes return empty lists everywhere -> honest empty result
    sys.modules["jarvis.tools.builtin.gmail_pack"].gmail_search = \
        lambda args: {"messages": []}
    sys.modules["jarvis.tools.builtin.telegram_pack"].telegram_updates = \
        lambda args: {"updates": []}
    sys.modules["jarvis.tools.builtin.comms"].mail_read = \
        lambda args: {"messages": [], "count": 0}
    out = _call(registry, "inbox.triage", {})
    assert out["messages"] == []
    assert out["channels"]["gmail"] == "ok (0)"
    assert out["channels"]["telegram"] == "ok (0)"
    assert out["channels"]["imap"] == "ok (0)"


# ---------------------------------------------------------------- read

def test_read_delegates_to_gmail(fake_channels, registry):
    out = _call(registry, "inbox.read", {"id": "gmail:m1"})
    assert out["id"] == "m1"
    assert "invoice" in out["body"]
    assert fake_channels["gmail_read"] == [{"id": "m1"}]


def test_read_bad_id_shape(registry):
    out = _call(registry, "inbox.read", {"id": "no-colon-here"})
    assert "error" in out


def test_read_unknown_channel(registry):
    out = _call(registry, "inbox.read", {"id": "pigeon:42"})
    assert "error" in out and "unknown channel" in out["error"]


def test_read_telegram_honest_error(registry):
    out = _call(registry, "inbox.read", {"id": "telegram:7"})
    assert "error" in out


# ---------------------------------------------------------------- reply

def test_reply_delegates_to_gmail_send(fake_channels, registry):
    out = _call(registry, "inbox.reply",
                {"id": "gmail:m1", "text": "On it, paying today."})
    assert out.get("sent") is True
    sent = fake_channels["gmail_send"][0]
    assert sent["to"] == "boss@corp.com"
    assert sent["subject"].startswith("Re:")
    assert sent["body"] == "On it, paying today."


def test_reply_requires_text(registry):
    out = _call(registry, "inbox.reply", {"id": "gmail:m1"})
    assert "error" in out


def test_reply_telegram_honest_error(registry):
    out = _call(registry, "inbox.reply",
                {"id": "telegram:7", "text": "hi"})
    assert "error" in out


def test_reply_sms_honest_error(registry):
    out = _call(registry, "inbox.reply", {"id": "sms:1", "text": "hi"})
    assert "error" in out


# ------------------------------------------------------------ summarize

def test_extractive_returns_at_most_k_sentences():
    texts = ["The invoice is urgent and must be paid. "
             "Please pay the invoice before the deadline. "
             "The weather is nice today and the sky is blue."]
    got = inbox_pack._extractive(texts, 2)
    assert len(got) == 2
    assert any("invoice" in s for s in got)
    got1 = inbox_pack._extractive(texts, 1)
    assert len(got1) == 1 and "invoice" in got1[0]


def test_extractive_empty():
    assert inbox_pack._extractive([], 5) == []
    assert inbox_pack._extractive([""], 5) == []


def test_summarize_triage_set(fake_channels, registry):
    out = _call(registry, "inbox.summarize", {"k": 3})
    assert out["source"] == "triage"
    assert out["messages"] == 4
    assert len(out["summary"]) <= 3
    assert all(isinstance(s, str) and s for s in out["summary"])


def test_summarize_single_message(fake_channels, registry):
    out = _call(registry, "inbox.summarize", {"id": "gmail:m1", "k": 5})
    assert out["source"] == "gmail:m1"
    assert out["messages"] == 1
    assert len(out["summary"]) <= 5
    assert any("invoice" in s.lower() for s in out["summary"])


def test_summarize_unknown_id(fake_channels, registry):
    out = _call(registry, "inbox.summarize", {"id": "gmail:nope"})
    assert "error" in out


# ---------------------------------------------------------- registration

def test_register_contract(registry):
    assert set(inbox_pack.RISK_TABLE_ADDITIONS) == {
        "inbox.triage", "inbox.read", "inbox.reply", "inbox.summarize"}
    assert inbox_pack.RISK_TABLE_ADDITIONS["inbox.reply"] == ("high", True)
    assert inbox_pack.RISK_TABLE_ADDITIONS["inbox.triage"] == ("low", True)
    for name, (risk, needs_net) in inbox_pack.RISK_TABLE_ADDITIONS.items():
        tool = registry.tools[name]
        assert isinstance(tool, Tool)
        assert tool.risk == risk
        assert tool.needs_network == needs_net
        assert callable(tool.handler)


def test_register_merges_policy_risk_table(registry):
    from jarvis.agent.policy import RISK_TABLE, PolicyEngine
    for name, (risk, _net) in inbox_pack.RISK_TABLE_ADDITIONS.items():
        assert RISK_TABLE[name] == (risk, inbox_pack.RISK_TABLE_ADDITIONS[name][1])
    # high-risk reply must now require confirmation instead of default-deny
    verdict = PolicyEngine().decide("inbox.reply", {"id": "gmail:m1"})
    assert verdict["action"] == "confirm"


def test_handlers_never_raise(registry):
    # garbage args must produce dicts, not exceptions
    for name, args in [("inbox.triage", {"limit": "banana"}),
                       ("inbox.triage", None),
                       ("inbox.read", {}),
                       ("inbox.reply", {"id": "gmail:x"}),
                       ("inbox.summarize", {"k": "zzz"})]:
        out = registry.tools[name].handler(args)
        assert isinstance(out, dict), name
