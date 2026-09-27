"""Phone Conversations Tool Pack (Phase 9): real voice calls that hold a
conversation.

Pipeline: Twilio outbound call -> TwiML <Stream> back to our public websocket
-> local STT (faster-whisper) -> the bound agent (or a scripted fallback)
decides the reply -> local TTS (Piper) -> mulaw 8kHz back to the caller.

Partial-but-honest: every missing piece (no Twilio credentials, no public
websocket URL, no STT/TTS backends) returns a precise {"error"} naming
exactly what is missing. Nothing is faked on a real call.

Config (defensive read, everything defaults to None):
  ~/workspace/jarvis/dynamic/tools_config.phase9.yaml
    phone:
      twilio_sid: "AC..."
      twilio_token: "..."
      from_number: "+15551234567"   # your Twilio caller ID
      public_ws_url: "wss://your-host/twilio-stream"
      db_path: ""                    # optional override for the sqlite log
      tts_voice_onnx: ""             # optional Piper voice override

The live Agent is bound at server startup via bind_agent() (same pattern as
builtin/delegate_tool.py). Unbound -> scripted lines are used as replies.
"""
from __future__ import annotations

import base64
import datetime as _dt
import html
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

from ..base import Tool
from ...config import DATA_DIR
from ...security import egress
from .phone_stream import PhoneCallSession, QueuedSTT

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
PHASE9_CONFIG_PATH = JARVIS_DIR / "dynamic" / "tools_config.phase9.yaml"

# config keys under the "phone:" section. Missing file/section/key -> None.
_PHONE_DEFAULTS = {
    "twilio_sid": None,
    "twilio_token": None,
    "from_number": None,
    "public_ws_url": None,
    "db_path": None,
    "tts_voice_onnx": None,
}

_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0

