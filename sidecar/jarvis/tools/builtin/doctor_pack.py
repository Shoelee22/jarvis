"""Phase 8 Doctor pack: capability health report + human-readable capability summary.

Handlers take dict in, return dict out, NEVER raise. All optional imports are
defensive: a missing sibling pack (gui, browser, pc, telegram, gmail, studio,
plugins) becomes an entry with status "unavailable" and a not_installed note.
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
from pathlib import Path

JARVIS_DIR = Path.home() / "workspace" / "jarvis"
PHASE8_CONFIG = JARVIS_DIR / "dynamic" / "tools_config.phase8.yaml"

BUILTIN_PREFIX = "jarvis.tools.builtin."

# ---------------------------------------------------------------------------
# defensive import helpers
# ---------------------------------------------------------------------------

_NOT_INSTALLED = object()


def _safe_import(modname):
    """Import defensively. Returns (module | None, note, is_missing)."""
    try:
        return importlib.import_module(modname), "", False
    except ImportError:
        return None, "pack not installed", True
    except Exception as e:  # never crash on a broken pack
        return None, f"pack import failed: {type(e).__name__}: {e}", False


def _count_tools(mod):
    """Best-effort tool count across pack conventions (TOOL_DEFS/TOOLS/REGISTRATIONS)."""
    for attr in ("TOOL_DEFS", "TOOLS", "REGISTRATIONS"):
        seq = getattr(mod, attr, None)
        if seq is not None:
            try:
                return len(seq)
            except TypeError:
                pass
    return None


def _entry(pack, status, tools, detail):
    return {"pack": pack, "status": status, "tools": tools, "detail": detail}


def _phase8_config():
    """Read the Phase-8 YAML config; empty dict on any failure."""
    try:
        text = PHASE8_CONFIG.read_text()
    except OSError:
        return {}
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# new-pack probes (sibling packs built in parallel Phase 8)
# ---------------------------------------------------------------------------

def _probe_gui():
    mod, note, missing = _safe_import(BUILTIN_PREFIX + "gui_pack")
    if mod is None:
        return _entry("gui", "unavailable", None, note)
    tools = _count_tools(mod)
    backends = []
    sys = platform.system()
    if sys == "Windows":
        backends.append("ctypes/user32")
    else:
        try:
            import pyautogui  # noqa: F401
            backends.append("pyautogui")
        except Exception:
            pass
        if shutil.which("xdotool"):
            backends.append("xdotool")
    display = bool(os.environ.get("DISPLAY"))
    if sys == "Windows" or (display and backends):
        return _entry("gui", "live", tools,
                       "backends: " + ", ".join(backends))
    if sys != "Windows" and not display:
        return _entry("gui", "unavailable", tools, "no display server (DISPLAY unset)")
    return _entry("gui", "needs_package", tools,
                   "install a backend: pip install pyautogui or apt install xdotool")


def _probe_browser():
    mod, note, missing = _safe_import(BUILTIN_PREFIX + "browser_pack")
    if mod is None:
        return _entry("browser", "unavailable", None, note)
    tools = _count_tools(mod)
    try:
        import playwright  # noqa: F401
    except Exception:
        return _entry("browser", "needs_package", tools,
                       "pip install playwright, then: playwright install chromium")
    # playwright module exists; check a browser binary is actually downloaded
    cache = Path.home() / ".cache" / "ms-playwright"
    browsers = sorted(p.name for p in cache.glob("chromium-*")) if cache.is_dir() else []
    if not browsers:
        return _entry("browser", "needs_config", tools,
                       "playwright installed but no chromium browser: run `playwright install chromium`")
    return _entry("browser", "live", tools, "chromium: " + browsers[0])


def _probe_pc():
    mod, note, missing = _safe_import(BUILTIN_PREFIX + "pc_pack")
    if mod is None:
        return _entry("pc", "unavailable", None, note)
    tools = _count_tools(mod)
    if platform.system() != "Windows":
        return _entry("pc", "unavailable", tools,
                       "Windows-only features (winget); this host is " + platform.system())
    if shutil.which("winget"):
        return _entry("pc", "live", tools, "winget available")
    return _entry("pc", "unavailable", tools, "winget not found on this Windows host")


def _probe_telegram():
    mod, note, missing = _safe_import(BUILTIN_PREFIX + "telegram_pack")
    if mod is None:
        return _entry("telegram", "unavailable", None, note)
    tools = _count_tools(mod)
    token = (_phase8_config().get("telegram") or {}).get("bot_token") if isinstance(
        _phase8_config().get("telegram"), dict) else None
    if token:
        return _entry("telegram", "live", tools, "bot token configured")
    return _entry("telegram", "needs_config", tools,
                   "set telegram.bot_token in " + str(PHASE8_CONFIG))


_GMAIL_TOKEN_CANDIDATES = (
    JARVIS_DIR / "dynamic" / "gmail" / "token.json",
    Path.home() / ".config" / "gmail-token.json",
    Path.home() / ".gmail_token.json",
)


def _probe_gmail():
    mod, note, missing = _safe_import(BUILTIN_PREFIX + "gmail_pack")
    if mod is None:
        return _entry("gmail", "unavailable", None, note)
    tools = _count_tools(mod)
    cfg_gmail = _phase8_config().get("gmail")
    if isinstance(cfg_gmail, dict) and cfg_gmail.get("client_secrets_file"):
        f = Path(cfg_gmail["client_secrets_file"]).expanduser()
        if f.is_file():
            return _entry("gmail", "live", tools, f"client secrets: {f}")
    found = next((str(p) for p in _GMAIL_TOKEN_CANDIDATES if p.is_file()), None)
    if found:
        return _entry("gmail", "live", tools, f"credentials: {found}")
    return _entry("gmail", "needs_config", tools,
                   "no Gmail OAuth credentials found (client secrets / token.json)")


def _probe_studio():
    mod, note, missing = _safe_import(BUILTIN_PREFIX + "studio_pack")
    if mod is None:
        return _entry("studio", "unavailable", None, note)
    tools = _count_tools(mod)
    parts = []
    if shutil.which("ffmpeg"):
        parts.append("ffmpeg")
    if shutil.which("piper"):
        parts.append("piper-cli")
    else:
        try:
            import piper  # noqa: F401
            parts.append("piper-py")
        except Exception:
            pass
    try:
        import faster_whisper  # noqa: F401
        parts.append("faster-whisper")
    except Exception:
        pass
    if "ffmpeg" not in parts:
        return _entry("studio", "needs_package", tools,
                       "install ffmpeg (studio audio/video pipeline needs it)")
    missing_parts = {"piper-cli/piper-py", "faster-whisper"} - set(parts)
    detail = "available: " + ", ".join(parts)
    if missing_parts:
        detail += "; missing: " + ", ".join(sorted(missing_parts))
    return _entry("studio", "live", tools, detail)


def _probe_plugins():
    mod, note, missing = _safe_import("jarvis.tools.plugins.loader")
    if mod is None:
        return _entry("plugins", "unavailable", None, note)
    count = None
    try:
        for attr in ("list_plugins", "get_plugins", "PLUGINS", "plugins"):
            val = getattr(mod, attr, None)
            if val is None:
                continue
            seq = val() if callable(val) else val
            try:
                count = len(seq)
                break
            except TypeError:
                pass
    except Exception:
        pass
    detail = f"{count} plugin(s) loaded" if count is not None else "loader importable"
    return _entry("plugins", "live", count, detail)


# ---------------------------------------------------------------------------
# pre-existing packs
# ---------------------------------------------------------------------------

_PREEXISTING = ("comms", "web_pack", "data_pack", "dev_pack",
                "media_pack", "system_pack", "home_pack", "creator")


def _probe_preexisting(name):
    mod, note, missing = _safe_import(BUILTIN_PREFIX + name)
    if mod is None:
        return _entry(name, "unavailable", None, note)
    return _entry(name, "live", _count_tools(mod), "builtin pack loaded")


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def doctor(args: dict) -> dict:
    """Probe all capability packs; return a structured health report. Never raises."""
    try:
        report = [
            _probe_gui(),
            _probe_browser(),
            _probe_pc(),
            _probe_telegram(),
            _probe_gmail(),
            _probe_studio(),
            _probe_plugins(),
        ]
        for name in _PREEXISTING:
            report.append(_probe_preexisting(name))
        summary = {"live": 0, "needs_config": 0, "needs_package": 0, "unavailable": 0}
        for r in report:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        return {"report": report, "summary": summary,
                "host": {"platform": platform.system(), "display": bool(os.environ.get("DISPLAY"))}}
    except Exception as e:  # absolute last resort
        return {"report": [], "summary": {}, "error": f"{type(e).__name__}: {e}"}


# Domain map: concise, honest capabilities per domain. {domain: {packs, lines}}.
DOMAINS = {
    "Create": {
        "packs": ("creator", "studio"),
        "lines": [
            "Build a single-file responsive HTML website from a brief (website.build).",
            "Generate AI images from a prompt via Pollinations (needs network).",
            "Compose Ken Burns slideshow reels from images (needs ffmpeg).",
            "Run Python code in a jailed sandbox with captured output (code.run).",
            "Post images to Instagram (confirmation-gated).",
        ],
    },
    "Communicate": {
        "packs": ("comms", "telegram", "gmail"),
        "lines": [
            "Send email via SMTP (confirmation-gated); WhatsApp/message bridges where configured.",
            "Telegram bot messaging once a bot token is configured.",
            "Gmail integration once OAuth client credentials are connected.",
        ],
    },
    "Browse & Research": {
        "packs": ("web_pack", "browser"),
        "lines": [
            "Web search and page fetching for research.",
            "Drive a real local browser (Chromium via Playwright) for forms and bookings — irreversible steps need confirmation.",
            "Describe what the camera sees or what is on screen.",
        ],
    },
    "Manage PC": {
        "packs": ("pc", "gui", "system_pack"),
        "lines": [
            "Desktop notifications, screenshots, battery, disk usage, top processes, uptime.",
            "GUI automation: mouse, keyboard, and screen control (needs a display + backend).",
            "Windows app management via winget (Windows only).",
            "File operations and guardrailed shell commands.",
        ],
    },
    "Automate": {
        "packs": ("data_pack", "dev_pack", "plugins"),
        "lines": [
            "Reminders (cron or one-shot), timers, and searchable notes.",
            "Delegate subtasks to specialist roles (researcher, coder, writer, planner).",
            "Data tools (10 registrations) and developer tools (9 tools).",
            "Extensible via community plugins when the plugin loader is installed.",
        ],
    },
    "Media Studio": {
        "packs": ("media_pack", "studio"),
        "lines": [
            "Text-to-speech with a local Piper voice (needs voice model).",
            "Transcribe audio with faster-whisper (needs the model installed).",
            "Resize/convert images; trim video, make GIFs, build thumbnail sheets (needs ffmpeg/PIL).",
            "File hashing, zip/unzip with zip-slip protection.",
        ],
    },
    "Knowledge": {
        "packs": ("data_pack", "comms"),
        "lines": [
            "Search saved notes; keep a personal memory store.",
            "Structured data helpers (CSV/JSON style tooling).",
            "Summarize fetched web pages and documents.",
        ],
    },
}


def capabilities(args: dict) -> dict:
    """Human-readable summary of what Jarvis can do. Optional domain filter. Never raises."""
    try:
        domain = (args or {}).get("domain")
        try:
            rep = doctor({})["report"]
        except Exception:
            rep = []
        by_pack = {r["pack"]: r for r in rep}

        def pack_badge(names):
            bits = []
            for n in names:
                r = by_pack.get(n)
                if not r:
                    bits.append(f"{n}: unknown")
                else:
                    bits.append(f"{n}: {r['status']}")
            return "; ".join(bits)

        def render(name, spec):
            lines = [f"## {name}", f"({pack_badge(spec['packs'])})"]
            lines += [f"- {ln}" for ln in spec["lines"]]
            return "\n".join(lines)

        if domain:
            want = domain.strip().lower()
            match = next((k for k in DOMAINS if k.lower() == want), None)
            if not match:
                return {"summary": "Unknown domain. Available: " + ", ".join(DOMAINS),
                        "domain": domain, "available_domains": list(DOMAINS)}
            return {"summary": render(match, DOMAINS[match]), "domain": match}

        live = sum(1 for r in rep if r["status"] == "live")
        total = len(rep)
        header = (f"Jarvis capabilities — {live} of {total} packs live right now. "
                  "Anything marked needs_config or needs_package is honest about "
                  "what is missing so you can enable it.")
        body = "\n\n".join(render(name, spec) for name, spec in DOMAINS.items())
        return {"summary": header + "\n\n" + body, "domain": None,
                "live_packs": live, "total_packs": total}
    except Exception as e:
        return {"summary": f"capabilities unavailable: {type(e).__name__}: {e}", "domain": None}


TOOL_DEFS = [
    {
        "name": "system.doctor",
        "description": "Probe all capability packs (new Phase-8 packs + pre-existing builtins) and return a structured health report with live/needs_config/needs_package/unavailable statuses.",
        "handler": doctor,
        "risk": "low",
        "needs_network": False,
        "schema": {},
    },
    {
        "name": "system.capabilities",
        "description": "Human-readable summary of what Jarvis can do, grouped by domain, annotated with live pack health. Optional domain filter.",
        "handler": capabilities,
        "risk": "low",
        "needs_network": False,
        "schema": {"domain": "string?"},
    },
]
