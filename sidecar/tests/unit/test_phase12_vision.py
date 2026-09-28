"""Offline tests for the Vision pack (Phase 12).

All backend I/O goes through three small mockable functions —
_capture_screen(), _capture_camera(), _vlm_ask() — so these tests never touch
a real display, webcam, or ollama server. Real-error paths (ollama down,
model not pulled, tesseract missing) are exercised by letting the REAL
_vlm_ask / _ocr_tesseract run against faked transports.
"""
from __future__ import annotations

import base64
import io
import json
import sys
import urllib.error

import pytest

import jarvis.tools.builtin.vision_pack as vp
from jarvis.tools.base import Registry


FAKE_PNG = base64.b64decode(
    # 1x1 black PNG — small, valid, safe to feed Pillow
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def fresh_cfg(monkeypatch):
    """Reset the module config cache and point at a missing config file."""
    monkeypatch.setattr(vp, "_CFG", None)
    monkeypatch.setattr(vp, "CONFIG_PATH", vp.HOME / "no-such-config.yaml")


@pytest.fixture
def passthrough(monkeypatch):
    """Make _prepare_png / _downscale identity so byte plumbing is verifiable."""
    monkeypatch.setattr(vp, "_prepare_png", lambda raw, region: raw)
    monkeypatch.setattr(vp, "_downscale", lambda raw, max_w: raw)


# ------------------------------------------------------------- vision.screen

def test_screen_sends_question_and_image(fresh_cfg, passthrough, monkeypatch):
    captured = {}

    def fake_vlm(image_b64, question):
        captured["image_b64"] = image_b64
        captured["question"] = question
        return {"answer": "a red error dialog", "model": "moondream",
                "host": "http://127.0.0.1:11434"}

    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setattr(vp, "_vlm_ask", fake_vlm)

    out = vp.vision_screen({"question": "what error is on my screen?"})
    assert out["answer"] == "a red error dialog"
    assert captured["question"] == "what error is on my screen?"
    assert base64.b64decode(captured["image_b64"]) == FAKE_PNG
    assert "privacy" in out and "local" in out["privacy"].lower()


def test_screen_needs_question(fresh_cfg, monkeypatch):
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    out = vp.vision_screen({})
    assert "error" in out and "question" in out["error"]


def test_screen_capture_failure_is_honest(fresh_cfg, monkeypatch):
    monkeypatch.setattr(
        vp, "_capture_screen",
        lambda: (_ for _ in ()).throw(RuntimeError("no screen-capture backend available")))
    out = vp.vision_screen({"question": "what do you see?"})
    assert "error" in out and "no screen-capture backend" in out["error"]


def test_ollama_down_names_setup_steps(fresh_cfg, passthrough, monkeypatch):
    """Real _vlm_ask against a refused connection -> honest error, no fake answer."""
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setattr(vp, "_ollama_host", lambda: "http://127.0.0.1:9")  # dead port

    out = vp.vision_screen({"question": "what is on my screen?"})
    assert "error" in out
    assert "ollama pull moondream" in out["error"]
    assert "ollama serve" in out["error"]
    assert "answer" not in out  # never fabricate


def test_ollama_model_missing_names_pull(fresh_cfg, passthrough, monkeypatch):
    """Real _vlm_ask against a 404 -> honest 'pull the model' error."""
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {},
            io.BytesIO(b'{"error":"model \'moondream\' not found"}'))

    monkeypatch.setattr(vp.urllib.request, "urlopen", fake_urlopen)
    out = vp.vision_screen({"question": "describe the window"})
    assert "error" in out
    assert "ollama pull moondream" in out["error"]


# ------------------------------------------------------------- vision.camera

def test_camera_sends_question_and_frame(fresh_cfg, passthrough, monkeypatch):
    captured = {}

    def fake_cam():
        return FAKE_PNG

    def fake_vlm(image_b64, question):
        captured["image_b64"] = image_b64
        captured["question"] = question
        return {"answer": "a person at a desk", "model": "moondream",
                "host": "http://127.0.0.1:11434"}

    monkeypatch.setattr(vp, "_capture_camera", fake_cam)
    monkeypatch.setattr(vp, "_vlm_ask", fake_vlm)

    out = vp.vision_camera({"question": "describe what the camera sees"})
    assert out["answer"] == "a person at a desk"
    assert captured["question"] == "describe what the camera sees"
    assert base64.b64decode(captured["image_b64"]) == FAKE_PNG
    assert "privacy" in out


