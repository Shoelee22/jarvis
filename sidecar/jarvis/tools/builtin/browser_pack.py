"""Browser control pack: Playwright-based web navigation for the JARVIS sidecar.

Tools: browser.open / browser.read_text / browser.fill / browser.click /
       browser.shot / browser.close.

All handlers take a dict and return a dict — NEVER raise. Playwright is an
optional dependency: if it isn't importable, every tool returns the same
install-hint error. One module-level lazy session holds the playwright
starter + persistent context (logins persist in
~/workspace/jarvis/data/browser_profile) + current page. Timeouts are
bounded at 30s everywhere. close() is robust: it tears everything down and
always releases the session slot, even when individual steps fail.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

TIMEOUT_MS = 30_000
PLAYWRIGHT_MISSING = (
    "playwright not installed: pip install playwright && "
    "python -m playwright install chromium"
)
PROFILE_DIR = Path.home() / "workspace" / "jarvis" / "data" / "browser_profile"
SHOTS_DIR = Path.home() / "workspace" / "jarvis" / "output" / "shots"

# One module-level session, created lazily: {"pw": starter, "context": ..., "page": ...}
_session: dict | None = None


# ------------------------------------------------------------- internals
def _get_sync_playwright():
    """Return the sync_playwright factory, or None when playwright is unavailable.

    Works with the real install (lazy import) and with test doubles that
    monkeypatch sys.modules['playwright'].
    """
    try:
        pw_mod = sys.modules.get("playwright")
        if pw_mod is not None:
            sync_api = getattr(pw_mod, "sync_api", None)
            factory = getattr(sync_api, "sync_playwright", None) or getattr(
                pw_mod, "sync_playwright", None
            )
            if factory is not None:
                return factory
        from playwright.sync_api import sync_playwright  # noqa: E402

        return sync_playwright
    except Exception:
        return None


def _require_playwright():
    factory = _get_sync_playwright()
    if factory is None:
        return None, {"error": PLAYWRIGHT_MISSING}
    return factory, None


def _require_page():
    """Return (page, None) or (None, error_dict)."""
    factory, err = _require_playwright()
    if err:
        return None, err
    if _session is None or _session.get("page") is None:
        return None, {"error": "no page open: call browser.open first"}
    return _session["page"], None


# ------------------------------------------------------------- handlers
def browser_open(args: dict) -> dict:
    try:
        factory, err = _require_playwright()
        if err:
            return err
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return {"error": "browser.open requires a 'url' string"}
        url = url.strip()
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return {"error": f"unsupported URL scheme {scheme!r}: http/https only"}
        headless = args.get("headless", True)
        if not isinstance(headless, bool):
            return {"error": "'headless' must be a boolean"}

        global _session
        # Reuse the session if the profile/context is already live; otherwise start it.
        if _session is None:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            pw = factory().start()
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR), headless=headless
                )
            except Exception:
                try:
                    pw.stop()
                except Exception:
                    pass
                raise
            _session = {"pw": pw, "context": context, "page": None}

        context = _session["context"]
        page = _session.get("page")
        if page is None:
            page = context.new_page()
            _session["page"] = page

        response = page.goto(url, timeout=TIMEOUT_MS)
        page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
        status = getattr(response, "status", None)
        return {"title": page.title(), "url": page.url, "status": status}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def browser_read_text(args: dict) -> dict:
    try:
        page, err = _require_page()
        if err:
            return err
        max_chars = args.get("max_chars", 8000)
        if not isinstance(max_chars, int) or max_chars <= 0:
            return {"error": "'max_chars' must be a positive integer"}
        text = page.inner_text("body", timeout=TIMEOUT_MS) or ""
        truncated = len(text) > max_chars
        return {"text": text[:max_chars], "truncated": truncated,
                "length": len(text)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def browser_fill(args: dict) -> dict:
    try:
        page, err = _require_page()
        if err:
            return err
        selector = args.get("selector")
        text = args.get("text")
        if not isinstance(selector, str) or not selector.strip():
            return {"error": "browser.fill requires a 'selector' string"}
        if not isinstance(text, str):
            return {"error": "browser.fill requires a 'text' string"}
        page.fill(selector, text, timeout=TIMEOUT_MS)
        return {"ok": True, "selector": selector}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def browser_click(args: dict) -> dict:
    try:
        page, err = _require_page()
        if err:
            return err
        selector = args.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            return {"error": "browser.click requires a 'selector' string"}
        page.click(selector, timeout=TIMEOUT_MS)
        return {"ok": True, "selector": selector}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def browser_shot(args: dict) -> dict:
    try:
        page, err = _require_page()
        if err:
            return err
        path = args.get("path")
        if path is None:
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            path = str(SHOTS_DIR / f"shot-{int(time.time())}.png")
        page.screenshot(path=path, timeout=TIMEOUT_MS)
        return {"path": str(path)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def browser_close(args: dict) -> dict:
    """Tear down page, context, and playwright. Never raises; never leaves zombies."""
    global _session
    notes: list[str] = []
    if _session is not None:
        page, context, pw = (_session.get("page"), _session.get("context"),
                             _session.get("pw"))
        _session = None  # release the slot FIRST so nothing lingers on failure
        for label, obj, method in (("page", page, "close"),
                                  ("context", context, "close"),
                                  ("playwright", pw, "stop")):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception as e:  # noqa: BLE001 — robustness over precision here
                notes.append(f"{label}: {type(e).__name__}: {e}")
    result: dict = {"closed": True}
    if notes:
        result["notes"] = notes
    return result


# ------------------------------------------------------------- tool defs
TOOL_DEFS = [
    {
        "name": "browser.open",
        "description": (
            "Open a URL in a persistent Chromium browser (logins persist across "
            "sessions). Returns page title, final URL, and HTTP status. "
            "http/https URLs only."
        ),
        "handler": browser_open,
        "risk": "low",
        "needs_network": True,
        "schema": {"url": "string", "headless?": "bool"},
    },
    {
        "name": "browser.read_text",
        "description": (
            "Return the visible text of the current page (body inner_text), "
            "truncated to max_chars."
        ),
        "handler": browser_read_text,
        "risk": "low",
        "needs_network": True,
        "schema": {"max_chars?": "int"},
    },
    {
        "name": "browser.fill",
        "description": "Fill a form field matched by a CSS selector with text.",
        "handler": browser_fill,
        "risk": "medium",
        "needs_network": True,
        "schema": {"selector": "string", "text": "string"},
    },
    {
        "name": "browser.click",
        "description": "Click the element matched by a CSS selector.",
        "handler": browser_click,
        "risk": "medium",
        "needs_network": True,
        "schema": {"selector": "string"},
    },
    {
        "name": "browser.shot",
        "description": (
            "Screenshot the current page. Defaults to a timestamped PNG under "
            "output/shots/ when no path is given."
        ),
        "handler": browser_shot,
        "risk": "low",
        "needs_network": True,
        "schema": {"path?": "string"},
    },
    {
        "name": "browser.close",
        "description": (
            "Close the page and browser and release all resources. "
            "Safe to call even with nothing open."
        ),
        "handler": browser_close,
        "risk": "low",
        "needs_network": True,
        "schema": {},
    },
]
