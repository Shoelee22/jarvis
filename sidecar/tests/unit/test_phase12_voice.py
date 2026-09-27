"""Unit tests for the Phase 12 Voice Conversation pack. All offline:

- All audio backends (_mic_stream, _wake_detected, _transcribe, _play_wav,
  _stop_playback, _list_devices) are monkeypatched; real microphone,
  openwakeword, faster-whisper and piper are never loaded.
- Missing-package paths are exercised by patching the *_available() probes.
"""
import sys
import struct

sys.path.insert(0, "sidecar")

import pytest  # noqa: E402

from jarvis.tools.base import Registry  # noqa: E402
from jarvis.tools.builtin import voice_pack  # noqa: E402


def _loud_frame() -> bytes:
    return struct.pack("<800h", *([2000] * 800))


def _quiet_frame() -> bytes:
    return struct.pack("<800h", *([0] * 800))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    voice_pack.reset_backends()
    yield
    voice_pack.reset_backends()


def _fake_mic(*frames):
    def _gen(seconds, sample_rate=16000):
        for f in frames:
            yield f
    return _gen


def _full_pipeline(monkeypatch, text="hello jarvis"):
    monkeypatch.setattr(voice_pack, "_openwakeword_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_whisper_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_sounddevice_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_mic_stream",
                        _fake_mic(_loud_frame(), *([_quiet_frame()] * 20)))
    monkeypatch.setattr(voice_pack, "_wake_detected", lambda frame, s=0.5: True)
    monkeypatch.setattr(voice_pack, "_transcribe", lambda frames: text)


# ---------------------------------------------------------------------------
# voice.listen

def test_listen_returns_transcript_through_pipeline(monkeypatch):
    _full_pipeline(monkeypatch)
    out = voice_pack.listen_handler({"timeout": 5})
    assert out == {"wake": True, "text": "hello jarvis"}


def test_listen_never_raises(monkeypatch):
    # every probe says "missing" and _mic_stream yields nothing
    monkeypatch.setattr(voice_pack, "_openwakeword_available", lambda: False)
    out = voice_pack.listen_handler({"timeout": 1})
    assert "error" in out
    assert "openwakeword" in out["error"]


def test_listen_missing_faster_whisper_names_package(monkeypatch):
    monkeypatch.setattr(voice_pack, "_openwakeword_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_whisper_available", lambda: False)
    monkeypatch.setattr(voice_pack, "_sounddevice_available", lambda: True)
    out = voice_pack.listen_handler({"timeout": 1})
    assert "error" in out
    assert "faster-whisper" in out["error"]
    assert "pip install faster-whisper" in out["error"]


def test_listen_missing_sounddevice_names_package(monkeypatch):
    monkeypatch.setattr(voice_pack, "_openwakeword_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_whisper_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_sounddevice_available", lambda: False)
    out = voice_pack.listen_handler({"timeout": 1})
    assert "error" in out
    assert "sounddevice" in out["error"]


def test_listen_no_wake_word_is_honest(monkeypatch):
    monkeypatch.setattr(voice_pack, "_openwakeword_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_whisper_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_sounddevice_available", lambda: True)
    monkeypatch.setattr(voice_pack, "_mic_stream",
                        _fake_mic(*([_quiet_frame()] * 5)))
    monkeypatch.setattr(voice_pack, "_wake_detected", lambda frame, s=0.5: False)
    out = voice_pack.listen_handler({"timeout": 2})
    assert out["wake"] is False
    assert "no wake word" in out["error"]


# ---------------------------------------------------------------------------
# voice.speak

def test_speak_missing_piper_names_setup(monkeypatch):
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: None)
    out = voice_pack.speak_handler({"text": "hello sir"})
    assert "error" in out
    assert "Piper TTS is not installed" in out["error"]
    assert "piper" in out["error"].lower()


def test_speak_missing_voice_model_names_path(monkeypatch):
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: "binary")
    monkeypatch.setattr(voice_pack, "VOICE_DIR",
                        voice_pack.Path("/nonexistent/piper-voices-xyz"))
    out = voice_pack.speak_handler({"text": "hello sir"})
    assert "error" in out
    assert "piper-voices-xyz" in out["error"]
    assert ".onnx" in out["error"]


