"""Self-Learning Memory pack (Phase 12): JARVIS learns durable facts about the user.

Tools:
    memory.learn        low     Extract durable facts/preferences from a conversation
                                turn (rule-based, LLM-free, precision over recall).
    memory.consolidate  MEDIUM  Merge duplicates, resolve contradictions (newer wins),
                                drop low-value stale entries. Rewrites memory.
    memory.feedback     low     Store durable liked/disliked preferences from feedback.
    memory.routines     low     Detect repeated tool-use patterns from history and
                                PROPOSE standing rules. Never creates schedules.

FUTURE WIRING NOTE FOR THE PARENT (approval queue):
    Phase 11's approval queue (autopilot.propose) is being built in parallel by
    another crew and may not exist yet, so this module deliberately does NOT
    import it. memory.routines persists detected routine proposals in its own
    sqlite table (routine_proposals) AND returns them in the result. Once the
    approval queue exists, a thin forwarder can call
    registry.call("autopilot.propose", {"kind": "routine", ...}) for each
    proposal with status='proposed'. The tool itself only proposes — it never
    creates schedules, crons, or workers.

INTEGRATION NOTE FOR THE SIDECAR LOOP (parent: wire this in):
    In sidecar/jarvis/ipc/server.py (or wherever the registry is built)::

        from jarvis.tools.builtin import mind_pack
        mind_pack.register(registry)
        from jarvis.agent.policy import RISK_TABLE
        RISK_TABLE.update(mind_pack.RISK_TABLE_ADDITIONS)

    The brain.db MemoryStore is shared with the server (server.py already
    builds MemoryStore(settings.data_dir / "brain.db")); this pack reuses it —
    it does not modify store.py.

SAFETY:
    - needs_network=false for all tools. Memory stays local.
    - Handlers take dict -> return dict and never raise; failures return
      {"error": "..."}.
    - memory.consolidate is medium risk (rewrites memory) and goes through the
      policy confirmation flow like any other medium-risk tool.
    - The extractor is intentionally conservative: precision over recall.
      Ambiguous, hypothetical, or question turns yield nothing. Stored facts
      carry confidence < 1.0 and source "memory.learn" so the agent can treat
      them as candidates and confirm them with the user.
    - State lives in sqlite (stdlib only). Memory facts go to the shared
      brain.db (JARVIS_MIND_MEMORY_DB override); routine proposals and the
      lightweight usage log go to mind.db (JARVIS_MIND_DB override); routine
      detection prefers the sidecar audit log jarvis.db (JARVIS_MIND_AUDIT_DB
      override), falling back to the local usage log when the audit table is
      unavailable.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ...config import DATA_DIR
from ...memory.store import MemoryStore, _connect as _mem_connect


def _env_path(name: str, default: Path) -> str:
    return os.environ.get(name, str(default))


def _memory_store() -> MemoryStore:
    return MemoryStore(_env_path("JARVIS_MIND_MEMORY_DB", DATA_DIR / "brain.db"))


def _mind_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_env_path("JARVIS_MIND_DB", DATA_DIR / "mind.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, detail TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS routine_proposals(
        id TEXT PRIMARY KEY, detected_at INTEGER, tool TEXT, days_seen INTEGER,
        window_days INTEGER, tod_minutes INTEGER, pattern TEXT, suggestion TEXT,
        status TEXT DEFAULT 'proposed')""")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Rule-based fact extractor (LLM-free, precision-oriented)
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_TAIL_RE = re.compile(r"[.!?…\u2014:\-]+$")
# Vague adjective-only values that carry no durable signal ("my day is good").
_VAGUE_VALUES = {
    "good", "bad", "fine", "ok", "okay", "great", "terrible", "awful",
    "busy", "tired", "hard", "easy", "weird", "nice", "fine", "ok",
}
# Attributes too ephemeral to bother storing ("my mood is ...").
_EPHEMERAL_ATTRS = {
    "day", "mood", "life", "time", "luck", "problem", "issue", "question",
    "point", "thing", "stuff", "head", "turn", "gut", "fault", "day",
}
_UNCERTAIN_RE = re.compile(r"\b(if|whether|maybe|might be|could be|probably|i think)\b")


