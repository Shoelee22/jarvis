"""Unit tests for the Phase 9 Phone Conversations pack. All offline:

- urllib.request.urlopen is monkeypatched with canned Twilio responses.
- The sqlite call log is redirected to a tmp dir.
- voice.pipeline's real backends are never loaded (QueuedSTT/FakeTTS only).
"""
import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, "sidecar")

import pytest  # noqa: E402

from jarvis.security import egress  # noqa: E402
from jarvis.tools.base import Registry  # noqa: E402
from jarvis.tools.builtin import phone_pack, phone_stream  # noqa: E402
from jarvis.tools.builtin.phone_stream import (  # noqa: E402
    PhoneCallSession, QueuedSTT, TURN_BYTES,
    mulaw_decode, mulaw_encode, mulaw_bytes_to_pcm16,
    pcm16_16k_chunks_to_mulaw_8k,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # egress default-deny would block api.twilio.com in tests.
    egress.set_enabled(False)
    # redirect the sqlite call log to a tmp db per test.
    monkeypatch.setattr(phone_pack, "_db_path",
                        lambda: tmp_path / "phone_calls.db")
    # no agent bound by default.
    monkeypatch.setattr(phone_pack, "_AGENT", None)
    phone_pack.reset_backends()
    phone_pack._SESSIONS.clear()
    phone_pack._PENDING_VOICE.clear()
    yield
    egress.set_enabled(True)
    phone_pack._AGENT = None
    phone_pack._SESSIONS.clear()
    phone_pack._PENDING_VOICE.clear()


# ------------------------------------------------------------- canned twilio
class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(payloads, captured):
    """payloads: url-substring -> response dict."""
    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = urllib.parse.parse_qs(req.data.decode("utf-8")) \
            if req.data else {}
        for needle, payload in payloads.items():
            if needle in req.full_url:
                return FakeResp(payload)
        raise AssertionError(f"unexpected twilio url: {req.full_url}")
    return fake


_FAKE_CFG = {
    "twilio_sid": "ACtest",
    "twilio_token": "tokentest",
    "from_number": "+15550001111",
    "public_ws_url": "wss://example.com/twilio-stream",
    "db_path": None,
    "tts_voice_onnx": None,
}


def _with_config(monkeypatch, **over):
    cfg = dict(_FAKE_CFG)
    cfg.update(over)
    monkeypatch.setattr(phone_pack, "_phone_config", lambda: cfg)


# ------------------------------------------------- script_test: the real path
def test_script_test_full_conversation():
    script = ["Hello, this is Jarvis calling.", "Just checking in, sir.",
              "Very good — goodbye."]
    callers = ["hello?", "who is this?", "ok bye"]
    out = phone_pack.script_test({"script": script, "caller_lines": callers})
    assert "error" not in out, out
    assert out["turn_count"] == 3
    assert len(out["turns"]) == 3
    t = out["transcript"]
    assert len(t) == 6, t
    # strict alternation: caller, assistant, caller, assistant, ...
    for i, entry in enumerate(t):
        assert entry["who"] == ("caller" if i % 2 == 0 else "assistant")
        assert entry["ts"]
    caller_texts = [e["text"] for e in t if e["who"] == "caller"]
    assistant_texts = [e["text"] for e in t if e["who"] == "assistant"]
    assert caller_texts == callers
    # assistant used the script lines in order
    assert assistant_texts == script
    assert out["spoken"] == script
    assert out["agent_bound"] is False


def test_script_test_bound_agent_answers():
    class FakeAgent:
        def run(self, text):
            return {"reply": f"AGENT heard: {text}"}

    phone_pack._AGENT = FakeAgent()
    out = phone_pack.script_test({"script": ["ignored line"],
                                  "caller_lines": ["hi there"]})
    assert "error" not in out, out
    t = out["transcript"]
    assert [e["who"] for e in t] == ["caller", "assistant"]
    assert t[1]["text"] == "AGENT heard: hi there"
    assert out["agent_bound"] is True


def test_script_test_requires_inputs():
    assert "error" in phone_pack.script_test({"script": [], "caller_lines": ["x"]})
    assert "error" in phone_pack.script_test({"script": ["x"], "caller_lines": []})
    assert "error" in phone_pack.script_test({})


# ------------------------------------------- call_and_talk validation/errors
def test_call_and_talk_missing_config_names_keys(monkeypatch):
    # No phase9 config file exists -> every phone.* key is None.
    monkeypatch.setattr(phone_pack, "_phone_config",
                        lambda: dict(phone_pack._PHONE_DEFAULTS))
    out = phone_pack.call_and_talk({"to": "+15551234567", "script": ["hi"]})
    assert "error" in out
    err = out["error"]
    assert "phone.twilio_sid" in err
    assert "phone.twilio_token" in err
    assert "phone.from_number" in err
    assert "tools_config.phase9.yaml" in err


def test_call_and_talk_missing_ws_url(monkeypatch):
    _with_config(monkeypatch, public_ws_url=None)
    out = phone_pack.call_and_talk({"to": "+15551234567", "script": ["hi"]})
    assert out == {"error": "phone.public_ws_url not set — Twilio cannot reach your media stream"}


@pytest.mark.parametrize("bad", ["", "5551234567", "+1", "+1234567890123456",
                                 "++15551234567", "+1555-123-4567", None])
def test_call_and_talk_invalid_number(bad):
    out = phone_pack.call_and_talk({"to": bad, "script": ["hi"]})
    assert "error" in out
    assert "E.164" in out["error"]


def test_call_and_talk_posts_twiml_and_logs(monkeypatch):
    _with_config(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({"/Calls.json": {"sid": "CA999", "status": "queued"}},
                      captured))
    out = phone_pack.call_and_talk({"to": "+15551234567",
                                    "script": ["Hello, sir."]})
    assert out["call_id"] == "CA999"
    assert out["status"] == "ringing"
    # Basic auth header present, TwiML param carries the stream URL.
    assert captured["auth"] == "Basic " + base64.b64encode(
        b"ACtest:tokentest").decode()
    twiml = captured["body"]["Twiml"][0]
    assert "wss://example.com/twilio-stream" in twiml
    assert "<Stream" in twiml
    assert captured["body"]["To"] == ["+15551234567"]
    assert captured["body"]["From"] == ["+15550001111"]
    # logged to sqlite
    row = phone_pack.phone_log({"call_id": "CA999"})
    assert row["status"] == "ringing"
    assert row["to"] == "+15551234567"


# ------------------------------------------------------------- hangup
def test_hangup_completes_and_updates_db(monkeypatch):
    _with_config(monkeypatch)
    phone_pack._log_call("CA123", "+15551234567", "ringing")
    captured = {}
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen({"/Calls/CA123.json": {"sid": "CA123", "status": "completed"}},
                      captured))
    out = phone_pack.hangup({"call_id": "CA123"})
    assert out == {"call_id": "CA123", "status": "completed"}
    assert captured["body"]["Status"] == ["completed"]
    assert "CA123" in captured["url"]
    row = phone_pack.phone_log({"call_id": "CA123"})
    assert row["status"] == "completed"


