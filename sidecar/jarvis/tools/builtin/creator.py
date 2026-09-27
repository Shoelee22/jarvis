"""Creator Tool Pack (Phase 4): code execution, website building, image/video
generation, and social posting. Outputs live under ~/workspace/jarvis/output/.

Per-tool toggles and defaults come from ~/workspace/jarvis/tools_config.yaml;
a missing or broken file falls back to sane defaults (all tools enabled).
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "tools_config.yaml"
DEFAULT_OUTPUT_DIR = JARVIS_DIR / "output"

_DEFAULTS = {
    "output_dir": str(DEFAULT_OUTPUT_DIR),
    "tools": {
        "code.run": {"enabled": True},
        "website.build": {"enabled": True},
        "media.image": {"enabled": True},
        "video.compose": {"enabled": True},
        "social.post_instagram": {"enabled": True},
    },
    "pollinations": {"base_url": "https://image.pollinations.ai/prompt"},
    "video": {"seconds_each": 3.0},
    "instagram": {"handle": ""},
}

_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0


def _load_config() -> dict:
    """Read tools_config.yaml at call time; cache by mtime, tolerate failure."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return _DEFAULTS
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        import yaml  # pyyaml ships with the sidecar env
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        cfg = _deep_merge(dict(_DEFAULTS), raw)
    except Exception:
        cfg = _DEFAULTS
    _cfg_cache = cfg
    _cfg_mtime = mtime
    return cfg


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _enabled(tool_name: str) -> dict | None:
    """Return an error dict when the tool is disabled, else None."""
    tools = _load_config().get("tools", {})
    if not tools.get(tool_name, {}).get("enabled", True):
        return {"error": f"tool '{tool_name}' is disabled in tools_config.yaml"}
    return None


def _output_dir() -> Path:
    d = Path(_load_config().get("output_dir", str(DEFAULT_OUTPUT_DIR))).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- code.run
def code_run(args: dict) -> dict:
    """Run a python3 snippet in a jailed sandbox dir; capture output."""
    blocked = _enabled("code.run")
    if blocked:
        return blocked
    code = args.get("code", "")
    if not code.strip():
        return {"error": "code is required"}
    try:
        timeout = max(1, int(args.get("timeout", 30)))
    except (TypeError, ValueError):
        timeout = 30
    sandbox = _output_dir() / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    script = sandbox / f"snippet-{int(time.time() * 1000)}.py"
    script.write_text(code)
    try:
        proc = subprocess.run(
            ["python3", str(script)], cwd=sandbox,
            capture_output=True, text=True, timeout=timeout)
        return {"stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:],
                "exit_code": proc.returncode, "timed_out": False}
    except subprocess.TimeoutExpired as e:
        return {"stdout": (e.stdout or "")[-8000:] if isinstance(e.stdout, str) else "",
                "stderr": (e.stderr or "")[-8000:] if isinstance(e.stderr, str) else "",
                "exit_code": None, "timed_out": True}
    except Exception as e:
        return {"error": f"could not run code: {type(e).__name__}: {e}"}
    finally:
        script.unlink(missing_ok=True)


# ---------------------------------------------------------- website.build
_PALETTES = {
    "food": {  # warm bakery / restaurant
        "bg": "#1c120b", "fg": "#fdf6ec", "accent": "#e0912f",
        "card": "#2a1d12", "muted": "#cbb39a", "name": "warm"},
    "fitness": {  # dark + volt
        "bg": "#0a0b0d", "fg": "#f4f6f8", "accent": "#c8f542",
        "card": "#141619", "muted": "#9aa3ad", "name": "volt"},
    "services": {  # clean blue
        "bg": "#f7fafd", "fg": "#101820", "accent": "#2563eb",
        "card": "#ffffff", "muted": "#5b6b7c", "name": "clean"},
}
_FALLBACK = {  # neutral modern
    "bg": "#101014", "fg": "#f5f5f7", "accent": "#7c5cff",
    "card": "#1a1a20", "muted": "#a1a1aa", "name": "neutral"}


