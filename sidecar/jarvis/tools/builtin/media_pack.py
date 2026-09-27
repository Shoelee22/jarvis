"""Media+ Tool Pack (Phase 6 / Pack 5): TTS, transcription, image utils,
video utils, hashing, and archives. Outputs live under ~/workspace/jarvis/output/.

Conventions follow creator.py: per-tool toggles come from
~/workspace/jarvis/tools_config.yaml via creator._load_config() (tools default
to enabled when absent), outputs go under creator._output_dir()/"media",
ffmpeg work runs through a creator._run-style subprocess helper, and every
handler returns a dict and NEVER raises.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import wave
import zipfile
from pathlib import Path

from . import creator

HOME = Path.home()
TTS_CHAR_CAP = 2000

# ------------------------------------------------------------------ helpers
def _enabled(tool_name: str) -> dict | None:
    return creator._enabled(tool_name)


def _media_dir() -> Path:
    d = creator._output_dir() / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffmpeg_ok() -> dict | None:
    if not _ffmpeg():
        return {"error": "ffmpeg not found"}
    return None


def _ts() -> int:
    return int(time.time() * 1000)


def _jail_root() -> Path:
    """Root every archive path must live under. Monkeypatchable in tests."""
    return HOME


def _jailed(p: str | Path) -> Path | None:
    """Resolve p and return it only if it stays under the jail root."""
    try:
        rp = Path(p).expanduser().resolve()
    except Exception:
        return None
    try:
        rp.relative_to(_jail_root().resolve())
        return rp
    except ValueError:
        return None


def _require_file(path: str, label: str = "path") -> tuple[Path | None, dict | None]:
    if not path or not str(path).strip():
        return None, {"error": f"{label} is required"}
    p = Path(str(path)).expanduser()
    if not p.is_file():
        return None, {"error": f"file not found: {path}"}
    return p, None


# --------------------------------------------------------------- tts.speak
def _default_voice_path() -> str | None:
    """Piper voice model path via the voice pipeline; None when unavailable."""
    try:
        from jarvis.voice.pipeline import default_voice_path
        return default_voice_path()
    except Exception:
        return None


def _load_real_tts(voice_onnx: str):
    from jarvis.voice.pipeline import load_real_tts
    return load_real_tts(voice_onnx)


def tts_speak(args: dict) -> dict:
    """Synthesize text to a WAV via Piper (the JARVIS voice pipeline)."""
    blocked = _enabled("tts.speak")
    if blocked:
        return blocked
    text = str(args.get("text", "") or "")
    if not text.strip():
        return {"error": "text is required"}
    text = text[:TTS_CHAR_CAP]
    voice = str(args.get("voice") or "").strip()
    try:
        voice_path = voice if voice else _default_voice_path()
        if not voice_path or not Path(voice_path).is_file():
            return {"error": "no Piper voice model — download voices via make models"}
        tts = _load_real_tts(voice_path)
        chunks = b"".join(tts.speak(text))
    except Exception as e:
        # e.g. `piper` package missing on this machine.
        return {"error": f"TTS unavailable: {type(e).__name__}: {e}"}
    if not chunks:
        return {"error": "TTS produced no audio"}
    # Piper streams PCM16 mono at 16 kHz (see voice/pipeline.py).
    out = _media_dir() / f"tts-{_ts()}.wav"
    try:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(chunks)
    except Exception as e:
        return {"error": f"could not write wav: {type(e).__name__}: {e}"}
    return {"path": str(out), "chars": len(text)}


# --------------------------------------------------------- audio.transcribe
def _whisper_model_id() -> str:
    cfg = creator._load_config().get("media", {})
    return str(cfg.get("whisper_model", "small"))


def _load_whisper():
    """Return (WhisperModel class, error dict). Importable + local model only —
    never downloads over the network from inside a tool call."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        return None, {"error": "transcription needs faster-whisper + a whisper model (make models)"}
    model_id = _whisper_model_id()
    cache_roots = [
        Path(os.environ.get("HF_HOME", "")) / "hub",
        Path.home() / ".cache" / "huggingface" / "hub",
        Path.home() / ".cache" / "huggingface",
    ]
    found = any(
        root.is_dir() and any("whisper" in child.name.lower() for child in root.iterdir())
        for root in cache_roots
    )
    # ctranslate2-style cache: ~/.cache/huggingface/hub/models--<org>--<name>
    if not found:
        return None, {"error": "transcription needs faster-whisper + a whisper model (make models)"}
    try:
        model = WhisperModel(model_id, device="auto", compute_type="int8",
                             download_root=str(Path.home() / ".cache" / "huggingface"))
        return model, None
    except Exception as e:
        return None, {"error": f"could not load whisper model: {type(e).__name__}: {e}"}