# The live Agent, bound by the IPC server after it is constructed.
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# --------------------------------------------------------------------- config
def _load_config() -> dict:
    """Read tools_config.phase9.yaml at call time; cache by mtime, tolerate
    failure (missing file -> empty dict -> all phone keys are None)."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = PHASE9_CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        import yaml  # pyyaml ships with the sidecar env
        raw = yaml.safe_load(PHASE9_CONFIG_PATH.read_text()) or {}
        cfg = raw if isinstance(raw, dict) else {}
    except Exception:
        cfg = {}
    _cfg_cache = cfg
    _cfg_mtime = mtime
    return cfg


def _phone_config() -> dict:
    raw = _load_config().get("phone", {}) or {}
    return {**_PHONE_DEFAULTS, **{k: v for k, v in raw.items() if k in _PHONE_DEFAULTS}}


def _enabled(tool_name: str) -> dict | None:
    """Per-tool kill switch: tools: {phone.call_and_talk: {enabled: false}}."""
    tools = _load_config().get("tools", {}) or {}
    if not tools.get(tool_name, {}).get("enabled", True):
        return {"error": f"tool '{tool_name}' is disabled in tools_config.phase9.yaml"}
    return None


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


_E164 = re.compile(r"^\+\d{7,15}$")


# ------------------------------------------------------------------ lazy STT/TTS
_stt_state: dict = {"loaded": False, "backend": None, "error": None}
_tts_state: dict = {"loaded": False, "backend": None, "error": None}


def reset_backends() -> None:
    """Forget cached STT/TTS backends (tests / after installing models)."""
    _stt_state.update(loaded=False, backend=None, error=None)
    _tts_state.update(loaded=False, backend=None, error=None)


def get_stt() -> tuple:
    """(backend, error_dict). Lazy-loads faster-whisper via voice.pipeline;
    caches; on failure returns a precise stt_unavailable error."""
    if _stt_state["loaded"]:
        return _stt_state["backend"], _stt_state["error"]
    try:
        from ...voice import pipeline
        backend = pipeline.load_real_stt()
        _stt_state.update(loaded=True, backend=backend, error=None)
    except Exception as e:
        _stt_state.update(loaded=True, backend=None,
                          error={"error": f"stt_unavailable: install faster-whisper / enable in config ({type(e).__name__}: {e})"})
    return _stt_state["backend"], _stt_state["error"]


def get_tts(voice_onnx: str | None = None) -> tuple:
    """(backend, error_dict). Lazy-loads Piper via voice.pipeline. Never falls
    back to FakeTTS on the real path — a missing model is an honest error."""
    if _tts_state["loaded"] and not voice_onnx:
        return _tts_state["backend"], _tts_state["error"]
    try:
        import os
        from ...voice import pipeline
        path = voice_onnx or _phone_config().get("tts_voice_onnx") or pipeline.default_voice_path()
        if not path or not os.path.exists(path):
            err = {"error": f"tts_unavailable: install piper-tts / download a voice model / enable in config (no voice model at {path!r}; expected en_GB-alan-medium, see voice/pipeline.load_real_tts)"}
            if not voice_onnx:
                _tts_state.update(loaded=True, backend=None, error=err)
            return None, err
        backend = pipeline.load_real_tts(path)
        if not voice_onnx:
            _tts_state.update(loaded=True, backend=backend, error=None)
        return backend, None
    except Exception as e:
        err = {"error": f"tts_unavailable: install piper-tts / download a voice model / enable in config ({type(e).__name__}: {e})"}
        if not voice_onnx:
            _tts_state.update(loaded=True, backend=None, error=err)
        return None, err


# ------------------------------------------------------------------- twilio REST
_TWILIO_HOST = "api.twilio.com"


def _twilio_post(path: str, params: dict, tool_name: str) -> tuple[dict | None, dict | None]:
    """POST to the Twilio REST API with Basic auth. Returns (payload, error).

    Reads credentials from the phase9 config; every failure is a precise
    {"error"} naming the missing piece or the Twilio-reported cause.
    """
    cfg = _phone_config()
    missing = [k for k in ("twilio_sid", "twilio_token") if not cfg.get(k)]
    if missing:
        return None, {"error": "twilio not configured: set " +
                               ", ".join(f"phone.{k}" for k in missing) +
                               " in dynamic/tools_config.phase9.yaml"}
    sid, token = cfg["twilio_sid"], cfg["twilio_token"]
    if not egress.check(tool_name, _TWILIO_HOST):
        return None, {"error": f"egress to {_TWILIO_HOST} blocked by policy"}
    url = f"https://{_TWILIO_HOST}/2010-04-01/Accounts/{sid}/{path}"
    body = urllib.parse.urlencode(params).encode("utf-8")
    creds = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "jarvis-sidecar/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload, None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            pass
        return None, {"error": f"twilio API error {e.code}: {detail or e.reason}"}
    except Exception as e:
        return None, {"error": f"twilio request failed: {type(e).__name__}: {e}"}


# ------------------------------------------------------------------- sqlite log
def _db_path() -> Path:
    override = _phone_config().get("db_path")
    if override:
        return Path(str(override)).expanduser()
    return DATA_DIR / "phone_calls.db"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS phone_calls ("
        "call_id TEXT PRIMARY KEY, to_number TEXT NOT NULL, "
        "started_at TEXT NOT NULL, status TEXT NOT NULL, "
        "voice TEXT, transcript_json TEXT NOT NULL DEFAULT '[]')")
    return conn


def _log_call(call_id: str, to_number: str, status: str, voice: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO phone_calls "
            "(call_id, to_number, started_at, status, voice, transcript_json) "
            "VALUES (?, ?, ?, ?, ?, COALESCE((SELECT transcript_json FROM phone_calls WHERE call_id = ?), '[]'))",
            (call_id, to_number, _utcnow(), status, voice, call_id))
        conn.commit()


def _set_call_status(call_id: str, status: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE phone_calls SET status = ? WHERE call_id = ?",
                     (status, call_id))
        conn.commit()


def _set_call_transcript(call_id: str, transcript: list[dict]) -> None:
    try:
        with _conn() as conn:
            conn.execute("UPDATE phone_calls SET transcript_json = ? WHERE call_id = ?",
                         (json.dumps(transcript), call_id))
            conn.commit()
    except Exception:
        pass  # finalizing must never break the hangup path


def _get_call(call_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT call_id, to_number, started_at, status, voice, transcript_json "
            "FROM phone_calls WHERE call_id = ?", (call_id,)).fetchone()
    if not row:
        return None
    try:
        transcript = json.loads(row[5] or "[]")
    except Exception:
        transcript = []
    return {"call_id": row[0], "to": row[1], "started_at": row[2],
            "status": row[3], "voice": row[4], "transcript": transcript}


# ------------------------------------------------------- session registry (real path)
# Live sessions keyed by Twilio call SID. The parent's websocket server calls
# ensure_session(call_id) when Twilio opens the media stream.
_SESSIONS: dict[str, PhoneCallSession] = {}
_PENDING_VOICE: dict[str, str] = {}


def ensure_session(call_id: str, script_lines: list[str] | None = None) -> PhoneCallSession:
    """Get-or-create the live PhoneCallSession for a Twilio call.

    Loads real STT/TTS lazily; when a backend is missing the session keeps
    stt/tts as None so handle_event -> turn() returns the precise
    stt_unavailable / tts_unavailable error instead of faking audio.
    """
    if call_id in _SESSIONS:
        return _SESSIONS[call_id]
    stt, stt_err = get_stt()
    voice = _PENDING_VOICE.pop(call_id, None)
    tts, tts_err = get_tts(voice)
    session = PhoneCallSession(call_id, script_lines or [], agent=_AGENT,
                               stt=stt, tts=tts)
    session.finalize_cb = _set_call_transcript
    if stt_err or tts_err:
        session.status = "degraded"
    _SESSIONS[call_id] = session
    return session


def drop_session(call_id: str) -> None:
    _SESSIONS.pop(call_id, None)


# ------------------------------------------------------------ phone.call_and_talk
def call_and_talk(args: dict) -> dict:
    """Place a Twilio outbound call whose audio streams back to us for a real
    conversation. HIGH risk, needs network, needs user confirmation."""
    if not isinstance(args, dict):
        args = {}
    blocked = _enabled("phone.call_and_talk")
    if blocked:
        return blocked
    to = str(args.get("to", "")).strip()
    if not to:
        return {"error": "to is required (E.164, e.g. +15551234567)"}
    if not _E164.match(to):
        return {"error": f"invalid phone number {to!r}: must be E.164 like +15551234567"}
    script = args.get("script")
    if script is None:
        script = []
    if not isinstance(script, (list, tuple)):
        return {"error": "script must be a list of assistant lines"}
    script = [str(x) for x in script]
    voice = args.get("voice")
    voice = str(voice).strip() if voice else None

    cfg = _phone_config()
    missing = [k for k in ("twilio_sid", "twilio_token", "from_number")
               if not cfg.get(k)]
    if missing:
        return {"error": "twilio not configured: set " +
                         ", ".join(f"phone.{k}" for k in missing) +
                         " in dynamic/tools_config.phase9.yaml"}
    if not cfg.get("public_ws_url"):
        return {"error": "phone.public_ws_url not set — Twilio cannot reach your media stream"}

    twiml = ('<Response><Connect><Stream url="%s"/></Connect></Response>'
             % html.escape(cfg["public_ws_url"], quote=True))
    payload, err = _twilio_post(
        "Calls.json",
        {"To": to, "From": cfg["from_number"], "Twiml": twiml},
        "phone.call_and_talk")
    if err:
        return err
    call_id = payload.get("sid") if isinstance(payload, dict) else None
    if not call_id:
        return {"error": "twilio returned no call sid — call not placed"}
    if voice:
        _PENDING_VOICE[call_id] = voice
    try:
        _log_call(call_id, to, "ringing", voice)
    except Exception as e:
        return {"error": f"call placed ({call_id}) but logging failed: {type(e).__name__}: {e}"}
    return {"call_id": call_id, "status": "ringing", "to": to,
            "stream_url": cfg["public_ws_url"]}


# ---------------------------------------------------------------- phone.hangup
def hangup(args: dict) -> dict:
    """End a Twilio call (Status=completed) and mark it logged. Medium risk."""
    if not isinstance(args, dict):
        args = {}
    blocked = _enabled("phone.hangup")
    if blocked:
        return blocked
    call_id = str(args.get("call_id", "")).strip()
    if not call_id:
        return {"error": "call_id is required"}
    cfg = _phone_config()
    missing = [k for k in ("twilio_sid", "twilio_token") if not cfg.get(k)]
    if missing:
        return {"error": "twilio not configured: set " +
                         ", ".join(f"phone.{k}" for k in missing) +
                         " in dynamic/tools_config.phase9.yaml"}
    payload, err = _twilio_post(f"Calls/{urllib.parse.quote(call_id, safe='')}.json",
                                {"Status": "completed"}, "phone.hangup")
    if err:
        return err
    try:
        _set_call_status(call_id, "completed")
    except Exception as e:
        return {"error": f"twilio hung up {call_id} but logging failed: {type(e).__name__}: {e}"}
    drop_session(call_id)
    status = payload.get("status") if isinstance(payload, dict) else None
    return {"call_id": call_id, "status": status or "completed"}


# ------------------------------------------------------------------- phone.log
def phone_log(args: dict) -> dict:
    """Read a logged call: status + full transcript. Low risk, local sqlite."""
    if not isinstance(args, dict):
        args = {}
    blocked = _enabled("phone.log")
    if blocked:
        return blocked
    call_id = str(args.get("call_id", "")).strip()
    if not call_id:
        return {"error": "call_id is required"}
    try:
        row = _get_call(call_id)
    except Exception as e:
        return {"error": f"phone.log failed: {type(e).__name__}: {e}"}
    if row is None:
        return {"error": f"no call logged with call_id {call_id!r}"}
    return row


# ------------------------------------------------------------ phone.script_test
def script_test(args: dict) -> dict:
    """DRY-RUN: play a scripted conversation with zero Twilio involvement.

    Feeds synthetic Twilio Media Streams events (connected/start/media/stop)
    into a PhoneCallSession wired with a per-turn queued STT (the caller_lines
    in order) and a recording fake TTS. The bound agent answers when one is
    bound; otherwise the assistant replies with the next script line.

    This is the real test path for the conversation logic.
    """
    if not isinstance(args, dict):
        args = {}
    blocked = _enabled("phone.script_test")
    if blocked:
        return blocked
    script = args.get("script")
    caller_lines = args.get("caller_lines")
    if not isinstance(script, (list, tuple)) or not script:
        return {"error": "script is required (non-empty list of assistant lines)"}
    if not isinstance(caller_lines, (list, tuple)) or not caller_lines:
        return {"error": "caller_lines is required (non-empty list of caller lines)"}
    script = [str(x) for x in script]
    caller_lines = [str(x) for x in caller_lines]

    try:
        from ...voice.pipeline import FakeTTS
    except Exception as e:  # pragma: no cover - pipeline is stdlib-only
        return {"error": f"test harness broken: cannot import FakeTTS ({type(e).__name__}: {e})"}

    stt = QueuedSTT(list(caller_lines))
    tts = FakeTTS()
    call_id = "dryrun-" + _utcnow().replace(":", "").replace("+", "")
    session = PhoneCallSession(call_id, script, agent=_AGENT, stt=stt, tts=tts)

    events = [{"event": "connected"},
              {"event": "start",
               "start": {"streamSid": "MZ-dryrun", "callSid": call_id}}]
    # 3 media events x 8000 bytes = 3s of (silent) mulaw -> triggers turn().
    silent_3s = base64.b64encode(b"\xff" * 8000).decode("ascii")
    for _ in caller_lines:
        for _ in range(3):
            events.append({"event": "media", "media": {"payload": silent_3s}})
    events.append({"event": "stop"})

    turns = []
    for evt in events:
        out = session.handle_event(evt)
        if "error" in out:
            return {"error": f"dry run failed on event {evt.get('event')!r}: {out['error']}",
                    "transcript": session.transcript}
        if evt.get("event") == "media" and out.get("reply_text") is not None:
            turns.append({"caller_text": out.get("caller_text"),
                          "reply_text": out.get("reply_text"),
                          "audio_bytes": out.get("audio_bytes", 0)})
    return {"call_id": call_id, "turns": turns, "turn_count": len(turns),
            "transcript": session.transcript,
            "spoken": list(tts.spoken or []),
            "agent_bound": _AGENT is not None}


# ---------------------------------------------------------- registry wiring
PHONE_PACK_TOOLS = [
    Tool("phone.call_and_talk",
         "Place a Twilio outbound voice call that holds a real conversation via "
         "media streams + local STT/agent/TTS. IRREVERSIBLE — needs confirmation.",
         {"to": "string (E.164)", "script": "list of assistant lines",
          "voice": "optional Piper .onnx voice path"},
         call_and_talk, risk="high", needs_network=True),
    Tool("phone.hangup",
         "End a Twilio call by SID (Status=completed) and mark it logged.",
         {"call_id": "string"}, hangup, risk="medium", needs_network=True),
    Tool("phone.log",
         "Read a logged call's status and full transcript.",
         {"call_id": "string"}, phone_log, risk="low"),
    Tool("phone.script_test",
         "DRY-RUN a scripted phone conversation with no Twilio: feeds synthetic "
         "media-stream events through the real session + turn logic.",
         {"script": "list of assistant lines",
          "caller_lines": "list of caller lines"}, script_test, risk="low"),
]

RISK_TABLE_ADDITIONS = {
    "phone.call_and_talk": ("high", True),
    "phone.hangup": ("medium", True),
    "phone.log": ("low", False),
    "phone.script_test": ("low", False),
}


def register(reg) -> None:
    """Register the 4 phone tools. Merges RISK_TABLE_ADDITIONS into the policy
    table so the tools are not default-denied (unknown tools are denied)."""
    try:
        from ...agent.policy import RISK_TABLE
        RISK_TABLE.update(RISK_TABLE_ADDITIONS)
    except Exception:
        pass  # policy merge is best-effort; the dict is the contract
    for tool in PHONE_PACK_TOOLS:
        reg.register(tool)
