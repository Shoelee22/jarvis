"""Episodic Memory pack (Phase 14 "HYPER" capstone, Workstream C).

What this is: while mind_pack (Phase 12) remembers durable FACTS about the
user ("my gym is Iron Culture", "I prefer concise answers"), this pack
remembers what HAPPENED — dated life events: "finished the gym website",
"client X replied angrily", "the electrician came to fix the fan".
Semantic memory answers "what is true"; episodic memory answers "what
happened, and when".

Tools:
    memory.episode  low   Record a life event with a timestamp
                          (deterministic natural-language date parser;
                          unparseable dates are stored as-given with a flag).
    memory.recall   low   Time-aware search: full-text over title / what_happened /
                          outcome / people, optionally filtered by a time window
                          ("last week", "2026-09", "last Tuesday"). Answers
                          "what did we do last Tuesday?" and "when did I last
                          talk to the electrician?".
    memory.timeline low   Chronological replay of episodes in a date range,
                          oldest first.
    memory.link     low   Connect an episode to a SEMANTIC fact owned by
                          mind_pack. mind_pack's store is NOT imported or
                          duplicated here: the link is stored as
                          {episode_id, fact_text, fact_source:"mind_pack"} in
                          this pack's own sqlite table.
    memory.links    low   List an episode's fact links (link round-trip helper).

Module function (NOT a tool):
    note_from_audit(audit_rows) -> list of candidate episode dicts.
    Called by the nightly sleep.cycle to turn the sidecar audit log into
    episode PROPOSALS. It never writes anything — the caller decides what
    to record (propose, don't auto-write).

INTEGRATION NOTE FOR THE PARENT (wire this in):
    1. In sidecar/jarvis/tools/builtin/__init__.py:
       - extend the pack import line with `episodic_pack`, and
       - in build_registry(), extend the Phase 13 pack loop (or add a
         Phase 14 section) with:

             for _pack in (episodic_pack,):
                 _pack.register(reg)
                 _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    2. Suggested sleep.cycle call site (in sleep_pack.py's
       sleep_cycle_handler, after the day-summary step):

             from . import episodic_pack
             rows = _read_audit(w0)  # already available in the handler
             if rows:
                 candidates = episodic_pack.note_from_audit(rows)
                 for cand in candidates:
                     _propose(
                         f"Episodic memory candidate: {cand['title']}",
                         [{"tool": "memory.episode", "args": cand,
                           "why": "Drafted overnight from the audit log — "
                                  "record as an episode with one tap."}])

       The sleep cycle must NOT call memory.episode directly; episodes
       become record only via the approval queue (the user approves the
       bunch), consistent with the standing "propose, don't auto-write"
       rule for memory writes.

LINK CONTRACT (memory.link / memory.links):
    mind_pack owns durable facts in its own store (MemoryStore / mind.db).
    This pack deliberately does not import it. A link stores fact_text
    verbatim plus fact_source="mind_pack". A future thin forwarder can
    resolve fact_text against mind_pack's memory.learn store
    (e.g. fuzzy-match the text to confirm the fact still exists) without
    changing this table's shape.

SAFETY:
    - Handlers take dict -> return dict and never raise; failures return
      {"error": "..."}.
    - needs_network=False for all tools. Episodes stay local.
    - stdlib sqlite3 only. Db default ~/workspace/jarvis/data/episodic.db,
      overridable with the JARVIS_EPISODIC_DB env var (tests use a tmp file).
    - The date parser is deterministic: it understands ISO dates/datetimes
      and a fixed set of phrases ("today", "yesterday", "last Tuesday",
      "last week", "2026-09", ...). Anything else is stored verbatim with
      date_is_guess=1 and happened_at=now(), never silently misfiled.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
DEFAULT_DB_PATH = HOME / "workspace" / "jarvis" / "data" / "episodic.db"

_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_EPISODIC_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episodes("
        " id TEXT PRIMARY KEY,"
        " title TEXT NOT NULL,"
        " what_happened TEXT NOT NULL,"
        " outcome TEXT DEFAULT '',"
        " happened_at TEXT NOT NULL,"        # ISO datetime string (local)
        " happened_at_ts REAL NOT NULL,"    # epoch seconds, for range queries
        " date_is_guess INTEGER NOT NULL DEFAULT 0,"
        " when_raw TEXT DEFAULT '',"        # the verbatim `when` given
        " people_json TEXT DEFAULT '[]',"
        " created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(happened_at_ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episode_links("
        " id TEXT PRIMARY KEY,"
        " episode_id TEXT NOT NULL,"
        " fact_text TEXT NOT NULL,"
        " fact_source TEXT NOT NULL DEFAULT 'mind_pack',"
        " created_at TEXT NOT NULL)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_links_episode "
        "ON episode_links(episode_id)")
    # FTS5 over the searchable text columns. Content is synced manually on
    # insert (episodes are append-mostly; no update/delete tools exist).
    # ep_id is an UNINDEXED stored column linking each FTS row back to its
    # episode — FTS5 has no real foreign keys, so we join on it explicitly.
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5("
        "ep_id UNINDEXED, title, what_happened, outcome, people)")
    return conn


def _today() -> date:
    """Current local date. Module-level indirection so tests can monkeypatch
    a fixed reference date for deterministic 'last Tuesday' assertions."""
    return date.today()


# ---------------------------------------------------------------------------
# Deterministic natural-language date parser
# ---------------------------------------------------------------------------
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_AGO_RE = re.compile(r"^(\d+)\s+(day|days|week|weeks|month|months)\s+ago$")
_THIS_LAST_RE = re.compile(r"^(this|last|next)\s+(week|month|[a-z]+)$")


def _parse_when(when, ref: date | None = None) -> tuple[date, date, bool]:
    """Parse `when` into an inclusive (start_date, end_date) window.

    Returns (start, end, was_guessed). was_guessed is True when the text
    could not be understood — callers should store now() and keep the raw
    text with a flag instead of silently misfiling.
    """
    ref = ref or _today()
    if when is None or (isinstance(when, str) and not when.strip()):
        return ref, ref, False
    text = str(when).strip().lower()

    if text in ("today", "tonight"):
        return ref, ref, False
    if text == "yesterday":
        d = ref - timedelta(days=1)
        return d, d, False
    if text == "tomorrow":
        d = ref + timedelta(days=1)
        return d, d, False

    m = _ISO_RE.match(text)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return ref, ref, True
        return d, d, False

    m = _MONTH_RE.match(text)
    if m:
        try:
            y, mo = int(m.group(1)), int(m.group(2))
            start = date(y, mo, 1)
        except ValueError:
            return ref, ref, True
        end = (date(y + (mo == 12), (mo % 12) + 1, 1) - timedelta(days=1))
        return start, end, False

    m = _AGO_RE.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith("week"):
            delta = timedelta(weeks=n)
        elif unit.startswith("month"):
            delta = timedelta(days=30 * n)  # documented approximation
        else:
            delta = timedelta(days=n)
        d = ref - delta
        return d, d, False

    m = _THIS_LAST_RE.match(text)
    if m:
        which, word = m.group(1), m.group(2)
        if word in ("week", "month"):
            if word == "week":
                monday = ref - timedelta(days=ref.weekday())
                if which == "this":
                    return monday, monday + timedelta(days=6), False
                if which == "last":
                    return monday - timedelta(days=7), monday - timedelta(days=1), False
                nxt = monday + timedelta(days=7)
                return nxt, nxt + timedelta(days=6), False
            # month
            first = ref.replace(day=1)
            if which == "this":
                nxt = date(first.year + (first.month == 12),
                           (first.month % 12) + 1, 1)
                return first, nxt - timedelta(days=1), False
            if which == "last":
                prev_end = first - timedelta(days=1)
                return prev_end.replace(day=1), prev_end, False
            nxt_first = date(first.year + (first.month == 12),
                              (first.month % 12) + 1, 1)
            after = date(nxt_first.year + (nxt_first.month == 12),
                         (nxt_first.month % 12) + 1, 1)
            return nxt_first, after - timedelta(days=1), False
        if word in _WEEKDAYS:
            target = _WEEKDAYS[word]
            monday = ref - timedelta(days=ref.weekday())
            if which == "this":
                # This week's occurrence (Mon-Sun week; may be past or today).
                d = monday + timedelta(days=target)
                return d, d, False
            if which == "last":
                # Most recent occurrence strictly before today.
                delta = (ref.weekday() - target) % 7 or 7
                d = ref - timedelta(days=delta)
                return d, d, False
            # Next: upcoming occurrence strictly after today.
            delta = (target - ref.weekday()) % 7 or 7
            d = ref + timedelta(days=delta)
            return d, d, False

    if text in _WEEKDAYS:
        # Bare weekday name: most recent occurrence (today counts).
        target = _WEEKDAYS[text]
        delta = (ref.weekday() - target) % 7
        d = ref - timedelta(days=delta)
        return d, d, False

    return ref, ref, True  # unparseable


def _window_to_ts(start: date, end: date) -> tuple[float, float]:
    lo = datetime(start.year, start.month, start.day, 0, 0, 0).timestamp()
    hi = datetime(end.year, end.month, end.day, 23, 59, 59).timestamp()
    return lo, hi


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
def _fts_escape(query: str) -> str:
    """AND together individually-quoted terms so FTS5 syntax chars stay
    harmless and multi-word queries match docs containing all terms."""
    terms = [t for t in query.split() if t]
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _row_to_episode(row: sqlite3.Row) -> dict:
    import json as _json
    try:
        people = _json.loads(row["people_json"] or "[]")
    except (TypeError, ValueError):
        people = []
    return {
        "episode_id": row["id"],
        "title": row["title"],
        "what_happened": row["what_happened"],
        "outcome": row["outcome"] or "",
        "when": row["happened_at"],
        "date": row["happened_at"][:10],
        "date_is_guess": bool(row["date_is_guess"]),
        "people": people,
        "created_at": row["created_at"],
    }


def episode_handler(args: dict) -> dict:
    """memory.episode {title, what_happened, when?, people[]?, outcome?} —
    record a life event."""
    try:
        import json as _json
        a = args or {}
        title = (a.get("title") or "").strip()
        what = (a.get("what_happened") or "").strip()
        if not title:
            return {"error": "title is required (e.g. 'Finished the gym website')"}
        if not what:
            return {"error": "what_happened is required (a sentence or two)"}

        people = a.get("people") or []
        if not isinstance(people, list):
            return {"error": "people must be a list of names"}
        people = [str(p).strip() for p in people if str(p).strip()]
        outcome = str(a.get("outcome") or "").strip()

        when_raw = a.get("when")
        start, _end, guessed = _parse_when(when_raw)
        if guessed:
            # Unparseable: store now(), keep the raw text, flag the guess.
            happened = datetime.now().replace(microsecond=0)
            date_is_guess = 1
        else:
            happened = datetime(start.year, start.month, start.day, 0, 0, 0)
            # If an ISO datetime with a time was given, keep the time.
            m = _ISO_RE.match(str(when_raw or "").strip())
            if m and m.group(4):
                happened = happened.replace(
                    hour=int(m.group(4)), minute=int(m.group(5)),
                    second=int(m.group(6) or 0))
            date_is_guess = 0

        eid = uuid.uuid4().hex[:12]
        now_iso = datetime.now().replace(microsecond=0).isoformat()
        happened_iso = happened.isoformat()
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO episodes(id, title, what_happened, outcome,"
                    " happened_at, happened_at_ts, date_is_guess, when_raw,"
                    " people_json, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (eid, title, what, outcome, happened_iso,
                     happened.timestamp(), date_is_guess,
                     "" if when_raw is None else str(when_raw),
                     _json.dumps(people), now_iso))
                conn.execute(
                    "INSERT INTO episodes_fts(ep_id, title, what_happened,"
                    " outcome, people) VALUES (?,?,?,?,?)",
                    (eid, title, what, outcome, " ".join(people)))
                conn.commit()
            finally:
                conn.close()
        out = {"episode_id": eid, "title": title, "when": happened_iso,
               "date": happened_iso[:10]}
        if guessed:
            out["date_is_guess"] = True
            out["note"] = (f"could not parse when={when_raw!r}; stored "
                           "with the current time and flagged as a guess")
        return out
    except Exception as exc:
        return {"error": f"memory.episode failed: {exc}"}


def recall_handler(args: dict) -> dict:
    """memory.recall {query, when?} — time-aware search over episodes."""
    try:
        a = args or {}
        query = (a.get("query") or "").strip()
        when = a.get("when")
        n = a.get("n", 10)
        try:
            n = int(n)
        except (TypeError, ValueError):
            return {"error": "n must be an integer"}
        n = max(1, min(n, 50))

        params: list = []
        window_clause = ""
        if when:
            start, end, guessed = _parse_when(when)
            if guessed:
                return {"error": (f"could not parse the time window "
                                  f"'{when}' — try 'last week', 'last Tuesday', "
                                  f"'2026-09', or an ISO date")}
            lo, hi = _window_to_ts(start, end)
            window_clause = " AND e.happened_at_ts BETWEEN ? AND ?"
            params.extend([lo, hi])

        with _LOCK:
            conn = _connect()
            try:
                if query:
                    match = _fts_escape(query)
                    try:
                        rows = conn.execute(
                            "SELECT e.* FROM episodes_fts f"
                            " JOIN episodes e ON e.id = f.ep_id"
                            " WHERE episodes_fts MATCH ?" + window_clause +
                            " ORDER BY bm25(episodes_fts), e.happened_at_ts DESC"
                            " LIMIT ?",
                            (match, *params, n)).fetchall()
                    except sqlite3.OperationalError as exc:
                        return {"error": f"search query rejected by FTS5: {exc}"}
                elif window_clause:
                    rows = conn.execute(
                        "SELECT * FROM episodes e WHERE 1=1" + window_clause +
                        " ORDER BY e.happened_at_ts DESC LIMIT ?",
                        (*params, n)).fetchall()
                else:
                    return {"error": ("memory.recall needs a query, a time "
                                      "window, or both")}
            finally:
                conn.close()
        episodes = [_row_to_episode(r) for r in rows]
        return {"query": query or None, "when": str(when) if when else None,
                "episodes": episodes, "count": len(episodes)}
    except Exception as exc:
        return {"error": f"memory.recall failed: {exc}"}


def timeline_handler(args: dict) -> dict:
    """memory.timeline {from, to} — chronological replay, oldest first."""
    try:
        a = args or {}
        start, _e1, g1 = _parse_when(a.get("from") or "today")
        _s2, end, g2 = _parse_when(a.get("to") or "today")
        if g1:
            return {"error": (f"could not parse 'from'={a.get('from')!r} — "
                              "use an ISO date or a phrase like 'last week'")}
        if g2:
            return {"error": (f"could not parse 'to'={a.get('to')!r} — "
                              "use an ISO date or a phrase like 'today'")}
        if end < start:
            return {"error": f"'from' ({start}) is after 'to' ({end})"}
        lo, hi = _window_to_ts(start, end)
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM episodes"
                    " WHERE happened_at_ts BETWEEN ? AND ?"
                    " ORDER BY happened_at_ts ASC", (lo, hi)).fetchall()
            finally:
                conn.close()
        episodes = [_row_to_episode(r) for r in rows]
        return {"from": start.isoformat(), "to": end.isoformat(),
                "episodes": episodes, "count": len(episodes)}
    except Exception as exc:
        return {"error": f"memory.timeline failed: {exc}"}


def link_handler(args: dict) -> dict:
    """memory.link {episode_id, fact} — connect an episode to a semantic
    fact owned by mind_pack. The fact text is stored verbatim with
    fact_source='mind_pack'; a future thin forwarder can resolve it against
    mind_pack's memory.learn store."""
    try:
        a = args or {}
        eid = (a.get("episode_id") or "").strip()
        fact = (a.get("fact") or "").strip()
        if not eid:
            return {"error": "episode_id is required"}
        if not fact:
            return {"error": "fact is required (the semantic fact text, e.g. "
                             "'User's gym is Iron Culture')"}
        with _LOCK:
            conn = _connect()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM episodes WHERE id=?", (eid,)).fetchone()
                if exists is None:
                    return {"error": f"no episode with id '{eid}'"}
                lid = uuid.uuid4().hex[:12]
                conn.execute(
                    "INSERT INTO episode_links(id, episode_id, fact_text,"
                    " fact_source, created_at) VALUES (?,?,?,?,?)",
                    (lid, eid, fact, "mind_pack",
                     datetime.now().replace(microsecond=0).isoformat()))
                conn.commit()
            finally:
                conn.close()
        return {"episode_id": eid, "link_id": lid, "fact": fact,
                "fact_source": "mind_pack",
                "note": ("link stored; mind_pack's store was not touched — "
                         "a future forwarder can resolve this text against "
                         "mind_pack's memory.learn store")}
    except Exception as exc:
        return {"error": f"memory.link failed: {exc}"}