def test_hangup_missing_config(monkeypatch):
    monkeypatch.setattr(phone_pack, "_phone_config",
                        lambda: dict(phone_pack._PHONE_DEFAULTS))
    out = phone_pack.hangup({"call_id": "CA123"})
    assert "error" in out
    assert "phone.twilio_sid" in out["error"]
    assert "phone.twilio_token" in out["error"]


def test_hangup_requires_call_id():
    out = phone_pack.hangup({})
    assert out == {"error": "call_id is required"}


# ---------------------------------------------------------------- phone.log
def test_phone_log_unknown_call():
    out = phone_pack.phone_log({"call_id": "CA-nope"})
    assert out == {"error": "no call logged with call_id 'CA-nope'"}


def test_phone_log_roundtrip():
    phone_pack._log_call("CA7", "+15550002222", "ringing")
    phone_pack._set_call_transcript(
        "CA7", [{"who": "caller", "text": "hi", "ts": "t"}])
    row = phone_pack.phone_log({"call_id": "CA7"})
    assert row["to"] == "+15550002222"
    assert row["transcript"] == [{"who": "caller", "text": "hi", "ts": "t"}]


# --------------------------------------- media stream events on the session
class _RecTTS:
    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)
        yield b"\x00" * 320  # 10ms of fake PCM16 16kHz


def _session(lines=("caller says hi",), script=("assistant replies",)):
    return PhoneCallSession("CA-test", list(script),
                            stt=QueuedSTT(list(lines)), tts=_RecTTS())


def _b64(n):
    return base64.b64encode(b"\xff" * n).decode("ascii")


def test_media_events_accumulate_then_turn():
    s = _session()
    assert s.handle_event({"event": "connected"})["ok"] is True
    st = s.handle_event({"event": "start",
                         "start": {"streamSid": "MZ1", "callSid": "CA-test"}})
    assert st["stream_sid"] == "MZ1"

    # two small media events: audio buffered, no turn yet
    m1 = s.handle_event({"event": "media", "media": {"payload": _b64(160)}})
    assert m1["ok"] is True and m1["buffered_ms"] == 20
    assert m1.get("reply_text") is None
    m2 = s.handle_event({"event": "media", "media": {"payload": _b64(160)}})
    assert m2["buffered_ms"] == 40

    # a mark forces the turn: STT -> scripted reply -> TTS mulaw bytes
    mk = s.handle_event({"event": "mark"})
    assert mk["caller_text"] == "caller says hi"
    assert mk["reply_text"] == "assistant replies"
    assert mk["audio_bytes"] > 0
    assert isinstance(mk["audio"], bytes)

    # stop finalizes with the transcript
    done = s.handle_event({"event": "stop"})
    assert done["ok"] is True
    assert [e["who"] for e in done["transcript"]] == ["caller", "assistant"]


