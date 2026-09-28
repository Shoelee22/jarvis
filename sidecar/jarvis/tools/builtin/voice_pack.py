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
import shlex
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
    return _has_package("openwakeword") or _vp_has_package("openwakeword")


def _whisper_available() -> bool:
    return _has_package("faster_whisper") or _vp_has_package("faster_whisper")


def _sounddevice_available() -> bool:
    # NOTE: intentionally in-process only. Microphone capture and speaker
    # playback run inside this interpreter; the desktop UI captures the mic
    # in the browser, so push-to-talk does not need it here.
    return _has_package("sounddevice")


def _piper_kind() -> str | None:
    """'binary' if a piper CLI exists, 'module' if python piper-tts importable
    (in-process or via the voice-bridge python)."""
    if shutil.which("piper"):
        return "binary"
    if _has_package("piper") or _vp_has_package("piper"):
        return "module"
    return None


# ---------------------------------------------------------------------------
# Voice-bridge: delegate ML voice work to a real Python.
#
# The frozen (PyInstaller) sidecar cannot import faster-whisper / piper-tts /
# openwakeword in-process — they are excluded from the bundle on purpose to
# keep the installer small. Work that can run out-of-process (Piper
# synthesis, faster-whisper transcription, pip installs) is delegated to a
# "voice python":
#   1. $JARVIS_VOICE_PYTHON (a command line, e.g. "py -3.11" or a full path),
#   2. the current interpreter, when it is not frozen (dev / source runs —
#      this keeps every existing test hermetic),
#   3. auto-discovered system Pythons (Windows: py -3.13/-3.12/-3.11, python).
# The frozen exe itself is NEVER used: it has no pip and no ML packages
# (probing it would just launch another sidecar server).
# Microphone capture, speaker playback and wake-word detection stay
# in-process-only.

_VOICE_PKGS = ("piper", "faster_whisper", "sounddevice", "openwakeword")
_voice_python_argv: list[str] | None = None
_voice_python_probed = False
_vp_pkg_cache: dict[str, bool] = {}


