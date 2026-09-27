"""Phone conversation session: the Twilio Media Streams side of a real voice call.

Twilio opens a websocket to `phone.public_ws_url` and sends JSON events:
  "connected" -> the socket is up
  "start"     -> stream metadata (streamSid, callSid, ...)
  "media"     -> base64 mulaw 8kHz audio from the caller
  "mark"      -> the caller finished hearing our audio (turn boundary)
  "stop"      -> the call ended

When enough caller audio accumulates (3 seconds) or a "mark" arrives, we run
one conversation turn: STT -> agent/script reply -> TTS -> mulaw bytes the
parent's websocket server sends back down the stream.

No third-party dependencies: mulaw encode/decode is pure Python, STT/TTS are
passed in (real ones from voice.pipeline, fakes for tests and dry runs).

Handlers never raise; every failure is a precise {"error": ...} naming what
is missing (stt_unavailable, tts_unavailable, unknown event, ...).
"""
from __future__ import annotations

import base64
import datetime as _dt

# mulaw 8kHz mono: 8000 bytes == 1 second of caller audio.
TURN_BYTES = 3 * 8000


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------- mulaw codecs
# Pure-Python G.711 mu-law, so the session has zero new dependencies.
_MULAW_BIAS = 0x84


def mulaw_decode(code: int) -> int:
    """One mulaw byte -> signed 16-bit PCM sample."""
    code = (~code) & 0xFF
    sign = code & 0x80
    exponent = (code >> 4) & 0x07
    mantissa = code & 0x0F
    sample = (((mantissa << 3) + _MULAW_BIAS) << exponent) - _MULAW_BIAS
    return -sample if sign else sample


def mulaw_encode(sample: int) -> int:
    """Signed 16-bit PCM sample -> one mulaw byte."""
    if sample < -32768:
        sample = -32768
    elif sample > 32767:
        sample = 32767
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > 32635:
        sample = 32635
    sample += _MULAW_BIAS
    # exponent: index of highest set bit of (sample >> 7), i.e. segment number
    shifted = sample >> 7
    exponent = 0
    while shifted > 1:
        shifted >>= 1
        exponent += 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def mulaw_bytes_to_pcm16(raw: bytes) -> bytes:
    """Twilio's mulaw 8kHz -> PCM16 8kHz bytes (what an STT expects)."""
    out = bytearray(len(raw) * 2)
    for i, b in enumerate(raw):
        s = mulaw_decode(b)
        out[2 * i] = s & 0xFF
        out[2 * i + 1] = (s >> 8) & 0xFF
    return bytes(out)