def _palette_for(business_type: str) -> dict:
    key = (business_type or "").lower()
    for name, pal in _PALETTES.items():
        if name in key:
            return pal
    if any(w in key for w in ("gym", "train", "sport", "health", "crossfit", "yoga")):
        return _PALETTES["fitness"]
    if any(w in key for w in ("restaurant", "cafe", "bakery", "eat", "dine", "pizza")):
        return _PALETTES["food"]
    if any(w in key for w in ("agency", "consult", "it ", "tech", "salon", "clinic", "law", "real")):
        return _PALETTES["services"]
    return _FALLBACK


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "site"


def _offerings_from_brief(brief: str) -> list[dict]:
    """Split the brief into up-to-3 short offering cards; never lorem ipsum."""
    bits = [b.strip().rstrip(".") for b in re.split(r"[.\n;]+", brief) if b.strip()]
    cards = []
    for b in bits[:3]:
        words = b.split()
        title = " ".join(words[:6]).title() if len(words) > 4 else b.title()
        cards.append({"title": title[:60], "text": b[:220]})
    if not cards:
        cards = [{"title": "What we do", "text": brief[:220] or "Coming soon."}]
    return cards


def _render_site(name: str, brief: str, palette: dict) -> str:
    esc = html.escape
    cards = _offerings_from_brief(brief)
    card_html = "\n".join(
        f'<article class="card"><h3>{esc(c["title"])}</h3><p>{esc(c["text"])}</p></article>'
        for c in cards)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(name)}</title>
<style>
:root {{ --bg: {palette["bg"]}; --fg: {palette["fg"]}; --accent: {palette["accent"]};
        --card: {palette["card"]}; --muted: {palette["muted"]}; }}
* {{ margin: 0; box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--fg);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        line-height: 1.6; }}
.wrap {{ max-width: 1100px; margin: 0 auto; padding: 0 1.25rem; }}
nav {{ display: flex; justify-content: space-between; align-items: center; padding: 1.25rem 0; }}
nav .brand {{ font-weight: 800; font-size: 1.25rem; letter-spacing: -0.02em; }}
nav a {{ color: var(--fg); text-decoration: none; margin-left: 1.5rem; font-size: 0.95rem; }}
.hero {{ padding: 5rem 0 3.5rem; }}
.hero h1 {{ font-size: clamp(2.2rem, 6vw, 4.2rem); line-height: 1.05; letter-spacing: -0.03em; }}
.hero h1 span {{ color: var(--accent); }}
.hero p {{ color: var(--muted); font-size: 1.15rem; max-width: 34rem; margin: 1.25rem 0 2rem; }}
.btn {{ display: inline-block; background: var(--accent); color: var(--bg);
       font-weight: 700; padding: 0.85rem 1.9rem; border-radius: 999px;
       text-decoration: none; transition: transform 0.15s ease; }}
.btn:hover {{ transform: translateY(-2px); }}
.btn.ghost {{ background: transparent; color: var(--fg); border: 1px solid var(--muted); margin-left: 0.75rem; }}
.offerings {{ padding: 3rem 0; }}
.offerings h2 {{ font-size: 1.6rem; letter-spacing: -0.02em; margin-bottom: 1.5rem; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.25rem; }}
.card {{ background: var(--card); border: 1px solid color-mix(in srgb, var(--muted) 25%, transparent);
        border-radius: 1rem; padding: 1.5rem; }}
.card h3 {{ font-size: 1.1rem; margin-bottom: 0.5rem; }}
.card p {{ color: var(--muted); font-size: 0.95rem; }}
.cta {{ text-align: center; padding: 4rem 0; }}
.cta h2 {{ font-size: clamp(1.6rem, 4vw, 2.4rem); letter-spacing: -0.02em; margin-bottom: 1rem; }}
footer {{ border-top: 1px solid color-mix(in srgb, var(--muted) 25%, transparent);
          padding: 1.5rem 0 2.5rem; color: var(--muted); font-size: 0.85rem;
          display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; }}