def test_three_seconds_of_audio_auto_triggers_turn():
    s = _session()
    s.handle_event({"event": "connected"})
    s.handle_event({"event": "start", "start": {}})
    out = None
    for _ in range(TURN_BYTES // 8000):  # 3 events x 8000 bytes = 3s
        out = s.handle_event({"event": "media",
                              "media": {"payload": _b64(8000)}})
    assert out["reply_text"] == "assistant replies"
    assert out["caller_text"] == "caller says hi"


def test_turn_without_stt_is_honest_error():
    s = PhoneCallSession("CA-x", ["hi"], stt=None, tts=_RecTTS())
    s.handle_event({"event": "media", "media": {"payload": _b64(8000)}})
    out = s.turn()
    assert out["error"].startswith("stt_unavailable")
    assert "faster-whisper" in out["error"]


def test_turn_without_tts_is_honest_error():
    s = PhoneCallSession("CA-x", ["hi"], stt=QueuedSTT(["hello"]), tts=None)
    s.handle_event({"event": "media", "media": {"payload": _b64(8000)}})
    out = s.turn()
    assert out["error"].startswith("tts_unavailable")
    assert "piper" in out["error"]


def test_unknown_event_is_error():
    s = _session()
    out = s.handle_event({"event": "teleport"})
    assert "error" in out and "teleport" in out["error"]


def test_silence_produces_no_reply():
    s = PhoneCallSession("CA-x", ["hi"], stt=QueuedSTT([]), tts=_RecTTS())
    s.handle_event({"event": "media", "media": {"payload": _b64(8000)}})
    out = s.turn()
    assert out.get("silence") is True
    assert s.transcript == []


# ------------------------------------------------- mulaw round trips
def test_mulaw_roundtrip():
    for sample in (0, 1, -1, 1000, -1000, 12345, -12345, 32767, -32768):
        assert abs(mulaw_decode(mulaw_encode(sample)) - sample) < 700
    pcm = mulaw_bytes_to_pcm16(b"\xff\x00\x7f")
    assert len(pcm) == 6
    audio = pcm16_16k_chunks_to_mulaw_8k([b"\x00" * 320])
    assert len(audio) == 80  # 160 samples @16k -> 80 @8k


# ------------------------------------------------- registration contract
def test_register_and_risk_table():
    reg = Registry()
    phone_pack.register(reg)
    for name in ("phone.call_and_talk", "phone.hangup",
                 "phone.log", "phone.script_test"):
        assert name in reg.tools, name
    assert phone_pack.RISK_TABLE_ADDITIONS == {
        "phone.call_and_talk": ("high", True),
        "phone.hangup": ("medium", True),
        "phone.log": ("low", False),
        "phone.script_test": ("low", False),
    }
    tools = {t.name: t for t in reg.tools.values()}
    assert tools["phone.call_and_talk"].risk == "high"
    assert tools["phone.call_and_talk"].needs_network is True
    assert tools["phone.hangup"].needs_network is True
    assert tools["phone.log"].needs_network is False
    assert tools["phone.script_test"].needs_network is False
    # policy table merged so the tools are not default-denied
    from jarvis.agent.policy import RISK_TABLE, PolicyEngine
    for name in phone_pack.RISK_TABLE_ADDITIONS:
        assert RISK_TABLE[name] == phone_pack.RISK_TABLE_ADDITIONS[name]
    assert PolicyEngine().decide("phone.call_and_talk")["action"] == "confirm"
    assert PolicyEngine().decide("phone.script_test")["action"] == "allow"


def test_handlers_never_raise():
    # garbage in -> error dicts, never exceptions
    for fn in (phone_pack.call_and_talk, phone_pack.hangup,
               phone_pack.phone_log, phone_pack.script_test):
        out = fn(None)
        assert isinstance(out, dict) and "error" in out
    s = _session()
    assert "error" in s.handle_event(None)
    assert "error" in s.handle_event({"event": "media"})  # missing payload
    assert "error" in s.handle_event({"event": "media",
                                      "media": {"payload": "!!!"}})


def test_lazy_backends_report_precise_errors_when_absent(monkeypatch):
    # faster-whisper / piper are not installed in this env
    stt, stt_err = phone_pack.get_stt()
    assert stt is None
    assert stt_err["error"].startswith("stt_unavailable")
    tts, tts_err = phone_pack.get_tts()
    assert tts is None
    assert tts_err["error"].startswith("tts_unavailable")
