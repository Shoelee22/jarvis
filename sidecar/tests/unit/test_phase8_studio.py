"""Unit tests for the Phase 8 Studio Tool Pack. Fully offline: shutil.which,
subprocess.run, the internal _synth step, and faster-whisper are
monkeypatched; nothing touches the network or real TTS/STT backends."""
import shutil
import subprocess
import sys
import types
import wave
from pathlib import Path

sys.path.insert(0, "sidecar")  # noqa: E402

from jarvis.tools.builtin import creator  # noqa: E402
from jarvis.tools.builtin import studio_pack as sp  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_wav(path: Path, seconds: float = 1.0) -> Path:
    sp._write_silence_wav(path, seconds=seconds)
    return path


def _with_ffmpeg(monkeypatch, real=subprocess.run):
    """shutil.which reports a fake ffmpeg; subprocess.run is pass-through."""

    def fake_which(name, *a, **k):
        if name == "ffmpeg":
            return "/usr/bin/ffmpeg"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)
    return real


def _fake_voice(monkeypatch, tmp_path):
    v = tmp_path / "en_GB-alan-medium.onnx"
    v.write_bytes(b"fake-onnx")
    monkeypatch.setattr(sp, "_find_voice", lambda requested=None: v)


# ------------------------------------------------------------- podcast

def test_podcast_missing_ffmpeg_honest_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    res = sp.podcast({"segments": [{"text": "Hello sir."}]})
    assert "error" in res
    assert "ffmpeg" in res["error"] or "Piper" in res["error"]