def _norm(text: str) -> str:
    return _PUNCT_TAIL_RE.sub("", _WS_RE.sub(" ", text.strip().lower())).strip()


def _clean(text: str) -> str:
    """Strip whitespace/punctuation but preserve original casing for storage."""
    return _PUNCT_TAIL_RE.sub("", _WS_RE.sub(" ", text.strip())).strip()


def _split_sentences(text: str) -> list[str]:
    # Keep "remember that ..." whole; split on sentence terminators otherwise.
    parts = re.split(r"(?<=[.!?])\s+|;\s*|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _extract_one(sentence: str) -> list[dict]:
    """Return candidate fact dicts {type, text, confidence} for one sentence."""
    s = sentence.strip()
    low = s.lower()
    if not s or low.endswith("?"):
        return []
    # Strip trailing sentence punctuation so `$`-anchored patterns match.
    s = _PUNCT_TAIL_RE.sub("", s).strip()
    low = s.lower()
    if not s:
        return []
    # Hypotheticals / uncertainties carry no durable signal.
    if _UNCERTAIN_RE.search(low):
        return []

    found: list[dict] = []

    m = re.match(r"(?i)^(?:please\s+)?remember that\s+(.+)$", s)
    if m:
        fact = _clean(m.group(1))
        if len(fact) >= 4:
            found.append({"type": "fact", "text": "User asked to remember: " + fact,
                          "confidence": 0.85})
        return found  # "remember that X" is explicit; nothing else to squeeze.

    m = re.match(r"(?i)^call me\s+([a-z][a-z\-']{0,29}(?:\s+[a-z][a-z\-']{0,29}){0,2})$", s)
    if m:
        found.append({"type": "identity",
                      "text": "User wants to be called " + m.group(1).strip(),
                      "confidence": 0.8})
        return found

    m = re.match(r"(?i)^my name is\s+([a-z][a-z\-']{0,29}(?:\s+[a-z][a-z\-']{0,29}){0,2})$", s)
    if m:
        found.append({"type": "identity",
                      "text": "User's name is " + m.group(1).strip(),
                      "confidence": 0.85})
        return found

    m = re.match(r"(?i)^i (?:really )?(prefer|like|love|enjoy)\s+(.+)$", s)
    if m:
        val = _clean(m.group(2))
        if 2 <= len(val) <= 80 and "http" not in val:
            verb = {"prefer": "prefers", "like": "likes",
                    "love": "loves", "enjoy": "enjoys"}[m.group(1).lower()]
            found.append({"type": "preference",
                          "text": f"User {verb} {val}", "confidence": 0.75})
        return found

    m = re.match(r"(?i)^i (?:really )?(?:don't|do not|dont)\s+like\s+(.+)$", s)
    if m:
        val = _clean(m.group(1))
        if 2 <= len(val) <= 80 and "http" not in val:
            found.append({"type": "preference",
                          "text": "User dislikes " + val, "confidence": 0.75})
        return found

    m = re.match(r"(?i)^i (?:really )?hate\s+(.+)$", s)
    if m:
        val = _clean(m.group(1))
        if 2 <= len(val) <= 80 and "http" not in val:
            found.append({"type": "preference",
                          "text": "User dislikes " + val, "confidence": 0.8})
        return found

    m = re.match(r"(?i)^my\s+([a-z]+(?:\s+[a-z]+){0,2})['\u2019]s name is\s+(.+)$", s)
    if m:
        attr, val = m.group(1).lower().strip(), _clean(m.group(2))
        if 2 <= len(val) <= 60 and "http" not in val.lower() and val.lower() not in _VAGUE_VALUES:
            found.append({"type": "fact",
                          "text": f"User's {attr}'s name is {val}",
                          "confidence": 0.75})
        return found

    m = re.match(r"(?i)^my\s+([a-z]+(?:\s+[a-z]+){0,2})\s+is\s+(.+)$", s)
    if m:
        attr, val = m.group(1).lower().strip(), _clean(m.group(2))
        if (attr not in _EPHEMERAL_ATTRS and 2 <= len(val) <= 60
                and "http" not in val and val not in _VAGUE_VALUES
                and not re.match(r"(?i)^(if|when|while)\b", val)):
            found.append({"type": "fact",
                          "text": f"User's {attr} is {val}", "confidence": 0.7})
        return found

    for pat, tmpl, conf in (
        (r"(?i)^i live in\s+(.+)$", "User lives in {}", 0.8),
        (r"(?i)^i(?:'m| am) from\s+(.+)$", "User is from {}", 0.8),
        (r"(?i)^i work (?:at|for|in)\s+(.+)$", "User works at {}", 0.75),
        (r"(?i)^i work as\s+(?:an?\s+)?(.+)$", "User works as {}", 0.75),
        (r"(?i)^my birthday is\s+(.+)$", "User's birthday is {}", 0.85),
        (r"(?i)^i was born (?:on|in)\s+(.+)$", "User was born {}", 0.85),
    ):
        m = re.match(pat, s)
        if m:
            val = _clean(m.group(1))
            if 2 <= len(val) <= 60 and "http" not in val:
                found.append({"type": "identity" if "birthday" in tmpl or "born" in tmpl else "fact",
                              "text": tmpl.format(val), "confidence": conf})
            return found

    return found


def extract_facts(text: str) -> list[dict]:
    """Run the rule-based extractor over a conversation turn.

    Precision over recall: chit-chat, questions, hypotheticals and vague
    statements yield nothing. Never invents facts not present in the text.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for sentence in _split_sentences(text or ""):
        for fact in _extract_one(sentence):
            key = _norm(fact["text"])
            if key not in seen:
                seen.add(key)
                out.append(fact)
    return out


def _log_usage(kind: str, detail: str) -> None:
    try:
        conn = _mind_conn()
        try:
            conn.execute("INSERT INTO usage_log(ts,kind,detail) VALUES(?,?,?)",
                         (int(time.time()), kind, detail))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # usage logging must never break a handler


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _safe(fn):
    def wrapper(args: dict):
        try:
            return fn(args or {})
        except Exception as e:  # never raise out of a handler
            return {"error": f"{fn.__name__}: {e}"}
    return wrapper


@_safe
def learn_handler(args: dict) -> dict:
    text = (args.get("text") or "").strip()
    source = (args.get("source") or "conversation").strip() or "conversation"
    if not text:
        return {"error": "memory.learn: 'text' is required"}
    candidates = extract_facts(text)
    if not candidates:
        _log_usage("learn", "no facts extracted")
        return {"stored": 0, "facts": [], "skipped": ["no durable facts detected"]}

    store = _memory_store()
    try:
        # Snapshot existing texts per type for exact/near dedupe (local, cheap).
        existing: dict[str, list[str]] = defaultdict(list)
        conn = _mem_connect(store.db_path)
        try:
            rows = conn.execute("SELECT type, text FROM memories").fetchall()
            for r in rows:
                existing[r["type"]].append(_norm(r["text"]))
        finally:
            conn.close()

        stored: list[dict] = []
        skipped: list[str] = []
        for cand in candidates:
            normed = _norm(cand["text"])
            dupe = any(normed == e or (len(normed) > 12 and normed in e)
                       or (len(e) > 12 and e in normed)
                       for e in existing[cand["type"]])
            if dupe:
                skipped.append(f"duplicate: {cand['text']}")
                continue
            mid = store.remember(cand["type"], cand["text"],
                                 confidence=cand["confidence"],
                                 source=f"memory.learn:{source}")
            existing[cand["type"]].append(normed)
            stored.append({"id": mid, **cand})
        _log_usage("learn", f"stored={len(stored)} skipped={len(skipped)}")
        return {"stored": len(stored), "facts": stored, "skipped": skipped}
    finally:
        store.close()


_ATTR_RE = re.compile(
    r"(?i)^(?:user'?s|my)\s+(.+?)\s+(?:is|are)\s+(.+)$")
_PREF_RE = re.compile(
    r"(?i)^user\s+(prefers|likes|loves|enjoys|dislikes)\s+(.+)$")


_AFFIRM_VERBS = {"prefers", "likes", "loves", "enjoys"}
_NEGATE_VERBS = {"dislikes"}


def _contradiction_key(mem_type: str, text: str) -> tuple[str, str] | None:
    """(group_key, family) for facts that can contradict each other.

    Facts: "User's gym is X" -> ("gym", "attr") — same attribute, different
    value means a contradiction. Preferences: "User likes tea" vs
    "User dislikes tea" -> ("tea", "affirm"/"negate") — a contradiction only
    when affirm and negate families meet on the same topic. Returns None when
    the text has no comparable structure.
    """
    m = _ATTR_RE.match(text)
    if m and mem_type in ("fact", "identity"):
        return (_norm(m.group(1)), "attr")
    m = _PREF_RE.match(text)
    if m and mem_type == "preference":
        verb, val = m.group(1).lower(), _norm(m.group(2))
        topic = val.split()[0] if val.split() else val
        family = "affirm" if verb in _AFFIRM_VERBS else (
            "negate" if verb in _NEGATE_VERBS else "other")
        return (topic, family)
    return None


@_safe
def consolidate_handler(args: dict) -> dict:
    store = _memory_store()
    try:
        conn = _mem_connect(store.db_path)
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, type, text, confidence, created_at, updated_at,"
                " expires_at, source FROM memories ORDER BY created_at").fetchall()]
        finally:
            conn.close()

        before = len(rows)
        merged: list[dict] = []
        resolved: list[dict] = []
        dropped: list[dict] = []
        now = int(time.time())
        keep: dict[str, dict] = {}       # id -> row, survivors
        kill_ids: set[str] = set()

        # 1) Exact duplicates -> keep the newest.
        by_text: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in rows:
            by_text[(r["type"], _norm(r["text"]))].append(r)
        for (mtype, normed), group in by_text.items():
            if len(group) > 1:
                group.sort(key=lambda r: (r["created_at"], r["id"]))
                winner = group[-1]
                for loser in group[:-1]:
                    kill_ids.add(loser["id"])
                    merged.append({"kept": winner["text"], "removed": loser["text"]})
                keep[winner["id"]] = winner
            else:
                keep[group[0]["id"]] = group[0]

        # 2) Contradictions -> newer wins, history note preserved.
        by_key: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for r in keep.values():
            ck = _contradiction_key(r["type"], r["text"])
            if ck:
                by_key[(r["type"], ck[0], ck[1])].append(r)
        seen_groups: set[tuple[str, str, str]] = set()
        for (mtype, key, family), group in list(by_key.items()):
            if (mtype, key, family) in seen_groups:
                continue
            texts = {_norm(r["text"]) for r in group}
            if family in ("affirm", "negate"):
                # Preference contradiction only when affirm meets negate on
                # the same topic; same-family variants are compatible.
                other = "negate" if family == "affirm" else "affirm"
                rivals = by_key.get((mtype, key, other), [])
                if not rivals:
                    continue
                seen_groups.add((mtype, key, other))
                group = list(group) + list(rivals)
            elif len(group) < 2 or len(texts) < 2:
                continue
            seen_groups.add((mtype, key, family))
            group.sort(key=lambda r: (r["created_at"], r["id"]))
            winner = group[-1]
            for loser in group[:-1]:
                kill_ids.add(loser["id"])
                note = (f"Superseded memory (contradiction resolved "
                        f"{datetime.fromtimestamp(now).strftime('%Y-%m-%d')}, "
                        f"newer wins): '{loser['text']}' -> '{winner['text']}'")
                store.remember("memory_history", note, confidence=0.5,
                               source="memory.consolidate")
                resolved.append({"kept": winner["text"],
                                 "superseded": loser["text"]})
                keep[winner["id"]] = winner
        for rid in list(keep):
            if rid in kill_ids:
                del keep[rid]

        # 3) Low-value stale entries: expired, or very low confidence and old.
        for r in keep.values():
            stale = False
            if r["expires_at"] and r["expires_at"] < now:
                stale = True
            elif (r["confidence"] or 0) < 0.35 and r["updated_at"] < now - 30 * 86400:
                stale = True
            if stale and r["type"] != "memory_history":
                kill_ids.add(r["id"])
                dropped.append({"text": r["text"],
                                "reason": "expired" if r["expires_at"] and r["expires_at"] < now
                                else "low confidence + stale"})
        for rid in list(keep):
            if rid in kill_ids:
                del keep[rid]

        if kill_ids:
            conn = _mem_connect(store.db_path)
            try:
                for rid in kill_ids:
                    conn.execute("DELETE FROM memories WHERE id=?", (rid,))
                    try:
                        store.vectors.remove(rid)
                    except Exception:
                        pass
                conn.commit()
            finally:
                conn.close()

        after = before - len(kill_ids)
        _log_usage("consolidate",
                   f"before={before} after={after} merged={len(merged)}")
        return {"before": before, "after": after,
                "merged_duplicates": merged,
                "contradictions_resolved": resolved,
                "dropped_stale": dropped}
    finally:
        store.close()


@_safe
def feedback_handler(args: dict) -> dict:
    about = (args.get("about") or "").strip()
    detail = (args.get("detail") or "").strip()
    if not about:
        return {"error": "memory.feedback: 'about' is required"}
    liked = args.get("liked")
    if not isinstance(liked, bool):
        return {"error": "memory.feedback: 'liked' must be true or false"}
    sentiment = "likes" if liked else "dislikes"
    text = f"User {sentiment} {about}"
    if detail:
        text += f": {detail}"
    store = _memory_store()
    try:
        mid = store.remember("preference", text, confidence=0.9,
                             source="memory.feedback")
        _log_usage("feedback", f"liked={liked} about={about[:60]}")
        return {"stored": True, "id": mid, "text": text, "liked": liked}
    finally:
        store.close()


def _history_rows(days: int) -> tuple[list[tuple[int, str]], str]:
    """(ts, tool) rows from the sidecar audit log, else the local usage log."""
    cutoff = int(time.time()) - days * 86400
    audit_path = _env_path("JARVIS_MIND_AUDIT_DB", DATA_DIR / "jarvis.db")
    try:
        conn = sqlite3.connect(audit_path)
        try:
            rows = conn.execute(
                "SELECT ts, tool FROM audit_log WHERE ts>? ORDER BY ts",
                (cutoff,)).fetchall()
            if rows:
                return [(int(r[0]), str(r[1])) for r in rows], "audit"
        finally:
            conn.close()
    except Exception:
        pass
    # Fallback: our own lightweight usage log (kind=tool, detail=tool name).
    try:
        conn = _mind_conn()
        try:
            rows = conn.execute(
                "SELECT ts, detail FROM usage_log WHERE ts>? AND kind='tool' ORDER BY ts",
                (cutoff,)).fetchall()
            return [(int(r[0]), str(r[1])) for r in rows], "usage_log"
        finally:
            conn.close()
    except Exception:
        return [], "usage_log"
    return [], "audit"


def _detect_routines(rows: list[tuple[int, str]], days: int, min_days: int,
                     window_min: int = 45) -> list[dict]:
    by_tool: dict[str, list[int]] = defaultdict(list)
    for ts, tool in rows:
        if tool:
            by_tool[tool].append(ts)
    proposals: list[dict] = []
    for tool, tss in sorted(by_tool.items()):
        daymap: dict[str, list[int]] = defaultdict(list)
        for ts in tss:
            dt = datetime.fromtimestamp(ts)  # server-local
            daymap[dt.strftime("%Y-%m-%d")].append(dt.hour * 60 + dt.minute)
        if len(daymap) < min_days:
            continue
        tods = sorted({m for mins in daymap.values() for m in mins})
        for center in tods:
            hit_days = [d for d, mins in daymap.items()
                        if any(abs(m - center) <= window_min for m in mins)]
            if len(hit_days) >= min_days:
                hh, mm = divmod(center, 60)
                proposals.append({
                    "tool": tool,
                    "days_seen": len(hit_days),
                    "window_days": days,
                    "tod_minutes": center,
                    "pattern": (f"'{tool}' used on {len(hit_days)} of the last "
                                f"{days} days at ~{hh:02d}:{mm:02d} "
                                f"(±{window_min} min)"),
                    "suggestion": (f"Create a standing rule: run '{tool}' daily "
                                   f"at ~{hh:02d}:{mm:02d}. (Proposal only — no "
                                   f"schedule was created.)"),
                })
                break  # one proposal per tool: the strongest window
    return proposals


@_safe
def routines_handler(args: dict) -> dict:
    days = args.get("days", 14)
    min_days = args.get("min_days", 4)
    try:
        days = int(days)
        min_days = int(min_days)
    except (TypeError, ValueError):
        return {"error": "memory.routines: 'days' and 'min_days' must be ints"}
    if not (1 <= min_days <= days <= 90):
        return {"error": "memory.routines: require 1 <= min_days <= days <= 90"}

    rows, source = _history_rows(days)
    detected = _detect_routines(rows, days, min_days)

    # Persist proposals; skip ones already proposed for the same tool+window.
    conn = _mind_conn()
    try:
        existing = {r["tool"] for r in conn.execute(
            "SELECT tool FROM routine_proposals WHERE status='proposed'").fetchall()}
        new_proposals: list[dict] = []
        for p in detected:
            if p["tool"] in existing:
                continue
            pid = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO routine_proposals(id,detected_at,tool,days_seen,"
                "window_days,tod_minutes,pattern,suggestion,status)"
                " VALUES(?,?,?,?,?,?,?,?, 'proposed')",
                (pid, int(time.time()), p["tool"], p["days_seen"],
                 p["window_days"], p["tod_minutes"], p["pattern"], p["suggestion"]))
            new_proposals.append({"id": pid, **p, "status": "proposed"})
        conn.commit()
    finally:
        conn.close()

    _log_usage("routines", f"proposals={len(new_proposals)} source={source}")
    return {
        "proposals": new_proposals,
        "history_source": source,
        "events_analyzed": len(rows),
        "created": 0,  # PROPOSAL ONLY: no schedule, cron, or worker was created.
        "forwarding_note": ("When Phase 11's approval queue exists, forward each "
                            "proposal via registry.call('autopilot.propose', "
                            "{'kind': 'routine', ...}). This tool never creates "
                            "schedules itself."),
    }


# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "memory.learn",
     "description": ("Extract durable facts and preferences from a conversation "
                     "turn (e.g. 'my dog is named Bruno', 'I prefer concise "
                     "answers', 'call me Raj') and store them in local memory. "
                     "Rule-based and conservative: precision over recall, never "
                     "invents facts, dedupes against existing memories. Facts "
                     "are stored as candidates (confidence < 1.0) for the agent "
                     "to confirm."),
     "handler": learn_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string", "source?": "string"}},
    {"name": "memory.consolidate",
     "description": ("Tidy the memory store: merge exact-duplicate facts (keep "
                     "newest), resolve contradictions (newer wins, history note "
                     "kept), drop expired or low-value stale entries. Rewrites "
                     "memory — needs confirmation."),
     "handler": consolidate_handler, "risk": "medium", "needs_network": False,
     "schema": {}},
    {"name": "memory.feedback",
     "description": ("Record durable feedback: liked=true/false about a topic "
                     "plus free-text detail (e.g. 'I didn't like that answer "
                     "about X'). Stored as a long-term preference that briefings "
                     "and future turns can surface."),
     "handler": feedback_handler, "risk": "low", "needs_network": False,
     "schema": {"about": "string", "liked": "bool", "detail?": "string"}},
    {"name": "memory.routines",
     "description": ("Detect repeated patterns from tool-call/audit history "
                     "(e.g. 'briefing requested 5 weekdays at ~9am') and return "
                     "standing-rule PROPOSALS. Proposal only: persists them for "
                     "later approval-queue forwarding, never creates schedules."),
     "handler": routines_handler, "risk": "low", "needs_network": False,
     "schema": {"days?": "int", "min_days?": "int"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(mind_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "memory.learn": ("low", False),
    "memory.consolidate": ("medium", False),
    "memory.feedback": ("low", False),
    "memory.routines": ("low", False),
}


def register(reg) -> None:
    """Wire the four Self-Learning Memory tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