def audio_transcribe(args: dict) -> dict:
    """Transcribe an audio file with faster-whisper (local model only)."""
    blocked = _enabled("audio.transcribe")
    if blocked:
        return blocked
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    try:
        model, merr = _load_whisper()
        if merr:
            return merr
        segments, info = model.transcribe(str(src))
        text = " ".join(s.text for s in segments).strip()
        out = {"text": text}
        lang = getattr(info, "language", None)
        if lang:
            out["language"] = lang
        return out
    except Exception as e:
        return {"error": f"transcription failed: {type(e).__name__}: {e}"}


# -------------------------------------------------------------- image.resize
def _pil():
    try:
        from PIL import Image
        return Image, None
    except Exception:
        return None, {"error": "PIL not installed"}


def image_resize(args: dict) -> dict:
    """Resize an image to exact width x height pixels."""
    blocked = _enabled("image.resize")
    if blocked:
        return blocked
    Image, perr = _pil()
    if perr:
        return perr
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    try:
        width = int(args.get("width", 0))
        height = int(args.get("height", 0))
    except (TypeError, ValueError):
        return {"error": "width and height must be integers"}
    if width < 1 or height < 1 or width > 8192 or height > 8192:
        return {"error": "width/height must be between 1 and 8192"}
    out_arg = str(args.get("out") or "").strip()
    try:
        img = Image.open(src)
        resized = img.resize((width, height), Image.LANCZOS)
        if out_arg:
            out = Path(out_arg).expanduser()
        else:
            out = _media_dir() / f"resize-{_ts()}{src.suffix or '.png'}"
        out.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs = {}
        if out.suffix.lower() in (".jpg", ".jpeg") and resized.mode in ("RGBA", "P", "LA"):
            resized = resized.convert("RGB")
        resized.save(out, **save_kwargs)
        return {"path": str(out), "size": [width, height]}
    except Exception as e:
        return {"error": f"resize failed: {type(e).__name__}: {e}"}


# ------------------------------------------------------------- image.convert
_CONVERT_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}