def test_speak_plays_through_pipeline(monkeypatch):
    played = {}
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: "binary")
    monkeypatch.setattr(voice_pack, "_piper_synth",
                        lambda text, voice: b"RIFFfakewav")
    monkeypatch.setattr(voice_pack, "_play_wav",
                        lambda wav: played.setdefault("wav", wav))
    out = voice_pack.speak_handler({"text": "hello sir"})
    assert out["ok"] is True
    assert played["wav"] == b"RIFFfakewav"


def test_speak_empty_text_is_error():
    assert "error" in voice_pack.speak_handler({"text": "   "})


# ---------------------------------------------------------------------------
# voice.converse (with barge-in)

def test_converse_listen_then_advise(monkeypatch):
    _full_pipeline(monkeypatch, text="what time is it")
    out = voice_pack.converse_handler({"timeout": 5})
    assert out["heard"] == "what time is it"
    assert out["wake"] is True
    assert "voice.speak" in out["say_next"]


def test_converse_barge_in_stops_playback(monkeypatch):
    _full_pipeline(monkeypatch, text="stop talking")
    voice_pack._SPEAKING.set()  # a voice.speak is in flight
    stopped = {"n": 0}
    monkeypatch.setattr(voice_pack, "_stop_playback",
                        lambda: stopped.__setitem__("n", stopped["n"] + 1)
                        or voice_pack._SPEAKING.clear())
    out = voice_pack.converse_handler({"timeout": 5})
    assert out.get("barged_in") is True
    assert stopped["n"] == 1
    assert not voice_pack._SPEAKING.is_set()
    assert out["heard"] == "stop talking"


def test_converse_propagates_listen_error(monkeypatch):
    monkeypatch.setattr(voice_pack, "_openwakeword_available", lambda: False)
    out = voice_pack.converse_handler({"timeout": 1})
    assert "error" in out
    assert "openwakeword" in out["error"]


# ---------------------------------------------------------------------------
# voice.devices

def test_devices_no_sounddevice_graceful(monkeypatch):
    monkeypatch.setattr(voice_pack, "_sounddevice_available", lambda: False)
    out = voice_pack.devices_handler({})
    assert "error" in out
    assert "sounddevice" in out["error"]
    assert "pip install sounddevice" in out["error"]


def test_devices_lists_through_backend(monkeypatch):
    monkeypatch.setattr(voice_pack, "_list_devices", lambda: {
        "microphones": [{"name": "USB Mic", "index": 0}],
        "speakers": [{"name": "Speakers", "index": 1}],
    })
    out = voice_pack.devices_handler({})
    assert out["microphones"][0]["name"] == "USB Mic"
    assert out["speakers"][0]["name"] == "Speakers"


# ---------------------------------------------------------------------------
# wiring

def test_register_and_risk_table():
    reg = Registry()
    voice_pack.register(reg)
    assert set(voice_pack.RISK_TABLE_ADDITIONS) == {
        "voice.listen", "voice.speak", "voice.converse", "voice.devices"}
    for name, (risk, needs_network) in voice_pack.RISK_TABLE_ADDITIONS.items():
        tool = reg.tools[name]
        assert tool.risk == risk
        assert tool.needs_network is needs_network
        assert tool.handler is not None
    # RISK_TABLE merge works like the parent wiring expects
    risk_table: dict = {}
    risk_table.update(voice_pack.RISK_TABLE_ADDITIONS)
    for name in voice_pack.RISK_TABLE_ADDITIONS:
        assert risk_table[name] == voice_pack.RISK_TABLE_ADDITIONS[name]
    # mic tools are high risk, no network anywhere
    assert voice_pack.RISK_TABLE_ADDITIONS["voice.listen"][0] == "high"
    assert voice_pack.RISK_TABLE_ADDITIONS["voice.converse"][0] == "high"
    assert all(v[1] is False for v in voice_pack.RISK_TABLE_ADDITIONS.values())