def pcm16_16k_chunks_to_mulaw_8k(chunks: list[bytes]) -> bytes:
    """TTS PCM16 16kHz chunks -> Twilio's mulaw 8kHz wire format.

    Nearest-neighbor downsample (take every 2nd sample) then mu-law encode.
    """
    out = bytearray()
    for chunk in chunks:
        n = (len(chunk) // 2)
        for i in range(0, n, 2):
            s = int.from_bytes(chunk[2 * i:2 * i + 2], "little", signed=True)
            out.append(mulaw_encode(s))
    return bytes(out)


# ------------------------------------------------------------------ dry-run STT
class QueuedSTT:
    """Deterministic per-turn STT for dry runs: each turn pops the next queued
    caller line. Matches the FakeSTT contract (transcribe_stream -> generator
    of {"type": "final", "text": ...}) but returns lines in order across turns,
    so a scripted dry run plays out one line per turn."""

    def __init__(self, lines: list[str]):
        self._lines = list(lines or [])

    def transcribe_stream(self, pcm_chunks):
        if self._lines:
            yield {"type": "final", "text": self._lines.pop(0)}
        # silence: no yield -> caller said nothing this turn


# ------------------------------------------------------------------- the session
class PhoneCallSession:
    """One live phone conversation. Feed Twilio Media Streams events into
    handle_event(); it returns dicts describing what happened (and, on a turn,
    the mulaw audio bytes to play back to the caller).

    transcript: list of {"who": "caller"|"assistant", "text": ..., "ts": ...}.
    """

    def __init__(self, call_id: str, script_lines: list[str] | None,
                 agent=None, stt=None, tts=None):
        self.call_id = call_id
        self.script_lines = [str(x) for x in (script_lines or [])]
        self.agent = agent          # optional Agent; agent.run(text) -> {"reply": ...}
        self.stt = stt              # STT backend (or None -> honest error)
        self.tts = tts              # TTS backend (or None -> honest error)
        self.transcript: list[dict] = []
        self.connected = False
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.status = "created"
        self.finalize_cb = None     # set by phone_pack: cb(call_id, transcript)
        self._buf = bytearray()     # accumulated mulaw bytes from the caller
        self._script_idx = 0
        self._turns = 0

    # ------------------------------------------------------------- event intake
    def handle_event(self, evt: dict) -> dict:
        """Route one Twilio Media Streams event. Never raises."""
        try:
            if not isinstance(evt, dict):
                return {"error": "event must be a dict"}
            ev = evt.get("event")
            if ev == "connected":
                self.connected = True
                self.status = "connected"
                return {"ok": True, "event": "connected"}
            if ev == "start":
                start = evt.get("start") or {}
                self.stream_sid = start.get("streamSid")
                self.call_sid = start.get("callSid")
                self.status = "streaming"
                return {"ok": True, "event": "start",
                        "stream_sid": self.stream_sid, "call_sid": self.call_sid}
            if ev == "media":
                payload = (evt.get("media") or {}).get("payload", "")
                if not payload:
                    return {"error": "media event missing audio payload"}
                try:
                    raw = base64.b64decode(payload, validate=True)
                except Exception:
                    return {"error": "invalid base64 in media payload"}
                self._buf += raw
                buffered_ms = len(self._buf) // 8  # 8 mulaw bytes == 1 ms
                if len(self._buf) >= TURN_BYTES:
                    return self.turn()
                return {"ok": True, "event": "media", "buffered_ms": buffered_ms}
            if ev == "mark":
                # The caller finished hearing our last reply -> take a turn.
                return self.turn()
            if ev == "stop":
                self.status = "ended"
                self._finalize()
                return {"ok": True, "event": "stop",
                        "turns": self._turns, "transcript": self.transcript}
            return {"error": f"unknown media stream event '{ev}'"}
        except Exception as e:  # pragma: no cover - defensive belt and braces
            return {"error": f"handle_event failed: {type(e).__name__}: {e}"}

    # ------------------------------------------------------------------- a turn
    def turn(self) -> dict:
        """One conversation turn: STT on buffered audio -> reply -> TTS.

        Returns {"caller_text", "reply_text", "audio"} on success, or a precise
        {"error"} (stt_unavailable / tts_unavailable) naming the missing piece.
        """
        # 1. Transcribe. The session only calls turn() when caller audio (or a
        # mark) demands it, so a missing STT is always a hard honest error.
        if self.stt is None:
            self._buf.clear()
            return {"error": "stt_unavailable: install faster-whisper / enable in config"}
        caller_text = ""
        if self._buf:
            pcm = mulaw_bytes_to_pcm16(bytes(self._buf))
            self._buf.clear()
            try:
                chunks = [pcm]
                texts = []
                for item in self.stt.transcribe_stream(chunks):
                    if isinstance(item, dict) and item.get("text"):
                        texts.append(str(item["text"]).strip())
                    elif isinstance(item, str) and item.strip():
                        texts.append(item.strip())
                caller_text = " ".join(t for t in texts if t)
            except Exception as e:
                return {"error": f"stt failed: {type(e).__name__}: {e}"}
        if not caller_text:
            return {"silence": True, "caller_text": ""}
        self.transcript.append({"who": "caller", "text": caller_text,
                                "ts": _utcnow()})

        # 2. Decide the reply: the bound agent, or the next scripted line.
        reply_text = ""
        if self.agent is not None:
            try:
                out = self.agent.run(caller_text)
                if isinstance(out, dict):
                    reply_text = str(out.get("reply", "") or "")
                else:
                    reply_text = str(out)
            except Exception as e:
                return {"error": f"agent reply failed: {type(e).__name__}: {e}"}
        else:
            if self._script_idx < len(self.script_lines):
                reply_text = self.script_lines[self._script_idx]
                self._script_idx += 1
            else:
                reply_text = "Thank you for calling — goodbye."
        reply_text = reply_text.strip()
        if not reply_text:
            self.transcript.append({"who": "assistant", "text": "",
                                    "ts": _utcnow()})
            return {"caller_text": caller_text, "reply_text": "",
                    "audio": b"", "note": "no reply text produced"}

        # 3. Synthesize. Missing TTS is a hard honest error — never fake audio.
        if self.tts is None:
            return {"error": "tts_unavailable: install piper-tts / download a voice model / enable in config"}
        try:
            chunks = list(self.tts.speak(reply_text))
        except Exception as e:
            return {"error": f"tts failed: {type(e).__name__}: {e}"}
        audio = pcm16_16k_chunks_to_mulaw_8k(chunks)
        self.transcript.append({"who": "assistant", "text": reply_text,
                                "ts": _utcnow()})
        self._turns += 1
        return {"caller_text": caller_text, "reply_text": reply_text,
                "audio": audio, "audio_bytes": len(audio)}

    # -------------------------------------------------------------- finalizing
    def _finalize(self) -> None:
        if self.finalize_cb is not None:
            try:
                self.finalize_cb(self.call_id, self.transcript)
            except Exception:
                pass  # finalizing must never break the hangup path