def links_handler(args: dict) -> dict:
    """memory.links {episode_id} — list an episode's fact links."""
    try:
        a = args or {}
        eid = (a.get("episode_id") or "").strip()
        if not eid:
            return {"error": "episode_id is required"}
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT id, fact_text, fact_source, created_at"
                    " FROM episode_links WHERE episode_id=? ORDER BY created_at",
                    (eid,)).fetchall()
            finally:
                conn.close()
        links = [{"link_id": r["id"], "fact": r["fact_text"],
                  "fact_source": r["fact_source"],
                  "created_at": r["created_at"]} for r in rows]
        return {"episode_id": eid, "links": links, "count": len(links)}
    except Exception as exc:
        return {"error": f"memory.links failed: {exc}"}


# ---------------------------------------------------------------------------
# note_from_audit — candidate episode synthesis for the sleep crew.
# NOT a registered tool. The nightly sleep.cycle calls this and queues the
# returned candidates as approval-queue proposals; it never writes episodes
# itself (propose, don't auto-write).
# ---------------------------------------------------------------------------
# Audit rows from sleep_pack._read_audit carry:
#   ts, actor, tool, args_json, result_summary, risk
# The task spec also allows the generic shape:
#   timestamp, actor, tool, args_summary
_NOISE_TOOLS = {
    "heartbeat", "sleep.tick", "sleep.cycle", "sleep.review",
    "autopilot.status", "autopilot.list", "system.uptime",
    "screen.capture", "battery.status",
}
# Coarse buckets for summarizing a day's activity.
_CREATE_TOOLS = {"website.build", "code.run", "video.compose",
                 "media.image", "social.post_instagram"}
