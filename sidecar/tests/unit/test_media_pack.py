"""Unit tests for the Media+ Tool Pack (Phase 6 / Pack 5). All offline:
heavy backends (Piper, faster-whisper) are mocked; ffmpeg subprocess calls are
mocked except one real end-to-end trim test guarded by shutil.which."""
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import creator, media_pack  # noqa: E402


def _cfg(monkeypatch, tmp_path, **overrides):
    cfg = json.loads(json.dumps(creator._DEFAULTS))  # deep copy
    cfg["output_dir"] = str(tmp_path)
    for k, v in overrides.items():
        cfg["tools"][k] = v
    monkeypatch.setattr(creator, "_load_config", lambda: cfg)
    return cfg


def _ok_run_factory(touch_out=True):
    def fake_run(cmd, timeout=300):
        if touch_out:
            Path(cmd[-1]).touch()
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return fake_run


def test_disabled_tool(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, **{"tts.speak": {"enabled": False}})
    assert "disabled" in media_pack.tts_speak({"text": "hi"})["error"]


def test_never_raises_on_empty_args(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    handlers = [media_pack.tts_speak, media_pack.audio_transcribe,
                media_pack.image_resize, media_pack.image_convert,
                media_pack.video_trim, media_pack.video_gif,
                media_pack.video_contact_sheet, media_pack.file_hash,
                media_pack.archive_zip, media_pack.archive_unzip]
    for h in handlers:
        r = h({})
        assert isinstance(r, dict) and "error" in r, h.__name__


# --------------------------------------------------------------- tts.speak
def test_tts_speak_no_model_honest_error(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(media_pack, "_default_voice_path", lambda: None)
    r = media_pack.tts_speak({"text": "hello"})
    assert r == {"error": "no Piper voice model — download voices via make models"}


def test_tts_speak_fake_success(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    fake_onnx = tmp_path / "voice.onnx"
    fake_onnx.write_bytes(b"fake")

    class FakeTTS:
        def speak(self, text):
            yield b"\x00" * 3200

    monkeypatch.setattr(media_pack, "_default_voice_path", lambda: str(fake_onnx))
    monkeypatch.setattr(media_pack, "_load_real_tts", lambda p: FakeTTS())
    r = media_pack.tts_speak({"text": "hello sir"})
    assert Path(r["path"]).is_file() and r["path"].endswith(".wav")
    assert r["chars"] == 9


def test_tts_speak_caps_at_2000_chars(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    fake_onnx = tmp_path / "voice.onnx"
    fake_onnx.write_bytes(b"fake")

    class FakeTTS:
        def speak(self, text):
            yield b"\x00" * 100

    monkeypatch.setattr(media_pack, "_default_voice_path", lambda: str(fake_onnx))
    monkeypatch.setattr(media_pack, "_load_real_tts", lambda p: FakeTTS())
    r = media_pack.tts_speak({"text": "x" * 5000})
    assert r["chars"] == 2000


# --------------------------------------------------------- audio.transcribe
def test_transcribe_no_backend_honest_error(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....")
    # simulate "backend not installed" through the pack's own seam, so the
    # test is deterministic whether or not faster-whisper is installed here
    monkeypatch.setattr(
        media_pack, "_load_whisper",
        lambda: (None, {"error": "transcription needs faster-whisper"}))
    r = media_pack.audio_transcribe({"path": str(audio)})
    assert "faster-whisper" in r["error"]  # honest error, never a fake result


def test_transcribe_fake_success(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....")

    class Seg:
        text = "hello world"

    class Info:
        language = "en"

    class FakeModel:
        def transcribe(self, path):
            assert path == str(audio)
            return [Seg()], Info()

    monkeypatch.setattr(media_pack, "_load_whisper", lambda: (FakeModel(), None))
    r = media_pack.audio_transcribe({"path": str(audio)})
    assert r == {"text": "hello world", "language": "en"}


# -------------------------------------------------------------- image tools
def _make_png(path, size=(100, 100), color=(255, 0, 0)):
    from PIL import Image
    Image.new("RGB", size, color).save(path)


def test_image_resize_real(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "src.png"
    _make_png(src)
    r = media_pack.image_resize({"path": str(src), "width": 20, "height": 30})
    assert r["size"] == [20, 30]
    from PIL import Image
    assert Image.open(r["path"]).size == (20, 30)


def test_image_resize_bad_dims(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "src.png"
    _make_png(src)
    assert "error" in media_pack.image_resize({"path": str(src), "width": 0, "height": 5})


def test_image_resize_no_pil(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "src.png"
    src.write_bytes(b"fake")
    monkeypatch.setitem(sys.modules, "PIL", None)
    r = media_pack.image_resize({"path": str(src), "width": 10, "height": 10})
    assert r == {"error": "PIL not installed"}


def test_image_convert_real(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "src.png"
    _make_png(src)
    r = media_pack.image_convert({"path": str(src), "format": "jpg"})
    assert r["path"].endswith(".jpg") and r["format"] == "jpg"
    assert Path(r["path"]).is_file()


def test_image_convert_bad_format(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "src.png"
    _make_png(src)
    assert "error" in media_pack.image_convert({"path": str(src), "format": "tiff"})


# ---------------------------------------------------------------- video.trim
def test_video_trim_command_shape(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake-video")
    seen = []
    monkeypatch.setattr(media_pack, "_run", lambda cmd, timeout=300:
                        (seen.append(cmd),
                         Path(cmd[-1]).touch(),
                         subprocess.CompletedProcess(cmd, 0, "", ""))[2])
    # pretend ffmpeg exists: CI runners have none installed
    monkeypatch.setattr(media_pack.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    r = media_pack.video_trim({"path": str(src), "start": 1.5, "end": 4.0})
    cmd = seen[0]
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "1.5"
    assert "-to" in cmd and cmd[cmd.index("-to") + 1] == "4.0"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert r["duration"] == 2.5 and Path(r["path"]).is_file()


def test_video_trim_no_ffmpeg(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(media_pack, "_ffmpeg", lambda: None)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    assert media_pack.video_trim({"path": str(src), "start": 0, "end": 1}) == \
        {"error": "ffmpeg not found"}


def test_video_trim_bad_range(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    assert "error" in media_pack.video_trim({"path": str(src), "start": 5, "end": 2})


def test_video_trim_real_ffmpeg(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=64x64:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
                   capture_output=True, check=True, timeout=60)
    r = media_pack.video_trim({"path": str(src), "start": 0.5, "end": 1.5})
    assert r["duration"] == 1.0 and Path(r["path"]).stat().st_size > 0


# ----------------------------------------------------------------- video.gif
def test_video_gif_two_pass_shape(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake-video")
    calls = []

    def fake_run(cmd, timeout=300):
        calls.append(cmd)
        Path(cmd[-1]).touch()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(media_pack, "_run", fake_run)
    # pretend ffmpeg exists: CI runners have none installed
    monkeypatch.setattr(media_pack.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    r = media_pack.video_gif({"path": str(src), "fps": 8, "width": 320})
    assert len(calls) == 2
    assert "palettegen" in calls[0][-2] and "fps=8" in calls[0][-2]
    lavfi = calls[1][calls[1].index("-lavfi") + 1]
    assert "paletteuse" in lavfi and "fps=8" in lavfi
    assert r == {"path": r["path"], "fps": 8, "width": 320}


def test_video_gif_no_ffmpeg(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(media_pack, "_ffmpeg", lambda: None)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    assert media_pack.video_gif({"path": str(src)}) == {"error": "ffmpeg not found"}


# ------------------------------------------------------- video.contact_sheet
def test_video_contact_sheet_shape(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake-video")
    calls = []
    # pretend ffmpeg/ffprobe exist: CI runners have none installed
    monkeypatch.setattr(media_pack.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, timeout=300):
        calls.append(cmd)
        if "ffprobe" in cmd[0]:
            return subprocess.CompletedProcess(cmd, 0, stdout="12.0\n", stderr="")
        Path(cmd[-1]).touch()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(media_pack, "_run", fake_run)
    r = media_pack.video_contact_sheet({"path": str(src), "cols": 4, "rows": 3})
    ffmpeg_call = calls[-1]
    vf = ffmpeg_call[ffmpeg_call.index("-vf") + 1]
    assert "tile=4x3" in vf and "-frames:v" in ffmpeg_call
    assert r["cols"] == 4 and r["rows"] == 3 and Path(r["path"]).is_file()


# ----------------------------------------------------------------- file.hash
def test_file_hash_real(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "data.bin"
    f.write_bytes(b"abc")
    r = media_pack.file_hash({"path": str(f)})
    assert r == {"path": str(f), "algo": "sha256",
                 "hash": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}


def test_file_hash_unknown_algo(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "data.bin"
    f.write_bytes(b"abc")
    assert "error" in media_pack.file_hash({"path": str(f), "algo": "nope"})


# ------------------------------------------------------------------ archives
def test_archive_zip_and_unzip_real(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(media_pack, "HOME", tmp_path)
    a = tmp_path / "a.txt"
    a.write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world")
    zr = media_pack.archive_zip({"paths": [str(a), str(sub)],
                                 "out": str(tmp_path / "out.zip")})
    assert Path(zr["path"]).is_file()
    assert sorted(zr["files"]) == ["a.txt", "b.txt"]
    ur = media_pack.archive_unzip({"path": zr["path"],
                                   "dest": str(tmp_path / "restored")})
    assert sorted(ur["files"]) == ["a.txt", "b.txt"]
    assert (tmp_path / "restored" / "a.txt").read_text() == "hello"


def test_archive_zip_jail_rejects_outside_home(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(media_pack, "HOME", tmp_path)
    a = tmp_path / "a.txt"
    a.write_text("hello")
    r = media_pack.archive_zip({"paths": ["/etc/hostname"],
                                "out": str(tmp_path / "out.zip")})
    assert "outside allowed home" in r["error"]


def test_archive_unzip_rejects_zip_slip(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    monkeypatch.setattr(media_pack, "HOME", tmp_path)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../pwned.txt", "evil")
    r = media_pack.archive_unzip({"path": str(evil),
                                  "dest": str(tmp_path / "dest")})
    assert "zip-slip" in r["error"]
    assert not (tmp_path / "pwned.txt").exists()
