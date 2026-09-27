"""Vision Tool Pack (Phase 12): eyes for JARVIS.

Three tools:

  - ``vision.screen {question, region?}`` — screenshot the display and ask a
    local vision-language model (VLM) a question about it, e.g. "what error
    is on my screen?" or "summarize this window".
  - ``vision.camera {question}`` — capture one frame from the webcam and ask
    the VLM about it. TURNS ON THE USER'S WEBCAM — high risk.
  - ``vision.read_text {region?}`` — pure-local OCR of a screenshot via
    tesseract; returns the extracted text, no VLM involved.

PRIVACY (stated plainly so agents and users both see it):
  Images NEVER leave the machine except to the user's OWN configured ollama
  host, which defaults to loopback http://127.0.0.1:11434. There are no cloud
  vision APIs in this pack — no OpenAI, no Claude vision endpoint, nothing
  external. If ollama is not reachable (or no vision model is pulled), the
  handlers return an honest {"error": ...} with exact setup steps and NEVER
  fake a description of the screen.

Backend gating (all lazy, all optional — zero new hard dependencies):
  - screenshot:  pyautogui -> Windows ctypes BitBlt -> Linux CLI tools
                 (reuses gui_pack's capture helpers; headless -> honest error)
  - VLM:         ollama at vision.ollama_host (default 127.0.0.1:11434),
                 model vision.model (default "moondream"; "llava" works too)
  - camera:      opencv (cv2.VideoCapture(0), one frame)
  - OCR:         pytesseract if importable, else the tesseract binary on PATH

Optional config (works with tools_config.yaml ABSENT — defaults kick in):
    vision:
      ollama_host: http://127.0.0.1:11434   # only host ever contacted
      model: moondream                        # ollama vision model tag
      max_width: 1280                         # downscale captures to this
      camera_index: 0

Every handler takes a dict and returns a dict, NEVER raises.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = HOME / "workspace" / "jarvis" / "tools_config.yaml"

_DEFAULT_HOST = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "moondream"
_DEFAULT_MAX_WIDTH = 1280
_VLM_TIMEOUT_S = 120

_CFG = None  # module-level cache of the vision section


def _vision_cfg() -> dict:
    """Read the optional `vision:` section of tools_config.yaml; {} if absent."""
    global _CFG
    if _CFG is not None:
        return _CFG
    cfg: dict = {}
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        # tolerate missing pyyaml — fall back to a tiny 2-level parse
        try:
            import yaml  # pyyaml ships with the sidecar env
            data = yaml.safe_load(raw) or {}
        except Exception:
            data = _mini_yaml(raw)
        if isinstance(data.get("vision"), dict):
            cfg = dict(data["vision"])
    except OSError:
        cfg = {}
    except Exception:
        cfg = {}
    _CFG = cfg
    return _CFG


def _mini_yaml(text: str) -> dict:
    """Bare-bones fallback parser for a simple `vision:` block (no pyyaml)."""
    out: dict = {}
    cur: dict | None = None
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            cur = {}
            out[line.rstrip()[:-1]] = cur
            continue
        if cur is not None and ":" in line:
            k, v = line.split(":", 1)
            v = v.strip().strip("\"'")
            cur[k.strip()] = v if not v.isdigit() else int(v)
    return out


def _ollama_host() -> str:
    return str(_vision_cfg().get("ollama_host", _DEFAULT_HOST)).rstrip("/")


def _vision_model() -> str:
    return str(_vision_cfg().get("model", _DEFAULT_MODEL))


def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _privacy_note(host: str) -> str:
    return (f"Privacy: this image stayed local — sent only to your own ollama "
            f"host ({host}); no cloud vision API was contacted.")


def _ollama_unreachable_hint(host: str) -> str:
    return (
        f"Vision backend unreachable at {host}. To enable it: "
        "1) start ollama with `ollama serve`, "
        "2) pull a vision model with `ollama pull moondream` "
        "(or `ollama pull llava`), "
        "3) re-run the tool. "
        "Host and model are configurable as vision.ollama_host / vision.model "
        "in ~/workspace/jarvis/tools_config.yaml. "
        "Images are sent only to your own ollama host — never to a cloud API."
    )


def _model_missing_hint(host: str, model: str) -> str:
    return (
        f"The vision model '{model}' is not available on your ollama host ({host}). "
        f"Pull it with `ollama pull {model}` (or `ollama pull moondream`), "
        "then re-run the tool. Images are sent only to your own ollama host — "
        "never to a cloud API."
    )


# ------------------------------------------------------- capture backends
#
# All backend I/O lives behind three small mockable functions:
#   _capture_screen() -> bytes (PNG, or BMP on Windows without Pillow)
#   _capture_camera() -> bytes (PNG)
#   _vlm_ask(image_b64, question) -> dict {"answer", "model", "host"}
# They raise RuntimeError with honest messages; handlers catch everything.

def _capture_screen() -> bytes:
    """Return a screenshot as image bytes. Raises RuntimeError when impossible."""
    from . import gui_pack  # sibling pack: reuse its proven capture helpers

    fd, tmp = tempfile.mkstemp(prefix="jarvis-vision-", suffix=".png")
    os.close(fd)
    bmp_path = tmp + ".bmp"
    try:
        pag = gui_pack._pyautogui()
        if pag is not None:
            gui_pack._screenshot_pyautogui(tmp, pag)
            data = Path(tmp).read_bytes()
        elif gui_pack._is_windows():
            gui_pack._screenshot_windows_ctypes(bmp_path)
            data = Path(bmp_path).read_bytes()
        elif gui_pack._is_linux() and gui_pack._has_display():
            gui_pack._screenshot_linux(tmp)
            data = Path(tmp).read_bytes()
        else:
            raise RuntimeError(
                "no screen-capture backend available on this machine "
                "(tried pyautogui, Windows BitBlt, Linux CLI tools). "
                "Headless session or missing dependencies.")
        return data
    finally:
        for p in (tmp, bmp_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _capture_camera() -> bytes:
    """Return one webcam frame as PNG bytes. Raises RuntimeError when impossible."""
    try:
        import cv2
    except Exception:
        raise RuntimeError(
            "opencv (cv2) is not installed — camera capture needs it. "
            "Install with: pip install opencv-python")
    index = _vision_cfg().get("camera_index", 0)
    cap = cv2.VideoCapture(int(index))
    try:
        if not cap.isOpened():
            raise RuntimeError(
                f"could not open camera index {index} — no webcam found or "
                "it is already in use by another app.")
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(
                f"camera index {index} opened but produced no frame — "
                "the webcam may be covered, disabled, or busy.")
        png_ok, buf = cv2.imencode(".png", frame)
        if not png_ok:
            raise RuntimeError("failed to encode the camera frame as PNG.")
        return bytes(buf)
    finally:
        cap.release()


def _prepare_png(raw: bytes, region: str | None) -> bytes:
    """Crop `region` ("x,y,w,h") and downscale; needs Pillow for either."""
    if region is None:
        max_w = int(_vision_cfg().get("max_width", _DEFAULT_MAX_WIDTH))
        return _downscale(raw, max_w)
    try:
        x, y, w, h = (int(v) for v in str(region).split(","))
    except Exception:
        raise RuntimeError(
            f"bad region {region!r}: expected \"x,y,w,h\" of non-negative integers.")
    if min(x, y, w, h) < 0 or w == 0 or h == 0:
        raise RuntimeError(
            f"bad region {region!r}: x,y must be >= 0 and w,h > 0.")
    try:
        from PIL import Image
    except Exception:
        raise RuntimeError(
            "region cropping needs Pillow: pip install pillow "
            "(re-run without `region` to use the full screen).")
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = img.crop((x, y, x + w, y + h))
    max_w = int(_vision_cfg().get("max_width", _DEFAULT_MAX_WIDTH))
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _downscale(raw: bytes, max_w: int) -> bytes:
    """Downscale to max_w if Pillow is available; otherwise pass through."""
    try:
        from PIL import Image
    except Exception:
        return raw
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return raw  # not decodable (e.g. raw BMP without Pillow) — pass on
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _vlm_ask(image_b64: str, question: str) -> dict:
    """Ask the local ollama VLM. Returns {"answer","model","host"}.

    Raises RuntimeError with exact setup steps when ollama is unreachable
    or the model is not pulled — NEVER returns a fabricated description.
    """
    host = _ollama_host()
    model = _vision_model()
    payload = {
        "model": model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": str(question),
            "images": [image_b64],
        }],
    }
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_VLM_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300].lower()
        except Exception:
            detail = ""
        if e.code == 404 or "model" in detail and "not found" in detail:
            raise RuntimeError(_model_missing_hint(host, model))
        raise RuntimeError(f"ollama host {host} returned HTTP {e.code}: {detail[:200]}")
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
        raise RuntimeError(f"{_ollama_unreachable_hint(host)} (detail: {e})")
    msg = (body.get("message") or {}).get("content", "")
    if not str(msg).strip():
        raise RuntimeError(
            f"ollama model '{model}' returned an empty answer — "
            "try a different question or model (e.g. `ollama pull llava`).")
    return {"answer": str(msg).strip(), "model": model, "host": host}


# ------------------------------------------------------------------ OCR

def _ocr_tesseract(image_bytes: bytes) -> str:
    """Extract text via pytesseract or the tesseract binary. Raises RuntimeError."""
    # path 1: pytesseract (needs Pillow to decode the image)
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        text = pytesseract.image_to_string(img)
        return str(text or "")
    except ImportError:
        pass
    except Exception as e:
        raise RuntimeError(f"pytesseract failed: {e}")
    # path 2: tesseract binary on PATH, PNG on stdin, text on stdout
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise RuntimeError(
            "tesseract OCR is not installed. Install it with one of: "
            "Windows: `winget install --id UB-Mannheim.TesseractOCR` "
            "(then ensure tesseract.exe is on PATH), "
            "Linux: `sudo apt install tesseract-ocr`, "
            "or Python: `pip install pytesseract pillow` "
            "(still needs the tesseract binary). Then re-run vision.read_text.")
    try:
        r = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "6"],
            input=image_bytes, capture_output=True, timeout=60)
    except Exception as e:
        raise RuntimeError(f"tesseract run failed: {e}")
    if r.returncode != 0:
        raise RuntimeError(
            f"tesseract exited {r.returncode}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return r.stdout.decode("utf-8", "replace")


# -------------------------------------------------------------- handlers

def vision_screen(args: dict) -> dict:
    """Screenshot the screen and ask the local VLM about it.

    {question: string, region?: "x,y,w,h"}. Images stay local: they are sent
    only to the user's own ollama host (default http://127.0.0.1:11434).
    """
    try:
        question = str(args.get("question") or "").strip()
        if not question:
            return {"error": "vision.screen needs a 'question', e.g. "
                             "'what error is on my screen?'"}
        raw = _capture_screen()
        png = _prepare_png(raw, args.get("region"))
        b64 = base64.b64encode(png).decode("ascii")
        res = _vlm_ask(b64, question)
        res["privacy"] = _privacy_note(res["host"])
        if args.get("region"):
            res["region"] = str(args["region"])
        return res
    except Exception as e:
        return _fail("vision.screen", e)


def vision_camera(args: dict) -> dict:
    """Capture ONE frame from the webcam and ask the local VLM about it.

    {question: string}. TURNS ON THE USER'S WEBCAM. Images stay local: sent
    only to the user's own ollama host (default http://127.0.0.1:11434).
    """
    try:
        question = str(args.get("question") or "").strip()
        if not question:
            return {"error": "vision.camera needs a 'question', e.g. "
                             "'describe what the camera sees'"}
        raw = _capture_camera()
        png = _downscale(raw, int(_vision_cfg().get("max_width", _DEFAULT_MAX_WIDTH)))
        b64 = base64.b64encode(png).decode("ascii")
        res = _vlm_ask(b64, question)
        res["privacy"] = _privacy_note(res["host"])
        return res
    except Exception as e:
        return _fail("vision.camera", e)


def vision_read_text(args: dict) -> dict:
    """OCR the screen (or a region) locally via tesseract. {region?: "x,y,w,h"}."""
    try:
        raw = _capture_screen()
        png = _prepare_png(raw, args.get("region"))
        text = _ocr_tesseract(png)
        out: dict = {"text": text, "chars": len(text)}
        if args.get("region"):
            out["region"] = str(args["region"])
        if not text.strip():
            out["note"] = "OCR found no text in this capture."
        return out
    except Exception as e:
        return _fail("vision.read_text", e)


# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "vision.screen",
     "description": ("Take a screenshot and ask a question about it "
                     "('what error is on my screen?', 'summarize this window'). "
                     "Answered by a VISION MODEL RUNNING LOCALLY via your own "
                     "ollama host (default http://127.0.0.1:11434). Images stay "
                     "local: they are sent ONLY to your configured ollama host "
                     "and NEVER to any cloud vision API. If ollama is not "
                     "reachable or no vision model is pulled, this returns an "
                     "honest error with setup steps — it never fakes a "
                     "description. Optional region: \"x,y,w,h\"."),
     "handler": vision_screen, "risk": "medium", "needs_network": False,
     "schema": {"question": "string", "region?": "string"}},
    {"name": "vision.camera",
     "description": ("Capture ONE still frame from the user's WEBCAM (turns the "
                     "camera on for a single shot) and ask a question about it. "
                     "Answered by a VISION MODEL RUNNING LOCALLY via your own "
                     "ollama host (default http://127.0.0.1:11434). Images stay "
                     "local: they are sent ONLY to your configured ollama host "
                     "and NEVER to any cloud vision API. Needs opencv "
                     "(pip install opencv-python) and ollama + a vision model "
                     "(`ollama serve`, `ollama pull moondream`); otherwise "
                     "returns an honest error — it never fakes what the camera "
                     "sees."),
     "handler": vision_camera, "risk": "high", "needs_network": False,
     "schema": {"question": "string"}},
    {"name": "vision.read_text",
     "description": ("Extract text from the screen (or a region \"x,y,w,h\") "
                     "using LOCAL OCR (tesseract) — no VLM, no network at all, "
                     "never touches any cloud API. "
                     "Returns the extracted text. If tesseract is not "
                     "installed, returns an honest error with install steps."),
     "handler": vision_read_text, "risk": "low", "needs_network": False,
     "schema": {"region?": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(vision_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "vision.screen": ("medium", False),
    "vision.camera": ("high", False),
    "vision.read_text": ("low", False),
}


def register(reg) -> None:
    """Wire the three Vision tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
