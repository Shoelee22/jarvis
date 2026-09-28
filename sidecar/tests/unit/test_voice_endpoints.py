"""Unit tests for the push-to-talk HTTP + WS endpoints (server.py).

Exercises the module-level handlers directly with fakes — no full app
build, no network, no audio hardware. fastapi is required for the
HTTPException shapes (installed on the dev VM / CI).
"""
import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from jarvis.ipc import server  # noqa: E402
from jarvis.tools.builtin import voice_pack  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_sessions():
    server._listen_sessions.clear()
    yield
    server._listen_sessions.clear()


class FakeWS:
    """Starlette WebSocket surface, scripted."""

    def __init__(self, session_id, script):
        self.query_params = {"session_id": session_id}
        self._script = list(script)
        self.sent = []
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if not self._script:
            raise RuntimeError("client disconnected")
        item = self._script.pop(0)
        if item == "disconnect":
            raise RuntimeError("client disconnected")
        return item

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000):
        self.closed = code


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- listen start/stop
def test_listen_start_stop_roundtrip():
    r = server.listen_start_handler()
    sid = r["session_id"]
    assert sid and sid in server._listen_sessions
    out = server.listen_stop_handler({"session_id": sid})
    assert out == {"ok": True}
    assert sid not in server._listen_sessions


def test_listen_stop_unknown_session_ok():
    assert server.listen_stop_handler({"session_id": "nope"}) == {"ok": True}
    assert server.listen_stop_handler({}) == {"ok": True}


# ------------------------------------------------------------------ audio WS
def test_audio_happy_path(monkeypatch):
    seen = {}

    def _fake_transcribe(frames):
        seen["n"] = len(frames)
        return "hello jarvis"

    monkeypatch.setattr(voice_pack, "_transcribe", _fake_transcribe)
    sid = server.listen_start_handler()["session_id"]
    ws = FakeWS(sid, [{"bytes": b"\x01\x02" * 800},
                      {"bytes": b"\x03\x04" * 800},
                      {"text": '{"event": "stop"}'}])
    _run(server.audio_ws_handler(ws))
    assert ws.accepted
    assert ws.sent == [{"event": "final", "text": "hello jarvis"}]
    assert seen["n"] == 2
    assert ws.closed == 1000
    assert sid not in server._listen_sessions, "session cleaned up"


def test_audio_unknown_session_rejected(monkeypatch):
    called = []
    monkeypatch.setattr(voice_pack, "_transcribe",
                        lambda frames: called.append(1) or "x")
    ws = FakeWS("nope", [{"bytes": b"\x00" * 100}])
    _run(server.audio_ws_handler(ws))
    assert ws.closed == 4401
    assert not called, "no transcription for unknown session"
    assert ws.sent == []


def test_audio_disconnect_still_finalizes(monkeypatch):
    monkeypatch.setattr(voice_pack, "_transcribe", lambda frames: "partial ok")
    sid = server.listen_start_handler()["session_id"]
    ws = FakeWS(sid, [{"bytes": b"\x00" * 1600}, "disconnect"])
    _run(server.audio_ws_handler(ws))
    assert ws.sent == [{"event": "final", "text": "partial ok"}]
    assert sid not in server._listen_sessions


def test_audio_transcribe_error_honest(monkeypatch):
    def _boom(frames):
        raise RuntimeError("stt exploded")
    monkeypatch.setattr(voice_pack, "_transcribe", _boom)
    sid = server.listen_start_handler()["session_id"]
    ws = FakeWS(sid, [{"text": '{"event": "stop"}'}])
    _run(server.audio_ws_handler(ws))
    assert ws.sent[0]["event"] == "final"
    assert ws.sent[0]["text"] == ""
    assert "stt exploded" in ws.sent[0]["error"]


def test_audio_frame_cap(monkeypatch):
    seen = {}

    def _fake_transcribe(frames):
        seen["n"] = len(frames)
        return ""

    monkeypatch.setattr(voice_pack, "_transcribe", _fake_transcribe)
    sid = server.listen_start_handler()["session_id"]
    ws = FakeWS(sid, [{"bytes": b"\x00" * 100}] * 3700
                + [{"text": '{"event": "stop"}'}])
    _run(server.audio_ws_handler(ws))
    assert seen["n"] == server._MAX_FRAMES


# ------------------------------------------------------------------ voice TTS
def test_voice_tts_roundtrip(monkeypatch):
    monkeypatch.setattr(voice_pack, "_piper_synth",
                        lambda text, voice: b"RIFFfake")
    out = server.voice_tts_handler({"text": "hello sir"})
    assert out["bytes"] == 8
    assert base64.b64decode(out["audio_b64"]) == b"RIFFfake"


def test_voice_tts_empty_text_400():
    with pytest.raises(HTTPException) as ei:
        server.voice_tts_handler({"text": "   "})
    assert ei.value.status_code == 400


def test_voice_tts_synth_failure_500(monkeypatch):
    monkeypatch.setattr(voice_pack, "_piper_synth",
                        lambda text, voice: {"error": "no piper"})
    with pytest.raises(HTTPException) as ei:
        server.voice_tts_handler({"text": "hi"})
    assert ei.value.status_code == 500
    assert "no piper" in ei.value.detail
