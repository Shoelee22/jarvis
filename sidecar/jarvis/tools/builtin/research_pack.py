"""Deep research pack (Phase 15, workstream A): cited, honest investigations.

Tools:

    research.investigate {question, depth?}  low, network — a real multi-step
        research loop:
          (1) decompose the question into sub-questions (mechanical clause
              splitting — labeled syntactic, not comprehension) and persist
              them as a brain.plan through the live registry (each step =
              one web.search call);
          (2) execute the steps through the bound agent's registry — low-risk
              read-only steps only (web.search, web.fetch). If the pack is
              not bound to a live agent (bind_agent never called), it
              degrades HONESTLY: it returns the plan only, says so plainly,
              and records nothing as verified;
          (3) dedupe findings by normalized text + normalized URL;
          (4) flag contradictions between sources with a heuristic detector
              (shared topic keywords + differing numeric claims) — every
              flag is labeled "heuristic";
          (5) write a cited briefing to sqlite. EVERY claim carries its
              source URL; anything that could not be backed by a fetched
              page is marked "unverified" / "could not verify".
        NEVER invents sources or claims: a failed web.search or web.fetch
        turns into an explicit "could not verify" entry, never a guess.

    research.briefing {id}  low, offline — read a past briefing back.
        Honest error when the id does not exist.

    research.compare {topic, options[]}  low, network — investigate each
        option, then build a comparison table: rows = options,
        columns = key attributes extracted from the sources (extraction is
        heuristic and labeled as such), with a citation per cell where the
        source supports it.

HONESTY NOTES (read before trusting output):
    - Decomposition is syntactic clause splitting, not comprehension.
    - Claim extraction is extractive (top keyword-overlap sentences from
      fetched pages), not summarization or understanding.
    - Contradiction detection is a keyword+number heuristic: two claims with
      overlapping topic words and different numbers. It can produce false
      positives (different units, different years) — every flag says so.
    - Comparison attributes are regex-extracted label/value mentions from
      source text; coverage is partial and labeled heuristic.
    - This pack is mechanical research plumbing, not expertise.

SAFETY:
    - Handlers take dict -> return dict and never raise.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/research.db,
      overridable with the JARVIS_RESEARCH_DB env var (tests use a tmp file).
    - _regcall executes ONLY low-risk tools (web.search, web.fetch,
      brain.plan). Medium/high/unknown risk is refused, fail-closed.

INTEGRATION NOTE FOR THE PARENT (apply — do not let the subagent edit):
    1. In sidecar/jarvis/tools/builtin/__init__.py:
         a. add ``research_pack`` to the big ``from . import ...`` line
            (append after ``episodic_pack``), and
         b. after the Phase 14 block, add::

                # --- Phase 15: deep research pack ---
                for _pack in (research_pack,):
                    _pack.register(reg)
                    _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    2. In sidecar/jarvis/ipc/server.py, after the Phase 14 bind block, add::

           from ..tools.builtin import research_pack
           research_pack.bind_agent(agent)

       Without this, research.investigate returns the plan only and says so.
    3. Optional tunables: merge packaging/config_fragments/research_pack.yaml
       into tools_config.yaml (caps for sub-queries, fetches, claims).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ...config import DATA_DIR

DEFAULT_DB_PATH = DATA_DIR / "research.db"

# ---------------------------------------------------------------------------
# Agent binding (same pattern as builtin/sleep_pack.py)
# ---------------------------------------------------------------------------
_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


# ---------------------------------------------------------------------------
# SQLite state (one table: research_briefings)
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()


def _db_path() -> Path:
    override = os.environ.get("JARVIS_RESEARCH_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS research_briefings("
        " id TEXT PRIMARY KEY,"
        " question TEXT NOT NULL,"
        " kind TEXT NOT NULL,"
        " created_at INTEGER NOT NULL,"
        " briefing_json TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def _save_briefing(briefing_id: str, question: str, kind: str,
                   briefing: dict) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO research_briefings"
                "(id, question, kind, created_at, briefing_json)"
                " VALUES(?,?,?,?,?)",
                (briefing_id, question, kind, int(time.time()),
                 json.dumps(briefing, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def _load_briefing(briefing_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT question, kind, created_at, briefing_json"
                " FROM research_briefings WHERE id=?",
                (briefing_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                briefing = json.loads(row["briefing_json"])
            except (TypeError, ValueError):
                return None
            return {"id": briefing_id, "question": row["question"],
                    "kind": row["kind"], "created_at": row["created_at"],
                    "briefing": briefing}
        finally:
            conn.close()


def _list_briefings(limit: int = 20) -> list[dict]:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, question, kind, created_at FROM research_briefings"
                " ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Config tunables (optional; defaults keep everything bounded)
# ---------------------------------------------------------------------------
def _cfg() -> dict:
    try:
        from .creator import _load_config  # noqa: PLC0415 (lazy: no cycles)
        raw = _load_config().get("research", {}) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _caps(depth: int) -> dict:
    cfg = _cfg()
    sub_q = int(cfg.get("max_subqueries", 5))
    fetches = int(cfg.get("max_fetches", 8))
    per_source = int(cfg.get("claims_per_source", 4))
    if depth <= 1:
        return {"subqueries": min(3, sub_q), "fetch_per_query": 1,
                "max_fetches": min(4, fetches), "claims_per_source": per_source}
    return {"subqueries": sub_q, "fetch_per_query": 2,
            "max_fetches": fetches, "claims_per_source": per_source}


# ---------------------------------------------------------------------------
# Risk-gated registry calls (copied pattern from sleep_pack._regcall)
# ---------------------------------------------------------------------------
def _tool_risk(reg, name: str) -> str:
    try:
        tool = getattr(reg, "tools", {}).get(name)
    except Exception:
        tool = None
    if tool is not None:
        return getattr(tool, "risk", None) or "unknown"
    try:
        from ...agent.policy import RISK_TABLE  # noqa: PLC0415 (lazy: no cycles)
        entry = RISK_TABLE.get(name)
        if entry:
            return entry[0]
    except Exception:
        pass
    return "unknown"


def _regcall(name: str, args: dict | None, actor: str = "research") -> dict:
    """Call a tool through the bound agent's registry — low-risk only.

    Medium/high/unknown risk is REFUSED (fail closed). confirmed=True is
    never passed. When the pack is not bound, the call is skipped honestly
    and the caller must degrade to plan-only mode.
    """
    if _AGENT is None:
        return {"skipped": True, "tool": name,
                "reason": "research pack is not bound to a live agent "
                          "(bind_agent was not called)"}
    reg = getattr(_AGENT, "registry", None)
    if reg is None:
        return {"skipped": True, "tool": name,
                "reason": "bound agent exposes no .registry"}
    risk = _tool_risk(reg, name)
    if risk != "low":
        return {"skipped": True, "tool": name, "risk": risk,
                "reason": ("research only executes low-risk read-only tools — "
                           "this one is not verifiably low risk")}
    try:
        return reg.call(name, args or {}, actor=actor)
    except Exception as exc:
        return {"ok": False, "error": f"registry.call raised: {exc}"}


def _unwrap(res: dict | None) -> dict | None:
    """Unwrap a Registry.call envelope -> handler output (or None)."""
    if isinstance(res, dict) and res.get("ok"):
        inner = res.get("result")
        return inner if isinstance(inner, dict) else None
    return None


# ---------------------------------------------------------------------------
# Step 1 — syntactic decomposition (mechanical, labeled as such)
# ---------------------------------------------------------------------------
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:[?;]\s*|\band\b|\bvs\b|\bversus\b|\bcompared to\b|\bcompare\b|\s*,\s*)\s*",
    re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9']+")
_STOP = frozenset(
    "a an the of to in on for and or is are was were be been it its this that "
    "these those with by from as at into how what why when where which who "
    "whom whose do does did can could should would will has have had not no "
    "vs versus".split())


def _keywords(text: str) -> list[str]:
    words = [w.lower() for w in _WORD.findall(text or "")]
    return [w for w in words if len(w) >= 4 and w not in _STOP]


def _decompose(question: str, max_sub: int) -> list[str]:
    """Syntactic clause splitting — NOT comprehension. Labeled as heuristic.

    Splits the question on ? ; and/,, vs/versus/compare boundaries; each
    surviving clause becomes a search sub-query. Falls back to the raw
    question when nothing survives.
    """
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(question or "")]
    seen, subs = set(), []
    for c in clauses:
        c = re.sub(r"\s+", " ", c).strip(" .,-–—")
        if len(c) < 6:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        subs.append(c)
        if len(subs) >= max_sub:
            break
    if not subs:
        q = re.sub(r"\s+", " ", (question or "").strip())
        subs = [q[:200]] if q else []
    return subs


def _plan_steps(sub_queries: list[str]) -> list[dict]:
    """One web.search step per sub-query (brain.plan schema: id/tool/args)."""
    return [{"id": f"search_{i + 1}", "tool": "web.search",
             "args": {"query": q, "count": 8}, "depends_on": []}
            for i, q in enumerate(sub_queries)]


def _persist_plan(question: str, steps: list[dict]) -> dict:
    """Persist the investigation plan via brain.plan through the registry.

    Returns {"plan_id": ...} or {"skipped": ..., "reason": ...} — never raises.
    """
    if _AGENT is None:
        return {"skipped": True,
                "reason": "research pack is not bound to a live agent "
                          "(bind_agent was not called) — plan is local only"}
    res = _regcall("brain.plan",
                   {"goal": f"Investigate: {question[:220]}", "steps": steps},
                   actor="research")
    if isinstance(res, dict) and res.get("skipped"):
        return res
    inner = _unwrap(res)
    if inner and inner.get("plan_id"):
        return {"plan_id": inner["plan_id"]}
    err = (res.get("error") if isinstance(res, dict) else None) or \
        "brain.plan returned no plan_id"
    return {"skipped": True, "reason": f"brain.plan failed: {err}"}


# ---------------------------------------------------------------------------
# Step 2 — execute: search, fetch, extract claims (read-only, bounded)
# ---------------------------------------------------------------------------
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _norm_url(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = u.split("#")[0].split("?")[0]
    return u.rstrip("/")


def _norm_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _score_sentence(sent: str, keywords: list[str]) -> int:
    s = _norm_text(sent)
    if not s or len(s) < 40:
        return -1
    return sum(1 for k in keywords if k in s)


def _extract_claims(page_text: str, sub_query: str,
                    max_claims: int) -> list[str]:
    """Extractive: top keyword-overlap sentences. Mechanical, not a summary."""
    kws = _keywords(sub_query)
    scored = []
    for sent in _SENT_SPLIT.split(page_text or ""):
        sent = re.sub(r"\s+", " ", sent).strip()
        if len(sent) > 400:
            sent = sent[:400] + "…"
        score = _score_sentence(sent, kws)
        if score > 0:
            scored.append((score, sent))
    scored.sort(key=lambda p: -p[0])
    return [s for _, s in scored[:max_claims]]


_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|percent|x|×|years?|yrs?|days?|"
                     r"months?|hrs?|hours?|mins?|seconds?|km|miles?|kg|lbs?|"
                     r"gb|mb|tb|mp|cores?|w|kw|hp|mph|km/h|usd|\$|inr|rs|€|£)?",
                     re.IGNORECASE)


def _findings_from_searches(sub_queries: list[str], caps: dict) -> dict:
    """Run web.search + web.fetch per sub-query. Returns findings + stats.

    Every failure lands as a "could not verify" entry — NEVER as a guess.
    """
    findings: list[dict] = []
    unverified: list[dict] = []
    searches_ok = searches_failed = fetches_ok = fetches_failed = 0
    fetch_budget = caps["max_fetches"]
    per_query = caps["fetch_per_query"]
    claims_per = caps["claims_per_source"]

    for qi, sub_q in enumerate(sub_queries):
        s_res = _regcall("web.search", {"query": sub_q, "count": 8},
                         actor="research")
        s_inner = _unwrap(s_res)
        if isinstance(s_res, dict) and s_res.get("skipped"):
            unverified.append({"sub_question": sub_q, "verified": False,
                               "status": "could not verify",
                               "reason": s_res.get("reason", "search skipped")})
            continue
        if s_inner is None or s_inner.get("error"):
            searches_failed += 1
            err = (s_inner or {}).get("error") or \
                (s_res.get("error") if isinstance(s_res, dict) else None) or \
                "unknown search error"
            unverified.append({"sub_question": sub_q, "verified": False,
                               "status": "could not verify",
                               "reason": f"web.search failed: {err}"[:200]})
            continue
        searches_ok += 1
        results = s_inner.get("results") or []
        if not results:
            unverified.append({"sub_question": sub_q, "verified": False,
                               "status": "could not verify",
                               "reason": "web.search returned no results"})
            continue
        fetched_any = False
        for r in results[:per_query]:
            if fetch_budget <= 0:
                break
            url = str(r.get("url") or "").strip()
            if not url:
                continue
            fetch_budget -= 1
            f_res = _regcall("web.fetch", {"url": url}, actor="research")
            f_inner = _unwrap(f_res)
            if f_inner is None or f_inner.get("error"):
                fetches_failed += 1
                continue
            text = f_inner.get("text") or ""
            title = f_inner.get("title") or ""
            if not text.strip():
                fetches_failed += 1
                continue
            fetches_ok += 1
            fetched_any = True
            for sent in _extract_claims(text, sub_q, claims_per):
                findings.append({
                    "claim": sent,
                    "source_url": f_inner.get("url") or url,
                    "source_title": title,
                    "verified": True,
                    "sub_question": sub_q,
                })
        if not fetched_any:
            unverified.append({"sub_question": sub_q, "verified": False,
                               "status": "could not verify",
                               "reason": "all web.fetch calls for this "
                                         "sub-question failed"})
    return {"findings": findings, "unverified": unverified,
            "stats": {"searches_ok": searches_ok,
                      "searches_failed": searches_failed,
                      "fetches_ok": fetches_ok,
                      "fetches_failed": fetches_failed}}


# ---------------------------------------------------------------------------
# Step 3 — dedupe by normalized text / URL
# ---------------------------------------------------------------------------
def _dedupe(findings: list[dict]) -> tuple[list[dict], int]:
    """Drop exact-duplicate URLs and near-duplicate claim texts."""
    seen_urls: set[str] = set()
    seen_texts: set[str] = set()
    kept, dropped = [], 0
    for f in findings:
        url_key = _norm_url(f.get("source_url", ""))
        text_key = _norm_text(f.get("claim", ""))
        # Same URL that already contributed, or an identical normalized
        # claim sentence seen before -> duplicate.
        if url_key and url_key in seen_urls:
            dropped += 1
            continue
        if text_key and text_key in seen_texts:
            dropped += 1
            continue
        if url_key:
            seen_urls.add(url_key)
        if text_key:
            seen_texts.add(text_key)
        kept.append(f)
    return kept, dropped


# ---------------------------------------------------------------------------
# Step 4 — heuristic contradiction flagging
# ---------------------------------------------------------------------------
def _flag_contradictions(findings: list[dict]) -> list[dict]:
    """Heuristic: two claims sharing >=2 topic keywords with different
    numbers may contradict. Labeled heuristic; false positives possible
    (different years, units, contexts)."""
    flags = []
    indexed = []
    for f in findings:
        claim = f.get("claim") or ""
        kws = set(_keywords(claim))
        nums = sorted(set(_NUM_RE.findall(claim)))
        nums = [n.strip().lower() for n in nums if n.strip()]
        indexed.append((f, kws, nums))
    n = len(indexed)
    for i in range(n):
        fi, kwi, numi = indexed[i]
        if not numi:
            continue
        for j in range(i + 1, n):
            fj, kwj, numj = indexed[j]
            if not numj:
                continue
            overlap = kwi & kwj
            if len(overlap) < 2:
                continue
            if numi == numj:
                continue
            flags.append({
                "detector": "heuristic",
                "note": ("two sources share topic keywords but state "
                         "different numbers — may be different years, units, "
                         "or contexts; verify before trusting"),
                "topic_keywords": sorted(overlap)[:6],
                "claim_a": fi.get("claim"),
                "source_a": fi.get("source_url"),
                "numbers_a": numi[:3],
                "claim_b": fj.get("claim"),
                "source_b": fj.get("source_url"),
                "numbers_b": numj[:3],
            })
            if len(flags) >= 10:
                return flags
    return flags


# ---------------------------------------------------------------------------
# Core investigation (shared by research.investigate / research.compare)
# ---------------------------------------------------------------------------
def _investigate(question: str, depth: int) -> dict:
    """Run the full loop. Returns the briefing dict (not yet persisted)."""
    caps = _caps(depth)
    sub_queries = _decompose(question, caps["subqueries"])
    steps = _plan_steps(sub_queries)
    plan = _persist_plan(question, steps)

    if _AGENT is None:
        # Honest degradation: plan only, nothing verified.
        return {
            "question": question,
            "depth": depth,
            "bound": False,
            "executed": False,
            "plan": {"sub_questions": sub_queries, "steps": steps,
                     "brain_plan": plan},
            "message": ("research pack is not bound to a live agent "
                        "(bind_agent was not called) — this is the "
                        "investigation plan only; nothing was searched, "
                        "fetched, or verified."),
        }

    fx = _findings_from_searches(sub_queries, caps)
    findings, dropped = _dedupe(fx["findings"])
    contradictions = _flag_contradictions(findings)
    all_claims = findings + [
        {"claim": u["sub_question"], "source_url": None, "source_title": None,
         "verified": False, "status": "could not verify",
         "reason": u.get("reason")}
        for u in fx["unverified"]
    ]
    return {
        "question": question,
        "depth": depth,
        "bound": True,
        "executed": True,
        "plan": {"sub_questions": sub_queries, "steps": steps,
                 "brain_plan": plan},
        "stats": {**fx["stats"],
                  "sub_questions": len(sub_queries),
                  "duplicates_dropped": dropped,
                  "contradictions_flagged": len(contradictions)},
        "claims": all_claims,
        "unverified": fx["unverified"],
        "contradictions": contradictions,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def research_investigate_handler(args: dict) -> dict:
    """research.investigate {question, depth?} — the multi-step research loop.

    Depth 1 (default): <=3 sub-queries, 1 fetch each. Depth 2: <=5
    sub-queries, 2 fetches each. Persists the cited briefing in sqlite and
    returns it. Unbound -> plan only, stated plainly.
    """
    try:
        a = args or {}
        question = str(a.get("question") or "").strip()
        if not question:
            return {"error": "research.investigate: 'question' is required"}
        try:
            depth = int(a.get("depth", 1))
        except (TypeError, ValueError):
            return {"error": "research.investigate: 'depth' must be 1 or 2"}
        depth = max(1, min(depth, 2))
        briefing = _investigate(question, depth)
        briefing_id = f"brief_{uuid.uuid4().hex[:12]}"
        if briefing.get("executed"):
            _save_briefing(briefing_id, question, "investigate", briefing)
            briefing["id"] = briefing_id
        else:
            briefing["id"] = briefing_id
            briefing["note"] = ("plan-only investigation — not persisted as "
                                "a verified briefing (nothing was executed)")
        return {"ok": True, **briefing}
    except Exception as exc:  # never raise
        return {"error": f"research.investigate failed: {exc}"}


def research_briefing_handler(args: dict) -> dict:
    """research.briefing {id} — read a past briefing back from sqlite."""
    try:
        a = args or {}
        briefing_id = str(a.get("id") or "").strip()
        if not briefing_id:
            return {"error": "research.briefing: 'id' is required"}
        row = _load_briefing(briefing_id)
        if row is None:
            return {"error": (f"no briefing '{briefing_id}' — investigate "
                              "first with research.investigate; ids look "
                              "like 'brief_<12 hex chars>'")}
        return {"ok": True, **row}
    except Exception as exc:
        return {"error": f"research.briefing failed: {exc}"}


_ATTR_RE = re.compile(
    r"(?i)\b(prices?|costs?|batter(?:y|ies)|weigh(?:t|ts|s)|displays?|"
    r"screens?|cameras?|rams?|memor(?:y|ies)|storage|years?|founded|released|"
    r"speeds?|ranges?|horsepower|resolutions?|sizes?|capacit(?:y|ies)|"
    r"warrant(?:y|ies)|ratings?|processors?|cpus?|gpus?)\b\s*"
    r"(?:is|are|of|:|—|–|-)?\s*([^.;]{2,80}?)(?=[.;]|$)")


def _canon_attr(word: str) -> str:
    """Canonicalize an inflected attribute word -> base form."""
    w = word.lower()
    if w.endswith("ies"):
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return {"weigh": "weight", "cpu": "cpu", "gpu": "gpu"}.get(w, w)


def _attributes_from_claims(claims: list[dict]) -> dict[str, dict]:
    """Heuristic regex label/value extraction: attr -> {value, source_url}.

    Mechanical pattern matching on source text; coverage is partial.
    """
    attrs: dict[str, dict] = {}
    for c in claims:
        if not c.get("verified"):
            continue
        text = c.get("claim") or ""
        for m in _ATTR_RE.finditer(text):
            attr = _canon_attr(m.group(1))
            value = re.sub(r"\s+", " ", m.group(2)).strip(" :-–—")
            if len(value) < 2 or attr in attrs:
                continue
            attrs[attr] = {"value": value, "source_url": c.get("source_url")}
    return attrs


def _build_comparison(topic: str, options: list[str], caps: dict) -> dict:
    """Investigate each option, then cross-tabulate extracted attributes."""
    per_option: dict[str, dict] = {}
    for opt in options:
        per_option[opt] = _investigate(f"{topic}: {opt}", 1)
    if not any(p.get("executed") for p in per_option.values()):
        return {"topic": topic, "options": options, "executed": False,
                "per_option": {
                    o: {"executed": False,
                        "message": p.get("message", "not executed")}
                    for o, p in per_option.items()},
                "message": ("research pack is not bound to a live agent — "
                            "comparison plans only, nothing investigated")}

    # Union of heuristic attributes across options -> table columns.
    attr_maps = {o: _attributes_from_claims(p.get("claims", []))
                 for o, p in per_option.items()}
    columns = sorted({a for m in attr_maps.values() for a in m})[:8]
    rows = []
    for o in options:
        cells = []
        for col in columns:
            hit = attr_maps[o].get(col)
            cells.append({
                "attribute": col,
                "value": hit["value"] if hit else "—",
                "source_url": hit["source_url"] if hit else None,
                "cited": bool(hit and hit.get("source_url")),
            })
        claims = [c for c in per_option[o].get("claims", [])
                  if c.get("verified")][:3]
        rows.append({"option": o, "cells": cells,
                     "top_claims": claims,
                     "unverified_count": len(per_option[o].get("unverified",
                                                              []))})
    return {
        "topic": topic, "options": options, "executed": True,
        "columns": columns,
        "rows": rows,
        "extraction": "heuristic",
        "extraction_note": ("attribute columns are regex-extracted "
                            "label/value mentions from source text — "
                            "coverage is partial; every cited cell carries "
                            "its source URL; '—' means the sources said "
                            "nothing extractable about that attribute"),
        "per_option_stats": {o: p.get("stats", {}) for o, p in
                             per_option.items()},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def research_compare_handler(args: dict) -> dict:
    """research.compare {topic, options[]} — investigate each option, then a
    cited comparison table (rows=options, columns=heuristic attributes)."""
    try:
        a = args or {}
        topic = str(a.get("topic") or "").strip()
        options = a.get("options") or []
        if not topic:
            return {"error": "research.compare: 'topic' is required"}
        if not isinstance(options, list) or len(options) < 2:
            return {"error": "research.compare: 'options' needs at least 2 "
                             "items"}
        options = [str(o).strip() for o in options if str(o).strip()][:6]
        if len(options) < 2:
            return {"error": "research.compare: 'options' needs at least 2 "
                             "non-empty items"}
        table = _build_comparison(topic, options, _caps(1))
        briefing_id = f"brief_{uuid.uuid4().hex[:12]}"
        if table.get("executed"):
            _save_briefing(briefing_id, f"compare: {topic} "
                           f"[{', '.join(options)}]", "compare", table)
            table["id"] = briefing_id
        else:
            table["id"] = briefing_id
            table["note"] = "plan-only comparison — not persisted"
        return {"ok": True, **table}
    except Exception as exc:
        return {"error": f"research.compare failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract (same shape as teach_pack.py)
# ---------------------------------------------------------------------------
def research_cite_handler(args: dict) -> dict:
    """research.cite {id} — render a briefing as a fully cited report.

    Every claim gets a numbered [n] citation pointing at its source URL;
    claims with no source go under 'Unverified' and are NEVER cited.
    Returns the markdown report plus structured sources/claims.
    """
    try:
        a = args or {}
        briefing_id = str(a.get("id") or "").strip()
        if not briefing_id:
            return {"error": "research.cite: 'id' is required"}
        row = _load_briefing(briefing_id)
        if row is None:
            return {"error": (f"no briefing '{briefing_id}' — investigate "
                              "first with research.investigate")}
        briefing = row["briefing"] or {}
        question = row["question"]
        claims = briefing.get("claims") or []
        # number unique sources in first-seen order
        sources: list[dict] = []
        url_to_n: dict[str, int] = {}
        for c in claims:
            url = c.get("source_url")
            if url and url not in url_to_n:
                url_to_n[url] = len(sources) + 1
                sources.append({"n": len(sources) + 1,
                                "title": c.get("source_title") or url,
                                "url": url})
        cited, unverified = [], []
        for c in claims:
            url = c.get("source_url")
            if url and url in url_to_n:
                n = url_to_n[url]
                cited.append({"claim": c.get("claim"),
                              "citation": n,
                              "status": c.get("status", "verified")})
            else:
                unverified.append({"claim": c.get("claim"),
                                   "status": c.get("status",
                                                   "could not verify"),
                                   "reason": c.get("reason")})
        lines = [f"# Research: {question}", ""]
        if cited:
            lines.append("## Findings")
            for c in cited:
                lines.append(f"- {c['claim']} [{c['citation']}]")
            lines.append("")
        if unverified:
            lines.append("## Unverified (not cited — could not confirm)")
            for c in unverified:
                reason = f" — {c['reason']}" if c.get("reason") else ""
                lines.append(f"- {c['claim']}{reason}")
            lines.append("")
        if briefing.get("contradictions"):
            lines.append("## Contradictions flagged (heuristic)")
            for x in briefing["contradictions"]:
                lines.append(f"- {x}")
            lines.append("")
        lines.append("## Sources")
        for s in sources:
            lines.append(f"[{s['n']}] {s['title']} — {s['url']}")
        if not sources:
            lines.append("(no sources — nothing was verified)")
        report = "\n".join(lines)
        return {"ok": True, "id": briefing_id, "question": question,
                "cited": cited, "unverified": unverified,
                "sources": sources,
                "stats": {"claims_cited": len(cited),
                          "claims_unverified": len(unverified),
                          "sources": len(sources)},
                "report": report,
                "read": ("Every cited claim carries a numbered source; "
                         "unverified claims are never given citations.")}
    except Exception as exc:  # never raise
        return {"error": f"research.cite failed: {exc}"}


TOOL_DEFS = [
    {"name": "research.cite",
     "description": ("Render a research briefing as a fully cited report: "
                     "numbered [n] citations per claim pointing at source "
                     "URLs, a Sources section, and unverified claims kept "
                     "separate and NEVER cited."),
     "handler": research_cite_handler, "risk": "low",
     "needs_network": False,
     "schema": {"id": "string"}},
    {"name": "research.investigate",
     "description": ("Deep research: decompose a question into sub-questions, "
                     "persist the plan via brain.plan, execute web.search + "
                     "web.fetch through the live registry (low-risk read-only "
                     "only), dedupe findings, flag contradictions "
                     "(heuristic, labeled), and write a cited briefing to "
                     "sqlite — every claim carries its source URL, "
                     "unverifiable items are marked 'could not verify'. "
                     "NEVER invents sources or claims. Unbound (bind_agent "
                     "not called) -> returns the plan only and says so."),
     "handler": research_investigate_handler, "risk": "low",
     "needs_network": True,
     "schema": {"question": "string", "depth?": "int"}},
    {"name": "research.briefing",
     "description": ("Read a past research briefing back from sqlite "
                     "({id: brief_<12 hex chars>}). Honest error when the id "
                     "does not exist."),
     "handler": research_briefing_handler, "risk": "low",
     "needs_network": False,
     "schema": {"id": "string"}},
    {"name": "research.compare",
     "description": ("Investigate each of 2-6 options on a topic, then build "
                     "a comparison table: rows=options, columns=key "
                     "attributes extracted from sources (heuristic, labeled), "
                     "citations per cell where the source supports it. "
                     "Persisted to sqlite like investigate."),
     "handler": research_compare_handler, "risk": "low",
     "needs_network": True,
     "schema": {"topic": "string", "options": "list"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(research_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "research.cite": ("low", False),
    "research.investigate": ("low", True),
    "research.briefing": ("low", False),
    "research.compare": ("low", True),
}


def register(reg) -> None:
    """Wire the Research pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