def test_camera_without_opencv_is_honest(fresh_cfg, monkeypatch):
    """Real _capture_camera with cv2 import blocked -> honest install hint."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("No module named 'cv2'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = vp.vision_camera({"question": "what do you see?"})
    assert "error" in out and "opencv" in out["error"].lower()
    assert "pip install opencv-python" in out["error"]


def test_camera_is_high_risk():
    assert vp.RISK_TABLE_ADDITIONS["vision.camera"] == ("high", False)


# ----------------------------------------------------------- vision.read_text

def test_read_text_returns_ocr(fresh_cfg, passthrough, monkeypatch):
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setattr(vp, "_ocr_tesseract", lambda img: "Hello OCR\nline two\n")
    out = vp.vision_read_text({})
    assert out["text"] == "Hello OCR\nline two\n"
    assert out["chars"] == len("Hello OCR\nline two\n")


def test_read_text_missing_tesseract_install_hint(fresh_cfg, passthrough, monkeypatch):
    """Real _ocr_tesseract with pytesseract blocked and no binary on PATH."""
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setitem(sys.modules, "pytesseract", None)  # force ImportError
    monkeypatch.setattr("shutil.which", lambda name: None)  # no binary
    out = vp.vision_read_text({})
    assert "error" in out
    assert "tesseract" in out["error"].lower()
    assert "install" in out["error"].lower()


def test_read_text_empty_result_note(fresh_cfg, passthrough, monkeypatch):
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setattr(vp, "_ocr_tesseract", lambda img: "   \n")
    out = vp.vision_read_text({})
    assert out["text"] == "   \n"
    assert "note" in out


# ------------------------------------------------------------------ region

def test_bad_region_is_honest(fresh_cfg, monkeypatch):
    """Real _prepare_png rejects malformed regions before touching Pillow."""
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    out = vp.vision_read_text({"region": "not,a,region"})
    assert "error" in out and "x,y,w,h" in out["error"]


def test_region_passthrough_bytes(fresh_cfg, monkeypatch):
    """With passthrough identity, region arg is echoed back on success."""
    monkeypatch.setattr(vp, "_prepare_png", lambda raw, region: raw)
    monkeypatch.setattr(vp, "_downscale", lambda raw, max_w: raw)
    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setattr(vp, "_ocr_tesseract", lambda img: "text")
    out = vp.vision_read_text({"region": "10,20,300,400"})
    assert out["region"] == "10,20,300,400"


# ------------------------------------------------------------------- config

def test_config_absent_uses_defaults(fresh_cfg):
    assert vp._vision_cfg() == {}
    assert vp._ollama_host() == "http://127.0.0.1:11434"
    assert vp._vision_model() == "moondream"


def test_config_values_respected(tmp_path, monkeypatch):
    cfg = tmp_path / "tools_config.yaml"
    cfg.write_text("vision:\n  ollama_host: http://192.168.1.5:11434\n  model: llava\n")
    monkeypatch.setattr(vp, "_CFG", None)
    monkeypatch.setattr(vp, "CONFIG_PATH", cfg)
    assert vp._ollama_host() == "http://192.168.1.5:11434"
    assert vp._vision_model() == "llava"


# ------------------------------------------------------------- registration

def test_register_and_risk_table():
    reg = Registry()
    vp.register(reg)
    names = sorted(reg.tools.keys())
    assert names == ["vision.camera", "vision.read_text", "vision.screen"]
    for n in names:
        assert reg.tools[n].risk == vp.RISK_TABLE_ADDITIONS[n][0]
        assert reg.tools[n].needs_network is False
    assert vp.RISK_TABLE_ADDITIONS == {
        "vision.screen": ("medium", False),
        "vision.camera": ("high", False),
        "vision.read_text": ("low", False),
    }
    assert len(vp.TOOL_DEFS) == 3
    for spec in vp.TOOL_DEFS:
        assert set(spec) == {"name", "description", "handler",
                             "risk", "needs_network", "schema"}
        assert callable(spec["handler"])


def test_descriptions_state_privacy():
    descs = {s["name"]: s["description"] for s in vp.TOOL_DEFS}
    for name, d in descs.items():
        assert "local" in d.lower(), name
        assert "cloud" in d.lower(), name
    assert "WEBCAM" in descs["vision.camera"].upper() or "webcam" in descs["vision.camera"].lower()
    assert "turns the camera on" in descs["vision.camera"].lower()


def test_handlers_never_raise(fresh_cfg, monkeypatch):
    """Handlers must return {"error": ...}, never raise, even on weird input."""
    monkeypatch.setattr(vp, "_capture_screen",
                        lambda: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(vp, "_capture_camera",
                        lambda: (_ for _ in ()).throw(OSError("boom")))
    for tool, args in (("vision.screen", {"question": "x"}),
                       ("vision.camera", {"question": "x"}),
                       ("vision.read_text", {}),
                       ("vision.screen", None),
                       ("vision.camera", "nonsense")):
        try:
            out = {"vision.screen": vp.vision_screen,
                   "vision.camera": vp.vision_camera,
                   "vision.read_text": vp.vision_read_text}[tool](args or {})
        except Exception as e:  # pragma: no cover
            pytest.fail(f"{tool} raised {e!r}")
        assert isinstance(out, dict), tool


def test_vlm_payload_shape(fresh_cfg, passthrough, monkeypatch):
    """The ollama request carries the question text AND the base64 image."""
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"message": {"content": "looks fine"}}).encode()

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["payload"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(vp, "_capture_screen", lambda: FAKE_PNG)
    monkeypatch.setattr(vp.urllib.request, "urlopen", fake_urlopen)
    out = vp.vision_screen({"question": "is there an error dialog?"})
    assert out["answer"] == "looks fine"
    assert seen["url"].endswith("/api/chat")
    msg = seen["payload"]["messages"][0]
    assert msg["content"] == "is there an error dialog?"
    assert msg["images"] == [base64.b64encode(FAKE_PNG).decode()]
    assert seen["payload"]["model"] == "moondream"
    assert seen["payload"]["stream"] is False
