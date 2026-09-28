"""Unit tests for the Phase 4 Creator Tool Pack. All offline: network and
subprocess are monkeypatched; the real ffmpeg is used only if present."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "sidecar")

from jarvis.tools import builtin  # noqa: E402
from jarvis.tools.builtin import creator  # noqa: E402
from jarvis.agent.audit import AuditLog  # noqa: E402

PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
             b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")


def _reg():
    tmp = tempfile.mkdtemp()
    return builtin.build_registry(tmp), AuditLog(Path(tmp) / "a.db")


def _cfg(monkeypatch, tmp_path, **overrides):
    cfg = json.loads(json.dumps(creator._DEFAULTS))  # deep copy
    cfg["output_dir"] = str(tmp_path)
    for k, v in overrides.items():
        cfg["tools"][k] = v
    monkeypatch.setattr(creator, "_load_config", lambda: cfg)
    return cfg


# ------------------------------------------------------------- code.run
def test_code_run_hello(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.code_run({"code": "print('hello world')"})
    assert r["stdout"].strip() == "hello world"
    assert r["exit_code"] == 0
    assert r["timed_out"] is False


def test_code_run_timeout_kills(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.code_run({"code": "import time; time.sleep(5); print('done')",
                          "timeout": 1})
    assert r["timed_out"] is True
    assert r["exit_code"] is None


def test_code_run_never_raises(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.code_run({"code": ""})
    assert "error" in r
    r = creator.code_run({"code": "raise ValueError('boom')"})
    assert r["exit_code"] != 0 and "boom" in r["stderr"]


def test_disabled_tool(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path, **{"code.run": {"enabled": False}})
    r = creator.code_run({"code": "print('x')"})
    assert r["error"] == "tool 'code.run' is disabled in tools_config.yaml"


# --------------------------------------------------------- website.build
def test_website_build(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.website_build({"name": "Alibaba & 41 Dishes",
                               "brief": "A cozy Lucknow restaurant serving Awadhi biryani and kebabs late into the night.",
                               "business_type": "food"})
    p = Path(r["path"])
    assert p.is_file()
    html = p.read_text()
    assert "Alibaba &amp; 41 Dishes" in html
    assert "</html>" in html
    assert "lorem" not in html.lower()
    assert r["palette"] == "warm"
    assert r["url_hint"].startswith("file://")


def test_website_build_fitness_palette(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.website_build({"name": "Iron Den", "brief": "24/7 strength gym.",
                               "business_type": "fitness"})
    assert r["palette"] == "volt"


# ------------------------------------------------------------- media.image
def test_media_image(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"FAKEJPEGDATA"

    def fake_urlopen(req, timeout=60):
        assert req.full_url.startswith("https://image.pollinations.ai/prompt/")
        assert "width=512" in req.full_url and "nologo=true" in req.full_url
        return FakeResp()

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    r = creator.media_image({"prompt": "a red bicycle", "width": 512, "height": 512})
    p = Path(r["path"])
    assert p.is_file() and p.read_bytes() == b"FAKEJPEGDATA"


def test_media_image_network_failure(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)

    def fake_urlopen(req, timeout=60):
        raise OSError("dns down")

    monkeypatch.setattr(creator.urllib.request, "urlopen", fake_urlopen)
    r = creator.media_image({"prompt": "x"})
    assert "error" in r and "dns down" in r["error"]


# ------------------------------------------------------------ video.compose
def test_video_compose(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    imgs = []
    for i in range(2):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(PNG_BYTES)
        imgs.append(str(p))

    def fake_run(cmd, timeout=300):
        Path(cmd[-1]).touch()  # pretend ffmpeg wrote the segment/output
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(creator, "_run", fake_run)
    # pretend ffmpeg exists: the CI runner has no ffmpeg installed
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/ffmpeg")
    r = creator.video_compose({"images": imgs, "seconds_each": 2,
                               "orientation": "landscape"})
    assert Path(r["path"]).is_file()
    assert r["duration_sec"] == 4.0


def test_video_compose_missing_images(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.video_compose({"images": ["/nope/missing.png"]})
    assert "error" in r and "not found" in r["error"]


def test_video_compose_ffmpeg_absent(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    img = tmp_path / "img.png"
    img.write_bytes(PNG_BYTES)
    monkeypatch.setattr(creator.shutil, "which", lambda name: None)
    r = creator.video_compose({"images": [str(img)]})
    assert "error" in r and "ffmpeg" in r["error"]


# ----------------------------------------------------- social.post_instagram
def test_social_dry_run(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "post.jpg"
    f.write_bytes(PNG_BYTES)
    r = creator.social_post_instagram({"file": str(f), "caption": "hello #test"})
    assert r["dry_run"] is True
    assert r["would_post"]["caption"] == "hello #test"
    assert "note" in r


def test_social_file_missing(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    r = creator.social_post_instagram({"file": str(tmp_path / "nope.jpg"),
                                       "caption": "x"})
    assert "error" in r and "not found" in r["error"]


def test_social_no_cli(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "post.jpg"
    f.write_bytes(PNG_BYTES)
    monkeypatch.setattr(creator.shutil, "which", lambda name: None)
    r = creator.social_post_instagram({"file": str(f), "caption": "x",
                                       "dry_run": False})
    assert "error" in r and "instagram-cli not available" in r["error"]


def test_social_real_publish_mocked(monkeypatch, tmp_path):
    _cfg(monkeypatch, tmp_path)
    f = tmp_path / "post.jpg"
    f.write_bytes(PNG_BYTES)
    monkeypatch.setattr(creator, "_instagram_account_id",
                        lambda handle="": ("12345", None))

    def fake_run(cmd, timeout=180):
        assert "post-feed" in cmd
        assert "--account-id" in cmd and "12345" in cmd
        assert "--caption" in cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"ok": True}), "")

    monkeypatch.setattr(creator, "_run", fake_run)
    r = creator.social_post_instagram({"file": str(f), "caption": "real post",
                                       "dry_run": False})
    assert r.get("posted") is True


# ------------------------------------------------ registry + policy wiring
def test_registry_policy_wiring():
    reg, audit = _reg()
    for name in ("code.run", "website.build", "media.image",
                 "video.compose", "social.post_instagram"):
        assert name in reg.tools, f"{name} not registered"
    t = reg.tools["social.post_instagram"]
    assert t.risk == "high" and t.needs_network is True
    assert reg.tools["code.run"].risk == "medium"
    assert reg.tools["media.image"].needs_network is True
    # high-risk social post must require confirmation when not confirmed
    r = reg.call("social.post_instagram", {"file": "x", "caption": "y"},
                 audit=audit)
    assert r.get("needs_confirmation") is True
    assert r["ok"] is False


def test_config_file_fallback(monkeypatch, tmp_path):
    # missing config file -> defaults, tool enabled
    monkeypatch.setattr(creator, "CONFIG_PATH", tmp_path / "nope.yaml")
    monkeypatch.setattr(creator, "_cfg_cache", None)
    monkeypatch.setattr(creator, "_cfg_mtime", 0.0)
    cfg = creator._load_config()
    assert cfg["tools"]["code.run"]["enabled"] is True