def _is_real_python(argv: list[str]) -> bool:
    """True when argv runs a genuine Python 3.9+ (rejects the Windows Store
    stub and the frozen sidecar exe). Never raises."""
    try:
        r = subprocess.run(
            [*argv, "-c",
             "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _voice_candidates() -> list[list[str]]:
    cands: list[list[str]] = []
    env = os.environ.get("JARVIS_VOICE_PYTHON", "").strip()
    if env:
        try:
            cands.append(shlex.split(env, posix=os.name != "nt"))
        except Exception:
            pass
    if not getattr(sys, "frozen", False):
        cands.append([sys.executable])
    if os.name == "nt":
        cands.extend([["py", "-3.13"], ["py", "-3.12"], ["py", "-3.11"],
                      ["py", "-3"], ["python"]])
    else:
        cands.extend([["python3"], ["python"]])
    return cands


def _voice_python() -> list[str] | None:
    """argv of a real Python for voice ML work, or None when there is none.
    Cached after the first call (use reset_voice_python_cache in tests)."""
    global _voice_python_argv, _voice_python_probed
    if _voice_python_probed:
        return _voice_python_argv
    _voice_python_probed = True
    for cand in _voice_candidates():
        if _is_real_python(cand):
            _voice_python_argv = cand
            break
    return _voice_python_argv


def reset_voice_python_cache() -> None:
    """Test helper: forget the discovered voice python and package probes."""
    global _voice_python_argv, _voice_python_probed
    _voice_python_argv, _voice_python_probed = None, False
    _vp_pkg_cache.clear()


def _vp_has_package(name: str) -> bool:
    """Can the voice-bridge python import `name`? In-process fast path when
    the voice python IS this interpreter (dev/source runs). Cached."""
    vp = _voice_python()
    if vp is None:
        return False
    if name in _vp_pkg_cache:
        return _vp_pkg_cache[name]
    if vp == [sys.executable]:
        ok = _has_package(name)
    else:
        try:
            r = subprocess.run([*vp, "-c", f"import {name}"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60)
            ok = r.returncode == 0
        except Exception:
            ok = False
    _vp_pkg_cache[name] = ok
    return ok


# faster-whisper model cache (local, e.g. "tiny", "base", "small").
_WHISPER_MODEL_NAME = os.environ.get("JARVIS_WHISPER_MODEL", "tiny")
_WHISPER = None


def _whisper_model_ref() -> str:
    """faster-whisper model reference: a `make models`-downloaded local copy
    under DATA_DIR/models/ when present (works fully offline), otherwise the
    configured size name (faster-whisper auto-downloads it once)."""
    model_ref = _WHISPER_MODEL_NAME
    try:
        from jarvis.config import DATA_DIR
        local = DATA_DIR / "models" / f"faster-whisper-{_WHISPER_MODEL_NAME}"
        if (local / "model.bin").is_file():
            model_ref = str(local)
    except Exception:
        pass
    return model_ref


def _whisper_model():
    """Return a loaded faster-whisper model, or None if unavailable.

    In-process only (dev / source runs). The frozen sidecar transcribes via
    the voice-bridge instead (see _transcribe_via_bridge).
    """
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER
    if not _has_package("faster_whisper"):
        return None
    try:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel(_whisper_model_ref(),
                               device="cpu", compute_type="int8")
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
    """STT over PCM16 16k mono frames via faster-whisper. Never raises.

    In-process fast path when faster-whisper is importable here (dev /
    source runs); otherwise delegates to the voice-bridge python (frozen
    sidecar) which transcribes a temp wav and prints the text."""
    model = _whisper_model()
    if model is not None:
        try:
            import numpy as np  # type: ignore
            pcm = b"".join(frames)
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _ = model.transcribe(audio, language="en")
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            return ""
    vp = _voice_python()
    if vp is not None and _vp_has_package("faster_whisper"):
        return _transcribe_via_bridge(b"".join(frames), vp)
    return ""


def _transcribe_via_bridge(pcm: bytes, vp: list[str]) -> str:
    """Transcribe raw PCM16 mono 16k audio using the voice-bridge python.

    Writes a temp wav, runs faster-whisper out-of-process, returns the
    transcript ("" on any failure). Never raises."""
    if not pcm:
        return ""
    script = (
        "import sys, wave\n"
        "import numpy as np\n"
        "from faster_whisper import WhisperModel\n"
        "wav_path, model_ref = sys.argv[1], sys.argv[2]\n"
        "w = wave.open(wav_path, 'rb')\n"
        "pcm = w.readframes(w.getnframes())\n"
        "audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0\n"
        "model = WhisperModel(model_ref, device='cpu', compute_type='int8')\n"
        "segments, _ = model.transcribe(audio, language='en')\n"
        "print(' '.join(s.text.strip() for s in segments).strip())\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out = tmp.name
    try:
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        r = subprocess.run([*vp, "-c", script, out, _whisper_model_ref()],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180, text=True)
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except Exception:
        return ""
    finally:
        Path(out).unlink(missing_ok=True)


def _play_wav(wav_bytes: bytes) -> bool:
    """Play wav bytes on the default speakers. Sets/clears _SPEAKING.

    Returns True when audio was actually handed to an output device,
    False when there is no audio backend (headless machine).
    """
    _SPEAKING.set()
    try:
        if not _sounddevice_available():
            return False
        import sounddevice as sd  # type: ignore
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            data = w.readframes(w.getnframes())
            sr = w.getframerate()
            ch = w.getnchannels()
        import numpy as np  # type: ignore
        arr = np.frombuffer(data, dtype=np.int16)
        sd.play(arr.reshape(-1, ch) if ch > 1 else arr, sr)
        sd.wait()
        return True
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

def _voice_dirs() -> list[Path]:
    """Everywhere a Piper .onnx may live, dev or bundled.

    `make models` downloads to DATA_DIR/models/piper-voices/... (the
    layout default_voice_path() checks first); dev checkouts also keep a
    flat ~/workspace/jarvis/models/piper-voices/ copy.
    """
    dirs = [VOICE_DIR]
    try:
        from jarvis.config import DATA_DIR
        bundled = DATA_DIR / "models" / "piper-voices"
        if bundled != VOICE_DIR:
            dirs.append(bundled)
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


def _find_voice(stem: str | None) -> Path | None:
    for vdir in _voice_dirs():
        if stem:
            cand = vdir / f"{stem}.onnx"
            if cand.is_file():
                return cand
        onnx = sorted(vdir.rglob("*.onnx"))
        if onnx:
            return onnx[0]
    return None


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
        return {"error": (f"No Piper voice model found under "
                          f"{', '.join(str(d) for d in _voice_dirs()) or VOICE_DIR} "
                          f"(looked for '{voice or '<default>'}*.onnx'). "
                          "Run `voice.setup` to download the pinned voice, or place "
                          "an .onnx yourself, e.g. en_GB-alan-medium.onnx from "
                          "https://huggingface.co/rhasspy/piper-voices.")}
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
    # piper-tts >= 1.x exposes synthesize_wav(text, wave_file) — the older
    # synthesize_stream_raw() used by earlier drafts does not exist.
    # Runs under the voice-bridge python so the frozen sidecar can
    # synthesize too (sys.executable would be the frozen exe itself).
    script = (
        "import sys, wave\n"
        "from piper import PiperVoice\n"
        f"v = PiperVoice.load({str(voice_onnx)!r})\n"
        "text = sys.stdin.read()\n"
        "buf = wave.open(sys.argv[1], 'wb')\n"
        "v.synthesize_wav(text, buf)\n"
        "buf.close()\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out = tmp.name
    try:
        vp = _voice_python() or [sys.executable]
        r = subprocess.run([*vp, "-c", script, out],
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
        played = _play_wav(bytes(wav))
        out = {"ok": True, "chars": len(text), "voice": voice or "default",
               "played": played}
        if not played:
            out["note"] = ("audio synthesized but not played: no audio "
                           "output backend (sounddevice) on this machine")
        return out
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
# voice.setup — one-call voice bootstrap

_SETUP_PKGS = (("piper-tts", "piper"), ("faster-whisper", "faster_whisper"),
               ("openwakeword", "openwakeword"), ("sounddevice", "sounddevice"))
_SETUP_MODEL_IDS = ("tts-en", "stt")


def _pip_install(pkg: str) -> dict:
    """pip-install one package for voice work. Never raises.

    Installs into the voice-bridge python (the current interpreter on
    dev/source runs; a discovered system Python under the frozen sidecar) —
    never into the frozen exe itself, which has no pip.

    Tries a plain install first; on externally-managed Pythons (Debian/Ubuntu
    PEP 668) retries with --break-system-packages. The user's Windows Python
    takes the plain path.
    """
    vp = _voice_python()
    if vp is None:
        return {"installed": False,
                "error": ("No usable Python found for voice packages. Install "
                          "Python 3.11+ from https://www.python.org/downloads/ "
                          "(tick 'Add python.exe to PATH') or set the "
                          "JARVIS_VOICE_PYTHON environment variable, then run "
                          "voice.setup again.")}
    def _run(extra: list[str]) -> "subprocess.CompletedProcess":
        return subprocess.run([*vp, "-m", "pip", "install",
                               "--quiet", "--disable-pip-version-check",
                               *extra, pkg],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=900, text=True)
    try:
        r = _run([])
        err = r.stderr or ""
        if r.returncode != 0 and "externally-managed-environment" in err:
            r = _run(["--break-system-packages"])
            err = r.stderr or ""
        if r.returncode != 0 and "Cannot uninstall" in err:
            # System package (e.g. Debian's typing_extensions) blocks the
            # upgrade: install alongside it instead of replacing it.
            r = _run(["--break-system-packages", "--ignore-installed"])
        if r.returncode == 0:
            _vp_pkg_cache.clear()  # re-probe: the package set just changed
            return {"installed": True}
        tail = (r.stderr or r.stdout or "")[-500:].strip()
        return {"installed": False, "error": tail or f"pip exited {r.returncode}"}
    except Exception as e:
        return {"installed": False, "error": f"{type(e).__name__}: {e}"}


def _wav_self_test() -> dict:
    """Real Piper synthesis of a short sentence; verify the WAV is non-silent."""
    res = _piper_synth("JARVIS voice check. All systems nominal.", None)
    if isinstance(res, dict):
        return {"ok": False, "error": res.get("error", "synthesis failed")}
    try:
        buf = io.BytesIO(res)
        with wave.open(buf, "rb") as w:
            nframes = w.getnframes()
            rate = w.getframerate() or 1
            frames = w.readframes(nframes)
        dur = nframes / rate
        peak = max(frames) if frames else 0
        ok = dur > 0.5 and peak > 0
        return {"ok": ok, "seconds": round(dur, 2),
                "bytes": len(res),
                **({} if ok else {"error": "synthesis produced silence"})}
    except Exception as e:
        return {"ok": False, "error": f"wav decode failed: {e}"}


def setup_handler(args: dict) -> dict:
    """Bootstrap the whole voice stack in one call.

    1. Probes the four pip backends (piper-tts, faster-whisper, openwakeword,
       sounddevice) — in-process, or via the voice-bridge python under the
       frozen sidecar; installs missing ones with pip when a usable Python
       exists, or reports exact install steps when none does.
    2. Downloads the pinned voice + whisper models (SHA-256 verified where
       pinned); already-present files are skipped.
    3. Runs a real TTS self-test and verifies non-silent output.
    Returns {"voice_ready": bool, "push_to_talk_ready": bool, "backends": ...,
             "models": ..., "self_test": ...}. Never raises.

    voice_ready covers the voice.* TOOLS (mic/speaker in-process). The
    desktop app's push-to-talk path (browser mic + bridge STT/TTS) is gated
    by push_to_talk_ready instead.
    """
    try:
        want_install = bool(args.get("install", True))
        want_download = bool(args.get("download", True))
        frozen = bool(getattr(sys, "frozen", False))

        backends: dict = {}
        vp = _voice_python()
        bridged = vp is not None and vp != [sys.executable]
        for pip_name, import_name in _SETUP_PKGS:
            present = _has_package(import_name) or _vp_has_package(import_name)
            entry: dict = {"present": present}
            if bridged and present:
                entry["via"] = "voice-bridge python"
            if not present:
                if pip_name == "piper-tts" and _piper_kind() == "binary":
                    entry["present"] = True
                    entry["via"] = "piper CLI binary"
                elif want_install:
                    if _voice_python() is None:
                        entry["guidance"] = (
                            "No usable Python found for voice packages. "
                            "Install Python 3.11+ from "
                            "https://www.python.org/downloads/ (tick "
                            "'Add python.exe to PATH'), or run the voice-setup "
                            "script from the JARVIS install folder, then run "
                            "voice.setup again.")
                    else:
                        entry.update(_pip_install(pip_name))
                        entry["present"] = (_has_package(import_name)
                                            or _vp_has_package(import_name))
                        if bridged and entry["present"]:
                            entry["via"] = "voice-bridge python"
                else:
                    entry["guidance"] = (
                        "Not installed for voice work; re-run voice.setup "
                        f"with install=true, or: pip install {pip_name}")
            backends[pip_name] = entry

        models: dict = {}
        if want_download:
            try:
                from jarvis.models import download as _models_download
                from jarvis.models import default_manifest_path
                manifest = default_manifest_path()
                if manifest.is_file():
                    # Capture per-file output without printing to the console.
                    import contextlib as _cl
                    buf = io.StringIO()
                    with _cl.redirect_stdout(buf):
                        _models_download(str(manifest), yes=True,
                                         only=list(_SETUP_MODEL_IDS))
                    models = {"downloaded": True, "log": buf.getvalue().strip()}
                else:
                    models = {"downloaded": False,
                              "error": f"manifest not found: {manifest}"}
            except Exception as e:
                models = {"downloaded": False,
                          "error": f"{type(e).__name__}: {e}"}
        else:
            models = {"downloaded": False, "skipped": True}

        voice_onnx = _find_voice(None)
        models["voice_model"] = (str(voice_onnx) if voice_onnx
                                 else "missing")
        self_test = _wav_self_test()

        tts_ok = (_piper_kind() is not None and voice_onnx is not None
                  and self_test.get("ok"))
        listen_ok = (backends["faster-whisper"]["present"]
                     and backends["openwakeword"]["present"]
                     and backends["sounddevice"]["present"])
        # Desktop push-to-talk: mic is captured in the browser, STT/TTS run
        # through the voice-bridge. Needs no in-process audio backend.
        push_to_talk_ok = bool(tts_ok and _whisper_available())
        return {"voice_ready": bool(tts_ok and listen_ok),
                "tts_ready": bool(tts_ok), "listen_ready": bool(listen_ok),
                "push_to_talk_ready": push_to_talk_ok,
                "frozen": frozen, "backends": backends,
                "models": models, "self_test": self_test}
    except Exception as e:
        return {"error": f"voice.setup failed: {e}"}


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
    {"name": "voice.setup",
     "description": ("One-call voice bootstrap: installs missing pip backends "
                     "(piper-tts, faster-whisper, openwakeword, sounddevice) "
                     "when running under a real interpreter, downloads the "
                     "pinned Piper voice + whisper STT model (SHA-256 "
                     "verified), then runs a real TTS self-test. Returns "
                     "{\"voice_ready\": bool, \"backends\": ..., \"models\": "
                     "..., \"self_test\": ...}. `install`/`download` default "
                     "true; set false to probe only."),
     "handler": setup_handler, "risk": "medium", "needs_network": True,
     "schema": {"install?": "bool", "download?": "bool"}},
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
    "voice.setup": ("medium", True),
}


def register(reg) -> None:
    """Wire the four Voice Conversation tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
