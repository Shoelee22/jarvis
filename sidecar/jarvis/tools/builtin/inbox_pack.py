"""Universal Inbox pack (Phase 9): one triage across every configured channel.

Tools:
  inbox.triage    Pull unread from gmail / telegram / imap, rank by urgency.
  inbox.read      Read one message by "<channel>:<native_id>".
  inbox.reply     Reply to a message through its channel-native send (HIGH risk).
  inbox.summarize Extractive summary of one message or of the whole triage set.

Contract: every handler takes a dict and returns a dict, never raises.
Channel packs are imported defensively — a missing or unconfigured channel is
recorded in the per-channel report instead of crashing the tool.
"""
from __future__ import annotations

import importlib
import re
import time
from collections import Counter
from datetime import datetime, timezone

# ------------------------------------------------------------------ contract

RISK_TABLE_ADDITIONS = {
    "inbox.triage": ("low", True),
    "inbox.read": ("low", True),
    "inbox.reply": ("high", True),
    "inbox.triage_replies": ("medium", False),
    "inbox.summarize": ("low", False),
}

CHANNELS = ("gmail", "telegram", "imap", "sms")

# Pack module behind each channel, inside this package. Tests monkeypatch this
# map to force the "channel unavailable" path.
_CHANNEL_MODULES = {
    "gmail": "gmail_pack",
    "telegram": "telegram_pack",
    "imap": "comms",
    "sms": "comms",
}

_URGENT_KEYWORDS = ("urgent", "asap", "invoice", "payment", "deadline",
                    "contract", "call me")

_WORD_RE = re.compile(r"[a-z0-9']+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_STOPWORDS = frozenset(
    "a an the and or but if of to in on for with is are was were be been "
    "this that these those it its i you he she we they me my your his her "
    "our their as at by from up out about into over after has have had do "
    "does did will would can could should not no yes so than then there here "
    "what when where who how which all any some more most other such just "
    "like get got hi hello hey thanks thank please".split())


class ChannelError(Exception):
    """Raised by an adapter when a channel cannot be read."""


# ------------------------------------------------------------------ helpers

def _import_channel(channel: str):
    """Import the pack module behind a channel; ChannelError when missing."""
    try:
        mod_name = _CHANNEL_MODULES[channel]
    except KeyError:
        raise ChannelError(f"unknown channel {channel!r}")
    try:
        return importlib.import_module(f"{__package__}.{mod_name}")
    except ImportError as e:
        raise ChannelError(f"{mod_name} unavailable ({e})")


