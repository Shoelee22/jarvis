"""Gmail Tool Pack (Phase 8): search, read, send, and list labels via the Gmail API.

Real implementation through google-api-python-client + google-auth-oauthlib,
imported lazily; if the libraries are missing every tool returns an honest
{"error": "pip install ..."} instead of raising.

Auth model: installed-app OAuth flow (no PKCE) with the client creds from
~/workspace/jarvis/dynamic/tools_config.phase8.yaml (section `gmail`).
The token lives at ~/workspace/jarvis/data/gmail_token.json with 0600
permissions and is refreshed automatically via google-auth Credentials.

`gmail.auth_url` is dual-purpose (kept at 5 tools total):
  - called with no args -> returns a fresh authorization URL
  - called with {code}   -> exchanges the code, stores the token, returns
                           {"authenticated": True}

All handlers take a dict and return a dict; they NEVER raise.
"""
from __future__ import annotations

import base64
import functools
import re
from email.mime.text import MIMEText
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "dynamic" / "tools_config.phase8.yaml"
TOKEN_PATH = JARVIS_DIR / "data" / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

BODY_CHAR_CAP = 20_000
PIP_INSTALL_HINT = "pip install google-api-python-client google-auth-oauthlib"

SETUP_STEPS = [
    "Go to https://console.cloud.google.com/apis/credentials",
    "Create an OAuth client ID (application type: Desktop app) and enable the Gmail API",
    "Paste client_id and client_secret into ~/workspace/jarvis/dynamic/tools_config.phase8.yaml under the gmail: section",
]

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0
_flow = None  # stashed InstalledAppFlow between gmail.auth_url (URL) and gmail.auth_url {code}


# ---------------------------------------------------------------- config

def _load_config() -> dict:
    """Read the phase-8 config at call time; cache by mtime, tolerate failure."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        cfg = {}
    _cfg_cache = cfg
    _cfg_mtime = mtime
    return cfg


def _client_error() -> dict | None:
    """None when client creds are present, else an honest setup-steps error."""
    gmail = _load_config().get("gmail") or {}
    if gmail.get("client_id") and gmail.get("client_secret"):
        return None
    return {
        "error": "Gmail OAuth not configured: client_id/client_secret are missing.",
        "setup": SETUP_STEPS,
    }


def _deps_error() -> dict | None:
    """None when the Google client libraries import, else a pip-install error."""
    try:
        import googleapiclient.discovery  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
    except Exception:
        return {"error": PIP_INSTALL_HINT}
    return None


def _safe(fn):
    """Handlers never raise: any unexpected exception becomes {"error": ...}."""
    @functools.wraps(fn)
    def wrapper(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
    return wrapper


# ---------------------------------------------------------------- auth

def _make_flow():
    """Build an InstalledAppFlow from the configured client creds."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    gmail = _load_config()["gmail"]
    client_config = {
        "installed": {
            "client_id": gmail["client_id"],
            "client_secret": gmail["client_secret"],
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return InstalledAppFlow.from_client_config(client_config, SCOPES)


def _store_token(creds) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    TOKEN_PATH.chmod(0o600)


def _read_token():
    """Return Credentials or None when no token is stored yet."""
    from google.oauth2.credentials import Credentials

    try:
        info = TOKEN_PATH.read_text()
    except OSError:
        return None
    import json
    return Credentials.from_authorized_user_info(json.loads(info), SCOPES)


def _refresh(creds) -> None:
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request

        creds.refresh(Request())
        _store_token(creds)


def _fresh_auth_url() -> str:
    """Create a new flow (stashed for the later code exchange) and return its URL."""
    global _flow
    _flow = _make_flow()
    url, _state = _flow.authorization_url(access_type="offline", prompt="consent")
    return url


def _authed():
    """Return (service, None) on success or (None, error_dict) otherwise."""
    err = _client_error()
    if err:
        return None, err
    err = _deps_error()
    if err:
        return None, err
    creds = _read_token()
    if creds is None:
        return None, {
            "error": "not authenticated",
            "auth_url": _fresh_auth_url(),
            "next": "open the URL, then call gmail.auth_url with {code: <code>}",
        }
    _refresh(creds)
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds), None


# ---------------------------------------------------------------- tools

@_safe
def gmail_auth_url(args: dict) -> dict:
    """gmail.auth_url {code?}: no code -> authorization URL; with code -> exchange & store."""
    global _flow
    err = _client_error()
    if err:
        return err
    err = _deps_error()
    if err:
        return err
    code = (args.get("code") or "").strip()
    if not code:
        url = _fresh_auth_url()
        return {
            "auth_url": url,
            "note": "open the URL, approve access, then call gmail.auth_url with {code: <the code you are shown>}",
        }
    if _flow is None:
        return {
            "error": "no pending authorization (code exchange needs the flow from a fresh URL)",
            "next": "call gmail.auth_url with no arguments to get a fresh URL first",
        }
    _flow.fetch_token(code=code)
    _store_token(_flow.credentials)
    _flow = None
    return {"authenticated": True, "note": "token stored with 0600 permissions"}


def _message_headers(payload: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


@_safe
def gmail_search(args: dict) -> dict:
    """gmail.search {q, max=10}: list matching messages with snippet/subject/from."""
    q = args.get("q", "")
    try:
        max_n = max(1, min(50, int(args.get("max", 10))))
    except (TypeError, ValueError):
        return {"error": "max must be an integer (1-50)"}
    svc, err = _authed()
    if err:
        return err
    resp = svc.users().messages().list(userId="me", q=q, maxResults=max_n).execute()
    out = []
    for m in resp.get("messages", []):
        detail = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            )
            .execute()
        )
        headers = _message_headers(detail.get("payload", {}))
        out.append(
            {
                "id": m["id"],
                "threadId": m.get("threadId"),
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "date": headers.get("date", ""),
                "snippet": detail.get("snippet", ""),
            }
        )
    return {"messages": out, "resultSizeEstimate": resp.get("resultSizeEstimate", len(out))}


