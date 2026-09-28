"""Phase 20 — The closed loop: accountable cognition.

Phase 19 gave the brain an adversarial deliberation protocol. Phase 20
closes the loop around it: every deliberation starts from gathered
evidence (not just the caller's context string), can launch a plan and a
recorded forecast, and — critically — must eventually be CLOSED with what
actually happened. brain.cycles is the accountability ledger: open cycles
are decisions whose outcomes were never recorded.

The loop: brain.survey (Observe) -> brain.cycle (Orient+Decide+Act) ->
         brain.close_cycle (Reflect/Learn) -> brain.cycles (ledger)

Honesty contract: survey ranks by keyword overlap (labeled); the
deliberation underneath is the Phase 19 heuristic machinery (labeled);
the calibration half — forecast recorded, outcome scored, Brier updated —
is real statistics.
"""
from __future__ import annotations

import json

from . import brain_pack
from . import metacog_pack
from . import episodic_pack
from . import knowledge_pack


# ---------------------------------------------------------------------------
# sqlite: one extra table in the same brain db file.
# Lock convention (see Phase 19 lesson): _connect() NEVER takes the lock;
# callers hold brain_pack._LOCK around it. threading.Lock is not reentrant.
# ---------------------------------------------------------------------------

def _connect():
    conn = brain_pack._connect()  # base tables, WAL, dirs
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cycles("
        " cycle_id TEXT PRIMARY KEY,"
        " created_at TEXT NOT NULL,"
        " goal TEXT NOT NULL,"
        " survey_json TEXT NOT NULL,"
        " deliberation_json TEXT NOT NULL,"
        " forecast_id TEXT,"
        " plan_id TEXT,"
        " closed_at TEXT,"
        " outcome TEXT,"
        " reflection_json TEXT)"
    )
    conn.commit()
    return conn


def _load_cycle(conn, cycle_id: str) -> dict | None:
    row = conn.execute(
        "SELECT cycle_id, created_at, goal, survey_json, deliberation_json,"
        " forecast_id, plan_id, closed_at, outcome, reflection_json"
        " FROM cycles WHERE cycle_id=?", (cycle_id,)).fetchone()
    if not row:
        return None
    return {"cycle_id": row[0], "created_at": row[1], "goal": row[2],
            "survey": json.loads(row[3]),
            "deliberation": json.loads(row[4]),
            "forecast_id": row[5], "plan_id": row[6],
            "closed_at": row[7], "outcome": row[8],
            "reflection": json.loads(row[9]) if row[9] else None,
            "status": "closed" if row[7] else "open"}


# ---------------------------------------------------------------------------
# brain.survey — Observe: gather evidence before deliberating
# ---------------------------------------------------------------------------

def _keyword_terms(goal: str, k: int) -> list[str]:
    """Most distinctive content tokens: longest first, ties alphabetical."""
    toks = sorted(brain_pack._tokens(goal) - brain_pack._STOPWORDS,
                  key=lambda t: (-len(t), t))
    return toks[:k]


def _semantic_rerank(items: list[dict], key_fn, goal: str) -> tuple[list[dict], bool]:
    """Re-rank recalled items by cosine similarity to the goal.

    Returns (items, used_semantic). Silent no-op when the embedding
    backend is unavailable — keyword order is kept and the flag is False.
    """
    if not items:
        return items, False
    try:
        from . import semantics_pack as _sem
        _eng, _err = _sem.get_engine()
        if _eng is None:
            return items, False
        texts = [key_fn(it) for it in items]
        qv = _eng.embed([goal])[0]
        vecs = _eng.embed(texts)
        ranked = sorted(zip(items, vecs),
                        key=lambda iv: _sem.cosine(qv, iv[1]), reverse=True)
        return [it for it, _ in ranked], True
    except Exception:
        return items, False


def _recall_episodes(goal: str, n: int = 5) -> tuple[list[dict], bool]:
    """Keyword-distilled recall with backoff, then semantic re-rank.

    memory.recall ANDs every FTS term, so a full question almost never
    matches. Distill the goal to its most distinctive keywords, try the
    top 2 ANDed, and back off to the single most distinctive term when
    that finds nothing. Heuristic, and labeled as such.
    Returns (episodes, semantic_reranked).
    """
    episodes: list[dict] = []
    for k in (2, 1):
        terms = _keyword_terms(goal, k)
        if not terms:
            break
        try:
            rec = episodic_pack.recall_handler(
                {"query": " ".join(terms), "n": n})
        except Exception:
            break
        if isinstance(rec, dict) and not rec.get("error"):
            for ep in (rec.get("episodes") or rec.get("results") or [])[:n]:
                if isinstance(ep, dict):
                    episodes.append({
                        "text": str(ep.get("what_happened")
                                    or ep.get("text") or ep.get("summary")
                                    or "")[:280],
                        "when": ep.get("happened_at") or ep.get("when"),
                    })
        if episodes:
            break
    return _semantic_rerank(episodes, lambda e: e["text"], goal)