def test_podcast_missing_voice_model_honest_error(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    monkeypatch.setattr(sp, "_find_voice", lambda requested=None: None)
    monkeypatch.chdir(tmp_path)
    res = sp.podcast({"segments": [{"text": "Hello sir."}]})
    assert res == {"error": "no Piper voice model — download via make models"}


def test_podcast_concat_pipeline(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    _fake_voice(monkeypatch, tmp_path)
    monkeypatch.setattr(sp, "_piper_kind", lambda: "binary")

    def fake_synth(text, voice_onnx, out_wav, timeout=180):
        _fake_wav(Path(out_wav), seconds=2.0)
        return {}

    monkeypatch.setattr(sp, "_synth", fake_synth)

    def fake_run(cmd, *a, **k):
        out = Path(cmd[-1])
        out.write_bytes(b"fake-mp3")
        return _Proc(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    res = sp.podcast({"segments": [{"speaker": "host", "text": "One."},
                                   {"text": "Two."}],
                      "out_name": "test-podcast"})
    assert "error" not in res, res
    assert res["path"].endswith("test-podcast.mp3")
    assert res["duration_sec"] == 4.0  # 2 segments x 2.0s fake wavs
    assert res["segments"] == 2


def test_podcast_rejects_empty_segments(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    res = sp.podcast({"segments": []})
    assert "error" in res
    res2 = sp.podcast({"segments": [{"text": "  "}]})
    assert "error" in res2


# ---------------------------------------------------------- voiced_reel

def test_voiced_reel_missing_images_error(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    res = sp.voiced_reel({"images": [str(tmp_path / "nope.png")], "script": "Hi."})
    assert "error" in res and "not found" in res["error"]


def test_voiced_reel_pipeline(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    _fake_voice(monkeypatch, tmp_path)
    monkeypatch.setattr(sp, "_piper_kind", lambda: "binary")

    def fake_synth(text, voice_onnx, out_wav, timeout=180):
        _fake_wav(Path(out_wav), seconds=4.0)
        return {}

    monkeypatch.setattr(sp, "_synth", fake_synth)

    silent = tmp_path / "silent.mp4"
    silent.write_bytes(b"fake-mp4")

    def fake_compose(args):
        assert args["seconds_each"] == 2.0  # 4s voiceover / 2 images
        return {"path": str(silent), "duration_sec": 4.0}

    monkeypatch.setattr(creator, "video_compose", fake_compose)

    def fake_run(cmd, *a, **k):
        out = Path(cmd[-1])
        out.write_bytes(b"fake-mp4")
        return _Proc(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    img1 = tmp_path / "a.png"
    img2 = tmp_path / "b.png"
    img1.write_bytes(b"x")
    img2.write_bytes(b"x")
    res = sp.voiced_reel({"images": [str(img1), str(img2)],
                          "script": "Welcome to the reel.",
                          "orientation": "landscape",
                          "out_name": "reel-test"})
    assert "error" not in res, res
    assert res["path"].endswith("reel-test.mp4")
    assert res["duration_sec"] == 4.0
    assert res["orientation"] == "landscape"


def test_voiced_reel_bad_orientation(monkeypatch):
    res = sp.voiced_reel({"images": ["/tmp/x.png"], "script": "Hi.",
                          "orientation": "square"})
    assert "error" in res


# ----------------------------------------------------- transcribe_notes

def _mock_faster_whisper(monkeypatch, text=""):
    import re as _re
    segs = []
    parts = [p.strip() for p in _re.split(r"\.\s*", text) if p.strip().rstrip(".")]
    for i, t in enumerate(parts):
        t = t.rstrip(".")
        if not t:
            continue
        segs.append(types.SimpleNamespace(text=t + ".", start=float(i), end=float(i + 1)))

    class FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, *a, **k):
            return segs, {}

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)


def test_transcribe_notes_missing_backend(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    res = sp.transcribe_notes({"audio": str(audio)})
    assert res == {"error": "faster-whisper not installed"}


def test_transcribe_notes_mines_action_items(monkeypatch, tmp_path):
    _mock_faster_whisper(
        monkeypatch,
        "I will send the report by Friday. "
        "Call Alice about the venue. "
        "The weather is quite nice today. "
        "Follow up with the gym owner next week. "
        "Remember to buy milk.")
    audio = tmp_path / "notes.wav"
    audio.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    res = sp.transcribe_notes({"audio": str(audio)})
    assert "error" not in res, res
    assert res["segments"] == 5
    assert Path(res["transcript_path"]).is_file()
    items = res["action_items"]
    assert any("will send the report by Friday" in i for i in items)
    assert any("Call Alice" in i for i in items)
    assert any("Follow up" in i for i in items)
    assert not any("weather" in i for i in items)
    assert not any("milk" in i for i in items)


# ------------------------------------------------------------------ mix

def test_mix_missing_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    res = sp.mix({"audio_files": [str(tmp_path / "a.mp3"), str(tmp_path / "b.mp3")]})
    assert "error" in res and "ffmpeg" in res["error"]


def test_mix_concat_path(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    monkeypatch.setattr(sp, "_audio_codec", lambda p: "mp3")
    monkeypatch.setattr(sp, "_ffprobe_duration", lambda p: 12.5)

    def fake_run(cmd, *a, **k):
        out = Path(cmd[-1])
        out.write_bytes(b"fake-mp3")
        return _Proc(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    res = sp.mix({"audio_files": [str(a), str(b)], "out_name": "mix-test"})
    assert "error" not in res, res
    assert res["path"].endswith("mix-test.mp3")
    assert res["duration_sec"] == 12.5


def test_mix_amix_path_uses_filter(monkeypatch, tmp_path):
    _with_ffmpeg(monkeypatch)
    codecs = iter(["mp3", "wav"])
    monkeypatch.setattr(sp, "_audio_codec", lambda p: next(codecs))
    monkeypatch.setattr(sp, "_ffprobe_duration", lambda p: 7.0)
    seen = {}

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-mp3")
        return _Proc(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.wav"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    res = sp.mix({"audio_files": [str(a), str(b)]})
    assert "error" not in res, res
    assert "-filter_complex" in seen["cmd"]
    assert res["duration_sec"] == 7.0


# ------------------------------------------------------------- registry

def test_tool_defs_metadata():
    names = [t["name"] for t in sp.TOOL_DEFS]
    assert names == ["studio.podcast", "studio.voiced_reel",
                     "studio.transcribe_notes", "studio.mix"]
    for t in sp.TOOL_DEFS:
        assert t["risk"] == "low"
        assert t["needs_network"] is False
        assert set(t) >= {"name", "description", "handler", "risk",
                          "needs_network", "schema"}
        assert callable(t["handler"])