def image_convert(args: dict) -> dict:
    """Convert an image to png, jpg, or webp."""
    blocked = _enabled("image.convert")
    if blocked:
        return blocked
    Image, perr = _pil()
    if perr:
        return perr
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    fmt = str(args.get("format", "") or "").lower().lstrip(".")
    if fmt not in _CONVERT_FORMATS:
        return {"error": "format must be one of: png, jpg, webp"}
    pil_fmt = _CONVERT_FORMATS[fmt]
    ext = ".jpg" if pil_fmt == "JPEG" else f".{fmt}"
    out_arg = str(args.get("out") or "").strip()
    try:
        img = Image.open(src)
        if pil_fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        if out_arg:
            out = Path(out_arg).expanduser()
            if not out.suffix:
                out = out.with_suffix(ext)
        else:
            out = _media_dir() / f"convert-{_ts()}{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, pil_fmt)
        return {"path": str(out), "format": fmt}
    except Exception as e:
        return {"error": f"convert failed: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------- video.trim
def video_trim(args: dict) -> dict:
    """Trim a video with ffmpeg (-ss/-to, stream copy)."""
    blocked = _enabled("video.trim")
    if blocked:
        return blocked
    ferr = _ffmpeg_ok()
    if ferr:
        return ferr
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    try:
        start = float(args.get("start", 0))
        end = float(args.get("end", 0))
    except (TypeError, ValueError):
        return {"error": "start and end must be numbers (seconds)"}
    if start < 0 or end <= start:
        return {"error": "need 0 <= start < end (seconds)"}
    out_arg = str(args.get("out") or "").strip()
    out = (Path(out_arg).expanduser() if out_arg
           else _media_dir() / f"trim-{_ts()}{src.suffix or '.mp4'}")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [_ffmpeg(), "-y", "-ss", str(start), "-i", str(src),
               "-to", str(end), "-c", "copy", str(out)]
        r = _run(cmd, timeout=300)
        if r.returncode != 0 or not out.is_file():
            return {"error": f"ffmpeg trim failed: {r.stderr[-500:]}"}
        return {"path": str(out), "duration": round(end - start, 2)}
    except Exception as e:
        return {"error": f"trim failed: {type(e).__name__}: {e}"}


# ----------------------------------------------------------------- video.gif
def _gif_defaults() -> tuple[int, int]:
    cfg = creator._load_config().get("media", {})
    try:
        fps = max(1, min(30, int(cfg.get("gif_fps", 10))))
    except (TypeError, ValueError):
        fps = 10
    try:
        width = max(16, min(1920, int(cfg.get("gif_width", 480))))
    except (TypeError, ValueError):
        width = 480
    return fps, width


def video_gif(args: dict) -> dict:
    """Convert a video clip to GIF (ffmpeg two-pass palettegen/paletteuse)."""
    blocked = _enabled("video.gif")
    if blocked:
        return blocked
    ferr = _ffmpeg_ok()
    if ferr:
        return ferr
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    def_fps, def_w = _gif_defaults()
    try:
        fps = max(1, min(30, int(args.get("fps", def_fps))))
        width = max(16, min(1920, int(args.get("width", def_w))))
    except (TypeError, ValueError):
        return {"error": "fps and width must be integers"}
    out_arg = str(args.get("out") or "").strip()
    out = (Path(out_arg).expanduser() if out_arg
           else _media_dir() / f"clip-{_ts()}.gif")
    if out.suffix.lower() != ".gif":
        out = out.with_suffix(".gif")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        palette = out.with_suffix(".palette.png")
        vf = f"fps={fps},scale={width}:-1:flags=lanczos"
        r1 = _run([_ffmpeg(), "-y", "-i", str(src), "-vf", f"{vf},palettegen",
                   str(palette)], timeout=300)
        if r1.returncode != 0 or not palette.is_file():
            return {"error": f"ffmpeg palettegen failed: {r1.stderr[-500:]}"}
        r2 = _run([_ffmpeg(), "-y", "-i", str(src), "-i", str(palette),
                   "-lavfi", f"{vf} [x]; [x][1:v] paletteuse",
                   str(out)], timeout=300)
        if r2.returncode != 0 or not out.is_file():
            return {"error": f"ffmpeg paletteuse failed: {r2.stderr[-500:]}"}
        return {"path": str(out), "fps": fps, "width": width}
    except Exception as e:
        return {"error": f"gif failed: {type(e).__name__}: {e}"}
    finally:
        try:
            out.with_suffix(".palette.png").unlink(missing_ok=True)
        except Exception:
            pass


# ------------------------------------------------------- video.contact_sheet
def _probe_duration(src: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = _run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(src)], timeout=30)
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except Exception:
        return None


def video_contact_sheet(args: dict) -> dict:
    """Build a single-JPG contact sheet (cols x rows thumbnails) from a video."""
    blocked = _enabled("video.contact_sheet")
    if blocked:
        return blocked
    ferr = _ffmpeg_ok()
    if ferr:
        return ferr
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    try:
        cols = max(1, min(16, int(args.get("cols", 4))))
        rows = max(1, min(16, int(args.get("rows", 3))))
    except (TypeError, ValueError):
        return {"error": "cols and rows must be integers"}
    n = cols * rows
    out_arg = str(args.get("out") or "").strip()
    out = (Path(out_arg).expanduser() if out_arg
           else _media_dir() / f"sheet-{_ts()}.jpg")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        duration = _probe_duration(src)
        fps = max(n / duration, 0.05) if duration and duration > 0 else 1.0
        vf = f"fps={fps:.4f},scale=320:-1,tile={cols}x{rows}"
        r = _run([_ffmpeg(), "-y", "-i", str(src), "-vf", vf,
                  "-frames:v", "1", str(out)], timeout=300)
        if r.returncode != 0 or not out.is_file():
            return {"error": f"ffmpeg contact sheet failed: {r.stderr[-500:]}"}
        return {"path": str(out), "cols": cols, "rows": rows}
    except Exception as e:
        return {"error": f"contact sheet failed: {type(e).__name__}: {e}"}