def _collect_bodies(part: dict, plain: list, html: list) -> None:
    mime = part.get("mimeType", "")
    data = (part.get("body") or {}).get("data")
    if data:
        try:
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            text = ""
        if mime == "text/plain":
            plain.append(text)
        elif mime == "text/html":
            html.append(text)
    for sub in part.get("parts", []):
        _collect_bodies(sub, plain, html)


@_safe
def gmail_read(args: dict) -> dict:
    """gmail.read {id}: full body text (prefers text/plain), capped at 20k chars."""
    msg_id = (args.get("id") or "").strip()
    if not msg_id:
        return {"error": "id is required"}
    svc, err = _authed()
    if err:
        return err
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = msg.get("payload", {})
    headers = _message_headers(payload)
    plain, html = [], []
    _collect_bodies(payload, plain, html)
    body = "".join(plain) or "".join(html)
    truncated = len(body) > BODY_CHAR_CAP
    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "date": headers.get("date", ""),
        "body": body[:BODY_CHAR_CAP],
        "truncated": truncated,
    }


@_safe
def gmail_send(args: dict) -> dict:
    """gmail.send {to, subject, body}: send an email. HIGH risk — validate `to` first."""
    to = (args.get("to") or "").strip()
    subject = args.get("subject", "")
    body = args.get("body", "")
    if not to or not _EMAIL_RE.match(to):
        return {"error": f"invalid recipient email address: {to!r}"}
    svc, err = _authed()
    if err:
        return err
    message = MIMEText(body or "")
    message["To"] = to
    message["Subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"sent": True, "id": sent.get("id"), "threadId": sent.get("threadId")}


@_safe
def gmail_labels(args: dict) -> dict:
    """gmail.labels {}: list the account's label names."""
    svc, err = _authed()
    if err:
        return err
    resp = svc.users().labels().list(userId="me").execute()
    return {"labels": [lbl.get("name", "") for lbl in resp.get("labels", [])]}


TOOL_DEFS = [
    {
        "name": "gmail.auth_url",
        "description": "Get a Gmail OAuth authorization URL, or exchange an auth code (call with {code}) to store the token.",
        "handler": gmail_auth_url,
        "risk": "low",
        "needs_network": True,
        "schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Auth code shown after approving the URL"}},
        },
    },
    {
        "name": "gmail.search",
        "description": "Search Gmail messages; returns id, subject, from, date, and snippet per message.",
        "handler": gmail_search,
        "risk": "low",
        "needs_network": True,
        "schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Gmail search query"},
                "max": {"type": "integer", "default": 10, "description": "Max results (1-50)"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "gmail.read",
        "description": "Read a full Gmail message body (text/plain preferred, capped at 20k chars).",
        "handler": gmail_read,
        "risk": "low",
        "needs_network": True,
        "schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Gmail message id"}},
            "required": ["id"],
        },
    },
    {
        "name": "gmail.send",
        "description": "Send an email via Gmail. Validates the recipient address.",
        "handler": gmail_send,
        "risk": "high",
        "needs_network": True,
        "schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail.labels",
        "description": "List the Gmail account's label names.",
        "handler": gmail_labels,
        "risk": "low",
        "needs_network": True,
        "schema": {"type": "object", "properties": {}},
    },
]
