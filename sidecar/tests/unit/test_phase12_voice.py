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
    fake = voice_pack.Path("/nonexistent/piper-voices-xyz")
    # Patch the whole search list: the real DATA_DIR copy may exist on
    # this machine, which would (correctly) satisfy the lookup.
    monkeypatch.setattr(voice_pack, "_voice_dirs", lambda: [fake])
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


def test_speak_reports_when_nothing_played(monkeypatch):
    # Headless machine: synthesis succeeds but no audio backend -> the
    # result must say so instead of implying the user heard it.
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: "module")
    monkeypatch.setattr(voice_pack, "_piper_synth",
                        lambda text, voice: b"RIFFfakewav")
    monkeypatch.setattr(voice_pack, "_play_wav", lambda wav: False)
    out = voice_pack.speak_handler({"text": "hello sir"})
    assert out["ok"] is True
    assert out["played"] is False
    assert "not played" in out["note"]


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
        "voice.listen", "voice.speak", "voice.converse", "voice.devices",
        "voice.setup"}
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
    # mic tools are high risk; only voice.setup touches the network
    # (it downloads models and pip-installs backends)
    assert voice_pack.RISK_TABLE_ADDITIONS["voice.listen"][0] == "high"
    assert voice_pack.RISK_TABLE_ADDITIONS["voice.converse"][0] == "high"
    assert voice_pack.RISK_TABLE_ADDITIONS["voice.setup"][1] is True
    assert all(v[1] is False
               for n, v in voice_pack.RISK_TABLE_ADDITIONS.items()
               if n != "voice.setup")


# ---------------------------------------------------------------------------
# voice.setup

def test_setup_probe_mode_never_installs(monkeypatch):
    """install=False + download=False: pure probe, no side effects."""
    monkeypatch.setattr(voice_pack, "_has_package", lambda name: False)
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: None)
    monkeypatch.setattr(voice_pack, "_find_voice", lambda stem: None)
    out = voice_pack.setup_handler({"install": False, "download": False})
    assert out["voice_ready"] is False
    assert out["tts_ready"] is False
    assert out["listen_ready"] is False
    assert all(b["present"] is False for b in out["backends"].values())
    assert out["models"]["skipped"] is True
    assert out["self_test"]["ok"] is False


def test_setup_all_present_self_test_ok(monkeypatch):
    """Everything installed + voice found: self-test runs and passes."""
    monkeypatch.setattr(voice_pack, "_has_package", lambda name: True)
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: "module")
    monkeypatch.setattr(voice_pack, "_find_voice",
                        lambda stem: __import__("pathlib").Path("/v.onnx"))

    def fake_wav_self_test():
        return {"ok": True, "seconds": 2.1, "bytes": 50004}
    monkeypatch.setattr(voice_pack, "_wav_self_test", fake_wav_self_test)
    out = voice_pack.setup_handler({"install": False, "download": False})
    assert out["voice_ready"] is True
    assert out["tts_ready"] is True
    assert out["listen_ready"] is True
    assert out["self_test"]["ok"] is True


def test_setup_frozen_reports_guidance(monkeypatch):
    """Frozen exe with no usable Python: no pip attempt, honest guidance."""
    import sys as _sys
    monkeypatch.setattr(voice_pack, "_has_package", lambda name: False)
    monkeypatch.setattr(voice_pack, "_vp_has_package", lambda name: False)
    monkeypatch.setattr(voice_pack, "_voice_python", lambda: None)
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: None)
    monkeypatch.setattr(voice_pack, "_find_voice", lambda stem: None)
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    out = voice_pack.setup_handler({"install": True, "download": False})
    assert out["frozen"] is True
    assert "guidance" in out["backends"]["piper-tts"]
    assert "installed" not in out["backends"]["piper-tts"]
    assert out["push_to_talk_ready"] is False


def test_setup_frozen_uses_voice_python(monkeypatch):
    """Frozen exe WITH a usable Python: installs into it via the bridge."""
    import sys as _sys
    calls = []
    monkeypatch.setattr(voice_pack, "_has_package", lambda name: False)
    monkeypatch.setattr(voice_pack, "_vp_has_package", lambda name: False)
    monkeypatch.setattr(voice_pack, "_voice_python",
                        lambda: ["C:\\Python311\\python.exe"])
    monkeypatch.setattr(voice_pack, "_pip_install",
                        lambda pkg: calls.append(pkg) or {"installed": True})
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: "module")
    monkeypatch.setattr(voice_pack, "_find_voice", lambda stem: None)
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    out = voice_pack.setup_handler({"install": True, "download": False})
    assert out["frozen"] is True
    assert calls, "expected pip installs into the voice python"
    assert "installed" in out["backends"]["piper-tts"]
    assert "guidance" not in out["backends"]["piper-tts"]