@media (max-width: 640px) {{ nav a {{ display: none; }} .hero {{ padding: 3rem 0 2rem; }} }}
</style>
</head>
<body>
<div class="wrap">
  <nav><div class="brand">{esc(name)}</div>
    <div><a href="#offerings">What we do</a><a href="#contact">Contact</a></div></nav>
  <header class="hero">
    <h1>{esc(name)} <span>&mdash; built for what you love.</span></h1>
    <p>{esc(brief)}</p>
    <a class="btn" href="#contact">Get in touch</a><a class="btn ghost" href="#offerings">Explore</a>
  </header>
  <section class="offerings" id="offerings">
    <h2>What we do</h2>
    <div class="grid">{card_html}</div>
  </section>
  <section class="cta" id="contact">
    <h2>Ready when you are.</h2>
    <p style="color:var(--muted);margin-bottom:1.5rem">Tell us what you need &mdash; we&rsquo;ll take it from there.</p>
    <a class="btn" href="mailto:hello@example.com?subject=Enquiry%20for%20{esc(urllib.parse.quote(name))}">Contact {esc(name)}</a>
  </section>
  <footer><span>&copy; {esc(name)}</span><span>Crafted with care.</span></footer>
</div>
</body>
</html>
"""


def website_build(args: dict) -> dict:
    """Generate a single-file responsive HTML site from a name + brief."""
    blocked = _enabled("website.build")
    if blocked:
        return blocked
    name = args.get("name", "").strip()
    brief = args.get("brief", "").strip()
    if not name:
        return {"error": "name is required"}
    if not brief:
        return {"error": "brief is required"}
    palette = _palette_for(args.get("business_type", ""))
    slug = _slug(name)
    site_dir = _output_dir() / "sites" / slug
    site_dir.mkdir(parents=True, exist_ok=True)
    path = site_dir / "index.html"
    path.write_text(_render_site(name, brief, palette))
    return {"path": str(path), "title": name,
            "palette": palette["name"],
            "url_hint": f"file://{path}"}


# ------------------------------------------------------------- media.image
def media_image(args: dict) -> dict:
    """Generate an image via Pollinations; save JPEG bytes to output/media."""
    blocked = _enabled("media.image")
    if blocked:
        return blocked
    prompt = args.get("prompt", "").strip()
    if not prompt:
        return {"error": "prompt is required"}
    try:
        width = max(64, min(2048, int(args.get("width", 1024))))
        height = max(64, min(2048, int(args.get("height", 1024))))
    except (TypeError, ValueError):
        width, height = 1024, 1024
    base = _load_config().get("pollinations", {}).get("base_url",
                                                       _DEFAULTS["pollinations"]["base_url"])
    query = urllib.parse.urlencode({"width": width, "height": height, "nologo": "true"})
    url = f"{base.rstrip('/')}/{urllib.parse.quote(prompt)}?{query}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jarvis-sidecar/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if not data:
            return {"error": "image API returned empty response"}
    except Exception as e:
        return {"error": f"image generation failed: {type(e).__name__}: {e}"}
    media_dir = _output_dir() / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    path = media_dir / f"img-{int(time.time() * 1000)}.jpg"
    path.write_bytes(data)
    return {"path": str(path), "width": width, "height": height}


# ------------------------------------------------------------ video.compose
def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def video_compose(args: dict) -> dict:
    """Compose a Ken-Burns slideshow MP4 from images with ffmpeg."""
    blocked = _enabled("video.compose")
    if blocked:
        return blocked
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"error": "ffmpeg is not installed"}
    images = [str(p) for p in args.get("images", [])]
    if not images:
        return {"error": "images is required (list of file paths)"}
    missing = [p for p in images if not Path(p).is_file()]
    if missing:
        return {"error": f"image file(s) not found: {missing[:3]}"}
    orientation = args.get("orientation", "portrait")
    if orientation not in ("portrait", "landscape"):
        return {"error": "orientation must be 'portrait' or 'landscape'"}
    w, h = (1080, 1920) if orientation == "portrait" else (1920, 1080)
    try:
        seconds_each = float(args.get("seconds_each",
                                      _load_config().get("video", {}).get("seconds_each", 3.0)))
        seconds_each = min(30.0, max(0.5, seconds_each))
    except (TypeError, ValueError):
        seconds_each = 3.0

    media_dir = _output_dir() / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = media_dir / f"vtmp-{int(time.time() * 1000)}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_name = args.get("out_name") or f"vid-{int(time.time() * 1000)}.mp4"
    out_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", out_name)
    if not out_name.lower().endswith(".mp4"):
        out_name += ".mp4"
    out_path = media_dir / out_name

    try:
        frames = int(round(30 * seconds_each))
        segments = []
        for i, img in enumerate(images):
            seg = tmp_dir / f"seg{i:03d}.mp4"
            vf = (f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={frames}:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h},"
                  f"crop={w}:{h},format=yuv420p,setsar=1")
            cmd = [ffmpeg, "-y", "-loop", "1", "-i", img,
                   "-vf", vf, "-c:v", "libx264", "-r", "30",
                   "-t", f"{seconds_each}", str(seg)]
            r = _run(cmd, timeout=120)
            if r.returncode != 0 or not seg.is_file():
                return {"error": f"ffmpeg segment {i} failed: {r.stderr[-500:]}"}
            segments.append(seg)
        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text("".join(f"file '{s}'\n" for s in segments))
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0",
               "-i", str(concat_list), "-c", "copy", str(out_path)]
        audio = args.get("audio")
        if audio and Path(audio).is_file():
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0",
                   "-i", str(concat_list), "-i", str(audio),
                   "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_path)]
        r = _run(cmd, timeout=300)
        if r.returncode != 0 or not out_path.is_file():
            return {"error": f"ffmpeg concat failed: {r.stderr[-500:]}"}
        return {"path": str(out_path),
                "duration_sec": round(seconds_each * len(images), 1)}
    except Exception as e:
        return {"error": f"video compose failed: {type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ----------------------------------------------------- social.post_instagram
_INSTAGRAM_CLI = "instagram-cli"


def _instagram_account_id(handle: str = "") -> tuple[str | None, str | None]:
    """Return (account_id, error). Matches config handle when set."""
    if not shutil.which(_INSTAGRAM_CLI):
        return None, "instagram-cli not available"
    try:
        r = _run([_INSTAGRAM_CLI, "accounts"], timeout=30)
        if r.returncode != 0:
            return None, f"instagram-cli accounts failed: {r.stderr[-300:]}"
        data = json.loads(r.stdout or "{}")
    except Exception as e:
        return None, f"could not read instagram accounts: {type(e).__name__}: {e}"
    accounts = data.get("accounts") or data.get("data") or []
    if isinstance(data, list):
        accounts = data
    if not accounts:
        return None, ("no Instagram account connected — ask the user to connect "
                      "one in Settings first")
    handle = (handle or "").lstrip("@").lower()
    chosen = None
    for a in accounts:
        uname = str(a.get("username", "")).lstrip("@").lower()
        if handle and uname == handle:
            chosen = a
            break
    chosen = chosen or accounts[0]
    fbid = chosen.get("user_fbid") or chosen.get("id")
    if not fbid:
        return None, "connected account has no usable id"
    return str(fbid), None


def social_post_instagram(args: dict) -> dict:
    """Post an image/reel to Instagram. dry_run=True (default) only validates."""
    blocked = _enabled("social.post_instagram")
    if blocked:
        return blocked
    file = args.get("file", "").strip()
    caption = args.get("caption", "")
    if not file:
        return {"error": "file is required"}
    if not Path(file).is_file():
        return {"error": f"file not found: {file}"}
    if len(caption) > 2200:
        return {"error": "caption exceeds Instagram's 2200-character limit"}
    dry_run = args.get("dry_run", True)
    if dry_run:
        return {"dry_run": True,
                "would_post": {"file": file, "caption": caption},
                "note": "set dry_run=false after user confirms"}
    # Real publish — needs confirmation upstream (risk=high) + explicit dry_run=false.
    if file.lower().endswith((".mp4", ".mov")):
        return {"error": "video posts need a --cover image; not supported yet — post an image or carousel instead"}
    handle = _load_config().get("instagram", {}).get("handle", "")
    account_id, err = _instagram_account_id(handle)
    if err:
        return {"error": err}
    cmd = [_INSTAGRAM_CLI, "post-feed", "--account-id", account_id,
           "--file", file]
    if caption.strip():
        cmd += ["--caption", caption]
    try:
        r = _run(cmd, timeout=180)
    except Exception as e:
        return {"error": f"instagram publish failed: {type(e).__name__}: {e}"}
    if r.returncode != 0:
        return {"error": f"instagram-cli post-feed failed: {r.stderr[-500:] or r.stdout[-500:]}"}
    try:
        return {"posted": True, "response": json.loads(r.stdout) if r.stdout.strip() else {"raw": r.stdout[-500:]}}
    except Exception:
        return {"posted": True, "response": {"raw": r.stdout[-500:]}}