def _fn(mod, *names):
    """First callable attribute found under any of the given names."""
    for name in names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _parse_ts(value) -> float:
    """Best-effort unix timestamp from the date shapes channels return."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0].strip()[:160] if lines else ""


def _extract_email(text: str) -> str:
    m = _EMAIL_RE.search(text or "")
    return m.group(0) if m else ""


def _norm(channel: str, native_id, sender: str, subject_or_first_line: str,
          ts: float, snippet: str, reply_to: str = "") -> dict:
    return {
        "id": f"{channel}:{native_id}",
        "channel": channel,
        "sender": sender or "",
        "subject_or_first_line": subject_or_first_line or "",
        "ts": ts or 0.0,
        "snippet": (snippet or "")[:300],
        "reply_to": reply_to or "",
    }


# ----------------------------------------------------------------- adapters
# Each adapter returns a list of normalized messages, or raises ChannelError.

def _gmail(limit: int) -> list:
    mod = _import_channel("gmail")
    search = _fn(mod, "gmail_search", "search", "list_unread")
    if search is None:
        raise ChannelError("gmail_pack has no list function")
    res = search({"q": "is:unread", "max": limit})
    if not isinstance(res, dict):
        raise ChannelError("gmail_pack returned a non-dict result")
    if res.get("error"):
        raise ChannelError(str(res["error"])[:200])
    out = []
    for m in res.get("messages", []) or []:
        if not isinstance(m, dict):
            continue
        sender = m.get("from", "") or ""
        out.append(_norm(
            "gmail", m.get("id", "?"), sender,
            m.get("subject") or _first_line(m.get("snippet", "")),
            _parse_ts(m.get("date")), m.get("snippet", ""),
            reply_to=_extract_email(sender)))
    return out


def _telegram(limit: int) -> list:
    mod = _import_channel("telegram")
    updates = _fn(mod, "telegram_updates", "updates", "_updates_impl")
    if updates is None:
        raise ChannelError("telegram_pack has no updates function")
    res = updates({"limit": limit})
    if not isinstance(res, dict):
        raise ChannelError("telegram_pack returned a non-dict result")
    if res.get("error"):
        raise ChannelError(str(res["error"])[:200])
    now = time.time()
    out = []
    for u in res.get("updates", []) or []:
        if not isinstance(u, dict):
            continue
        text = u.get("text") or ""
        out.append(_norm(
            "telegram", u.get("update_id", "?"), u.get("from") or "",
            _first_line(text) or "(no text)", now, text))
    return out


def _email_imap(limit: int) -> list:
    mod = _import_channel("imap")
    read = _fn(mod, "mail_read", "read")
    if read is None:
        raise ChannelError("comms has no mail read function")
    res = read({"limit": limit, "unread_only": True})
    if not isinstance(res, dict):
        raise ChannelError("comms mail_read returned a non-dict result")
    if res.get("error"):
        raise ChannelError(str(res["error"])[:200])
    out = []
    for i, m in enumerate(res.get("messages", []) or []):
        if not isinstance(m, dict):
            continue
        sender = m.get("from", "") or ""
        out.append(_norm(
            "imap", i, sender,
            m.get("subject") or _first_line(m.get("snippet", "")),
            _parse_ts(m.get("date")), m.get("snippet", ""),
            reply_to=_extract_email(sender)))
    return out


def _sms(limit: int) -> list:
    # The comms pack only exposes sms.send (outbound webhook); there is no
    # inbound SMS store to triage. Recorded as unavailable, not a crash.
    try:
        _import_channel("sms")
    except ChannelError:
        raise
    raise ChannelError("comms has no sms read function (send-only webhook)")


_ADAPTERS = {
    "gmail": _gmail,
    "telegram": _telegram,
    "imap": _email_imap,
    "sms": _sms,
}


# ----------------------------------------------------------------- contacts

def _contact_identifiers() -> list:
    """Name/email/phone rows from the local address book; [] when unavailable."""
    try:
        mod = _import_channel("imap")  # comms module
    except ChannelError:
        return []
    list_fn = _fn(mod, "contacts_list", "contacts_search")
    if list_fn is None:
        return []
    try:
        res = list_fn({"limit": 2000})
    except Exception:
        return []
    if not isinstance(res, dict) or res.get("error"):
        return []
    contacts = res.get("contacts", []) or []
    return [c for c in contacts if isinstance(c, dict)]


def _sender_known(sender: str, contacts: list) -> bool:
    s = (sender or "").lower()
    if not s:
        return False
    digits = re.sub(r"\D", "", s)
    for c in contacts:
        email = (c.get("email") or "").lower()
        if email and email in s:
            return True
        name = (c.get("name") or "").lower()
        if name and len(name) >= 3 and name in s:
            return True
        phone = re.sub(r"\D", "", str(c.get("phone") or ""))
        if phone and len(phone) >= 7 and digits and phone in digits:
            return True
    return False


def _urgency(msg: dict, contacts: list, now: float) -> int:
    score = 0
    if contacts and _sender_known(msg.get("sender", ""), contacts):
        score += 2
    hay = " ".join([msg.get("sender", ""), msg.get("subject_or_first_line", ""),
                    msg.get("snippet", "")]).lower()
    if any(k in hay for k in _URGENT_KEYWORDS):
        score += 2
    ts = msg.get("ts") or 0.0
    if ts and ts >= now - 3600:
        score += 1
    return score


# ------------------------------------------------------------- extractive

def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _extractive(texts: list, k: int) -> list:
    """Top-k sentences by keyword-overlap scoring. No LLM involved.

    Keywords = the urgency lexicon plus the most frequent content words in the
    corpus; sentences are returned in original document order.
    """
    sents = []
    for t in texts:
        sents.extend(_split_sentences(t))
    if not sents:
        return []
    freq = Counter(w for s in sents for w in _WORD_RE.findall(s.lower())
                   if w not in _STOPWORDS)
    key_terms = set(_URGENT_KEYWORDS) | {w for w, _ in freq.most_common(30)}
    scored = []
    for i, s in enumerate(sents):
        words = _WORD_RE.findall(s.lower())
        score = sum(freq.get(w, 0) for w in words if w in key_terms)
        if any(k in s.lower() for k in _URGENT_KEYWORDS):
            score += 5
        scored.append((score, i, s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = sorted(scored[:max(1, k)], key=lambda t: t[1])
    return [s for _, _, s in top]


# ----------------------------------------------------------------- handlers

def _safe(fn):
    def wrapper(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    return wrapper


def _triage_impl(args: dict) -> dict:
    try:
        limit = max(1, min(100, int(args.get("limit", 30))))
    except (TypeError, ValueError):
        limit = 30
    per_channel = max(10, min(50, limit))
    messages, channels = [], {}
    for ch in CHANNELS:
        try:
            got = _ADAPTERS[ch](per_channel)
            messages.extend(got)
            channels[ch] = f"ok ({len(got)})"
        except ChannelError as e:
            channels[ch] = f"unavailable: {str(e)[:160]}"
        except Exception as e:  # belt and braces: triage never crashes
            channels[ch] = f"unavailable: {type(e).__name__}: {str(e)[:120]}"
    contacts = _contact_identifiers()
    now = time.time()
    for m in messages:
        m["urgency"] = _urgency(m, contacts, now)
    messages.sort(key=lambda m: (m.get("urgency", 0), m.get("ts", 0.0)),
                  reverse=True)
    return {"messages": messages[:limit], "channels": channels,
            "generated_at": int(now)}


def _split_id(mid: str):
    if ":" not in mid:
        return None, None
    channel, native = mid.split(":", 1)
    return channel, native


def _read_impl(args: dict) -> dict:
    mid = str(args.get("id", "")).strip()
    channel, native = _split_id(mid)
    if not channel:
        return {"error": "id must look like '<channel>:<native_id>'"}
    if channel == "gmail":
        try:
            mod = _import_channel("gmail")
        except ChannelError as e:
            return {"error": str(e)}
        read = _fn(mod, "gmail_read", "read")
        if read is None:
            return {"error": "gmail_pack has no read function"}
        return read({"id": native})
    if channel == "telegram":
        return {"error": "telegram has no read-by-id; use inbox.triage "
                         "to list recent updates"}
    if channel in ("imap", "sms"):
        return {"error": f"{channel} has no read-by-id in the comms pack"}
    return {"error": f"unknown channel {channel!r}"}


def _reply_impl(args: dict) -> dict:
    mid = str(args.get("id", "")).strip()
    text = str(args.get("text", "")).strip()
    channel, native = _split_id(mid)
    if not channel:
        return {"error": "id must look like '<channel>:<native_id>'"}
    if not text:
        return {"error": "text is required"}
    if channel == "gmail":
        try:
            mod = _import_channel("gmail")
        except ChannelError as e:
            return {"error": str(e)}
        read = _fn(mod, "gmail_read", "read")
        send = _fn(mod, "gmail_send", "send")
        if read is None or send is None:
            return {"error": "gmail_pack has no read/send functions"}
        orig = read({"id": native})
        if not isinstance(orig, dict):
            return {"error": "gmail_pack returned a non-dict result"}
        if orig.get("error"):
            return orig
        to = _extract_email(orig.get("from", ""))
        if not to:
            return {"error": "could not determine a reply address"}
        subj = orig.get("subject", "") or ""
        if not subj.lower().startswith("re:"):
            subj = f"Re: {subj}"
        return send({"to": to, "subject": subj, "body": text})
    if channel == "imap":
        return {"error": "imap reply needs the original message and comms "
                         "mail_read has no read-by-id; use mail.send directly"}
    if channel == "telegram":
        return {"error": "cannot resolve a chat id from telegram updates; "
                         "use telegram.send with an explicit chat_id"}
    if channel == "sms":
        return {"error": "sms is send-only in the comms pack; "
                         "use sms.send with an explicit recipient"}
    return {"error": f"unknown channel {channel!r}"}


def _summarize_impl(args: dict) -> dict:
    try:
        k = max(1, min(10, int(args.get("k", 5))))
    except (TypeError, ValueError):
        k = 5
    mid = str(args.get("id", "")).strip() or None
    triage = _triage_impl({"limit": 30})
    msgs = triage["messages"]
    if mid:
        msgs = [m for m in msgs if m["id"] == mid]
        if not msgs:
            return {"error": f"no message {mid!r} in the current triage set"}
    texts = [f"{m['subject_or_first_line']}. {m['snippet']}" for m in msgs]
    return {"summary": _extractive(texts, k),
            "source": mid or "triage",
            "messages": len(msgs),
            "channels": triage["channels"]}


# ------------------------------------------------------------------ Phase 36
# Email triage with drafted replies -> approval queue.
#
# inbox.triage_replies {limit?} runs the normal triage, picks the messages
# that look like they need a reply (heuristic, not comprehension), drafts
# a reply for each, and files every draft as an autopilot proposal whose
# single step is inbox.reply. NOTHING IS SENT by this tool — sending only
# happens if the user approves the proposal in the batch queue.
# Drafts are mechanical templates, clearly marked DRAFT, never written
# to sound like a finished human reply.

_REPLY_CUES = ("?", "please", "could you", "can you", "would you",
               "kindly", "asap", "urgent", "deadline", "waiting on",
               "need your", "let me know", "please confirm", "request")


def _needs_reply(msg: dict) -> bool:
    """Heuristic: higher-urgency message containing a question/request cue."""
    if msg.get("urgency", 0) < 40:
        return False
    text = ((msg.get("subject_or_first_line") or "") + " "
            + (msg.get("snippet") or "")).lower()
    return any(cue in text for cue in _REPLY_CUES)


def _draft_reply(msg: dict) -> str:
    """Mechanical reply template — honest draft, not a finished reply."""
    sender = (msg.get("sender") or "there").strip()
    first = sender.split()[0].split("@")[0].rstrip(",") or "there"
    subject = (msg.get("subject_or_first_line") or "").strip()[:120]
    ask = (msg.get("snippet") or "").strip().split(".")[0][:200]
    return (
        f"Hi {first},\n\n"
        f"Thanks for your message about \"{subject}\".\n\n"
        "[DRAFT — review before sending]\n\n"
        f"The ask as I read it: \"{ask}\".\n\n"
        "I'll look into this and follow up properly. "
        "Is there a deadline I should know about?\n"
    )


def _triage_replies_impl(args: dict) -> dict:
    try:
        limit = max(1, min(100, int(args.get("limit", 30))))
    except (TypeError, ValueError):
        limit = 30
    triage = _triage_impl({"limit": limit})
    from . import autopilot_pack  # lazy: avoids import cycles
    drafts = []
    filed = 0
    for m in triage["messages"]:
        if not _needs_reply(m):
            continue
        draft = _draft_reply(m)
        sender = m.get("sender", "")
        subject = (m.get("subject_or_first_line") or "")[:80]
        prop = autopilot_pack.propose_handler({
            "goal": f"Send reply to {sender}: {subject}",
            "steps": [{"tool": "inbox.reply",
                       "args": {"id": m["id"], "text": draft},
                       "why": "Draft reply to a message the triage "
                              "heuristic flagged as needing one. "
                              "Review the draft text before approving."}],
        })
        pid = prop.get("id") if isinstance(prop, dict) else None
        if pid:
            filed += 1
        drafts.append({"id": m["id"], "sender": sender,
                       "subject": subject, "urgency": m.get("urgency"),
                       "draft": draft, "proposal_id": pid})
    return {"drafts": drafts, "filed": filed,
            "flagged": len(drafts),
            "channels": triage["channels"],
            "read": ("Drafts only — nothing was sent. Each draft is an "
                     "approval proposal; approving one runs inbox.reply "
                     "with the draft text. Drafts are mechanical templates, "
                     "not finished replies.")}


# -------------------------------------------------------------- registration

def register(reg):
    """Register the four inbox tools on a Registry."""
    from ..base import Tool
    defs = [
        ("inbox.triage",
         "Pull unread from every configured channel (gmail, telegram, imap) "
         "and rank by urgency. Missing channels are reported, never crash.",
         {"limit": "int?"}, _safe(_triage_impl), "low", True),
        ("inbox.read",
         "Read one message by '<channel>:<native_id>' via the channel's "
         "native read function.",
         {"id": "string"}, _safe(_read_impl), "low", True),
        ("inbox.reply",
         "Reply to a message through its channel-native send. IRREVERSIBLE — "
         "needs confirmation.",
         {"id": "string", "text": "string"}, _safe(_reply_impl), "high", True),
        ("inbox.triage_replies",
         "Draft replies for triage-flagged messages and file each as an "
         "approval proposal (steps: inbox.reply). NOTHING IS SENT — drafts "
         "are mechanical templates marked DRAFT; sending needs approval.",
         {"limit": "int?"}, _safe(_triage_replies_impl), "medium", False),
        ("inbox.summarize",
         "Extractive (keyword-overlap, no LLM) summary of one message or of "
         "the whole triage set.",
         {"id": "string?", "k": "int?"}, _safe(_summarize_impl), "low", False),
    ]
    for name, desc, schema, handler, risk, needs_net in defs:
        reg.register(Tool(name, desc, schema, handler, risk, needs_net))
    # Make the policy engine aware of these tools (unknown tools default-deny).
    try:
        from ...agent.policy import RISK_TABLE
        RISK_TABLE.update(RISK_TABLE_ADDITIONS)
    except Exception:
        pass
    return reg