def test_wav_self_test_rejects_silence(monkeypatch):
    """Self-test fails honestly when synthesis returns an error dict."""
    monkeypatch.setattr(voice_pack, "_piper_synth",
                        lambda text, voice: {"error": "no piper"})
    out = voice_pack._wav_self_test()
    assert out["ok"] is False
    assert "error" in out


# ------------------------------------------------------- voice-bridge tests
@pytest.fixture()
def _fresh_bridge(monkeypatch):
    """Isolate bridge discovery from the real machine + warm caches."""
    voice_pack.reset_voice_python_cache()
    yield
    voice_pack.reset_voice_python_cache()


def test_voice_python_env_override(_fresh_bridge, monkeypatch):
    """JARVIS_VOICE_PYTHON is honored (Windows 'py -3.11' style)."""
    monkeypatch.setenv("JARVIS_VOICE_PYTHON", "py -3.11")
    monkeypatch.setattr(voice_pack, "_is_real_python", lambda argv: True)
    assert voice_pack._voice_python() == ["py", "-3.11"]


def test_voice_python_none_without_python(_fresh_bridge, monkeypatch):
    """No candidates at all -> None (honest 'no python' path)."""
    monkeypatch.setattr(voice_pack, "_voice_candidates", lambda: [])
    assert voice_pack._voice_python() is None


def test_pip_install_no_python_honest(_fresh_bridge, monkeypatch):
    """No voice python: no subprocess, honest install guidance."""
    from types import SimpleNamespace

    def _no_shell(*a, **k):
        pytest.fail("must not shell out with no python")

    monkeypatch.setattr(voice_pack, "_voice_python", lambda: None)
    monkeypatch.setattr(voice_pack, "subprocess",
                        SimpleNamespace(run=_no_shell))
    out = voice_pack._pip_install("piper-tts")
    assert out["installed"] is False
    assert "python.org" in out["error"]


def test_transcribe_bridge_argv(_fresh_bridge, monkeypatch):
    """Frozen sidecar: STT delegated out-of-process with a valid wav."""
    from types import SimpleNamespace
    seen = {}

    class _Done:
        returncode = 0
        stdout = "hello world"
        stderr = ""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        idx = argv.index("-c")
        wav_path = argv[idx + 2]
        with open(wav_path, "rb") as f:
            assert f.read(4) == b"RIFF", "bridge must write a real wav"
        return _Done()

    import subprocess as _sp
    monkeypatch.setattr(voice_pack, "subprocess",
                        SimpleNamespace(run=fake_run, PIPE=_sp.PIPE,
                                        DEVNULL=_sp.DEVNULL,
                                        TimeoutExpired=_sp.TimeoutExpired))
    monkeypatch.setattr(voice_pack, "_whisper_model", lambda: None)
    monkeypatch.setattr(voice_pack, "_voice_python",
                        lambda: [sys.executable])
    monkeypatch.setattr(voice_pack, "_vp_has_package",
                        lambda name: name == "faster_whisper")
    out = voice_pack._transcribe([b"\x00" * 3200])
    assert out == "hello world"
    assert seen["argv"][0] == sys.executable
    assert "-c" in seen["argv"]


def test_transcribe_no_backends_empty(_fresh_bridge, monkeypatch):
    """No in-process model and no voice python -> '' (never raises)."""
    monkeypatch.setattr(voice_pack, "_whisper_model", lambda: None)
    monkeypatch.setattr(voice_pack, "_voice_python", lambda: None)
    assert voice_pack._transcribe([b"\x00" * 3200]) == ""


def test_piper_synth_uses_voice_python(_fresh_bridge, monkeypatch, tmp_path):
    """Piper synthesis runs under the voice python, not the frozen exe."""
    from types import SimpleNamespace
    import subprocess as _sp
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        idx = argv.index("-c")
        out_path = argv[idx + 2]
        with open(out_path, "wb") as f:
            f.write(b"RIFFfakWAVEdata")
        class _Done:
            returncode = 0
            stdout = b""
            stderr = b""
        return _Done()

    monkeypatch.setattr(voice_pack, "subprocess",
                        SimpleNamespace(run=fake_run, PIPE=_sp.PIPE,
                                        DEVNULL=_sp.DEVNULL,
                                        TimeoutExpired=_sp.TimeoutExpired))
    onnx = tmp_path / "voice.onnx"
    onnx.write_bytes(b"fake")
    monkeypatch.setattr(voice_pack, "_find_voice", lambda stem: onnx)
    monkeypatch.setattr(voice_pack, "_piper_kind", lambda: "module")
    monkeypatch.setattr(voice_pack, "_voice_python",
                        lambda: ["C:\\Python311\\python.exe"])
    out = voice_pack._piper_synth("hello", None)
    assert out == b"RIFFfakWAVEdata"
    assert seen["argv"][0] == "C:\\Python311\\python.exe"