def _ask_knowledge(goal: str, n: int = 5) -> list[dict]:
    """Same keyword-distillation backoff for the offline knowledge index."""
    knowledge: list[dict] = []
    for k in (2, 1):
        terms = _keyword_terms(goal, k)
        if not terms:
            break
        try:
            ask = knowledge_pack.knowledge_ask_handler(
                {"query": " ".join(terms), "n": n})
        except Exception:
            break
        if isinstance(ask, dict) and not ask.get("error"):
            for hit in (ask.get("hits") or ask.get("results") or [])[:n]:
                if isinstance(hit, dict):
                    knowledge.append({
                        "title": str(hit.get("title") or "")[:120],
                        "snippet": str(hit.get("snippet") or "")[:280],
                        "dataset": hit.get("dataset"),
                    })
        if knowledge:
            break
    return _semantic_rerank(knowledge,
                              lambda k: f"{k['title']} {k['snippet']}", goal)


def _ranked_lessons(goal: str, n: int = 5) -> tuple[list[dict], bool]:
    """Rank stored lessons against the goal, semantic re-rank when available.

    Base rank is keyword overlap (heuristic, labeled); the top candidates
    are then re-ranked by cosine similarity to the goal when the embedding
    backend exists. Returns (lessons, semantic_reranked).
    """
    goal_toks = brain_pack._tokens(goal)
    if not goal_toks:
        return [], False
    conn = brain_pack._connect()
    try:
        rows = conn.execute("SELECT text FROM lessons").fetchall()
    finally:
        conn.close()
    scored = []
    for (text,) in rows:
        toks = brain_pack._tokens(text)
        overlap = (goal_toks & toks) - brain_pack._STOPWORDS
        if overlap:
            scored.append({"lesson": text[:280],
                           "matched_terms": sorted(overlap),
                           "weight": round(len(overlap) / len(goal_toks), 3)})
    scored.sort(key=lambda e: e["weight"], reverse=True)
    return _semantic_rerank(scored[:n], lambda e: e["lesson"], goal)