_RESEARCH_TOOLS = {"web.search", "web.fetch", "browser.task"}
_MESSAGE_TOOLS = {"gmail.send", "telegram.send", "email.send"}
_FAILURE_RE = re.compile(r"(?i)(error|traceback|denied|failed)")


def _audit_get(row: dict, *keys, default=""):
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return default


def note_from_audit(audit_rows: list[dict]) -> list[dict]:
    """Synthesize candidate episodes from audit rows. Returns a list of
    episode-shaped dicts {title, what_happened, when, people?, outcome?}
    WITHOUT writing anything to the db.

    Heuristic (deliberately simple, documented here):
      1. Rows are grouped by local calendar day.
      2. Noise rows are skipped: heartbeats/ticks, the sleep cycle's own
         bookkeeping (sleep.*), status probes (autopilot.status,
         system.uptime, battery.status, screen.capture).
      3. Per day, per tool, rows are counted. Tools seen fewer than 2 times
         and with no error and no "creation" signal are dropped as quiet-day
         background (a single web.search is noise; 3+ becomes research).
      4. Kept signals become one candidate each:
         - any _CREATE_TOOLS use            -> "Built something with <tool>"
         - _RESEARCH_TOOLS seen >= 3 times   -> "Researched <topic> N times"
         - _MESSAGE_TOOLS use                -> "Reached out via <tool>"
         - tool with >= 3 failure-looking summaries -> "Error spike in <tool>"
         - any other tool seen >= 4 times    -> "Repeated <tool> activity"
    Limits: topics are guessed from args (first 60 chars), not understood;
    failure detection is regex-based on the summary text; people are never
    inferred from audit rows (the caller can add them). A day with nothing
    above threshold yields no candidates — silence is fine.
    """
    try:
        if not audit_rows:
            return []
        by_day: dict[date, list[dict]] = {}
        for row in audit_rows:
            if not isinstance(row, dict):
                continue
            ts = _audit_get(row, "timestamp", "ts")
            try:
                dt = datetime.fromtimestamp(float(ts))
            except (TypeError, ValueError):
                continue
            by_day.setdefault(dt.date(), []).append(row)

        candidates: list[dict] = []
        for day in sorted(by_day):
            rows = [r for r in by_day[day]
                    if str(_audit_get(r, "tool")).strip() not in _NOISE_TOOLS]
            if not rows:
                continue
            by_tool: dict[str, list[dict]] = {}
            for r in rows:
                by_tool.setdefault(str(_audit_get(r, "tool")).strip(), []).append(r)

            for tool, trows in sorted(by_tool.items()):
                if not tool:
                    continue
                n = len(trows)
                summaries = [str(_audit_get(r, "args_summary", "result_summary",
                                            "args_json")) for r in trows]
                failures = sum(1 for s in summaries if _FAILURE_RE.search(s))
                when_iso = day.isoformat()

                def _topic() -> str:
                    # Coarse topic guess: first non-empty summary, truncated.
                    for s in summaries:
                        s = s.strip()
                        if s:
                            return s[:60]
                    return tool

                if failures >= 3:
                    candidates.append({
                        "title": f"Error spike in {tool}",
                        "what_happened": (f"{failures} of {n} calls to {tool} "
                                          f"on {when_iso} looked like failures."),
                        "when": when_iso,
                        "outcome": "worth a look in the morning",
                    })
                elif tool in _CREATE_TOOLS:
                    candidates.append({
                        "title": f"Built something with {tool}",
                        "what_happened": (f"Used {tool} {n} time(s) on "
                                          f"{when_iso}: {_topic()}."),
                        "when": when_iso,
                    })
                elif tool in _RESEARCH_TOOLS and n >= 3:
                    candidates.append({
                        "title": f"Researched {_topic()}",
                        "what_happened": (f"Looked up '{_topic()}' {n} times "
                                          f"on {when_iso}."),
                        "when": when_iso,
                    })
                elif tool in _MESSAGE_TOOLS:
                    candidates.append({
                        "title": f"Reached out via {tool}",
                        "what_happened": (f"Sent {n} message(s) with {tool} "
                                          f"on {when_iso}."),
                        "when": when_iso,
                    })
                elif n >= 4:
                    candidates.append({
                        "title": f"Busy day with {tool}",
                        "what_happened": (f"Used {tool} {n} times on "
                                          f"{when_iso}."),
                        "when": when_iso,
                    })
                # else: quiet background — deliberately no candidate.
        return candidates
    except Exception:
        # Never raise: the sleep crew must not crash on a synthesis bug.
        return []


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "memory.episode",
     "description": ("Record a life event: what HAPPENED, with a timestamp "
                     "(title, what_happened, optional when/people/outcome). "
                     "'when' accepts ISO dates/datetimes or phrases like "
                     "'today', 'yesterday', 'last Tuesday', 'last week', "
                     "'2026-09'. Unparseable dates are stored with the "
                     "current time and flagged, never silently misfiled. "
                     "Episodic memory (events) complements mind_pack's "
                     "semantic memory (durable facts) — never stores facts."),
     "handler": episode_handler, "risk": "low", "needs_network": False,
     "schema": {"title": "string", "what_happened": "string",
                "when?": "string", "people?": "array", "outcome?": "string"}},
    {"name": "memory.recall",
     "description": ("Time-aware search over recorded episodes: full-text "
                     "over title, what happened, outcome and people, "
                     "optionally filtered by a time window ('last week', "
                     "'last Tuesday', '2026-09', ISO dates). Answers 'what "
                     "did we do last Tuesday?' and 'when did I last talk to "
                     "the electrician?'. Results ranked by text relevance, "
                     "recent episodes first on ties."),
     "handler": recall_handler, "risk": "low", "needs_network": False,
     "schema": {"query": "string?", "when?": "string", "n?": "int"}},
    {"name": "memory.timeline",
     "description": ("Chronological replay of episodes between 'from' and "
                     "'to' (ISO dates or phrases like 'last week'), oldest "
                     "first."),
     "handler": timeline_handler, "risk": "low", "needs_network": False,
     "schema": {"from": "string", "to": "string"}},
    {"name": "memory.link",
     "description": ("Connect an episode to a SEMANTIC fact owned by "
                     "mind_pack. Stores {episode_id, fact_text, "
                     "fact_source:'mind_pack'} in this pack's own sqlite "
                     "table — mind_pack's store is never imported or "
                     "duplicated. A future thin forwarder can resolve "
                     "fact_text against mind_pack's memory.learn store."),
     "handler": link_handler, "risk": "low", "needs_network": False,
     "schema": {"episode_id": "string", "fact": "string"}},
    {"name": "memory.links",
     "description": ("List an episode's fact links (the memory.link "
                     "round-trip helper)."),
     "handler": links_handler, "risk": "low", "needs_network": False,
     "schema": {"episode_id": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(episodic_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "memory.episode": ("low", False),
    "memory.recall": ("low", False),
    "memory.timeline": ("low", False),
    "memory.link": ("low", False),
    "memory.links": ("low", False),
}


def register(reg) -> None:
    """Wire the five Episodic Memory pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
