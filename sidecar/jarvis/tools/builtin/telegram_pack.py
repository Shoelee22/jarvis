"""Telegram Tool Pack (Phase 8): talk to the Telegram Bot API with stdlib only.

Reads ``~/workspace/jarvis/dynamic/tools_config.phase8.yaml`` at call time
(never caches), ``telegram:`` section with ``bot_token`` (from @BotFather)
and optional ``default_chat_id``.

Egress note: the sidecar's default-deny egress blocks api.telegram.org until
the orchestrator allowlists it, e.g. ``"telegram.send": ["api.telegram.org"]``
in ``jarvis/security/egress.py`` (one of the 8 parallel Phase 8 packs owns
that file, so this pack cannot edit it — handlers return an honest error when
egress denies the call).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from ...security import egress

HOME = Path.home()
CONFIG_PATH = HOME / "workspace" / "jarvis" / "dynamic" / "tools_config.phase8.yaml"

HOST = "api.telegram.org"
TIMEOUT = 20
MAX_TEXT = 4000
CONFIG_ERROR = (
    "telegram not configured: add bot_token (from @BotFather) under "
    "telegram: in dynamic/tools_config.phase8.yaml"
)
EGRESS_ERROR = (
    "egress blocked: api.telegram.org is not allowlisted for telegram tools; "
    "add \"api.telegram.org\" to the ALLOWLIST entries for the telegram.* tools "
    "in jarvis/security/egress.py"
)


def _load_config() -> dict:
    """Read the phase8 config at call time; {} on any failure."""
    try:
        raw = CONFIG_PATH.read_text()
    except OSError:
        return {}
    try:
        import yaml  # pyyaml ships with the sidecar env
        data = yaml.safe_load(raw) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    tg = data.get("telegram", {})
    return tg if isinstance(tg, dict) else {}


def _token(cfg: dict) -> str | None:
    tok = cfg.get("bot_token")
    return tok if isinstance(tok, str) and tok.strip() else None


def _require_token(cfg: dict) -> str | dict:
    tok = _token(cfg)
    if not tok:
        return {"error": CONFIG_ERROR}
    return tok


def _api(method: str, token: str, tool: str, payload: dict) -> tuple[bool, dict]:
    """POST to the Bot API. Never raises; surfaces Telegram's own errors."""
    if not egress.check(tool, HOST):
        return False, {"error": EGRESS_ERROR}
    url = f"https://{HOST}/bot{token}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        desc = f"HTTP {e.code} {e.reason}"
        try:
            body = json.loads(e.read().decode("utf-8"))
            desc = body.get("description", desc)
        except Exception:
            pass
        return False, {"error": f"telegram API error: {desc}",
                       "http_status": e.code}
    except Exception as e:
        return False, {"error": f"telegram request failed: {e}"}
    if not isinstance(data, dict) or not data.get("ok"):
        desc = data.get("description", "unknown error") if isinstance(data, dict) else "bad response"
        return False, {"error": f"telegram API error: {desc}"}
    return True, data.get("result")