# ----------------------------------------------------------------- file.hash
def file_hash(args: dict) -> dict:
    """Hash a file with hashlib (default sha256)."""
    blocked = _enabled("file.hash")
    if blocked:
        return blocked
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    algo = str(args.get("algo", "sha256") or "").lower()
    try:
        h = hashlib.new(algo)
    except (ValueError, TypeError):
        return {"error": f"unknown hash algorithm: {algo}"}
    try:
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return {"path": str(src), "algo": algo, "hash": h.hexdigest()}
    except Exception as e:
        return {"error": f"hash failed: {type(e).__name__}: {e}"}


# --------------------------------------------------------------- archive.zip
def _collect_members(paths: list) -> tuple[list[tuple[Path, str]], dict | None]:
    """Resolve inputs to (real path, archive name) pairs."""
    members: list[tuple[Path, str]] = []
    for raw in paths:
        p = _jailed(raw)
        if p is None:
            return [], {"error": f"path outside allowed home dir: {raw}"}
        if not p.exists():
            return [], {"error": f"path not found: {raw}"}
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file():
                    members.append((child, str(child.relative_to(p))))
        else:
            members.append((p, p.name))
    if not members:
        return [], {"error": "no files to archive"}
    return members, None


def archive_zip(args: dict) -> dict:
    """Zip files/dirs into one archive. All inputs + out must be under home."""
    blocked = _enabled("archive.zip")
    if blocked:
        return blocked
    paths = args.get("paths", [])
    if not isinstance(paths, list) or not paths:
        return {"error": "paths is required (list of file/dir paths)"}
    out_arg = str(args.get("out") or "").strip()
    if not out_arg:
        return {"error": "out is required"}
    out = _jailed(out_arg)
    if out is None:
        return {"error": f"out path outside allowed home dir: {out_arg}"}
    try:
        members, merr = _collect_members(paths)
        if merr:
            return merr
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for real, arcname in members:
                z.write(real, arcname)
        return {"path": str(out), "files": [a for _, a in members]}
    except Exception as e:
        return {"error": f"zip failed: {type(e).__name__}: {e}"}


# ------------------------------------------------------------- archive.unzip
def _safe_member(name: str) -> bool:
    p = Path(name)
    return not (p.is_absolute() or ".." in p.parts)


def archive_unzip(args: dict) -> dict:
    """Unzip with zip-slip protection. Dest must be under home."""
    blocked = _enabled("archive.unzip")
    if blocked:
        return blocked
    src, err = _require_file(args.get("path", ""))
    if err:
        return err
    dest_arg = str(args.get("dest") or "").strip()
    if dest_arg:
        dest = _jailed(dest_arg)
        if dest is None:
            return {"error": f"dest outside allowed home dir: {dest_arg}"}
    else:
        dest = creator._output_dir() / "archives" / src.stem
    try:
        dest.mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        with zipfile.ZipFile(src, "r") as z:
            for info in z.infolist():
                if not _safe_member(info.filename):
                    return {"error": f"zip-slip member rejected: {info.filename!r}"}
            z.extractall(dest)
            names = [i.filename for i in z.infolist() if not i.is_dir()]
        return {"dest": str(dest), "files": names}
    except zipfile.BadZipFile:
        return {"error": "not a valid zip file"}
    except Exception as e:
        return {"error": f"unzip failed: {type(e).__name__}: {e}"}
