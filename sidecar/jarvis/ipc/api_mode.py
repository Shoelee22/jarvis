"""Phase 9 — Jarvis API Mode.

Exposes the assistant as an authenticated HTTP API (``/api/v1/*``) so the
user's other devices/apps can use it.

IMPORTANT: fastapi is an *optional* server dependency and is NOT installed
on the dev VM. This module must therefore NEVER import fastapi at module
import time — every fastapi import is lazy inside the function that needs
it. ``import api_mode`` is always safe, and all pure logic (auth
comparison, bind_host, truncation, size guard) is testable without fastapi.

Mounting (done by the parent, e.g. in ipc/server.py):

    from jarvis.ipc.api_mode import mount_api, bind_host, set_agent
    mount_api(app, agent=agent)          # registers /api/v1/* routes
    uvicorn.run(..., host=bind_host())   # "127.0.0.1" unless LAN enabled

The agent may be bound later with set_agent() — same pattern as
tools/builtin/delegate_tool.py's bind_agent().
"""
from __future__ import annotations

import hmac
import time
from pathlib import Path

API_MODE_VERSION = "1.0.0"   # version of *API mode*, not of the sidecar
MAX_TEXT_CHARS = 4000        # request size guard for /api/v1/ask
TRUNCATE_CHARS = 500         # per tool-result truncation in tool_trace
PHASE9_CONFIG = Path.home() / "workspace" / "jarvis" / "dynamic" / "tools_config.phase9.yaml"

# ------------------------------------------------------------------ state
_bound_agent = None  # set via mount_api(agent=...) or set_agent()


def set_agent(agent) -> None:
    """Bind the live agent after mount (same pattern as delegate_tool.bind_agent)."""
    global _bound_agent
    _bound_agent = agent


def get_agent():
    """Return the bound agent, or None if API mode was mounted without one."""
    return _bound_agent


# ---------------------------------------------------------- config / host
def bind_host(config_path: str | Path | None = None) -> str:
    """Host the HTTP server should bind to.

    Reads ``api.lan_enabled`` from the Phase 9 dynamic config (default
    FALSE). Returns "0.0.0.0" when LAN mode is explicitly enabled,
    otherwise "127.0.0.1". Any problem (missing file, missing yaml,
    missing section, unparsable content) defensively falls back to
    loopback — never expose the network by accident.
    """
    path = Path(config_path) if config_path is not None else PHASE9_CONFIG
    try:
        import yaml  # optional on the dev VM too — fall back if missing
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        api_cfg = data.get("api") or {}
        if bool(api_cfg.get("lan_enabled", False)):
            return "0.0.0.0"
    except Exception:
        pass
    return "127.0.0.1"


# ------------------------------------------------------------------ auth
class ApiError(Exception):
    """Honest, catchable API error. Pure logic raises it; the FastAPI
    routes translate it into HTTPException so nothing escapes unhandled."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _default_token_getter():
    """Defensively pull ensure_token() from ipc/server.py.

    Returns the callable, or None when the server module (or its token
    machinery) is unavailable — in which case auth is impossible and the
    API answers 503 "auth unavailable" instead of silently letting
    traffic through.
    """
    try:
        from .server import ensure_token  # fastapi-free at import time
        return ensure_token
    except Exception:
        return None


def check_auth(authorization: str | None, token_getter) -> tuple[bool, str]:
    """Pure auth check: constant-time compare of the Authorization header
    against ``Bearer <token>``. Returns (ok, detail). Never raises."""
    token = None
    if token_getter is not None:
        try:
            token = token_getter()
        except Exception:
            token = None
    if not token:
        return False, "auth unavailable"
    if not isinstance(authorization, str):
        authorization = ""
    if hmac.compare_digest(authorization, f"Bearer {token}"):
        return True, ""
    return False, "bad token"


# ------------------------------------------------------------ pure logic
def _truncate(value, limit: int = TRUNCATE_CHARS) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        return text[:limit] + "\u2026 (truncated)"
    return text


def api_health() -> dict:
    return {"ok": True, "version": API_MODE_VERSION}


def api_tools_list() -> list[dict]:
    """Return the registry's spec list. Honest 503 when no agent is bound."""
    agent = get_agent()
    if agent is None:
        raise ApiError(503, "agent not bound: API mode is up but no agent is attached yet")
    registry = getattr(agent, "registry", None)
    if registry is None or not hasattr(registry, "spec_list"):
        raise ApiError(503, "agent has no tool registry")
    try:
        return registry.spec_list()
    except Exception as exc:  # never leak a traceback to callers
        raise ApiError(500, f"registry error: {exc}") from None


def api_ask(text: str, confirmed_tools: list[str] | None = None) -> dict:
    """Run the agent on *text*. Pure logic — raises ApiError on honest
    failures (oversize text, no agent, agent blew up)."""
    if not isinstance(text, str):
        raise ApiError(400, "text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(400, f"text exceeds {MAX_TEXT_CHARS} chars (got {len(text)})")
    if not text.strip():
        raise ApiError(400, "text is empty")
    agent = get_agent()
    if agent is None:
        raise ApiError(503, "agent not bound: API mode is up but no agent is attached yet")
    confirmed = set(confirmed_tools or [])
    start = time.perf_counter()
    try:
        result = agent.run(text, confirmed)
    except Exception as exc:
        raise ApiError(500, f"agent error: {exc}") from None
    tool_trace = []
    for step in result.get("timeline", []) or []:
        tool_trace.append({
            "tool": step.get("tool"),
            "args": step.get("args"),
            "result": _truncate(step.get("result")),
        })
    return {
        "reply": result.get("reply", ""),
        "done": bool(result.get("done", False)),
        "tool_trace": tool_trace,
        "took_ms": int((time.perf_counter() - start) * 1000),
    }


# ------------------------------------------------------------- mounting
def mount_api(app, agent=None, token_getter=None):
    """Register the /api/v1/* routes on a FastAPI app.

    - ``agent``: the live Agent; may be None at mount time, then call
      set_agent() later (same late-binding pattern as delegate_tool).
    - ``token_getter``: callable returning the bearer token. Defaults to
      importing ensure_token() from ipc/server.py defensively; when that
      is unavailable every authed route answers 503 "auth unavailable".

    Requires fastapi (ImportError propagates honestly if it is missing).
    """
    from fastapi import Depends, Header, HTTPException  # lazy: optional dep

    if agent is not None:
        set_agent(agent)
    getter = token_getter if token_getter is not None else _default_token_getter()

    def _auth(authorization: str = Header("")):
        ok, detail = check_auth(authorization, getter)
        if not ok:
            raise HTTPException(503 if detail == "auth unavailable" else 401, detail)

    def _to_http(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            raise HTTPException(exc.status, exc.detail) from None

    @app.get("/api/v1/health")
    def health():
        return api_health()

    @app.get("/api/v1/tools", dependencies=[Depends(_auth)])
    def tools():
        return _to_http(api_tools_list)

    @app.post("/api/v1/ask", dependencies=[Depends(_auth)])
    def ask(body: dict):
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")
        return _to_http(api_ask, body.get("text", ""), body.get("confirmed_tools"))

    return app
