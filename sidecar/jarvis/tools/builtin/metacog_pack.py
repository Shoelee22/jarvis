"""Phase 19 — Metacognitive brain: the deliberation loop that argues with itself.

brain.think/decide are single-pass heuristics. Real deliberation is
adversarial: steelman the opposition, run a pre-mortem, audit the
load-bearing assumptions, trace second-order consequences — and then
check the result against the brain's own calibrated track record.

Honesty contract (same as brain_pack): the evidence weighing underneath
is keyword-overlap heuristics, and every tool says so. The DELIBERATION
STRUCTURE is the real advance — asking "why might this fail?" before
committing is sound decision science even when the scoring is rough.
One piece here is mathematically real, not heuristic: prediction
calibration. brain.predict records probabilistic forecasts; when they
resolve, brain.score_prediction updates a running Brier score and
brain.calibration reports the empirical hit rate per confidence bucket
("when the brain said 70%, it was right X% of the time"). That part is
actual calibration, not vibes.

Tools: brain.steelman, brain.premortem, brain.assumptions,
         brain.second_order, brain.predict, brain.score_prediction,
         brain.calibration, brain.deliberate
"""
from __future__ import annotations

import json
import sqlite3

from . import brain_pack

# ---------------------------------------------------------------------------
# sqlite: one extra table in the same brain db file
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    # Same convention as brain_pack._connect: this does NOT take the lock;
    # callers hold brain_pack._LOCK around it (threading.Lock is not
    # reentrant, so taking it here would deadlock the handlers below).
    conn = brain_pack._connect()  # creates base tables, WAL, dirs
    conn.execute(
        "CREATE TABLE IF NOT EXISTS predictions("
        " prediction_id TEXT PRIMARY KEY,"
        " created_at TEXT NOT NULL,"
        " question TEXT NOT NULL,"
        " p REAL NOT NULL,"
        " context_excerpt TEXT NOT NULL,"
        " resolved_at TEXT,"
        " outcome REAL)"
    )
    conn.commit()
    return conn


def _trace_evidence(trace_id: str) -> dict | None:
    """Re-read a think trace's evidence/nets from sqlite."""
    conn = brain_pack._connect()
    try:
        row = conn.execute(
            "SELECT decomposition_json, evidence_json, nets_json, conclusion "
            "FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"sub_questions": json.loads(row[0]),
            "evidence": json.loads(row[1]),
            "nets": json.loads(row[2]),
            "conclusion": row[3]}


def _think(question: str, context: str) -> dict:
    """Run one think pass; return {'trace', 'error'?}."""
    res = brain_pack.brain_think_handler({"question": question,
                                          "context": context})
    if res.get("error") or not res.get("trace_id"):
        return {"error": res.get("error", "think produced no trace")}
    trace = _trace_evidence(res["trace_id"])
    if not trace:
        return {"error": "think trace not found in store"}
    trace["trace_id"] = res["trace_id"]
    trace["confidence"] = res.get("confidence")
    return {"trace": trace}


def _leading(trace: dict) -> tuple[str, dict]:
    nets = trace["nets"]
    sq = max(nets, key=lambda k: nets[k]["net"])
    return sq, nets[sq]


# ---------------------------------------------------------------------------
# brain.steelman — the strongest case AGAINST the leading reading
# ---------------------------------------------------------------------------

