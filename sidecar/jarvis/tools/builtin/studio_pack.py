"""Studio Tool Pack (Phase 8): local audio/video production.

Four tools: podcast (Piper TTS + ffmpeg concat to MP3), voiced_reel
(Ken-Burns slideshow + Piper voiceover via creator.video_compose),
transcribe_notes (faster-whisper + extractive action-item mining),
mix (ffmpeg concat/amix of audio files).

Everything runs locally: no network, no LLM, no credentials.
All handlers are dict in -> dict out and NEVER raise; failures return
{"error": "..."} with an honest reason (missing ffmpeg, missing Piper
binary, missing voice model, missing faster-whisper, etc.).
"""
from __future__ import annotations

import array
import importlib.util
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
VOICE_DIR = JARVIS_DIR / "models" / "piper-voices"
OUTPUT_DIR = JARVIS_DIR / "output" / "studio"

_SYNTH_TIMEOUT = 180
_FFMPEG_TIMEOUT = 300
_WHISPER_TIMEOUT = 600


# ------------------------------------------------------------------ helpers

def _out_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _run(cmd: list[str], timeout: int = _FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _safe_name(name: str | None, default: str, ext: str) -> str:
    name = (name or "").strip() or default
    name = re.sub(r"[^a-zA-Z0-9_.-]", "-", name)
    if not name.lower().endswith(ext):
        name += ext
    return name


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = _run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=30)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def _audio_codec(path: Path) -> str | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = _run([ffprobe, "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=codec_name",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=30)
        out = r.stdout.strip().lower()
        return out or None
    except Exception:
        return None


def _voice_dirs() -> list[Path]:
    """Everywhere a Piper .onnx may live, dev or bundled (see voice_pack)."""
    dirs = [VOICE_DIR]
    try:
        from jarvis.config import DATA_DIR
        bundled = DATA_DIR / "models" / "piper-voices"
        if bundled != VOICE_DIR:
            dirs.append(bundled)
    except Exception:
        pass
    return [d for d in dirs if d.is_dir()]


def _find_voice(requested: str | None) -> Path | None:
    """Resolve a Piper .onnx voice: explicit arg first, then scan voice dirs."""
    if requested:
        p = Path(requested).expanduser()
        if p.is_file() and p.suffix == ".onnx":
            return p
    for vdir in _voice_dirs():
        for p in sorted(vdir.rglob("*.onnx")):
            return p
    return None


def _piper_kind() -> str | None:
    """'binary' if a piper CLI exists, 'module' if python piper-tts is importable."""
    if shutil.which("piper"):
        return "binary"
    if importlib.util.find_spec("piper") is not None:
        return "module"
    return None


def _write_silence_wav(path: Path, seconds: float = 1.0, rate: int = 22050) -> None:
    """Fallback content used only by tests; never used in production."""
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)


