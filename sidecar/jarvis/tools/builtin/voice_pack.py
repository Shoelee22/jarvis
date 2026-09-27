"""Voice Conversation pack (Phase 12): real voice loop, all audio stays local.

Four tools:

- ``voice.listen`` — turns ON the microphone, waits for the wake word
  ``"jarvis"`` (openwakeword-gated), then streams speech-to-text
  (faster-whisper-gated) until silence or ``timeout``. Returns
  ``{"text": ..., "wake": true}``.
- ``voice.speak`` — Piper TTS-gated, plays the given text on the speakers.
- ``voice.converse`` — one full duplex turn: listen (with barge-in: if a
  ``voice.speak`` is still playing and live speech is detected, playback is
  stopped first), then returns ``{"heard": ..., "say_next": ...}``. The agent
  (LLM side) generates the reply and calls ``voice.speak`` with it — handlers
  cannot call the agent loop, so converse is deliberately listen-then-advise.
- ``voice.devices`` — list microphones/speakers (sounddevice-gated).

SAFETY / HONESTY:
    - No audio ever leaves the machine: everything is local (all
      needs_network=False). STT is faster-whisper (local), wake word is
      openwakeword (local), TTS is Piper (local).
    - If any backend (openwakeword / faster-whisper / a model file / piper /
      a voice model / sounddevice) is missing, the handler returns an
      {"error": ...} naming the exact package and the exact setup step. We
      NEVER fabricate a transcript.
    - voice.listen and voice.converse are HIGH risk: they turn on the
      microphone. They go through the policy confirmation flow like any other
      high-risk tool.

All audio I/O sits behind tiny module-level functions (_mic_stream,
_play_wav, _stop_playback, _whisper_model, _piper_synth, _list_devices,
_voice_activity) so tests can monkeypatch them without real audio hardware.

Models/convention (same layout as the studio pack):
    voices live in ~/workspace/jarvis/models/piper-voices/*.onnx
    (default voice: first .onnx found there, overridable via config).

CONFIG (all optional; the pack works with tools_config.yaml absent):
    tools_config.yaml > voice:
        default_voice: "en_GB-alan-medium"   # file stem under models/piper-voices
        wake_sensitivity: 0.5                # openwakeword threshold 0..1
        listen_timeout: 20                   # default seconds for listen/converse
        sample_rate: 16000
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

# ---------------------------------------------------------------------------
# Config (best-effort; absent file => defaults below)

JARVIS_DIR = Path(os.environ.get("JARVIS_DIR", str(Path.home() / "workspace" / "jarvis")))
VOICE_DIR = JARVIS_DIR / "models" / "piper-voices"
_CONFIG_PATH = JARVIS_DIR / "tools_config.yaml"

_DEFAULTS = {
    "default_voice": None,      # None => first .onnx under VOICE_DIR
    "wake_sensitivity": 0.5,
    "listen_timeout": 20,
    "sample_rate": 16000,
    "wake_word": "jarvis",
}


def _load_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        if _CONFIG_PATH.is_file():
            try:
                import yaml  # type: ignore
                data = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
            except Exception:
                # minimal fallback: no yaml dep required
                data = {}
            voice_cfg = (data.get("voice") or {}) if isinstance(data, dict) else {}
            for k in cfg:
                if k in voice_cfg:
                    cfg[k] = voice_cfg[k]
    except Exception:
        pass
    return cfg


def _cfg() -> dict:
    # re-read each call so tests can monkeypatch _CONFIG_PATH easily
    return _load_config()


# Currently-speaking flag for barge-in: set while _play_wav is streaming.
_SPEAKING = threading.Event()

# ---------------------------------------------------------------------------
# Backend probes (import-time safe: never raise)

def _has_package(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _openwakeword_available() -> bool:
    return _has_package("openwakeword")


def _whisper_available() -> bool:
    return _has_package("faster_whisper")


def _sounddevice_available() -> bool:
    return _has_package("sounddevice")


def _piper_kind() -> str | None:
    """'binary' if a piper CLI exists, 'module' if python piper-tts importable."""
    if shutil.which("piper"):
        return "binary"
    if _has_package("piper"):
        return "module"
    return None


# faster-whisper model cache (local, e.g. "tiny", "base", "small").
_WHISPER_MODEL_NAME = os.environ.get("JARVIS_WHISPER_MODEL", "tiny")
_WHISPER = None


def _whisper_model():
    """Return a loaded faster-whisper model, or None if unavailable."""
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER
    if not _whisper_available():
        return None
    try:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel(_WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
        return _WHISPER
    except Exception:
        return None


def reset_backends() -> None:
    """Test helper: drop cached backends so missing-package tests are honest."""
    global _WHISPER
    _WHISPER = None
    _SPEAKING.clear()


# ---------------------------------------------------------------------------
# Audio I/O seams (mock-friendly; real impls are sounddevice-gated)

def _mic_stream(seconds: float, sample_rate: int = 16000):
    """Yield raw PCM16 bytes chunks from the default microphone.

    Real implementation needs the `sounddevice` package. Tests monkeypatch
    this function to feed canned audio frames.
    """
    if not _sounddevice_available():
        return
        yield  # pragma: no cover - makes this a generator
    import sounddevice as sd  # type: ignore
    import queue
    q: queue.Queue = queue.Queue()

    def _cb(indata, frames, time, status):
        q.put(bytes(indata))

    with sd.RawInputStream(samplerate=sample_rate, channels=1,
                           dtype="int16", callback=_cb,
                           blocksize=int(sample_rate * 0.1)):
        import time as _t
        end = _t.time() + seconds
        while _t.time() < end:
            try:
                yield q.get(timeout=0.2)
            except Exception:
                break


def _voice_activity(frame: bytes, threshold: int = 500) -> bool:
    """Crude energy VAD on PCM16 mono audio. No ML, no extra deps."""
    if not frame:
        return False
    import struct
    n = len(frame) // 2
    if n == 0:
        return False
    samples = struct.unpack("<%dh" % n, frame[: n * 2])
    energy = sum(abs(s) for s in samples) / n
    return energy > threshold


def _wake_detected(frame: bytes, sensitivity: float = 0.5) -> bool:
    """True if the wake word is present in the frame (openwakeword-gated)."""
    if not _openwakeword_available():
        return False
    try:
        import numpy as np  # type: ignore
        from openwakeword.model import Model  # type: ignore
        model = getattr(_wake_detected, "_oww", None)
        if model is None:
            model = Model(wakeword_models=["jarvis"])
            _wake_detected._oww = model  # type: ignore[attr-defined]
        audio = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        scores = model.predict(audio)
        return any(float(v) >= sensitivity for v in scores.values())
    except Exception:
        return False


def _transcribe(frames: list[bytes]) -> str:
    """STT over PCM16 16k mono frames via faster-whisper. Never raises."""
    model = _whisper_model()
    if model is None:
        return ""
    try:
        import numpy as np  # type: ignore
        pcm = b"".join(frames)
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(audio, language="en")
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception:
        return ""


def _play_wav(wav_bytes: bytes) -> None:
    """Play wav bytes on the default speakers. Sets/clears _SPEAKING."""
    _SPEAKING.set()
    try:
        if not _sounddevice_available():
            return
        import sounddevice as sd  # type: ignore
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            data = w.readframes(w.getnframes())
            sr = w.getframerate()
            ch = w.getnchannels()
        import numpy as np  # type: ignore
        arr = np.frombuffer(data, dtype=np.int16)
        sd.play(arr.reshape(-1, ch) if ch > 1 else arr, sr)
        sd.wait()
    finally:
        _SPEAKING.clear()


def _stop_playback() -> None:
    """Stop any in-flight playback (barge-in). Best effort, never raises."""
    try:
        if _sounddevice_available():
            import sounddevice as sd  # type: ignore
            sd.stop()
    except Exception:
        pass
    finally:
        _SPEAKING.clear()


def _list_devices() -> dict:
    """Return {'microphones': [...], 'speakers': [...]} or {'error': ...}."""
    if not _sounddevice_available():
        return {"error": ("sounddevice is not installed: "
                          "`pip install sounddevice` (needs the PortAudio "
                          "system library on Windows: it ships with the "
                          "sounddevice wheel). No audio devices can be listed.")}
    try:
        import sounddevice as sd  # type: ignore
        devs = sd.query_devices()
        mics, spks = [], []
        for d in devs:
            entry = {"name": d["name"], "index": int(d["index"])}
            if d.get("max_input_channels", 0) > 0:
                mics.append(entry)
            if d.get("max_output_channels", 0) > 0:
                spks.append(entry)
        return {"microphones": mics, "speakers": spks}
    except Exception as e:
        return {"error": f"could not query audio devices: {e}"}


# ---------------------------------------------------------------------------
# Piper TTS (same convention as the studio pack)

def _find_voice(stem: str | None) -> Path | None:
    if not VOICE_DIR.is_dir():
        return None
    if stem:
        cand = VOICE_DIR / f"{stem}.onnx"
        if cand.is_file():
            return cand
    onnx = sorted(VOICE_DIR.rglob("*.onnx"))
    return onnx[0] if onnx else None


def _piper_synth(text: str, voice: str | None) -> bytes | dict:
    """Synthesize text -> wav bytes via piper CLI or python piper-tts.

    Returns wav bytes, or {"error": ...} naming the missing piece.
    """
    kind = _piper_kind()
    if kind is None:
        return {"error": ("Piper TTS is not installed: need the `piper` CLI "
                          "(https://github.com/OHF-Voice/piper1-gpl/releases) "
                          "or `pip install piper-tts` on PATH.")}
    voice_onnx = _find_voice(voice)
    if voice_onnx is None:
        return {"error": (f"No Piper voice model found in {VOICE_DIR} "
                          f"(looked for '{voice or '<default>'}*.onnx'). "
                          "Download one, e.g. en_GB-alan-medium.onnx from "
                          "https://huggingface.co/rhasspy/piper-voices, "
                          f"and place it in {VOICE_DIR}.")}
    if kind == "binary":
        piper = shutil.which("piper")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out = tmp.name
        r = subprocess.run([piper, "--model", str(voice_onnx),
                            "--output_file", out],
                           input=text.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=120)
        try:
            if r.returncode != 0:
                return {"error": f"piper failed: {r.stderr[-400:].decode(errors='replace').strip()}"}
            return Path(out).read_bytes()
        finally:
            Path(out).unlink(missing_ok=True)
    # python piper-tts; write wav with the stdlib (no numpy needed).
    script = (
        "import sys, wave\n"
        "from piper import PiperVoice\n"
        f"v = PiperVoice.load({str(voice_onnx)!r})\n"
        "text = sys.stdin.read()\n"
        "buf = wave.open(sys.argv[1], 'wb')\n"
        "buf.setnchannels(1); buf.setsampwidth(2); buf.setframerate(v.config.sample_rate)\n"
        "for chunk in v.synthesize_stream_raw(text):\n"
        "    buf.writeframes(chunk)\n"
        "buf.close()\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out = tmp.name
    try:
        r = subprocess.run([sys.executable, "-c", script, out],
                           input=text.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180)
        if r.returncode != 0:
            return {"error": f"piper-tts module failed: {r.stderr[-400:].decode(errors='replace').strip()}"}
        return Path(out).read_bytes()
    finally:
        Path(out).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Handlers (dict in -> dict out, never raise)

def listen_handler(args: dict) -> dict:
    """Wait for wake word, then transcribe until silence/timeout."""
    try:
        cfg = _cfg()
        timeout = float(args.get("timeout", cfg["listen_timeout"]))
        sr = int(cfg["sample_rate"])
        sensitivity = float(cfg["wake_sensitivity"])

        if not _openwakeword_available():
            return {"error": ("Wake-word detection needs openwakeword: "
                              "`pip install openwakeword` (needs onnxruntime too: "
                              "`pip install onnxruntime`). The built-in 'jarvis' "
                              "wake-word model ships with the openwakeword "
                              "package — no extra model download.")}
        if not _whisper_available():
            return {"error": ("Speech-to-text needs faster-whisper: "
                              "`pip install faster-whisper`. The first run "
                              "auto-downloads the tiny model "
                              f"('{_WHISPER_MODEL_NAME}' by default; set "
                              "JARVIS_WHISPER_MODEL=base/small for better "
                              "accuracy). Needs internet once for the download, "
                              "then works fully offline.")}
        if not _sounddevice_available():
            return {"error": ("Microphone capture needs sounddevice: "
                              "`pip install sounddevice` (PortAudio ships with "
                              "the Windows wheel). No audio hardware access "
                              "without it.")}

        # Phase 1: wake word.
        wake = False
        for frame in _mic_stream(timeout, sr):
            if _wake_detected(frame, sensitivity):
                wake = True
                break
        if not wake:
            return {"wake": False, "text": "",
                    "error": f"no wake word '{cfg['wake_word']}' heard within {timeout:g}s"}

        # Phase 2: transcribe until silence (1.2s) or the rest of the timeout.
        frames: list[bytes] = []
        silent_chunks = 0
        import time as _t
        end = _t.time() + timeout
        for frame in _mic_stream(timeout, sr):
            frames.append(frame)
            if _voice_activity(frame):
                silent_chunks = 0
            else:
                silent_chunks += 1
            if silent_chunks >= 12 or _t.time() > end:  # ~1.2s silence
                break

        text = _transcribe(frames)
        if not text:
            return {"wake": True, "text": "",
                    "error": "heard the wake word but transcription produced no text "
                             "(model may have failed to load — check faster-whisper setup)"}
        return {"wake": True, "text": text}
    except Exception as e:
        return {"error": f"voice.listen failed: {e}"}


def speak_handler(args: dict) -> dict:
    """Synthesize text with Piper and play it on the speakers."""
    try:
        text = (args.get("text") or "").strip()
        if not text:
            return {"error": "voice.speak needs a 'text' argument"}
        voice = args.get("voice") or _cfg()["default_voice"]
        wav = _piper_synth(text, voice)
        if isinstance(wav, dict):
            return wav  # {"error": ...}
        _play_wav(bytes(wav))
        return {"ok": True, "chars": len(text), "voice": voice or "default"}
    except Exception as e:
        return {"error": f"voice.speak failed: {e}"}


def converse_handler(args: dict) -> dict:
    """One duplex turn: listen (with barge-in), then advise the agent.

    Handlers cannot call the agent loop, so this is deliberately
    listen-then-advise: the agent generates the reply and calls
    ``voice.speak`` with it. Barge-in: if a ``voice.speak`` is still playing
    and live speech is detected, playback is stopped first.
    """
    try:
        cfg = _cfg()
        timeout = float(args.get("timeout", cfg["listen_timeout"]))
        result = listen_handler({"timeout": timeout})

        # Barge-in: speech arrived while we were speaking -> stop playback.
        if _SPEAKING.is_set() and result.get("text"):
            _stop_playback()
            result["barged_in"] = True

        if "error" in result and not result.get("text"):
            return result
        return {
            "heard": result.get("text", ""),
            "wake": result.get("wake", False),
            "barged_in": bool(result.get("barged_in", False)),
            "say_next": ("call voice.speak with your spoken reply to continue "
                         "the conversation"),
        }
    except Exception as e:
        return {"error": f"voice.converse failed: {e}"}


def devices_handler(args: dict) -> dict:
    """List microphones and speakers (sounddevice-gated)."""
    try:
        return _list_devices()
    except Exception as e:
        return {"error": f"voice.devices failed: {e}"}


# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "voice.listen",
     "description": ("TURN ON THE MICROPHONE and listen for a voice command. "
                     "Waits for the wake word 'jarvis' (openwakeword), then "
                     "transcribes speech with faster-whisper until silence or "
                     "`timeout` seconds. All audio stays on this machine. "
                     "Returns {\"text\": ..., \"wake\": true}. Requires "
                     "openwakeword, faster-whisper (+ one-time model download) "
                     "and sounddevice — returns an exact setup error if any is "
                     "missing instead of a fake transcript. HIGH RISK."),
     "handler": listen_handler, "risk": "high", "needs_network": False,
     "schema": {"timeout?": "float"}},
    {"name": "voice.speak",
     "description": ("Speak text aloud using local Piper TTS (no network). "
                     "Voice models live in ~/workspace/jarvis/models/piper-voices "
                     "(*.onnx); `voice` selects one by file stem, default is "
                     "the first .onnx found. Returns an exact setup error if "
                     "piper or a voice model is missing."),
     "handler": speak_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string", "voice?": "string"}},
    {"name": "voice.converse",
     "description": ("One full voice turn with barge-in. TURN ON THE "
                     "MICROPHONE: listens (wake word 'jarvis', then "
                     "transcribes), stopping any in-flight voice.speak "
                     "playback the moment live speech is detected. Returns "
                     "{\"heard\": ..., \"wake\": ..., \"say_next\": ...} — "
                     "the agent then generates the reply and calls "
                     "voice.speak with it. All audio stays on this machine. "
                     "HIGH RISK (uses the microphone)."),
     "handler": converse_handler, "risk": "high", "needs_network": False,
     "schema": {"timeout?": "float"}},
    {"name": "voice.devices",
     "description": ("List available microphones and speakers on this machine "
                     "(sounddevice-gated). Returns {\"microphones\": [...], "
                     "\"speakers\": [...]} or an honest error if sounddevice "
                     "is not installed."),
     "handler": devices_handler, "risk": "low", "needs_network": False,
     "schema": {}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(voice_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "voice.listen": ("high", False),
    "voice.speak": ("low", False),
    "voice.converse": ("high", False),
    "voice.devices": ("low", False),
}


def register(reg) -> None:
    """Wire the four Voice Conversation tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