def survey_handler(args: dict) -> dict:
    """brain.survey {goal, context?} — evidence brief before deliberation.

    Pulls relevant episodic memories, offline knowledge hits, and stored
    lessons for the goal. Returns sections plus a brief_text ready to feed
    as context into brain.deliberate.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.survey: args must be an object"}
        goal = (args.get("goal") or "").strip()
        if not goal:
            return {"error": "brain.survey: 'goal' is required"}

        episodes, sem_ep = _recall_episodes(goal)
        knowledge, sem_kn = _ask_knowledge(goal)
        lessons, sem_le = _ranked_lessons(goal)
        sem_used = sem_ep or sem_kn or sem_le
        want_contra = bool(args.get("contradictions"))

        parts = [f"Evidence brief for: {goal}"]
        if episodes:
            parts.append("Past episodes:\n" + "\n".join(
                f"- {e['text']}" for e in episodes))
        if knowledge:
            parts.append("Offline knowledge:\n" + "\n".join(
                f"- {k['title']}: {k['snippet']}" for k in knowledge))
        if lessons:
            parts.append("Stored lessons:\n" + "\n".join(
                f"- {le['lesson']}" for le in lessons))
        if len(parts) == 1:
            parts.append("(No relevant episodes, knowledge hits, or lessons "
                         "found — deliberation will run on the caller's "
                         "context alone.)")

        # Phase 23: opt-in contradiction mining over the brief's evidence.
        contra_res = None
        if want_contra:
            from . import metacog_pack as _meta
            claim_texts = ([e["text"] for e in episodes]
                           + [le["lesson"] for le in lessons])
            if len(claim_texts) >= 2:
                contra_res = _meta.contradictions_handler(
                    {"claims": claim_texts})
                pairs = contra_res.get("contradictions") or []
                if pairs:
                    parts.append("Tensions to resolve:\n" + "\n".join(
                        f"- [{p['severity']}] {p['claim_a'][:140]}\n"
                        f"  vs ({p['stance_b']}) {p['claim_b'][:140]}"
                        for p in pairs[:5]))
        brief_text = "\n\n".join(parts)

        out = {"goal": goal,
                "episodes": episodes,
                "knowledge": knowledge,
                "lessons": lessons,
                "brief_text": brief_text,
                "sources": {"episodes": len(episodes),
                            "knowledge": len(knowledge),
                            "lessons": len(lessons)},
                "method": (("semantic re-rank: cosine similarity to the goal "
                           "over recalled items; " if sem_used else "")
                           + "heuristic: goal distilled to distinctive keywords "
                           "(2-term AND, backing off to 1 term) for FTS "
                           "recall over episodic memory and offline knowledge; "
                           "keyword-overlap ranking over stored lessons"),
                "semantic_rerank": sem_used}
        if contra_res is not None:
            out["contradictions"] = contra_res
        return out
    except Exception as exc:  # never raise
        return {"error": f"brain.survey failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.cycle — Orient + Decide + Act, recorded and left open
# ---------------------------------------------------------------------------

def cycle_handler(args: dict) -> dict:
    """brain.cycle {goal, context?, options?, criteria?, plan_steps?, forecast?}.

    Runs the full loop: survey -> deliberate -> (optional) forecast record
    -> (optional) plan creation. Stores the cycle OPEN; close it later with
    brain.close_cycle once the outcome is known.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.cycle: args must be an object"}
        goal = (args.get("goal") or "").strip()
        if not goal:
            return {"error": "brain.cycle: 'goal' is required"}
        context = str(args.get("context") or "")

        survey = survey_handler({"goal": goal, "context": context})
        if survey.get("error"):
            return {"error": survey["error"]}

        enriched = (survey["brief_text"]
                    + ("\n\nCaller context:\n" + context if context.strip() else ""))
        deliberation = metacog_pack.deliberate_handler({
            "question": goal,
            "context": enriched,
            **({"options": args["options"]} if args.get("options") else {}),
            **({"criteria": args["criteria"]} if args.get("criteria") else {}),
        })
        if deliberation.get("error"):
            return {"error": deliberation["error"]}

        forecast_id: str | None = None
        forecast = args.get("forecast")
        if isinstance(forecast, dict):
            f = metacog_pack.predict_handler({
                "question": str(forecast.get("question") or goal),
                "p": forecast.get("p"),
                "context": enriched[:2000],
            })
            if not f.get("error"):
                forecast_id = f["prediction_id"]

        plan_id: str | None = None
        steps = args.get("plan_steps")
        if isinstance(steps, list) and steps:
            p = brain_pack.brain_plan_handler({"goal": goal, "steps": steps})
            if not p.get("error"):
                plan_id = p.get("plan_id")

        cycle_id = brain_pack._new_id("cycle")
        deliberation_summary = {
            "trace_id": deliberation.get("trace_id"),
            "conclusion": deliberation.get("conclusion"),
            "confidence": deliberation.get("confidence"),
            "p_raw": deliberation.get("p_raw"),
            "steelman_mass": (deliberation.get("steelman") or {}).get("counter_mass"),
            "premortem_hypotheses": len(
                (deliberation.get("premortem") or {}).get("failure_hypotheses", [])),
            "keystone_flags": (deliberation.get("assumptions") or {}).get("flags", []),
            "decision": ((deliberation.get("decision") or {}).get("winner")
                         if isinstance(deliberation.get("decision"), dict) else None),
        }
        with brain_pack._LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO cycles(cycle_id, created_at, goal, survey_json,"
                    " deliberation_json, forecast_id, plan_id, closed_at,"
                    " outcome, reflection_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cycle_id, brain_pack._now(), goal,
                     json.dumps(survey), json.dumps(deliberation_summary),
                     forecast_id, plan_id, None, None, None))
                conn.commit()
            finally:
                conn.close()

        return {"cycle_id": cycle_id, "status": "open", "goal": goal,
                "survey_sources": survey["sources"],
                "deliberation": deliberation_summary,
                "forecast_id": forecast_id, "plan_id": plan_id,
                "next": ("When the outcome is known, call brain.close_cycle "
                         "with this cycle_id and what happened — open cycles "
                         "are decisions that were never held accountable.")}
    except Exception as exc:  # never raise
        return {"error": f"brain.cycle failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.close_cycle — Reflect / Learn: what actually happened
# ---------------------------------------------------------------------------

def close_cycle_handler(args: dict) -> dict:
    """brain.close_cycle {cycle_id, outcome, forecast_outcome?} — close the loop.

    Records the outcome, scores any linked forecast (Brier), files lessons
    via brain.reflect, and marks the cycle closed.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.close_cycle: args must be an object"}
        cycle_id = str(args.get("cycle_id") or "").strip()
        if not cycle_id:
            return {"error": "brain.close_cycle: 'cycle_id' is required"}
        outcome = str(args.get("outcome") or "").strip()
        if not outcome:
            return {"error": "brain.close_cycle: 'outcome' is required"}

        with brain_pack._LOCK:
            conn = _connect()
            try:
                cycle = _load_cycle(conn, cycle_id)
                if not cycle:
                    return {"error": f"unknown cycle_id '{cycle_id}'"}
                if cycle["status"] == "closed":
                    return {"error": f"cycle '{cycle_id}' already closed"}
            finally:
                conn.close()

        scoring = None
        if cycle["forecast_id"] and args.get("forecast_outcome") is not None:
            s = metacog_pack.score_prediction_handler({
                "prediction_id": cycle["forecast_id"],
                "outcome": args["forecast_outcome"],
            })
            scoring = s if not s.get("error") else {"error": s["error"]}

        reflection = brain_pack.brain_reflect_handler({"on": outcome})
        lessons_filed = (reflection.get("lessons", [])
                         if not reflection.get("error") else [])

        with brain_pack._LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "UPDATE cycles SET closed_at=?, outcome=?, reflection_json=?"
                    " WHERE cycle_id=?",
                    (brain_pack._now(), outcome,
                     json.dumps({"lessons_filed": lessons_filed,
                                 "forecast_scoring": scoring}),
                     cycle_id))
                conn.commit()
            finally:
                conn.close()

        return {"cycle_id": cycle_id, "status": "closed",
                "goal": cycle["goal"],
                "forecast_scoring": scoring,
                "lessons_filed": lessons_filed,
                "learning": ("Outcome recorded, forecast scored, lessons "
                             "filed. The brain's calibration and lesson store "
                             "are now one data point wiser.")}
    except Exception as exc:  # never raise
        return {"error": f"brain.close_cycle failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.cycles — the accountability ledger
# ---------------------------------------------------------------------------

def cycles_handler(args: dict) -> dict:
    """brain.cycles {open_only?} — ledger of cognitive cycles.

    Open cycles are decisions whose outcomes were never recorded — the
    accountability gaps. Closed cycles show goal, outcome, and lessons.
    """
    try:
        a = args if isinstance(args, dict) else {}
        open_only = bool(a.get("open_only"))
        with brain_pack._LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT cycle_id FROM cycles ORDER BY created_at DESC"
                ).fetchall()
                cycles = [_load_cycle(conn, r[0]) for r in rows]
            finally:
                conn.close()
        if open_only:
            cycles = [c for c in cycles if c["status"] == "open"]
        ledger = [{
            "cycle_id": c["cycle_id"], "status": c["status"],
            "goal": c["goal"][:160], "created_at": c["created_at"],
            "closed_at": c["closed_at"],
            "forecast_id": c["forecast_id"], "plan_id": c["plan_id"],
            "outcome": (c["outcome"][:200] if c["outcome"] else None),
            "lessons_filed": len((c["reflection"] or {}).get("lessons_filed", [])),
        } for c in cycles]
        n_open = sum(1 for c in cycles if c["status"] == "open")
        return {"cycles": ledger, "n_open": n_open, "n_total": len(ledger),
                "note": (f"{n_open} open cycle(s) — decisions awaiting their "
                         "outcomes. Close them with brain.close_cycle.")}
    except Exception as exc:  # never raise
        return {"error": f"brain.cycles failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.review — the nightly accountability pass (Phase 22)
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> float | None:
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return None


def review_handler(args: dict) -> dict:
    """brain.review {stale_days?} — the dreamer's accountability pass.

    Reads the cycles ledger: open cycles with their age in days (stale at
    >= stale_days, default 7), mean Brier score over closed cycles that
    scored a forecast, and lessons filed in the last 7 days. Returns a
    briefing-ready markdown section plus the raw numbers.

    The statistics are real; any interpretation ("stale", "healthy") is a
    disclosed heuristic threshold, not a judgment.
    """
    try:
        a = args if isinstance(args, dict) else {}
        try:
            stale_days = int(a.get("stale_days", 7))
        except (TypeError, ValueError):
            return {"error": "brain.review: 'stale_days' must be an integer"}
        stale_days = max(1, stale_days)

        import time
        now = time.time()
        with brain_pack._LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT cycle_id FROM cycles ORDER BY created_at DESC"
                ).fetchall()
                cycles = [_load_cycle(conn, r[0]) for r in rows]
            finally:
                conn.close()

        # lessons live in the brain store, not the cognition store
        recent_lessons: list = []
        try:
            bconn = brain_pack._connect()
            try:
                rows = bconn.execute(
                    "SELECT text, created_at FROM lessons"
                    " ORDER BY created_at DESC LIMIT 20").fetchall()
            finally:
                bconn.close()
            # created_at is ISO text in the brain store — filter in Python.
            cutoff = now - 7 * 86400
            recent_lessons = [
                (t, ts) for t, ts in rows
                if (_parse_ts(ts) or 0) >= cutoff][:5]
        except Exception:
            recent_lessons = []

        open_cycles = []
        briers = []
        for c in cycles:
            created = _parse_ts(c["created_at"]) or now
            age_days = round((now - created) / 86400, 1)
            if c["status"] == "open":
                open_cycles.append({
                    "cycle_id": c["cycle_id"],
                    "goal": c["goal"][:160],
                    "age_days": age_days,
                    "stale": age_days >= stale_days,
                    "has_forecast": bool(c["forecast_id"]),
                    "has_plan": bool(c["plan_id"]),
                })
            else:
                refl = c["reflection"] or {}
                fs = refl.get("forecast_scoring") or {}
                b = fs.get("brier_contribution", fs.get("brier"))
                if isinstance(b, (int, float)):
                    briers.append(float(b))

        stale = [c for c in open_cycles if c["stale"]]
        mean_brier = round(sum(briers) / len(briers), 4) if briers else None

        lines = ["## Open decisions awaiting outcomes"]
        if not open_cycles:
            lines.append("- None. Every decision has its outcome recorded. Tidy.")
        else:
            for c in open_cycles:
                flag = " — STALE" if c["stale"] else ""
                lines.append(f"- **{c['goal'][:80]}** ({c['age_days']}d old{flag})")
            if stale:
                lines.append(f"- Nudge: {len(stale)} decision(s) older than "
                             f"{stale_days}d still await outcomes — close them "
                             "with brain.close_cycle when results are in.")
        lines.append("")
        lines.append("## Forecast calibration (closed cycles)")
        if mean_brier is None:
            lines.append("- No scored forecasts yet — Brier trend needs "
                         "resolved forecasts to mean anything.")
        else:
            lines.append(f"- Mean Brier over {len(briers)} scored forecast(s): "
                         f"{mean_brier} (0 = perfect, 0.25 = coin-flip).")
        if recent_lessons:
            lines.append("")
            lines.append("## Lessons filed this week")
            for text, _ts in recent_lessons:
                lines.append(f"- {str(text)[:140]}")

        return {
            "open_cycles": open_cycles,
            "n_open": len(open_cycles),
            "n_stale": len(stale),
            "stale_days": stale_days,
            "closed_cycles": len(cycles) - len(open_cycles),
            "mean_brier": mean_brier,
            "n_scored_forecasts": len(briers),
            "recent_lessons": [str(t) for t, _ in recent_lessons],
            "briefing_md": "\n".join(lines),
            "method": ("heuristic: 'stale' is a flat age threshold "
                       f"({stale_days}d); Brier mean is a real average over "
                       "resolved forecasts; nothing here auto-closes cycles."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.review failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.handoff / brain.resume — session continuity (Phase 29)
# ---------------------------------------------------------------------------

def _handoff_path():
    import os
    from pathlib import Path
    override = os.environ.get("JARVIS_HANDOFF_FILE")
    if override:
        return Path(override)
    return Path.home() / "workspace" / "jarvis" / "data" / "handoff.md"


def handoff_handler(args: dict) -> dict:
    """brain.handoff {note?} — write the session carry-forward file.

    Snapshot of where things stand: open cycles, active plans with
    their next step, pending approvals, unresolved forecasts, recent
    lessons, calibration snapshot. Plain markdown at
    ~/workspace/jarvis/data/handoff.md (JARVIS_HANDOFF_FILE overrides).

    A snapshot, not a live view — it carries its own timestamp.
    Everything in it is re-derivable from the stores.
    """
    try:
        a = args if isinstance(args, dict) else {}
        note = str(a.get("note") or "").strip()[:500]
        now_ts = brain_pack._now()

        # Open cycles (cognition store).
        with brain_pack._LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT cycle_id FROM cycles WHERE closed_at IS NULL"
                    " ORDER BY created_at").fetchall()
                cycles = [_load_cycle(conn, r[0]) for r in rows]
            finally:
                conn.close()

        # Active plans + recent lessons (brain store).
        plans, lessons = [], []
        with brain_pack._LOCK:
            bconn = brain_pack._connect()
            try:
                for row in bconn.execute(
                        "SELECT plan_id, goal, steps_json FROM plans"
                        " WHERE status='active' ORDER BY updated_at DESC"
                        " LIMIT 10").fetchall():
                    try:
                        steps = json.loads(row[2])
                    except (TypeError, ValueError):
                        steps = []
                    nxt = brain_pack._next_step(steps) if steps else None
                    plans.append({
                        "plan_id": row[0], "goal": row[1][:120],
                        "next_step": (f"{nxt['id']} ({nxt['tool']})"
                                      if nxt else "none runnable"),
                    })
                lessons = [r[0] for r in bconn.execute(
                    "SELECT text FROM lessons ORDER BY created_at DESC"
                    " LIMIT 5").fetchall()]
            finally:
                bconn.close()

        # Pending approvals (autopilot store; lazy import, best effort).
        pending_approvals: list = []
        try:
            from . import autopilot_pack as _ap
            pending_approvals = [
                {"id": p["id"], "goal": p["goal"][:100]}
                for p in _ap._list_proposals(status="pending")]
        except Exception:
            pending_approvals = []

        # Unresolved forecasts + calibration (metacog store; best effort).
        n_unresolved = 0
        calib = "n/a"
        try:
            _meta = metacog_pack  # module-level import
            with brain_pack._LOCK:
                mconn = _meta._connect()
                try:
                    n_unresolved = mconn.execute(
                        "SELECT COUNT(*) FROM predictions"
                        " WHERE resolved_at IS NULL").fetchone()[0]
                finally:
                    mconn.close()
            c = _meta.calibration_handler({})
            if not c.get("error"):
                calib = (f"{c.get('n_resolved', 0)} resolved, "
                         f"Brier {c.get('brier_score', 'n/a')}")
        except Exception:
            pass

        lines = [f"# Session handoff — written {now_ts}", ""]
        if note:
            lines += [f"> {note}", ""]
        lines.append(f"## Open cycles ({len(cycles)})")
        if cycles:
            import time as _time
            now_epoch = _time.time()
            for c in cycles:
                age = _parse_ts(c["created_at"])
                age_s = (f"{round((now_epoch - age) / 86400, 1)}d old"
                         if age else "age unknown")
                lines.append(f"- `{c['cycle_id']}` {c['goal'][:100]} ({age_s})")
        else:
            lines.append("- none — every decision has its outcome")
        lines.append("")
        lines.append(f"## Active plans ({len(plans)})")
        if plans:
            for p in plans:
                lines.append(f"- `{p['plan_id']}` {p['goal']} — next: {p['next_step']}")
        else:
            lines.append("- none")
        lines.append("")
        lines.append(f"## Pending approvals ({len(pending_approvals)})")
        if pending_approvals:
            for p in pending_approvals:
                lines.append(f"- `{p['id']}` {p['goal']}")
        else:
            lines.append("- queue is clear")
        lines.append("")
        lines.append(f"## Unresolved forecasts: {n_unresolved}")
        lines.append(f"## Calibration: {calib}")
        lines.append("")
        lines.append(f"## Recent lessons ({len(lessons)})")
        for le in lessons:
            lines.append(f"- {str(le)[:160]}")
        if not lessons:
            lines.append("- none filed yet")

        path = _handoff_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {
            "path": str(path),
            "written_at": now_ts,
            "open_cycles": len(cycles),
            "active_plans": len(plans),
            "pending_approvals": len(pending_approvals),
            "unresolved_forecasts": n_unresolved,
            "note": ("Snapshot written. It is a convenience copy — the "
                     "stores remain the source of truth. Re-run handoff "
                     "when state changes; a stale handoff is worse than none."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.handoff failed: {exc}"}


def resume_handler(args: dict) -> dict:  # noqa: ARG001
    """brain.resume {} — read the handoff file back as a session briefing.

    Honest when no handoff was ever written: reports that instead of
    inventing state. Surfaces the file's own timestamp so a stale
    handoff is visible.
    """
    try:
        import os
        path = _handoff_path()
        if not path.exists():
            return {"handoff": None,
                    "note": "No handoff recorded yet — run brain.handoff "
                            "at the end of a session to leave one."}
        text = path.read_text(encoding="utf-8")
        mtime = os.path.getmtime(path)
        from datetime import datetime, timezone
        written = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        return {
            "handoff": text,
            "path": str(path),
            "file_mtime": written,
            "note": ("Snapshot from " + written + " — verify against live "
                     "state if the session did work after it was written."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.resume failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.integrity — the integrity loop (Phase 30 capstone)
# ---------------------------------------------------------------------------

def integrity_handler(args: dict) -> dict:
    """brain.integrity {sample_traces?: int, write_handoff?: bool} — one
    call running the whole accountability stack.

    Runs brain.review (stale cycles + Brier trend + weekly lessons),
    brain.contradictions (open claim tensions), brain.audit over the N
    most recent think traces (default 5), brain.calibration, and
    autopilot.digest (queue risk groups) — then composes one markdown
    "state of the brain" report with a mechanical verdict:
    sound / needs_attention / degraded.

    Read-only unless write_handoff is true, and even then it only
    appends the report to the handoff file — never mutates cycles,
    traces, or lessons. 'sound' means every check passed, not that the
    brain is right.
    """
    try:
        a = args if isinstance(args, dict) else {}
        sample_n = a.get("sample_traces", 5)
        sample_n = max(1, min(20, int(sample_n) if isinstance(
            sample_n, (int, float)) else 5))
        write_handoff = bool(a.get("write_handoff"))

        issues: list[str] = []
        sections: list[str] = []

        # 1. review — stale cycles, Brier, lessons
        rev = review_handler({})
        if rev.get("error"):
            issues.append(f"review failed: {rev['error']}")
            sections.append("## Accountability review\n- check failed")
        else:
            n_stale = rev.get("n_stale", 0)
            sections.append(
                f"## Accountability review\n"
                f"- open cycles: {len(rev.get('open_cycles', []))}, "
                f"stale: {n_stale}\n"
                f"- mean Brier: {rev.get('mean_brier', 'n/a')}\n"
                f"- lessons this week: {len(rev.get('recent_lessons', []))}")
            if n_stale:
                issues.append(f"{n_stale} stale decision cycle(s)")

        # 2. contradictions — mine recent lessons + open cycle goals as claims
        try:
            claims: list[str] = []
            with brain_pack._LOCK:
                bconn = brain_pack._connect()
                try:
                    claims += [r[0] for r in bconn.execute(
                        "SELECT text FROM lessons ORDER BY created_at DESC"
                        " LIMIT 20").fetchall()]
                finally:
                    bconn.close()
                cconn = _connect()
                try:
                    claims += [r[0] for r in cconn.execute(
                        "SELECT goal FROM cycles WHERE closed_at IS NULL"
                        " LIMIT 20").fetchall()]
                finally:
                    cconn.close()
            claims = [str(c)[:500] for c in claims if str(c).strip()]
            if len(claims) < 2:
                sections.append("## Open tensions\n- nothing to compare yet")
            else:
                con = metacog_pack.contradictions_handler({"claims": claims})
                if con.get("error"):
                    issues.append(
                        f"contradiction check failed: {con['error']}")
                    sections.append("## Open tensions\n- check failed")
                else:
                    n_ten = len(con.get("contradictions", []))
                    sections.append(
                        f"## Open tensions\n- candidate tensions: {n_ten} "
                        f"(from {len(claims)} recent claims)")
                    if n_ten:
                        issues.append(f"{n_ten} unresolved claim tension(s)")
        except Exception as exc:
            issues.append(f"contradiction check failed: {exc}")
            sections.append("## Open tensions\n- check failed")

        # 3. audit sample — most recent traces
        audited, dirty = 0, 0
        dirty_ids: list[str] = []
        try:
            with brain_pack._LOCK:
                bconn = brain_pack._connect()
                try:
                    rows = bconn.execute(
                        "SELECT trace_id FROM traces ORDER BY created_at DESC"
                        f" LIMIT {sample_n}").fetchall()
                    tids = [r[0] for r in rows]
                finally:
                    bconn.close()
            for tid in tids:
                res = metacog_pack.audit_handler({"trace_id": tid})
                audited += 1
                if res.get("verdict") != "clean":
                    dirty += 1
                    dirty_ids.append(tid)
            sections.append(f"## Trace audit (last {audited})\n"
                            f"- clean: {audited - dirty}, issues: {dirty}")
            if dirty:
                issues.append(f"{dirty} trace(s) failed audit: "
                              + ", ".join(dirty_ids[:3]))
        except Exception as exc:
            issues.append(f"audit sample failed: {exc}")
            sections.append("## Trace audit\n- check failed")

        # 4. calibration
        try:
            cal = metacog_pack.calibration_handler({})
            if cal.get("error"):
                sections.append("## Calibration\n- no resolved forecasts yet")
            else:
                sections.append(
                    f"## Calibration\n- resolved: {cal.get('n_resolved', 0)}, "
                    f"Brier: {cal.get('brier_score', 'n/a')}")
        except Exception as exc:
            issues.append(f"calibration failed: {exc}")
            sections.append("## Calibration\n- check failed")

        # 5. approval digest
        try:
            from . import autopilot_pack as _ap
            dg = _ap.digest_handler({})
            if dg.get("error"):
                issues.append(f"digest failed: {dg['error']}")
                sections.append("## Approval queue\n- check failed")
            else:
                g = dg.get("groups", {})
                sections.append(
                    f"## Approval queue ({dg.get('count', 0)} waiting)\n"
                    f"- safe to batch: {len(g.get('all_low', []))}, "
                    f"review first: {len(g.get('has_medium', []))}, "
                    f"careful: {len(g.get('has_high_or_unknown', []))}")
                if dg.get("count"):
                    issues.append(f"{dg['count']} proposal(s) awaiting approval")
        except Exception as exc:
            issues.append(f"digest failed: {exc}")
            sections.append("## Approval queue\n- check failed")

        verdict = ("sound" if not issues
                   else "degraded" if dirty else "needs_attention")
        report = [f"# State of the brain — verdict: **{verdict}**", ""]
        report.extend(sections)
        report += ["", "## Issues" if issues else "## Issues\n- none"]
        for i in issues:
            report.append(f"- {i}")
        report.append("")
        report.append("_Mechanical verdict from the accountability stack: "
                      "'sound' means every check passed, not that the brain "
                      "is right. Fabricated-but-consistent evidence still "
                      "audits clean._")
        report_md = "\n".join(report)

        handoff_note = None
        if write_handoff:
            h = handoff_handler(
                {"note": f"integrity loop verdict: {verdict} "
                         f"({len(issues)} issue(s))"})
            handoff_note = ("handoff write failed: " + h["error"]
                            if h.get("error") else "report appended to handoff")

        return {
            "verdict": verdict,
            "issues": issues,
            "traces_audited": audited,
            "report_md": report_md,
            "handoff": handoff_note,
            "read": ("One mechanical pass over the whole accountability "
                     "stack. Reuses the existing tools — one definition of "
                     "each check. A verdict of 'sound' is not a certificate "
                     "of truth."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.integrity failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.debate — structured multi-pass debate (Phase 33)
# ---------------------------------------------------------------------------

def debate_handler(args: dict) -> dict:
    """brain.debate {question, sides[]} — run brain.think once per side
    (each primed with that side's stance), then a final judging think
    over the sides' conclusions.

    sides: [{name, stance}] — stance is free text, e.g. "argue FOR
    raising prices". 2-4 sides. This is still one mechanical brain
    doing multiple passes, not independent minds — the description says
    so. Returns per-side conclusions plus a judged verdict.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.debate: args must be an object"}
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "brain.debate: 'question' is required"}
        sides = args.get("sides")
        if not isinstance(sides, list) or not 2 <= len(sides) <= 4:
            return {"error": "brain.debate: 'sides' must be a list of 2-4 {name, stance}"}
        norm = []
        for i, s in enumerate(sides):
            if not isinstance(s, dict):
                return {"error": f"brain.debate: side {i} is not an object"}
            name = str(s.get("name") or f"side{i+1}").strip()[:60]
            stance = str(s.get("stance") or "").strip()
            if not stance:
                return {"error": f"brain.debate: side '{name}' needs a 'stance'"}
            norm.append({"name": name, "stance": stance[:500]})

        results = []
        for s in norm:
            r = brain_pack.brain_think_handler({
                "question": question,
                "context": (f"Debate position for '{s['name']}': {s['stance']}. "
                            f"Argue this position as strongly as the evidence "
                            f"allows; note where the evidence is thin.")})
            if r.get("error"):
                return {"error": f"brain.debate: side '{s['name']}' failed: {r['error']}"}
            results.append({"name": s["name"], "stance": s["stance"],
                            "trace_id": r.get("trace_id"),
                            "conclusion": r.get("conclusion"),
                            "confidence": r.get("confidence")})

        judge_ctx = " ".join(
            f"[{r['name']}] concluded: {r['conclusion']} "
            f"(confidence {r['confidence']})." for r in results)
        judge = brain_pack.brain_think_handler({
            "question": f"Judge this debate and give a final verdict: {question}",
            "context": ("You are the judge. The debaters' conclusions "
                        "follow. Weigh them against each other, name the "
                        "strongest argument on each side, and deliver a "
                        "verdict. " + judge_ctx)})
        if judge.get("error"):
            return {"error": f"brain.debate: judging pass failed: {judge['error']}"}
        return {
            "question": question,
            "sides": results,
            "verdict": judge.get("conclusion"),
            "verdict_confidence": judge.get("confidence"),
            "verdict_trace_id": judge.get("trace_id"),
            "read": ("One mechanical brain, multiple primed passes — not "
                     "independent minds. The judge sees only the sides' "
                     "recorded conclusions, so a side that argued thin "
                     "evidence judges thin."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.debate failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "brain.survey",
     "description": ("Observe: gather an evidence brief for a goal before "
                     "deliberating — relevant episodic memories, offline "
                     "knowledge hits, and stored lessons, keyword-ranked. "
                     "Returns sections plus brief_text ready to feed into "
                     "brain.deliberate as context."),
     "handler": survey_handler, "risk": "low", "needs_network": False,
     "schema": {"goal": "string", "context?": "string",
                "contradictions?": "boolean"}},
    {"name": "brain.cycle",
     "description": ("The full cognitive loop in one call: survey -> "
                     "adversarial deliberation -> optional recorded forecast "
                     "-> optional plan creation. Stores the cycle OPEN with a "
                     "cycle_id; close it later with brain.close_cycle once "
                     "the outcome is known."),
     "handler": cycle_handler, "risk": "low", "needs_network": False,
     "schema": {"goal": "string", "context?": "string", "options?": "array",
                "criteria?": "array", "plan_steps?": "array",
                "forecast?": "object"}},
    {"name": "brain.close_cycle",
     "description": ("Close the loop: record what actually happened for a "
                     "cycle, score its linked forecast (Brier), file lessons "
                     "via brain.reflect, and mark it closed. This is the "
                     "learning half of cognition."),
     "handler": close_cycle_handler, "risk": "low", "needs_network": False,
     "schema": {"cycle_id": "string", "outcome": "string",
                "forecast_outcome?": "string"}},
    {"name": "brain.cycles",
     "description": ("Accountability ledger: all cognitive cycles, open and "
                     "closed. Open cycles are decisions whose outcomes were "
                     "never recorded. Pass open_only:true to see just the "
                     "gaps."),
     "handler": cycles_handler, "risk": "low", "needs_network": False,
     "schema": {"open_only?": "boolean"}},
    {"name": "brain.review",
     "description": ("Nightly accountability pass over the cycles ledger: "
                     "open decisions with age in days (stale at >= "
                     "stale_days, default 7), mean Brier over closed cycles "
                     "with scored forecasts, lessons filed this week, plus a "
                     "briefing-ready markdown section. The 2am dream run "
                     "calls this and folds it into the morning briefing. "
                     "Statistics are real; 'stale' is a disclosed heuristic "
                     "threshold. Nothing auto-closes cycles."),
     "handler": review_handler, "risk": "low", "needs_network": False,
     "schema": {"stale_days?": "int"}},
    {"name": "brain.handoff",
     "description": ("Write the session carry-forward file: open cycles, "
                     "active plans with next step, pending approvals, "
                     "unresolved forecasts, recent lessons, calibration "
                     "snapshot, plus an optional verbatim note. Plain "
                     "markdown at ~/workspace/jarvis/data/handoff.md "
                     "(JARVIS_HANDOFF_FILE overrides). A snapshot with its "
                     "own timestamp — the stores stay the source of truth."),
     "handler": handoff_handler, "risk": "low", "needs_network": False,
     "schema": {"note?": "string"}},
    {"name": "brain.resume",
     "description": ("Read the handoff file back as a session briefing, "
                     "with the file's timestamp so staleness is visible. "
                     "Honest 'no handoff recorded yet' when the file is "
                     "missing — never invents state."),
     "handler": resume_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "brain.integrity",
     "description": ("Run the whole accountability stack in one call — "
                     "brain.review, brain.contradictions, a brain.audit "
                     "sample of recent traces, brain.calibration, and the "
                     "autopilot digest — and compose a single 'state of the "
                     "brain' report with a mechanical verdict "
                     "(sound/needs_attention/degraded). Read-only unless "
                     "write_handoff is true. 'sound' means every check "
                     "passed, not that the brain is right."),
     "handler": integrity_handler, "risk": "low", "needs_network": False,
     "schema": {"sample_traces?": "int", "write_handoff?": "bool"}},
    {"name": "brain.debate",
     "description": ("Structured debate (Phase 33): run brain.think once per "
                     "side, each primed with that side's stance, then a "
                     "judging think over the recorded conclusions. 2-4 "
                     "sides. One mechanical brain doing multiple passes, "
                     "not independent minds — the result says so."),
     "handler": debate_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "sides": "list"}},
]

RISK_TABLE_ADDITIONS = {spec["name"]: (spec["risk"], spec["needs_network"])
                        for spec in TOOL_DEFS}


def register(reg) -> None:
    """Wire the four Phase 20 closed-loop tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