def _synth(text: str, voice_onnx: Path, out_wav: Path, timeout: int = _SYNTH_TIMEOUT) -> dict:
    """Synthesize text -> wav via piper CLI or python piper-tts. Returns {} or {"error"}."""
    text = (text or "").strip()
    if not text:
        return {"error": "empty text for segment"}
    kind = _piper_kind()
    if kind == "binary":
        piper = shutil.which("piper")
        r = subprocess.run([piper, "--model", str(voice_onnx),
                            "--output_file", str(out_wav)],
                           input=text, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not out_wav.is_file():
            return {"error": f"piper failed: {r.stderr[-400:].strip() or r.stdout[-200:].strip()}"}
        return {}
    if kind == "module":
        # python piper-tts; write wav without numpy (pure stdlib).
        # piper-tts >= 1.x AudioChunk exposes audio_int16_bytes (there is no
        # audio_float_array) — use it directly.
        script = (
            "import sys, wave\n"
            "from piper import PiperVoice\n"
            "voice = PiperVoice.load(sys.argv[1])\n"
            "rate = getattr(getattr(voice, 'config', None), 'sample_rate', 22050)\n"
            "pcm = b''.join(ch.audio_int16_bytes for ch in voice.synthesize(sys.argv[3]))\n"
            "w = wave.open(sys.argv[2], 'wb')\n"
            "w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)\n"
            "w.writeframes(pcm); w.close()\n"
        )
        r = subprocess.run([sys_exe(), "-c", script, str(voice_onnx),
                            str(out_wav), text],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not out_wav.is_file():
            return {"error": f"piper-tts module failed: {r.stderr[-400:].strip()}"}
        return {}
    return {"error": "Piper TTS is not installed (no 'piper' binary or python piper-tts)"}


def sys_exe() -> str:
    import sys
    return sys.executable


# ------------------------------------------------------------- studio.podcast

def podcast(args: dict) -> dict:
    """Synthesize each segment with Piper, concat wavs, encode MP3."""
    try:
        ffmpeg = _ffmpeg()
        if not ffmpeg:
            return {"error": "ffmpeg is not installed"}
        segments = args.get("segments")
        if not segments or not isinstance(segments, list):
            return {"error": "segments is required (list of {speaker?, text})"}
        texts = []
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict) or not (seg.get("text") or "").strip():
                return {"error": f"segments[{i}] must have non-empty 'text'"}
            texts.append(seg["text"].strip())
        voice = _find_voice(args.get("voice"))
        if not voice:
            return {"error": "no Piper voice model — download via make models"}
        if not _piper_kind():
            return {"error": "Piper TTS is not installed (no 'piper' binary or python piper-tts)"}

        out_dir = _out_dir()
        tmp = out_dir / f"pc-{int(time.time() * 1000)}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            wav_paths = []
            total = 0.0
            for i, text in enumerate(texts):
                wpath = tmp / f"seg{i:03d}.wav"
                err = _synth(text, voice, wpath)
                if err:
                    return err
                wav_paths.append(wpath)
                total += _wav_duration(wpath)
            lst = tmp / "concat.txt"
            lst.write_text("".join(f"file '{p}'\n" for p in wav_paths))
            out_name = _safe_name(args.get("out_name"),
                                  f"podcast-{int(time.time())}", ".mp3")
            out_path = out_dir / out_name
            r = _run([ffmpeg, "-y", "-f", "concat", "-safe", "0",
                      "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "128k",
                      str(out_path)], timeout=_FFMPEG_TIMEOUT)
            if r.returncode != 0 or not out_path.is_file():
                return {"error": f"ffmpeg concat failed: {r.stderr[-500:].strip()}"}
            return {"path": str(out_path),
                    "duration_sec": round(total, 1),
                    "segments": len(texts)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        return {"error": f"podcast failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------- studio.voiced_reel

def voiced_reel(args: dict) -> dict:
    """Ken-Burns slideshow (creator.video.compose) + Piper voiceover muxed in."""
    try:
        ffmpeg = _ffmpeg()
        if not ffmpeg:
            return {"error": "ffmpeg is not installed"}
        images = [str(p) for p in args.get("images", [])]
        if not images:
            return {"error": "images is required (list of file paths)"}
        missing = [p for p in images if not Path(p).is_file()]
        if missing:
            return {"error": f"image file(s) not found: {missing[:3]}"}
        script = (args.get("script") or "").strip()
        if not script:
            return {"error": "script is required"}
        orientation = args.get("orientation", "portrait")
        if orientation not in ("portrait", "landscape"):
            return {"error": "orientation must be 'portrait' or 'landscape'"}
        voice = _find_voice(args.get("voice"))
        if not voice:
            return {"error": "no Piper voice model — download via make models"}
        if not _piper_kind():
            return {"error": "Piper TTS is not installed (no 'piper' binary or python piper-tts)"}

        from . import creator  # read-only reuse of video.compose

        out_dir = _out_dir()
        tmp = out_dir / f"vr-{int(time.time() * 1000)}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            vo_path = tmp / "voiceover.wav"
            err = _synth(script, voice, vo_path)
            if err:
                return err
            vo_dur = _wav_duration(vo_path) or 3.0
            # Size the slideshow so the voiceover fits.
            seconds_each = min(30.0, max(0.5, vo_dur / len(images)))
            silent = creator.video_compose({
                "images": images,
                "orientation": orientation,
                "seconds_each": seconds_each,
                "out_name": f"vr-silent-{int(time.time() * 1000)}",
            })
            if "error" in silent:
                return silent
            silent_path = Path(silent["path"])

            out_name = _safe_name(args.get("out_name"),
                                  f"voiced-reel-{int(time.time())}", ".mp4")
            out_path = out_dir / out_name
            inputs = [ffmpeg, "-y", "-i", str(silent_path), "-i", str(vo_path)]
            bg = args.get("bg_music")
            filt = None
            if bg and Path(bg).is_file():
                inputs += ["-i", str(bg)]
                filt = ("[1:a]volume=1.0[vo];[2:a]volume=0.25[bg];"
                        "[vo][bg]amix=inputs=2:duration=first:dropout_transition=0[a]")
            cmd = inputs + (["-filter_complex", filt] if filt else []) + [
                "-map", "0:v", "-map", "[a]" if filt else "1:a",
                "-c:v", "copy", "-c:a", "aac",
                "-t", f"{silent.get('duration_sec', vo_dur)}", str(out_path)]
            r = _run(cmd, timeout=_FFMPEG_TIMEOUT)
            if r.returncode != 0 or not out_path.is_file():
                return {"error": f"ffmpeg mux failed: {r.stderr[-500:].strip()}"}
            return {"path": str(out_path),
                    "duration_sec": silent.get("duration_sec", round(vo_dur, 1)),
                    "orientation": orientation}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        return {"error": f"voiced_reel failed: {type(e).__name__}: {e}"}


# ----------------------------------------------------- studio.transcribe_notes

_ACTION_RE = re.compile(
    r"\b(will|todo|to-do|action|by\s+(monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|\d{1,2})|deadline|follow[- ]up|send|call|email|schedule|"
    r"book|prepare|draft|review|remind)\b", re.IGNORECASE)
_IMPERATIVE_RE = re.compile(
    r"^(send|call|email|draft|review|prepare|schedule|book|remind|check|update|"
    r"finish|complete|write|follow up|follow-up)\b", re.IGNORECASE)


def _mine_action_items(transcript: str) -> list[str]:
    """Extractive action-item mining: sentence-level cue matching, no LLM."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", transcript) if s.strip()]
    items: list[str] = []
    seen: set[str] = set()
    for s in sentences:
        key = s.lower()
        if key in seen or len(s) < 8:
            continue
        if _ACTION_RE.search(s) or _IMPERATIVE_RE.match(s):
            items.append(s[:300])
            seen.add(key)
        if len(items) >= 50:
            break
    return items


def transcribe_notes(args: dict) -> dict:
    """Transcribe audio with faster-whisper, then mine action items."""
    try:
        audio = (args.get("audio") or "").strip()
        if not audio:
            return {"error": "audio is required (path to an audio file)"}
        apath = Path(audio).expanduser()
        if not apath.is_file():
            return {"error": f"audio file not found: {audio}"}
        try:
            import faster_whisper  # type: ignore
        except ImportError:
            return {"error": "faster-whisper not installed"}
        model_name = args.get("model") or "tiny"
        model = faster_whisper.WhisperModel(model_name, device="cpu",
                                           compute_type="int8")
        segments_iter, _info = model.transcribe(str(apath))
        seg_list = list(segments_iter)
        texts = [s.text.strip() for s in seg_list if getattr(s, "text", "").strip()]
        transcript = " ".join(texts).strip()
        if not transcript:
            return {"transcript_path": None, "action_items": [], "segments": 0,
                    "note": "no speech detected"}
        out_dir = _out_dir()
        tname = f"transcript-{int(time.time())}.txt"
        tpath = out_dir / tname
        tpath.write_text(transcript, encoding="utf-8")
        return {"transcript_path": str(tpath),
                "action_items": _mine_action_items(transcript),
                "segments": len(seg_list)}
    except Exception as e:
        return {"error": f"transcribe_notes failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------- studio.mix

def mix(args: dict) -> dict:
    """Mix audio files: concat (same codec) or amix (different) into one MP3/WAV."""
    try:
        ffmpeg = _ffmpeg()
        if not ffmpeg:
            return {"error": "ffmpeg is not installed"}
        files = [str(p) for p in args.get("audio_files", [])]
        if len(files) < 2:
            return {"error": "audio_files is required (list of 2+ file paths)"}
        missing = [p for p in files if not Path(p).is_file()]
        if missing:
            return {"error": f"audio file(s) not found: {missing[:3]}"}
        codecs = {_audio_codec(Path(p)) for p in files}
        same_codec = len(codecs) == 1 and None not in codecs

        out_dir = _out_dir()
        out_name = _safe_name(args.get("out_name"),
                              f"mix-{int(time.time())}", ".mp3")
        out_path = out_dir / out_name
        if same_codec:
            tmp = out_dir / f"mx-{int(time.time() * 1000)}"
            tmp.mkdir(parents=True, exist_ok=True)
            try:
                lst = tmp / "concat.txt"
                lst.write_text("".join(f"file '{p}'\n" for p in files))
                r = _run([ffmpeg, "-y", "-f", "concat", "-safe", "0",
                          "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "192k",
                          str(out_path)], timeout=_FFMPEG_TIMEOUT)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        else:
            cmd = [ffmpeg, "-y"]
            for p in files:
                cmd += ["-i", p]
            amix = ",".join(f"[{i}:a]" for i in range(len(files)))
            cmd += ["-filter_complex",
                    f"{amix}amix=inputs={len(files)}:duration=longest:dropout_transition=0",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)]
            r = _run(cmd, timeout=_FFMPEG_TIMEOUT)
        if r.returncode != 0 or not out_path.is_file():
            return {"error": f"ffmpeg mix failed: {r.stderr[-500:].strip()}"}
        dur = _ffprobe_duration(out_path)
        return {"path": str(out_path),
                "duration_sec": round(dur, 1) if dur is not None else None}
    except Exception as e:
        return {"error": f"mix failed: {type(e).__name__}: {e}"}


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "studio.podcast", "description": "Synthesize podcast MP3 from text segments with Piper TTS, concatenated with ffmpeg.",
     "handler": podcast, "risk": "low", "needs_network": False,
     "schema": {"segments": "list", "voice": "string?", "out_name": "string?"}},
    {"name": "studio.voiced_reel", "description": "Ken-Burns slideshow video with a Piper TTS voiceover (optional background music).",
     "handler": voiced_reel, "risk": "low", "needs_network": False,
     "schema": {"images": "list", "script": "string", "voice": "string?",
                "orientation": "portrait|landscape", "out_name": "string?",
                "bg_music": "string?"}},
    {"name": "studio.transcribe_notes", "description": "Transcribe audio with faster-whisper and extract action items (regex mining, no LLM).",
     "handler": transcribe_notes, "risk": "low", "needs_network": False,
     "schema": {"audio": "string", "model": "string?"}},
    {"name": "studio.mix", "description": "Mix multiple audio files into one MP3 (concat if same codec, amix otherwise).",
     "handler": mix, "risk": "low", "needs_network": False,
     "schema": {"audio_files": "list", "out_name": "string?"}},
]

# Compatibility with existing packs, which expose TOOLS.
TOOLS = TOOL_DEFS

__all__ = ["TOOL_DEFS", "TOOLS", "podcast", "voiced_reel", "transcribe_notes", "mix"]
