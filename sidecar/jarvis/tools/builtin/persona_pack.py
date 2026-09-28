"""Persona Pack (Phase 13, Workstream D): conversational depth — the butler grows on you.

Six low-risk, local-only tools:

    persona.note      Record a durable TASTE note about the user ("loves concise
                      answers", "hates morning meetings"). TASTE, not facts:
                      stored separately from memory.learn facts and surfaced to
                      the agent as style guidance, never claimed as fact.
    persona.notes     List recorded taste notes.
    persona.greet     Compose a greeting LIVE from real sources: time of day +
                      live weather (weather.now via the bound registry when
                      available, else the pack handler directly) + unread mail
                      count (inbox.triage) + one standing persona note. Every
                      component is labeled with its source; missing sources are
                      omitted, never invented. Butler voice, addressed to "sir".
    persona.followup  Scan an utterance for future commitments ("my flight
                      Friday", "call mom tomorrow") -> extract {what, when}
                      with a small honest date parser. Ambiguous dates
                      ("soon") are returned as UNREGISTERED candidates for the
                      agent to confirm. Confident extractions are registered
                      via loops_pack (lazy import); when loops_pack is absent
                      the candidate is returned as a suggestion instead.
    persona.mood      Record a brief note on recent interaction tone ("user
                      seems rushed"). Tone calibration only.
    persona.tone      Return current calibration: brevity/warmth guidance
                      derived from recent mood notes. NEVER performs fake
                      emotion — it only tunes how real replies are phrased.

SAFETY:
    - Handlers take dict -> return dict and never raise; failures return
      {"error": "..."}.
    - All tools low risk, needs_network=False. Nothing here sends, executes,
      or schedules anything by itself (persona.followup only registers via
      loops_pack when that pack exists).
    - Taste notes are NOT memory facts: they live in persona.db, carry a
      "taste, not fact" label, and are handed to the agent as style guidance.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/persona.db,
      overridable with the JARVIS_PERSONA_DB env var (tests use a tmp file).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "persona.db"

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/teach_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# sqlite plumbing
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_PERSONA_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS persona_notes("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " note TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mood_notes("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " note TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    return conn


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(fn):
    def wrapper(args: dict):
        try:
            return fn(args or {})
        except Exception as e:  # never raise out of a handler
            return {"error": f"{fn.__name__}: {e}"}
    return wrapper


# ---------------------------------------------------------------------------
# persona.note / persona.notes
# ---------------------------------------------------------------------------
_MAX_NOTE_LEN = 500


@_safe
def note_handler(args: dict) -> dict:
    observation = args.get("observation")
    if not isinstance(observation, str) or not observation.strip():
        return {"error": "persona.note: 'observation' is required (a short taste note about the user)"}
    note = re.sub(r"\s+", " ", observation.strip())
    if len(note) > _MAX_NOTE_LEN:
        return {"error": f"persona.note: keep it under {_MAX_NOTE_LEN} characters (taste notes are brief)"}
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT INTO persona_notes(note, created_at) VALUES (?,?)",
                (note, _now_iso()))
            conn.commit()
            nid = cur.lastrowid
        finally:
            conn.close()
    return {"recorded": True, "id": nid, "note": note,
            "kind": "taste, not fact",
            "guidance": ("Surfaced to the agent as STYLE guidance (how to talk "
                         "to the user), never claimed as a fact about them.")}


@_safe
def notes_handler(args: dict) -> dict:  # noqa: ARG001
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, note, created_at FROM persona_notes ORDER BY id").fetchall()
        finally:
            conn.close()
    notes = [{"id": r[0], "note": r[1], "created_at": r[2]} for r in rows]
    return {"notes": notes, "count": len(notes), "kind": "taste, not fact"}


def _latest_note() -> str | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT note FROM persona_notes ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# persona.greet — composed live, sources labeled, never canned
# ---------------------------------------------------------------------------
def _period(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 24:
        return "evening"
    return "night"


def _fetch_weather(lat: float, lon: float) -> dict | None:
    """Live weather or None. Bound registry first, pack handler directly next.

    Returns {"temperature_c", "conditions", "source"} — never invented.
    """
    args = {"latitude": lat, "longitude": lon}
    # Path 1: the live agent's registry (real policy gates apply).
    try:
        if _AGENT is not None:
            reg = getattr(_AGENT, "registry", None)
            if reg is not None and "weather.now" in getattr(reg, "tools", {}):
                out = reg.call("weather.now", dict(args), actor="agent")
                if isinstance(out, dict) and out.get("ok"):
                    res = out.get("result") or {}
                    if isinstance(res, dict) and "error" not in res:
                        return {"temperature_c": res.get("temperature_c"),
                                "conditions": res.get("conditions"),
                                "source": "weather.now"}
    except Exception:
        pass
    # Path 2: the pack handler directly (no registry available).
    try:
        from . import weather_pack
        res = weather_pack.weather_now(dict(args))
        if isinstance(res, dict) and "error" not in res:
            return {"temperature_c": res.get("temperature_c"),
                    "conditions": res.get("conditions"),
                    "source": "weather.now"}
    except Exception:
        pass
    return None


def _fetch_unread() -> dict | None:
    """Unread mail count via inbox.triage, or None when unavailable."""
    try:
        if _AGENT is not None:
            reg = getattr(_AGENT, "registry", None)
            if reg is not None and "inbox.triage" in getattr(reg, "tools", {}):
                out = reg.call("inbox.triage", {"limit": 30}, actor="agent")
                if isinstance(out, dict) and out.get("ok"):
                    res = out.get("result") or {}
                    if isinstance(res, dict) and "error" not in res:
                        msgs = res.get("messages") or []
                        return {"count": len(msgs), "source": "inbox.triage"}
    except Exception:
        pass
    try:
        from . import inbox_pack
        res = inbox_pack._triage_impl({"limit": 30})
        if isinstance(res, dict) and "error" not in res:
            msgs = res.get("messages") or []
            return {"count": len(msgs), "source": "inbox.triage"}
    except Exception:
        pass
    return None


@_safe
def greet_handler(args: dict) -> dict:
    now = datetime.now().astimezone()
    period = _period(now.hour)
    salutation = {"morning": "Good morning", "afternoon": "Good afternoon",
                  "evening": "Good evening", "night": "Good evening"}[period]

    components: list[dict] = [
        {"source": "clock",
         "text": f"{period} ({now.strftime('%H:%M')} local)"}]
    omitted: list[str] = []

    # Weather — only when coordinates are supplied.
    lat, lon = args.get("latitude"), args.get("longitude")
    if lat is not None or lon is not None:
        try:
            w = _fetch_weather(float(lat), float(lon))
        except (TypeError, ValueError):
            w = None
        if w and w.get("temperature_c") is not None:
            components.append({
                "source": "weather.now",
                "text": (f"{w['temperature_c']}°C, "
                         f"{w.get('conditions') or 'conditions unknown'}")})
        else:
            omitted.append("weather.now (unavailable or errored — omitted, not invented)")
    else:
        omitted.append("weather.now (no coordinates given — omitted)")

    # Unread mail.
    mail = _fetch_unread()
    if mail:
        components.append({
            "source": "inbox.triage",
            "text": f"{mail['count']} unread in the inbox"})
    else:
        omitted.append("inbox.triage (unavailable — omitted)")

    # One standing taste note.
    note = _latest_note()
    if note:
        components.append({"source": "persona.note",
                           "text": f"standing taste note: {note}"})

    # Compose the butler's greeting from the real components.
    lines = [f"{salutation}, sir."]
    for comp in components:
        src, text = comp["source"], comp["text"]
        if src == "clock":
            lines[0] += f" It is {text}."
        elif src == "weather.now":
            lines.append(f"Outside, {text} (per weather.now).")
        elif src == "inbox.triage":
            n = mail["count"] if mail else 0
            lines.append(
                f"You have {n} unread in the inbox, sir (per inbox.triage).")
        elif src == "persona.note":
            lines.append(
                f"And by your standing taste note — {note} — I shall "
                f"conduct myself accordingly (per persona.note).")
    greeting = " ".join(lines)

    return {"greeting": greeting, "components": components,
            "omitted": omitted, "period": period,
            "generated_at": _now_iso()}


# ---------------------------------------------------------------------------
# persona.followup — commitment extraction + honest date parsing
# ---------------------------------------------------------------------------
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_AMBIGUOUS_RE = re.compile(
    r"\b(soon|sometime|later|eventually|whenever|one day|some day|at some point)\b")
_FUZZY = re.compile(r"^[.\s,;!\u2014\-]+|[.\s,;!\u2014\-]+$")
_INTRO_RE = re.compile(
    r"^(?:please\s+)?(?:remind me to|don't forget to|remember to|"
    r"make sure (?:i|to)|i need to|i have to|i must|i should)\s+", re.IGNORECASE)


def _parse_date(text: str, today: date) -> tuple[date | None, str | None, str | None]:
    """Return (date, matched_phrase, label). date None => ambiguous/no date.

    Small, honest parser — documents its rules, never fabricates precision.
    """
    low = text.lower()

    m = re.search(r"\bday after tomorrow\b", low)
    if m:
        return today + timedelta(days=2), m.group(0), "the day after tomorrow"

    m = re.search(r"\btomorrow\b", low)
    if m:
        return today + timedelta(days=1), m.group(0), "tomorrow"

    m = re.search(r"\b(?:today|tonight)\b", low)
    if m:
        return today, m.group(0), "today"

    m = re.search(r"\bin\s+(\d{1,2})\s+days?\b", low)
    if m:
        return today + timedelta(days=int(m.group(1))), m.group(0), f"in {m.group(1)} days"

    wd_names = "|".join(_WEEKDAYS)
    m = re.search(rf"\bnext\s+({wd_names})\b", low)
    if m:
        target = _WEEKDAYS[m.group(1)]
        delta = (target - today.weekday()) % 7 or 7  # this week's occurrence
        return today + timedelta(days=delta + 7), m.group(0), f"next {m.group(1)}"

    m = re.search(rf"\bthis\s+({wd_names})\b", low)
    if m:
        target = _WEEKDAYS[m.group(1)]
        delta = (target - today.weekday()) % 7  # upcoming, today if it matches
        return today + timedelta(days=delta), m.group(0), f"this {m.group(1)}"

    m = re.search(rf"\b({wd_names})\b", low)
    if m:
        target = _WEEKDAYS[m.group(1)]
        delta = (target - today.weekday()) % 7  # upcoming, today if it matches
        return today + timedelta(days=delta), m.group(0), m.group(1)

    month_names = "|".join(_MONTHS)
    m = re.search(rf"\bon\s+({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", low)
    if m:
        month, daynum = _MONTHS[m.group(1)], int(m.group(2))
        try:
            d = date(today.year, month, daynum)
        except ValueError:
            return None, None, None
        if d < today:  # already passed this year -> next year
            try:
                d = date(today.year + 1, month, daynum)
            except ValueError:
                return None, None, None
        return d, m.group(0), f"{d.day} {d.strftime('%B')}"

    return None, None, None


def _clean_what(text: str, phrase: str) -> str:
    """Strip the date phrase, strip reminder intros, tidy."""
    what = text
    # Remove the matched date phrase (case-insensitive, first occurrence).
    idx = what.lower().find(phrase.lower())
    if idx != -1:
        what = (what[:idx] + " " + what[idx + len(phrase):]).strip()
    what = _INTRO_RE.sub("", what).strip()
    what = _FUZZY.sub("", what).strip()
    what = re.sub(r"\s+", " ", what)
    return what


_FILLER = {"it", "that", "this", "things", "stuff", "something"}


@_safe
def followup_handler(args: dict) -> dict:
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"error": "persona.followup: 'text' is required (the user's utterance)"}
    text = text.strip()
    today = date.today()

    # Ambiguous timing: return a candidate, do NOT register.
    if _AMBIGUOUS_RE.search(text.lower()):
        return {"detected": True, "registered": False,
                "candidate": {"what": text, "when": None, "when_label": None},
                "reason": ("Timing is ambiguous ('soon'/'later' etc.) — no "
                           "loop was registered. Ask the user for a concrete "
                           "day/time before registering.")}

    # Questions are not commitments.
    if text.rstrip().endswith("?"):
        return {"detected": False, "registered": False,
                "reason": "The utterance is a question, not a commitment."}

    when, phrase, label = _parse_date(text, today)
    if when is None:
        return {"detected": False, "registered": False,
                "reason": ("No concrete date found in the utterance (the small "
                           "parser handles: today/tomorrow/day after tomorrow, "
                           "this|next <weekday>, bare <weekday>, in N days, "
                           "on <month> <day>). Nothing registered.")}

    what = _clean_what(text, phrase)
    if not what or what.lower() in _FILLER or len(what) < 2:
        return {"detected": False, "registered": False,
                "reason": "A date was found but no actionable 'what' survived cleaning."}

    candidate = {"what": what, "when": when.isoformat(),
                 "when_label": when.strftime("%A, %-d %B %Y")}

    # Register via loops_pack (lazy import — its absence is not a failure).
    try:
        from . import loops_pack  # noqa: PLC0415
    except ImportError:
        return {"detected": True, "registered": False, "candidate": candidate,
                "reason": ("loops_pack is not installed yet — candidate "
                           "returned as a suggestion; nothing registered.")}
    for entry in ("register_loop", "add_loop", "create_loop"):
        fn = getattr(loops_pack, entry, None)
        if callable(fn):
            try:
                out = fn(what, when.isoformat(), text)
            except Exception as exc:
                return {"detected": True, "registered": False,
                        "candidate": candidate,
                        "reason": f"loops_pack.{entry} raised: {exc}"}
            return {"detected": True, "registered": True, "candidate": candidate,
                    "via": f"loops_pack.{entry}", "loops_result": out}
    return {"detected": True, "registered": False, "candidate": candidate,
            "reason": ("loops_pack is present but exposes no known register "
                       "entry point (looked for register_loop/add_loop/"
                       "create_loop) — candidate returned as a suggestion.")}


# ---------------------------------------------------------------------------
# persona.mood / persona.tone — calibration only, never performed emotion
# ---------------------------------------------------------------------------
_TERSE_RE = re.compile(
    r"\b(rushed|rushing|hurry|hurrying|quick|quickly|brief|busy|impatient|"
    r"terse|short on time|in a rush|no time)\b", re.IGNORECASE)
_VERBOSE_RE = re.compile(
    r"\b(detailed|thorough|thoroughly|explain|walk me through|in depth|"
    r"deep dive|elaborate|step by step)\b", re.IGNORECASE)
_WARM_RE = re.compile(
    r"\b(joking|joke|funny|playful|laughing|cheerful|happy|excited|"
    r"amused|light-hearted|lighthearted)\b", re.IGNORECASE)
_DRY_RE = re.compile(
    r"\b(formal|serious|professional|strictly business|no jokes|"
    r"business-like)\b", re.IGNORECASE)


def _recent_moods(limit: int = 10) -> list[str]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT note FROM mood_notes ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            conn.close()
    return [r[0] for r in rows]


def _calibrate(moods: list[str]) -> dict:
    terse = sum(1 for n in moods if _TERSE_RE.search(n))
    verbose = sum(1 for n in moods if _VERBOSE_RE.search(n))
    warm = sum(1 for n in moods if _WARM_RE.search(n))
    dry = sum(1 for n in moods if _DRY_RE.search(n))

    if terse >= 3 or (terse >= 2 and terse > verbose):
        brevity, brevity_why = "terse", f"{terse} recent mood note(s) say he is rushed"
    elif verbose >= 3 or (verbose >= 2 and verbose > terse):
        brevity, brevity_why = "verbose", f"{verbose} recent mood note(s) ask for detail"
    else:
        brevity, brevity_why = "normal", "no strong signal in recent mood notes"

    if dry >= 2 and dry > warm:
        warmth, warmth_why = "dry", f"{dry} recent mood note(s) ask for formality"
    elif warm >= 2:
        warmth, warmth_why = "warm", f"{warm} recent mood note(s) show a playful mood"
    else:
        warmth, warmth_why = "warm", "the butler's default: politely warm"

    bits = []
    if brevity == "terse":
        bits.append("Keep replies short; lead with the answer; skip preamble and small talk.")
    elif brevity == "verbose":
        bits.append("Give full explanations with steps; he is in a learning mood.")
    else:
        bits.append("Normal reply length; concise but complete.")
    if warmth == "dry":
        bits.append("Keep tone professional and dry — no banter.")
    else:
        bits.append("Warm, composed butler tone is fine.")
    bits.append("This is calibration of brevity/warmth only — never performed emotion.")

    return {"brevity": brevity, "warmth": warmth,
            "guidance": " ".join(bits),
            "signals": {"terse": terse, "verbose": verbose,
                        "warm": warm, "dry": dry},
            "notes_considered": len(moods),
            "brevity_why": brevity_why, "warmth_why": warmth_why}


@_safe
def mood_handler(args: dict) -> dict:
    note = args.get("note")
    if not isinstance(note, str) or not note.strip():
        return {"error": "persona.mood: 'note' is required (a brief observation of interaction tone)"}
    clean = re.sub(r"\s+", " ", note.strip())
    if len(clean) > _MAX_NOTE_LEN:
        return {"error": f"persona.mood: keep it under {_MAX_NOTE_LEN} characters"}
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("INSERT INTO mood_notes(note, created_at) VALUES (?,?)",
                         (clean, _now_iso()))
            conn.commit()
        finally:
            conn.close()
    return {"recorded": True, "note": clean,
            "tone_now": _calibrate(_recent_moods()),
            "honesty": "Tone calibration only — this never performs or claims emotion."}


@_safe
def tone_handler(args: dict) -> dict:  # noqa: ARG001
    moods = _recent_moods()
    cal = _calibrate(moods)
    cal["honesty"] = ("Calibrates brevity/warmth of real replies only. "
                      "The assistant does not feel emotions and never claims to.")
    return cal


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "persona.note",
     "description": ("Record a durable TASTE note about the user ('loves concise "
                     "answers', 'hates morning meetings', 'prefers data over "
                     "adjectives'). TASTE is distinct from facts: these live in "
                     "persona.db (not memory.learn) and are surfaced to the "
                     "agent as style guidance, never claimed as facts."),
     "handler": note_handler, "risk": "low", "needs_network": False,
     "schema": {"observation": "string"}},
    {"name": "persona.notes",
     "description": "List the recorded taste notes about the user.",
     "handler": notes_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "persona.greet",
     "description": ("Compose a greeting LIVE from real sources, never canned: "
                     "time of day (clock) + live weather when latitude/longitude "
                     "are given (weather.now via the bound registry, or the pack "
                     "handler directly) + unread mail count (inbox.triage) + one "
                     "standing persona note. Every component is labeled with "
                     "its source; missing sources are omitted, never invented. "
                     "Spoken in the butler voice, addressed to 'sir'."),
     "handler": greet_handler, "risk": "low", "needs_network": False,
     "schema": {"latitude?": "number", "longitude?": "number"}},
    {"name": "persona.followup",
     "description": ("Scan a user utterance for future commitments ('my flight "
                     "Friday', 'call mom tomorrow'). A small honest date parser "
                     "extracts {what, when} (today/tomorrow/day after tomorrow, "
                     "this|next <weekday>, bare <weekday>, in N days, "
                     "on <month> <day>). Ambiguous timing ('soon') returns an "
                     "UNREGISTERED candidate for the agent to confirm. Confident "
                     "extractions register via loops_pack (lazy import); when "
                     "loops_pack is absent the candidate is returned as a "
                     "suggestion instead."),
     "handler": followup_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string"}},
    {"name": "persona.mood",
     "description": ("Record a brief note on recent interaction tone ('user "
                     "seems rushed', 'user is joking around'). Tone calibration "
                     "only: feeds persona.tone. Never performs emotion."),
     "handler": mood_handler, "risk": "low", "needs_network": False,
     "schema": {"note": "string"}},
    {"name": "persona.tone",
     "description": ("Current tone calibration: brevity/warmth guidance derived "
                     "from recent mood notes (e.g. several 'rushed' notes -> "
                     "'be terse'). Calibrates how real replies are phrased; "
                     "never performs or claims emotion."),
     "handler": tone_handler, "risk": "low", "needs_network": False,
     "schema": {}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(persona_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "persona.note": ("low", False),
    "persona.notes": ("low", False),
    "persona.greet": ("low", False),
    "persona.followup": ("low", False),
    "persona.mood": ("low", False),
    "persona.tone": ("low", False),
}


def register(reg) -> None:
    """Wire the six Persona pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