def steelman_handler(args: dict) -> dict:
    """brain.steelman {question, context?} — devil's advocate.

    Runs think, then builds the strongest counter-case to the leading
    reading from counter-stance evidence and rival sub-questions.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.steelman: args must be an object"}
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "brain.steelman: 'question' is required"}
        context = str(args.get("context") or "")

        t = _think(question, context)
        if t.get("error"):
            return {"error": t["error"]}
        trace = t["trace"]
        lead_sq, lead = _leading(trace)

        counter: list[dict] = []
        for ev in trace["evidence"]:
            sq = ev["sub_question"]
            if sq != lead_sq:
                counter.append({**ev, "role": "supports a rival reading"})
            elif ev["stance"] == "against":
                counter.append({**ev, "role": "directly opposes the leading reading"})
        counter.sort(key=lambda e: e["weight"], reverse=True)
        counter_mass = round(sum(e["weight"] for e in counter), 3)

        if not counter:
            case = ("No counter-evidence found: nothing in the context opposed "
                    "the leading reading or supported a rival one. That is a "
                    "property of the context supplied, not proof the reading "
                    "is safe.")
        else:
            top = "; ".join(
                f"'{e['sentence'][:110]}' (weight {e['weight']}, {e['role']})"
                for e in counter[:5])
            case = (f"The strongest case against '{lead_sq[:120]}' rests on "
                    f"{len(counter)} counter-evidence items (total weight "
                    f"{counter_mass} vs supporting weight {lead['for']}): {top}")

        return {"trace_id": trace["trace_id"],
                "leading_reading": lead_sq,
                "counter_case": case,
                "counter_items": counter[:10],
                "counter_mass": counter_mass,
                "supporting_mass": lead["for"],
                "method": ("heuristic: counter-stance sentences + rival "
                           "sub-question evidence, ranked by keyword weight")}
    except Exception as exc:  # never raise
        return {"error": f"brain.steelman failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.premortem — prospective hindsight: assume the decision failed
# ---------------------------------------------------------------------------

def premortem_handler(args: dict) -> dict:
    """brain.premortem {decision, context?} — assume it failed; say why.

    Inverts the supporting evidence: each load-bearing claim becomes a
    failure hypothesis ("if this claim is wrong, the decision fails
    because..."), ranked by how much of the case rests on it.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.premortem: args must be an object"}
        decision = (args.get("decision") or "").strip()
        if not decision:
            return {"error": "brain.premortem: 'decision' is required"}
        context = str(args.get("context") or "")

        t = _think(decision, context)
        if t.get("error"):
            return {"error": t["error"]}
        trace = t["trace"]
        lead_sq, lead = _leading(trace)
        total_for = lead["for"] or 1e-9

        supporting = [e for e in trace["evidence"]
                      if e["sub_question"] == lead_sq and e["stance"] == "for"]
        supporting.sort(key=lambda e: e["weight"], reverse=True)

        hypotheses = []
        for e in supporting:
            share = round(e["weight"] / total_for, 3)
            hypotheses.append({
                "if_wrong": e["sentence"][:280],
                "why_fatal": (f"This claim carries {share:.0%} of the supporting "
                              f"weight behind the decision. If it is false, the "
                              f"net case drops by {e['weight']}."),
                "weight": e["weight"],
                "share_of_case": share,
            })

        return {"trace_id": trace["trace_id"],
                "decision": decision,
                "scenario": ("It is six months later. The decision failed "
                             "spectacularly. The most plausible reasons, ranked "
                             "by how much of the original case rested on each "
                             "claim:"),
                "failure_hypotheses": hypotheses,
                "note": ("Heuristic inversion of the think evidence — the "
                         "technique (prospective hindsight) is sound decision "
                         "science; the ranking is keyword-weight rough. Absence "
                         "of counter-evidence is not evidence of safety."),
                "method": "heuristic: supporting claims inverted, ranked by weight"}
    except Exception as exc:  # never raise
        return {"error": f"brain.premortem failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.assumptions — load-bearing assumptions + fragility
# ---------------------------------------------------------------------------

def assumptions_handler(args: dict) -> dict:
    """brain.assumptions {question, context?} — what the conclusion stands on.

    Each supporting sentence of the leading reading is a load-bearing
    assumption; fragility = its share of the supporting weight. Ranked.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.assumptions: args must be an object"}
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "brain.assumptions: 'question' is required"}
        context = str(args.get("context") or "")

        t = _think(question, context)
        if t.get("error"):
            return {"error": t["error"]}
        trace = t["trace"]
        lead_sq, lead = _leading(trace)
        total_for = lead["for"] or 1e-9

        supporting = [e for e in trace["evidence"]
                      if e["sub_question"] == lead_sq and e["stance"] == "for"]
        assumptions = [{
            "assumption": e["sentence"][:280],
            "fragility": round(e["weight"] / total_for, 3),
            "weight": e["weight"],
        } for e in supporting]
        assumptions.sort(key=lambda a: a["fragility"], reverse=True)

        flags = []
        if len(supporting) < 3:
            flags.append("single-source risk: the leading reading rests on "
                         f"fewer than 3 supporting sentences ({len(supporting)}).")
        if assumptions and assumptions[0]["fragility"] >= 0.5:
            flags.append("keystone assumption: one claim carries >=50% of the "
                         "supporting weight — verify it first.")

        return {"trace_id": trace["trace_id"],
                "leading_reading": lead_sq,
                "assumptions": assumptions,
                "flags": flags,
                "method": ("heuristic: fragility = sentence weight / total "
                           "supporting weight of the leading reading")}
    except Exception as exc:  # never raise
        return {"error": f"brain.assumptions failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.second_order — consequence chains
# ---------------------------------------------------------------------------

_CAUSAL_CUES = ("leads to", "causes", "results in", "triggers", "produces",
                "creates", "brings about", "gives rise to", "sets off",
                "sparks", "drives", "means that", "so that")


def second_order_handler(args: dict) -> dict:
    """brain.second_order {decision, context?} — what happens after what happens.

    Extracts causal claims ("X leads to Y") from the context and chains
    them two levels deep where an effect shares terms with another cause.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.second_order: args must be an object"}
        decision = (args.get("decision") or "").strip()
        if not decision:
            return {"error": "brain.second_order: 'decision' is required"}
        context = str(args.get("context") or "")

        links: list[dict] = []
        for sent in brain_pack._sentences(context):
            low = sent.lower()
            for cue in _CAUSAL_CUES:
                idx = low.find(cue)
                if idx == -1:
                    continue
                cause = sent[:idx].strip()
                effect = sent[idx + len(cue):].strip()
                if cause and effect:
                    links.append({"cause": cause[:200], "cue": cue,
                                  "effect": effect[:200],
                                  "cause_toks": brain_pack._tokens(cause),
                                  "effect_toks": brain_pack._tokens(effect)})
                break  # one cue per sentence is enough

        chains: list[dict] = []
        for a in links:
            chained = False
            for b in links:
                if b is a:
                    continue
                shared = (a["effect_toks"] & b["cause_toks"]
                          ) - brain_pack._STOPWORDS
                if len(shared) >= 2:
                    chains.append({
                        "level_1": f"{a['cause']} {a['cue']} {a['effect']}",
                        "level_2": f"{b['cause']} {b['cue']} {b['effect']}",
                        "link_terms": sorted(shared)[:6],
                    })
                    chained = True
                    break
            if not chained:
                chains.append({
                    "level_1": f"{a['cause']} {a['cue']} {a['effect']}",
                    "level_2": None,
                    "link_terms": [],
                })

        clean = [{k: v for k, v in c.items()} for c in chains]
        return {"decision": decision,
                "chains": clean,
                "causal_claims_found": len(links),
                "note": ("Heuristic: cue-phrase extraction over the supplied "
                         "context, chained where effect/cause share >=2 "
                         "non-stopword terms. Not causal understanding — a "
                         "prompt to think about downstream effects."),
                "method": "heuristic: causal cue phrases + token-overlap chaining"}
    except Exception as exc:  # never raise
        return {"error": f"brain.second_order failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.predict / score_prediction / calibration — real calibration
# ---------------------------------------------------------------------------

def _parse_outcome(value) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and value in (0, 1):
        return float(value)
    s = str(value or "").strip().lower()
    if s in ("1", "yes", "true", "happened", "correct"):
        return 1.0
    if s in ("0", "no", "false", "didn't happen", "did not happen",
             "incorrect", "wrong"):
        return 0.0
    return None


def predict_handler(args: dict) -> dict:
    """brain.predict {question, p, context?} — record a probabilistic forecast."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.predict: args must be an object"}
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "brain.predict: 'question' is required"}
        try:
            p = float(args.get("p"))
        except (TypeError, ValueError):
            return {"error": "brain.predict: 'p' must be a number in [0, 1]"}
        if not (0.0 <= p <= 1.0):
            return {"error": "brain.predict: 'p' must be in [0, 1]"}
        context = str(args.get("context") or "")

        pid = brain_pack._new_id("pred")
        with brain_pack._LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO predictions(prediction_id, created_at, question,"
                    " p, context_excerpt, resolved_at, outcome)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (pid, brain_pack._now(), question, p, context[:500],
                     None, None))
                conn.commit()
            finally:
                conn.close()
        return {"prediction_id": pid, "p": p, "question": question,
                "note": ("Recorded. When the outcome is known, call "
                         "brain.score_prediction with outcome 1 (happened) or "
                         "0 (did not). Calibration is scored with the Brier "
                         "score: mean((p - outcome)^2); lower is better, "
                         "0.25 ~= chance.")}
    except Exception as exc:  # never raise
        return {"error": f"brain.predict failed: {exc}"}


def score_prediction_handler(args: dict) -> dict:
    """brain.score_prediction {prediction_id, outcome} — resolve a forecast."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.score_prediction: args must be an object"}
        pid = str(args.get("prediction_id") or "").strip()
        if not pid:
            return {"error": "brain.score_prediction: 'prediction_id' is required"}
        outcome = _parse_outcome(args.get("outcome"))
        if outcome is None:
            return {"error": ("brain.score_prediction: 'outcome' must be "
                              "1/0, true/false, or yes/no")}

        with brain_pack._LOCK:
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT p, resolved_at FROM predictions WHERE prediction_id=?",
                    (pid,)).fetchone()
                if not row:
                    return {"error": f"unknown prediction_id '{pid}'"}
                if row[1] is not None:
                    return {"error": f"prediction '{pid}' already resolved"}
                p = float(row[0])
                conn.execute(
                    "UPDATE predictions SET resolved_at=?, outcome=? "
                    "WHERE prediction_id=?",
                    (brain_pack._now(), outcome, pid))
                conn.commit()
                rows = conn.execute(
                    "SELECT p, outcome FROM predictions "
                    "WHERE resolved_at IS NOT NULL").fetchall()
            finally:
                conn.close()

        contrib = round((p - outcome) ** 2, 4)
        brier = round(sum((float(r[0]) - float(r[1])) ** 2 for r in rows)
                      / len(rows), 4)
        return {"prediction_id": pid, "p": p, "outcome": outcome,
                "brier_contribution": contrib,
                "running_brier": brier,
                "n_resolved": len(rows),
                "read": ("Brier 0 = perfect, 0.25 ~= chance. "
                         f"This forecast contributed {contrib}.")}
    except Exception as exc:  # never raise
        return {"error": f"brain.score_prediction failed: {exc}"}


_BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


def calibration_handler(args: dict) -> dict:  # noqa: ARG001
    """brain.calibration {} — the brain's actual forecasting track record."""
    try:
        with brain_pack._LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT p, outcome FROM predictions "
                    "WHERE resolved_at IS NOT NULL").fetchall()
                pending = conn.execute(
                    "SELECT COUNT(*) FROM predictions "
                    "WHERE resolved_at IS NULL").fetchone()[0]
            finally:
                conn.close()

        n = len(rows)
        if n == 0:
            return {"n_resolved": 0, "n_pending": pending,
                    "verdict": "no resolved predictions yet — nothing to calibrate",
                    "buckets": []}
        brier = round(sum((float(p) - float(o)) ** 2 for p, o in rows) / n, 4)

        buckets = []
        for lo, hi in _BUCKETS:
            in_b = [(float(p), float(o)) for p, o in rows if lo <= float(p) < hi]
            if not in_b:
                buckets.append({"range": [lo, round(min(hi, 1.0), 1)],
                                "n": 0, "avg_p": None, "hit_rate": None})
                continue
            avg_p = sum(p for p, _ in in_b) / len(in_b)
            hit = sum(o for _, o in in_b) / len(in_b)
            buckets.append({"range": [lo, round(min(hi, 1.0), 1)],
                            "n": len(in_b), "avg_p": round(avg_p, 3),
                            "hit_rate": round(hit, 3)})

        if n < 10:
            verdict = (f"only {n} resolved predictions — too few to judge "
                       "calibration (need >= 10)")
        else:
            gaps = [abs(b["avg_p"] - b["hit_rate"]) for b in buckets
                    if b["n"] and b["avg_p"] is not None]
            mean_gap = sum(gaps) / len(gaps) if gaps else 1.0
            avg_p_all = sum(float(p) for p, _ in rows) / n
            hit_all = sum(float(o) for _, o in rows) / n
            if mean_gap < 0.10:
                verdict = "well-calibrated: stated confidence matches outcomes"
            elif avg_p_all > hit_all + 0.10:
                verdict = ("overconfident: forecasts run hotter than reality — "
                           "shade probabilities down")
            elif avg_p_all < hit_all - 0.10:
                verdict = ("underconfident: forecasts run cooler than reality — "
                           "you can trust stronger statements")
            else:
                verdict = "roughly calibrated"

        return {"n_resolved": n, "n_pending": pending,
                "brier_score": brier,
                "brier_read": "0 = perfect, 0.25 ~= chance",
                "buckets": buckets, "verdict": verdict,
                "method": "real: empirical hit rates per confidence bucket"}
    except Exception as exc:  # never raise
        return {"error": f"brain.calibration failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.deliberate — the full loop in one call
# ---------------------------------------------------------------------------

def _p_raw_from_nets(nets: dict) -> tuple[float, str]:
    """Disclosed squash of keyword evidence mass into a pseudo-probability."""
    best = max(nets.values(), key=lambda v: v["net"])
    net = best["net"]
    p = 0.5 + 0.45 * (net / (1.0 + abs(net))) * (1 if net >= 0 else -1)
    p = round(min(0.95, max(0.05, p)), 3)
    mapping = ("p_raw = 0.5 + 0.45 * net/(1+|net|), signed by the leading "
               "reading's net keyword weight. A disclosed heuristic squash — "
               "NOT a true probability.")
    return p, mapping


def deliberate_handler(args: dict) -> dict:
    """brain.deliberate {question, context?, options?, criteria?} — full loop.

    think -> steelman -> premortem -> assumptions -> second_order ->
    (decide, if options+criteria given) -> calibration-adjusted verdict.
    One call, the whole adversarial deliberation protocol.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.deliberate: args must be an object"}
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "brain.deliberate: 'question' is required"}
        context = str(args.get("context") or "")

        think_args = {"question": question, "context": context}
        think_res = brain_pack.brain_think_handler(think_args)
        if think_res.get("error"):
            return {"error": think_res["error"]}

        trace = _trace_evidence(think_res["trace_id"])
        dossier: dict = {
            "question": question,
            "trace_id": think_res["trace_id"],
            "conclusion": think_res.get("conclusion"),
            "confidence": think_res.get("confidence"),
            "confidence_note": think_res.get("confidence_note"),
        }

        # Adversarial passes — each best-effort, never fatal.
        for name, handler, hargs in (
            ("steelman", steelman_handler, think_args),
            ("premortem", premortem_handler,
             {"decision": think_res.get("conclusion", question),
              "context": context}),
            ("assumptions", assumptions_handler, think_args),
            ("second_order", second_order_handler,
             {"decision": think_res.get("conclusion", question),
              "context": context}),
        ):
            try:
                dossier[name] = handler(hargs)
            except Exception as exc:  # never raise
                dossier[name] = {"error": f"{name} failed: {exc}"}

        options = args.get("options")
        criteria = args.get("criteria")
        if isinstance(options, list) and len(options) >= 2 and criteria:
            try:
                dossier["decision"] = brain_pack.brain_decide_handler(
                    {"options": options, "criteria": criteria})
            except Exception as exc:
                dossier["decision"] = {"error": f"decide failed: {exc}"}

        # Numeric confidence: heuristic squash + empirical calibration note.
        if trace:
            p_raw, mapping = _p_raw_from_nets(trace["nets"])
            dossier["p_raw"] = p_raw
            dossier["p_raw_mapping"] = mapping
            cal = calibration_handler({})
            n_res = cal.get("n_resolved", 0)
            if n_res >= 10:
                b = next((bk for bk in cal["buckets"]
                          if bk["n"] and bk["range"][0] <= p_raw < bk["range"][1]
                          + 0.01), None)
                if b and b["hit_rate"] is not None:
                    dossier["calibration_note"] = (
                        f"At this confidence level the brain's track record is "
                        f"{b['hit_rate']:.0%} hits over {b['n']} resolved "
                        f"forecasts (bucket {b['range']}, avg stated "
                        f"{b['avg_p']}). Stated p_raw {p_raw}; history says "
                        f"~{b['hit_rate']}.")
                else:
                    dossier["calibration_note"] = (
                        f"{n_res} resolved forecasts on record "
                        f"({cal.get('verdict')}), but none in this confidence "
                        "bucket yet.")
            else:
                dossier["calibration_note"] = (
                    f"Only {n_res} resolved forecasts on record — no "
                    "calibration adjustment yet. Use brain.predict + "
                    "brain.score_prediction to build the track record.")
        dossier["protocol"] = ("think -> steelman -> premortem -> assumptions "
                               "-> second_order -> [decide] -> calibration. "
                               "Adversarial by design: the case against is "
                               "built before the verdict is trusted.")
        return dossier
    except Exception as exc:  # never raise
        return {"error": f"brain.deliberate failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# brain.contradictions — the contradiction miner (Phase 23)
# ---------------------------------------------------------------------------

def _topic_clusters(texts: list[str], eng) -> tuple[list[list[int]], str]:
    """Greedy topic clustering. Returns (clusters, method).

    Cosine >= 0.6 with the embedding backend; Jaccard >= 0.25 on content
    tokens without it. Topic-level, deliberately looser than the
    near-duplicate clustering in semantics_pack.
    """
    if eng is not None:
        vecs = eng.embed(texts)
        thr, method = 0.6, "cosine>=0.6 (embedding backend)"
        def sim(i, j):
            from . import semantics_pack as _sem
            return max(0.0, _sem.cosine(vecs[i], vecs[j]))
    else:
        toks = [brain_pack._tokens(t) - brain_pack._STOPWORDS for t in texts]
        thr, method = 0.25, "jaccard>=0.25 (keyword fallback, no backend)"
        def sim(i, j):
            a, b = toks[i], toks[j]
            return (len(a & b) / len(a | b)) if (a | b) else 0.0
    clusters: list[list[int]] = []
    reps: list[int] = []
    for i in range(len(texts)):
        placed = False
        for ci, r in enumerate(reps):
            if sim(i, r) >= thr:
                clusters[ci].append(i)
                placed = True
                break
        if not placed:
            reps.append(i)
            clusters.append([i])
    return clusters, method


def contradictions_handler(args: dict) -> dict:
    """brain.contradictions {claims[]} — mine candidate contradictions.

    Clusters claims by topic, then pairs claims with opposing stance
    (for vs against, cue-word heuristic) inside each cluster. Returns
    the pairs with a severity score = the pair's topic similarity.

    These are CANDIDATES for deliberation, not verdicts: stance
    detection is crude and similarity is not understanding.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.contradictions: args must be an object"}
        raw = args.get("claims")
        if not isinstance(raw, list) or not raw:
            return {"error": "brain.contradictions: 'claims' must be a non-empty list"}
        texts = []
        for c in raw:
            t = c.get("text") if isinstance(c, dict) else c
            t = str(t or "").strip()
            if t:
                texts.append(t[:500])
        if len(texts) < 2:
            return {"contradictions": [], "n_claims": len(texts),
                    "note": "fewer than 2 non-empty claims — nothing to compare"}

        try:
            from . import semantics_pack as _sem
            eng, _err = _sem.get_engine()
        except Exception:
            eng = None
        clusters, method = _topic_clusters(texts, eng)

        stances = [brain_pack._stance(t) for t in texts]
        pairs = []
        for cl in clusters:
            if len(cl) < 2:
                continue
            for x in range(len(cl)):
                for y in range(x + 1, len(cl)):
                    i, j = cl[x], cl[y]
                    si, sj = stances[i], stances[j]
                    if {si, sj} != {"for", "against"}:
                        continue
                    if eng is not None:
                        from . import semantics_pack as _sem2
                        va, vb = eng.embed([texts[i], texts[j]])
                        sev = round(max(0.0, _sem2.cosine(va, vb)), 3)
                    else:
                        a = brain_pack._tokens(texts[i]) - brain_pack._STOPWORDS
                        b = brain_pack._tokens(texts[j]) - brain_pack._STOPWORDS
                        sev = round(len(a & b) / len(a | b), 3) if (a | b) else 0.0
                    pairs.append({
                        "claim_a": texts[i][:280], "stance_a": si,
                        "claim_b": texts[j][:280], "stance_b": sj,
                        "severity": sev,
                    })
        pairs.sort(key=lambda p: p["severity"], reverse=True)
        return {
            "contradictions": pairs,
            "n_claims": len(texts),
            "n_clusters": len(clusters),
            "method": (f"topic clusters via {method}; opposing stance "
                       "(for vs against) via cue-word heuristic; severity = "
                       "pair topic similarity"),
            "read": ("Candidate tensions for deliberation — not verdicts. "
                     "Stance cues are crude ('not bad' reads as against); "
                     "similarity is wording, not meaning."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.contradictions failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.audit — the trace receipt checker (Phase 26)
# ---------------------------------------------------------------------------

def _load_full_trace(trace_id: str) -> dict | None:
    conn = brain_pack._connect()
    try:
        row = conn.execute(
            "SELECT trace_id, created_at, question, decomposition_json,"
            " evidence_json, nets_json, conclusion, reasons_json,"
            " confidence, confidence_note FROM traces WHERE trace_id=?",
            (trace_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return {
            "trace_id": row[0], "created_at": row[1], "question": row[2],
            "sub_questions": json.loads(row[3]),
            "evidence": json.loads(row[4]),
            "nets": json.loads(row[5]),
            "conclusion": row[6],
            "reasons": json.loads(row[7]),
            "confidence": row[8],
            "confidence_note": row[9],
        }
    except (TypeError, ValueError):
        return None


def audit_handler(args: dict) -> dict:
    """brain.audit {trace_id} — audit a think trace's receipts.

    Re-derives the verdict from the stored evidence: recomputes
    for/against nets per sub-question and the leading reading, then
    verifies the stored nets and conclusion match. Also runs structural
    checks (weights in range, valid stances, evidence attached to real
    sub-questions, valid confidence grade).

    Read-only. Verdict "clean" or the failing checks with observed vs
    expected. Catches tampered traces — it cannot catch evidence that
    was fabricated at write time (garbage in, clean audit out).
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.audit: args must be an object"}
        trace_id = str(args.get("trace_id") or "").strip()
        if not trace_id:
            return {"error": "brain.audit: 'trace_id' is required"}
        trace = _load_full_trace(trace_id)
        if trace is None:
            return {"error": f"brain.audit: unknown trace_id '{trace_id}'"}

        checks: list[dict] = []

        def check(name: str, passed: bool, detail: str = ""):
            checks.append({"check": name, "passed": passed, "detail": detail})

        ev = trace["evidence"]
        sqs = trace["sub_questions"] or []
        nets = trace["nets"] or {}
        concl = str(trace["conclusion"] or "")
        # The honest no-evidence conclusion is a valid verdict on its own.
        no_reading = "No reading of the question is supported" in concl

        check("trace_parseable", True, "row loaded and JSON decoded")
        check("evidence_non_empty",
              (isinstance(ev, list) and len(ev) > 0) or no_reading,
              (f"{len(ev) if isinstance(ev, list) else 'non-list'} evidence "
               f"item(s){' — honest no-evidence conclusion' if no_reading else ''}"))

        recomputed: dict[str, dict] = {}
        items_ok = True
        if isinstance(ev, list):
            for i, item in enumerate(ev):
                if not isinstance(item, dict):
                    items_ok = False
                    continue
                sent = str(item.get("sentence") or "")
                w = item.get("weight")
                st = item.get("stance")
                sq = item.get("sub_question")
                ok = (bool(sent.strip())
                      and isinstance(w, (int, float)) and 0 <= w <= 1
                      and st in ("for", "against", "neutral")
                      and sq in sqs)
                if not ok:
                    items_ok = False
                if isinstance(sq, str) and isinstance(w, (int, float)):
                    bucket = recomputed.setdefault(
                        sq, {"for": 0.0, "against": 0.0})
                    if st == "for":
                        bucket["for"] += w
                    elif st == "against":
                        bucket["against"] += w
        check("evidence_items_well_formed", items_ok,
              "each item: non-empty sentence, weight in [0,1], stance "
              "for/against/neutral, sub_question from the decomposition")

        nets_ok = True
        net_detail = ""
        for sq in sqs:
            stored = nets.get(sq) or {}
            rec = recomputed.get(sq, {"for": 0.0, "against": 0.0})
            for k in ("for", "against"):
                sv = stored.get(k)
                if not isinstance(sv, (int, float)) or abs(sv - rec[k]) > 0.0011:
                    nets_ok = False
                    net_detail = (f"sub-question '{sq[:60]}': stored {k}={sv}, "
                                  f"recomputed {round(rec[k], 3)}")
                    break
            if not nets_ok:
                break
            stored_net = stored.get("net")
            rec_net = round(rec["for"] - rec["against"], 3)
            if not isinstance(stored_net, (int, float)) or abs(stored_net - rec_net) > 0.0011:
                nets_ok = False
                net_detail = (f"sub-question '{sq[:60]}': stored net={stored_net}, "
                              f"recomputed {rec_net}")
                break
        check("nets_match_recomputed", nets_ok,
              net_detail or "stored for/against/net match recomputed evidence sums")

        # Leading reading: recompute and verify the conclusion quotes it.
        leading = None
        if nets:
            leading = max(nets, key=lambda k: (nets[k] or {}).get("net", 0))
        quotes_leading = bool(leading) and leading[:120] in concl
        check("conclusion_matches_leading",
              quotes_leading or no_reading,
              (f"conclusion quotes leading sub-question '{(leading or '')[:60]}'"
               if quotes_leading else
               ("honest no-evidence conclusion" if no_reading else
                "conclusion does not quote the recomputed leading reading")))

        check("confidence_valid",
              str(trace["confidence"]).startswith(
                  ("low", "moderate", "reasonable")),
              f"confidence='{trace['confidence']}'")

        verdict = "clean" if all(c["passed"] for c in checks) else "issues_found"
        return {
            "trace_id": trace_id,
            "verdict": verdict,
            "checks": checks,
            "question": str(trace["question"])[:160],
            "read": ("Read-only audit: internal consistency between stored "
                     "evidence, nets, and conclusion. Clean means the trace "
                     "wasn't tampered with after writing — it cannot catch "
                     "evidence fabricated at write time (garbage in, clean "
                     "audit out)."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.audit failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.correct — correction learning (Phase 28)
# ---------------------------------------------------------------------------

def correct_handler(args: dict) -> dict:
    """brain.correct {trace_id, what_was_wrong, right_answer} — teach the
    brain it was wrong.

    Verifies the trace exists, then files the correction as a lesson
    (source "correction") with the user's words intact: the right answer
    plus what the trace concluded instead. Dedupes with brain.reflect's
    rule (normalized fuzzy match >= 0.85) — the same correction twice
    stores once. The original trace is never edited; survey's lesson
    ranking surfaces the correction for similar goals.

    Trust note: a correction is believed completely. A wrong correction
    becomes a wrong lesson.
    """
    try:
        from difflib import SequenceMatcher
        if not isinstance(args, dict):
            return {"error": "brain.correct: args must be an object"}
        trace_id = str(args.get("trace_id") or "").strip()
        what_was_wrong = str(args.get("what_was_wrong") or "").strip()
        right_answer = str(args.get("right_answer") or "").strip()
        if not trace_id:
            return {"error": "brain.correct: 'trace_id' is required"}
        if not what_was_wrong:
            return {"error": "brain.correct: 'what_was_wrong' is required — "
                             "say what the trace got wrong"}
        if not right_answer:
            return {"error": "brain.correct: 'right_answer' is required — "
                             "a correction without the right answer is just "
                             "a complaint"}
        trace = _load_full_trace(trace_id)
        if trace is None:
            return {"error": f"brain.correct: unknown trace_id '{trace_id}' — "
                             "corrections attach to real deliberations"}

        lesson = (f"[Correction] {right_answer} "
                  f"(trace {trace_id} concluded otherwise: {what_was_wrong})")
        norm = brain_pack._normalize_lesson(lesson)
        if not norm:
            return {"error": "brain.correct: correction text normalized to "
                             "nothing — be more specific"}

        with brain_pack._LOCK:
            conn = brain_pack._connect()
            try:
                existing = [r[0] for r in
                            conn.execute("SELECT normalized FROM lessons").fetchall()]
                dupe = any(norm == e or SequenceMatcher(None, norm, e).ratio() >= 0.85
                           for e in existing)
                if dupe:
                    return {"trace_id": trace_id, "stored": False,
                            "lesson": lesson,
                            "note": "duplicate of an existing lesson — stored once"}
                conn.execute(
                    "INSERT INTO lessons(created_at, text, normalized, source, forwarded)"
                    " VALUES (?,?,?,?,?)",
                    (brain_pack._now(), lesson, norm, "correction", 0))
                conn.commit()
            finally:
                conn.close()
        return {
            "trace_id": trace_id,
            "stored": True,
            "lesson": lesson,
            "question": str(trace["question"])[:160],
            "note": ("Filed as a 'correction' lesson — brain.survey surfaces "
                     "it for similar goals. The original trace was not "
                     "edited. Trust note: the correction is believed "
                     "completely; a wrong correction becomes a wrong lesson."),
        }
    except Exception as exc:  # never raise
        return {"error": f"brain.correct failed: {exc}"}


TOOL_DEFS = [
    {"name": "brain.steelman",
     "description": ("Devil's advocate: runs think, then builds the strongest "
                     "counter-case to the leading reading from counter-stance "
                     "evidence and rival sub-questions, ranked by weight. "
                     "Heuristic weighing; the adversarial structure is the "
                     "point."),
     "handler": steelman_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "context?": "string"}},
    {"name": "brain.premortem",
     "description": ("Prospective hindsight: assume the decision failed "
                     "spectacularly and list the most plausible reasons why — "
                     "each supporting claim inverted into a failure "
                     "hypothesis, ranked by how much of the case rested on "
                     "it."),
     "handler": premortem_handler, "risk": "low", "needs_network": False,
     "schema": {"decision": "string", "context?": "string"}},
    {"name": "brain.assumptions",
     "description": ("Assumption audit: lists the load-bearing assumptions "
                     "behind the leading reading, each with a fragility score "
                     "(its share of the supporting weight), ranked. Flags "
                     "single-source and keystone (>=50%) risks."),
     "handler": assumptions_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "context?": "string"}},
    {"name": "brain.second_order",
     "description": ("Consequence chains: extracts causal claims from the "
                     "context ('X leads to Y') and chains them two levels "
                     "deep where an effect shares terms with another cause. "
                     "Heuristic cue extraction — a prompt to think downstream."),
     "handler": second_order_handler, "risk": "low", "needs_network": False,
     "schema": {"decision": "string", "context?": "string"}},
    {"name": "brain.predict",
     "description": ("Record a probabilistic forecast {question, p in [0,1]}. "
                     "Returns a prediction_id. Resolve it later with "
                     "brain.score_prediction; the Brier score tracks real "
                     "calibration over time."),
     "handler": predict_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "p": "number", "context?": "string"}},
    {"name": "brain.score_prediction",
     "description": ("Resolve a forecast: {prediction_id, outcome 1/0}. "
                     "Returns the Brier contribution and the running Brier "
                     "score over all resolved predictions."),
     "handler": score_prediction_handler, "risk": "low",
     "needs_network": False,
     "schema": {"prediction_id": "string", "outcome": "string"}},
    {"name": "brain.calibration",
     "description": ("The brain's actual forecasting track record: resolved "
                     "count, Brier score, empirical hit rate per confidence "
                     "bucket, and a verdict (well-calibrated / overconfident / "
                     "underconfident). Real statistics, not heuristics."),
     "handler": calibration_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "brain.deliberate",
     "description": ("The full adversarial deliberation loop in one call: "
                     "think -> steelman -> premortem -> assumptions -> "
                     "second_order -> [decide, if options+criteria given] -> "
                     "calibration-adjusted verdict with p_raw and the brain's "
                     "empirical track record at that confidence level."),
     "handler": deliberate_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "context?": "string",
                "options?": "array", "criteria?": "array"}},
    {"name": "brain.contradictions",
     "description": ("Contradiction miner: cluster claims by topic (cosine "
                     ">= 0.6 with the embedding backend when available, else "
                     "keyword Jaccard), then pair opposite-stance claims "
                     "(for vs against, cue-word heuristic) inside each "
                     "cluster. Returns candidate tensions with severity "
                     "scores for deliberation — not verdicts."),
     "handler": contradictions_handler, "risk": "low", "needs_network": False,
     "schema": {"claims": "array"}},
    {"name": "brain.audit",
     "description": ("Trace receipt checker: re-derive a think trace's "
                     "verdict from its stored evidence — recompute "
                     "for/against nets per sub-question, verify the stored "
                     "nets match, and verify the conclusion quotes the "
                     "recomputed leading reading. Read-only; verdict 'clean' "
                     "or the failing checks. Catches tampered traces; "
                     "cannot catch evidence fabricated at write time."),
     "handler": audit_handler, "risk": "low", "needs_network": False,
     "schema": {"trace_id": "string"}},
    {"name": "brain.correct",
     "description": ("Teach the brain it was wrong: attach a correction to "
                     "a real think trace {trace_id, what_was_wrong, "
                     "right_answer}. Files the user's words as a lesson "
                     "(source 'correction'), deduped like brain.reflect. "
                     "The trace is never edited; survey surfaces the "
                     "correction for similar goals. Trust note: a wrong "
                     "correction becomes a wrong lesson."),
     "handler": correct_handler, "risk": "low", "needs_network": False,
     "schema": {"trace_id": "string", "what_was_wrong": "string",
                "right_answer": "string"}},
]

RISK_TABLE_ADDITIONS = {spec["name"]: (spec["risk"], spec["needs_network"])
                        for spec in TOOL_DEFS}


def register(reg) -> None:
    """Wire the eight Phase 19 metacognition tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