def _valid_chat_id(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("@"):
            return len(s) > 1
        return s.lstrip("-").isdigit() and len(s.lstrip("-")) > 0
    return False


def _norm_chat_id(value):
    """Normalize a validated chat id: int stays int, numeric strings become int."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return value


# ------------------------------------------------------------ handlers

def _me_impl(args: dict) -> dict:
    tok = _require_token(_load_config())
    if isinstance(tok, dict):
        return tok
    ok, res = _api("getMe", tok, "telegram.me", {})
    if not ok:
        return res
    res = res or {}
    return {"ok": True, "id": res.get("id"), "username": res.get("username"),
            "first_name": res.get("first_name")}


def _send_impl(args: dict) -> dict:
    cfg = _load_config()
    tok = _require_token(cfg)
    if isinstance(tok, dict):
        return tok
    chat_id = args.get("chat_id", cfg.get("default_chat_id"))
    if chat_id is None:
        return {"error": "telegram.send: no chat_id given and no default_chat_id "
                         "in config; use telegram.register_chat to set one"}
    if not _valid_chat_id(chat_id):
        return {"error": "telegram.send: chat_id must be an integer chat id or "
                         "an @channel username"}
    chat_id = _norm_chat_id(chat_id)
    text = args.get("text", "")
    text = "" if text is None else str(text)
    if not text.strip():
        return {"error": "telegram.send: text is empty"}
    truncated = len(text) > MAX_TEXT
    payload = {"chat_id": chat_id, "text": text[:MAX_TEXT]}
    ok, res = _api("sendMessage", tok, "telegram.send", payload)
    if not ok:
        return res
    res = res or {}
    out = {"message_id": res.get("message_id"), "chat_id": chat_id}
    if truncated:
        out["note"] = f"text truncated to {MAX_TEXT} chars"
    return out


def _updates_impl(args: dict) -> dict:
    tok = _require_token(_load_config())
    if isinstance(tok, dict):
        return tok
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        return {"error": "telegram.updates: limit must be an integer"}
    limit = max(1, min(limit, 100))
    ok, res = _api("getUpdates", tok, "telegram.updates",
                   {"limit": limit, "timeout": 0})
    if not ok:
        return res
    out = []
    for u in (res or []):
        if not isinstance(u, dict):
            continue
        msg = u.get("message") or u.get("edited_message") or \
            u.get("channel_post") or {}
        sender = msg.get("from") or msg.get("chat") or {}
        who = sender.get("username") or sender.get("first_name")
        out.append({"update_id": u.get("update_id"),
                    "from": who,
                    "text": msg.get("text") or msg.get("caption")})
    return {"updates": out}


def _register_chat_impl(args: dict) -> dict:
    chat_id = args.get("chat_id")
    if not _valid_chat_id(chat_id):
        return {"error": "telegram.register_chat: chat_id must be an integer "
                         "chat id (get it from @userinfobot or @RawDataBot) or "
                         "an @channel username"}
    chat_id = _norm_chat_id(chat_id)
    snippet = f"telegram:\n  default_chat_id: {chat_id}"
    return {"ok": True, "add_to_config": snippet,
            "note": "paste this under the top level of "
                    "dynamic/tools_config.phase8.yaml (the bot token goes on a "
                    "'bot_token:' line under telegram: too)"}


def _safe(fn):
    def wrapper(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # handlers never raise
            return {"error": f"{fn.__name__} failed: {e}"}
    return wrapper


telegram_me = _safe(_me_impl)
telegram_send = _safe(_send_impl)
telegram_updates = _safe(_updates_impl)
telegram_register_chat = _safe(_register_chat_impl)


TOOL_DEFS = [
    {"name": "telegram.me",
     "description": "Verify the bot token: returns the bot's id, username and first name.",
     "handler": telegram_me,
     "risk": "low",
     "needs_network": True,
     "schema": {}},
    {"name": "telegram.send",
     "description": "Send a text message via the bot (text capped at 4000 chars). "
                    "chat_id defaults to default_chat_id from config.",
     "handler": telegram_send,
     "risk": "high",
     "needs_network": True,
     "schema": {"chat_id": "int|string?", "text": "string"}},
    {"name": "telegram.updates",
     "description": "Poll getUpdates for recent bot messages; returns simplified "
                    "{update_id, from, text} entries.",
     "handler": telegram_updates,
     "risk": "low",
     "needs_network": True,
     "schema": {"limit": "int?"}},
    {"name": "telegram.register_chat",
     "description": "Validate a chat id / @channel and return the YAML snippet the "
                    "user must paste into tools_config.phase8.yaml to set it as "
                    "default_chat_id.",
     "handler": telegram_register_chat,
     "risk": "low",
     "needs_network": True,
     "schema": {"chat_id": "int|string"}},
]


def register(reg) -> object:
    """Register the Telegram pack on a Registry."""
    from ..base import Tool
    for t in TOOL_DEFS:
        reg.register(Tool(t["name"], t["description"], t.get("schema", {}),
                          t["handler"], t["risk"], t["needs_network"]))
    return reg
