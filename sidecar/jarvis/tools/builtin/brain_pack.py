"""Phase 14 (HYPER) — Brain pack: structured deliberation, plans, and reflection.

This is HONEST DELIBERATION MACHINERY, not consciousness. Every tool below
is a rule-based, keyword-driven procedure with explicitly labeled heuristic
scores. The pack never claims to understand anything; it records what it
did and how it scored things so the agent (and the user) can audit it.

WHAT IT DOES
    ``brain.think``    — decompose a question into 2-6 sub-questions, weigh
                         evidence for/against each from a provided context
                         string, and record the full reasoning trace
                         (steps, evidence, weights) in sqlite. Returns a
                         conclusion + honest confidence.
    ``brain.plan``     — create a multi-step plan (steps call registry tools
                         by name, with depends_on edges); persisted in sqlite.
    ``brain.replan``   — append a revision; full version history is kept and
                         never rewritten.
    ``brain.plan_status`` — status of one plan or all active plans.
    ``brain.run_step`` — execute the next runnable step through the LIVE
                         agent's real registry (policy still gates each step).
    ``brain.decide``   — score options against weighted criteria with a
                         stated, heuristic scorecard.
    ``brain.focus``    — working memory: active plans, what's blocked,
                         what's next.
    ``brain.reflect``  — post-action review: extract durable lesson phrasings,
                         dedupe, store; forward to memory.learn when available.

HONESTY NOTES (read before trusting output)
    - Decomposition is syntactic (clause splitting), not comprehension.
    - Evidence weights are keyword-overlap heuristics over the *provided*
      context only. If the context says nothing relevant, confidence says so.
    - decide scores are rule-based heuristics from option text + numeric
      hints, clearly labeled as such.
    - reflect extracts sentences with lesson-like markers; it does not truly
      learn. Dedupe is normalized-text fuzzy matching.

SAFETY:
    - Handlers take dict -> return dict and never raise; failures are
      returned as {"error": "..."}.
    - brain.run_step is medium risk: it causes real action, but each step
      executes through the live agent's registry, so the normal policy
      engine gates it (a high-risk step returns needs_confirmation instead
      of running). brain.run_step never bypasses policy.
    - State lives in sqlite (stdlib only): ~/workspace/jarvis/data/brain_cog.db,
      overridable with the JARVIS_BRAIN_COG_DB env var (tests use a tmp file).
    - All tools are local-only: needs_network=False everywhere.

INTEGRATION NOTE FOR THE PARENT (apply — do not let the subagent edit):
    1. In sidecar/jarvis/tools/builtin/__init__.py:
         a. add ``brain_pack`` to the big ``from . import ...`` line
            (append after ``persona_pack``), and
         b. after the Phase 13 block, add::

                # --- Phase 14: cognitive brain pack ---
                for _pack in (brain_pack,):
                    _pack.register(reg)
                    _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    2. In sidecar/jarvis/ipc/server.py, right after::

             from ..tools.builtin import teach_pack
             teach_pack.bind_agent(agent)

       add::

             # --- Phase 14: brain pack binds to the live agent (run_step/reflect) ---
             from ..tools.builtin import brain_pack
             brain_pack.bind_agent(agent)

    Until (2) is wired, brain.run_step honestly reports that plan execution
    needs the live agent, and brain.reflect stores lessons locally marked as
    not yet forwarded.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "brain_cog.db"

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
    override = os.environ.get("JARVIS_BRAIN_COG_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS traces("
        " trace_id TEXT PRIMARY KEY,"
        " created_at TEXT NOT NULL,"
        " question TEXT NOT NULL,"
        " context_excerpt TEXT NOT NULL,"
        " decomposition_json TEXT NOT NULL,"
        " evidence_json TEXT NOT NULL,"
        " nets_json TEXT NOT NULL,"
        " conclusion TEXT NOT NULL,"
        " reasons_json TEXT NOT NULL,"
        " confidence TEXT NOT NULL,"
        " confidence_note TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS plans("
        " plan_id TEXT PRIMARY KEY,"
        " created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL,"
        " goal TEXT NOT NULL,"
        " steps_json TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'active',"
        " version INTEGER NOT NULL DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS plan_revisions("
        " plan_id TEXT NOT NULL,"
        " version INTEGER NOT NULL,"
        " created_at TEXT NOT NULL,"
        " update_text TEXT NOT NULL,"
        " steps_json TEXT NOT NULL,"
        " PRIMARY KEY (plan_id, version))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lessons("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " created_at TEXT NOT NULL,"
        " text TEXT NOT NULL,"
        " normalized TEXT NOT NULL UNIQUE,"
        " source TEXT NOT NULL,"
        " forwarded INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Small text utilities (all heuristic, all labeled as such)
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset(
    "a an the and or but if then else when where what which who whom whose "
    "that this these those is are was were be been being has have had hasnt "
    "do does did doing will would should could can may might must shall its "
    "of in on at to for with by from as into about over under between through "
    "it they we you he she him her them us our your their my me i not no yes "
    "so than too very just such each other all any both few more most other "
    "some only own same because until while again once here there how when why "
    "now also per vs via get got getting make made many much etc".split()
)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    return [p.strip() for p in parts if len(p.strip()) > 4]


_FOR_CUES = frozenset("supports confirms yes proven works because benefits advantage good best correct".split())
_AGAINST_CUES = frozenset("however but although though unfortunately fails risk problem issue drawback wrong false not no lack missing".split())


def _stance(sentence: str) -> str:
    toks = _tokens(sentence)
    for_ = len(toks & _FOR_CUES)
    against = len(toks & _AGAINST_CUES)
    if against > for_:
        return "against"
    if for_ > against:
        return "for"
    return "neutral"


def _decompose(question: str) -> list[str]:
    """Syntactic clause splitting, capped at 2-6 sub-questions.

    This is NOT comprehension: it just breaks the question into clauses.
    """
    q = (question or "").strip()
    clauses = re.split(r"\s+(?:and|vs\.?|versus|or)\s+|;\s*|\s*\?\s*", q)
    clauses = [c.strip(" ?,.") for c in clauses if len(c.strip(" ?,.").split()) >= 2]
    # dedupe while preserving order
    seen: set[str] = set()
    clauses = [c for c in clauses if not (c.lower() in seen or seen.add(c.lower()))]
    if len(clauses) < 2:
        # Fall back to templated aspects of the single question.
        clauses = [
            f"What exactly is being asked by: '{q[:120]}'?",
            "What evidence is available for an answer?",
            "What argues for versus against each reading?",
            "What remains unknown or ambiguous?",
        ]
    return clauses[:6]


def _normalize_lesson(text: str) -> str:
    t = str(text).lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------------------
# brain.think
# ---------------------------------------------------------------------------
def brain_think_handler(args: dict) -> dict:
    """brain.think {question, context?} — structured, recorded deliberation.

    Decomposes the question (syntactically) into 2-6 sub-questions, scores
    each context sentence for relevance to each sub-question by keyword
    overlap, tags stance for/against by cue words, and stores the full trace
    in sqlite. Confidence is honest: thin evidence => low confidence.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.think: args must be an object"}
        question = (args.get("question") or "").strip()
        if not question:
            return {"error": "brain.think: 'question' is required"}
        context = str(args.get("context") or "")

        sub_questions = _decompose(question)
        sentences = _sentences(context)

        evidence: list[dict] = []
        nets: dict[str, dict] = {sq: {"for": 0.0, "against": 0.0} for sq in sub_questions}
        for sq in sub_questions:
            sq_toks = _tokens(sq)
            if not sq_toks:
                continue
            for sent in sentences:
                sent_toks = _tokens(sent)
                if not sent_toks:
                    continue
                overlap = sq_toks & sent_toks
                if not overlap:
                    continue
                weight = round(len(overlap) / len(sq_toks | sent_toks), 3)
                if weight <= 0:
                    continue
                stance = _stance(sent)
                evidence.append({
                    "sub_question": sq,
                    "sentence": sent[:300],
                    "matched_terms": sorted(overlap),
                    "weight": weight,
                    "stance": stance,
                })
                if stance == "for":
                    nets[sq]["for"] += weight
                elif stance == "against":
                    nets[sq]["against"] += weight

        ranked = sorted(
            ((sq, round(v["for"] - v["against"], 3), round(v["for"], 3), round(v["against"], 3))
             for sq, v in nets.items()),
            key=lambda t: t[1], reverse=True)

        total_mass = round(sum(abs(r[1]) for r in ranked), 3)
        for_mass = round(sum(r[2] for r in ranked), 3)

        reasons: list[str] = []
        for sq, net, f, a in ranked:
            if f or a:
                reasons.append(
                    f"On '{sq[:90]}': supporting evidence weight {f}, "
                    f"counter evidence weight {a} (net {net}).")
        if not reasons:
            reasons.append("No context sentence shared keywords with any sub-question.")

        if for_mass <= 0:
            conclusion = ("No reading of the question is supported by the provided "
                          "context — there is no evidence to weigh.")
        else:
            best = ranked[0]
            conclusion = (f"The best-supported reading (net evidence {best[1]} on "
                          f"'{best[0][:120]}') is the direction the context leans. "
                          "This is a keyword-overlap heuristic over the provided "
                          "context, not understanding.")

        if total_mass < 0.10:
            confidence = "low"
            note = ("Evidence is thin: the provided context contained almost no "
                    "text relevant to the question. This conclusion is "
                    "essentially a guess — supply more context or verify "
                    "externally.")
        elif total_mass < 0.50:
            confidence = "moderate"
            note = ("Limited relevant evidence. The weights are keyword "
                    "heuristics; treat the conclusion as a starting hypothesis.")
        else:
            confidence = "reasonable (for a rule-based deliberation)"
            note = ("Decent keyword evidence mass, but the scoring is mechanical "
                    "overlap, not comprehension. Cross-check anything load-bearing.")

        trace_id = _new_id("trace")
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO traces(trace_id, created_at, question, "
                    "context_excerpt, decomposition_json, evidence_json, nets_json, "
                    "conclusion, reasons_json, confidence, confidence_note) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (trace_id, _now(), question, context[:500],
                     json.dumps(sub_questions), json.dumps(evidence),
                     json.dumps({sq: {"net": r[1], "for": r[2], "against": r[3]}
                                 for sq, r in zip(nets.keys(), ranked)}),
                     conclusion, json.dumps(reasons), confidence, note))
                conn.commit()
            finally:
                conn.close()

        first_impressions: list[dict] = []
        s1_note: str | None = None
        if args.get("instant"):
            # System 1 fast lane: one batched JEV forward pass giving a gut
            # verdict per sub-question, reported alongside the deliberation.
            from jarvis.tools.builtin import system1_pack
            probe_text = context[:4000] if context.strip() else question
            batch = system1_pack.batch_handler({
                "text": probe_text,
                "questions": {
                    f"gut_{i}": {
                        "type": "noul",
                        "instructions": f"Is the answer to this yes? {sq}",
                    }
                    for i, sq in enumerate(sub_questions)
                },
            })
            if batch.get("ok"):
                answers = (batch.get("result") or {}).get("answers", {})
                for i, sq in enumerate(sub_questions):
                    ans = answers.get(f"gut_{i}") or {}
                    p_yes = ans.get("noul")
                    first_impressions.append({
                        "sub_question": sq[:160],
                        "verdict": ("yes" if p_yes >= 0.5 else "no"
                                    if isinstance(p_yes, (int, float)) else None),
                        "p_yes": (round(float(p_yes), 4)
                                  if isinstance(p_yes, (int, float)) else None),
                    })
                s1_note = ("System 1 gut verdicts (laya, single batched forward "
                           "pass) — instant reads, not deliberation.")
            else:
                s1_note = ("System 1 unavailable: "
                           + str(batch.get("error", "unknown")))

        return {"conclusion": conclusion,
                "reasons": reasons,
                "confidence": confidence,
                "confidence_note": note,
                "trace_id": trace_id,
                "sub_questions": sub_questions,
                "evidence_items": len(evidence),
                "first_impressions": first_impressions,
                "system1_note": s1_note,
                "scoring": "heuristic: keyword-overlap weights + cue-word stance"}
    except Exception as exc:  # never raise
        return {"error": f"brain.think failed: {exc}"}


# ---------------------------------------------------------------------------
# Plans: create / replan / status / run_step
# ---------------------------------------------------------------------------
def _validate_steps(steps) -> tuple[list[dict] | None, str | None]:
    if not isinstance(steps, list) or not steps:
        return None, "steps must be a non-empty list"
    seen: set[str] = set()
    clean: list[dict] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            return None, f"step {i + 1} must be an object"
        sid = s.get("id")
        tool = s.get("tool")
        if not isinstance(sid, str) or not sid.strip():
            return None, f"step {i + 1}: 'id' is required"
        if sid in seen:
            return None, f"duplicate step id '{sid}'"
        seen.add(sid)
        if not isinstance(tool, str) or not tool.strip():
            return None, f"step '{sid}': 'tool' is required"
        deps = s.get("depends_on") or []
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            return None, f"step '{sid}': 'depends_on' must be a list of step ids"
        args = s.get("args") or {}
        if not isinstance(args, dict):
            return None, f"step '{sid}': 'args' must be an object"
        clean.append({"id": sid, "tool": tool, "args": args,
                      "depends_on": deps, "status": "pending"})
    for s in clean:
        for d in s["depends_on"]:
            if d not in seen:
                return None, f"step '{s['id']}': depends_on unknown step '{d}'"
    return clean, None


def _load_plan(conn: sqlite3.Connection, plan_id: str) -> dict | None:
    row = conn.execute(
        "SELECT plan_id, created_at, updated_at, goal, steps_json, status, version "
        "FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
    if row is None:
        return None
    try:
        steps = json.loads(row[4])
    except (TypeError, ValueError):
        steps = []
    return {"plan_id": row[0], "created_at": row[1], "updated_at": row[2],
            "goal": row[3], "steps": steps, "status": row[5], "version": row[6]}


def _save_plan(conn: sqlite3.Connection, plan: dict) -> None:
    conn.execute(
        "UPDATE plans SET steps_json=?, status=?, version=?, updated_at=? "
        "WHERE plan_id=?",
        (json.dumps(plan["steps"]), plan["status"], plan["version"],
         _now(), plan["plan_id"]))


def _effective_statuses(steps: list[dict]) -> dict[str, str]:
    """Compute displayed status: pending step with a failed dep => blocked."""
    by_id = {s["id"]: s for s in steps}
    out: dict[str, str] = {}
    for s in steps:
        st = s.get("status", "pending")
        if st == "pending":
            if any(by_id.get(d, {}).get("status") == "failed" for d in s.get("depends_on", [])):
                st = "blocked"
        out[s["id"]] = st
    return out


def _next_step(steps: list[dict]) -> dict | None:
    eff = _effective_statuses(steps)
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        if eff[s["id"]] != "pending":
            continue
        if all(by_id.get(d, {}).get("status") == "done" for d in s.get("depends_on", [])):
            return s
    return None


def _plan_summary(plan: dict) -> dict:
    steps = plan["steps"]
    eff = _effective_statuses(steps)
    counts = {k: 0 for k in ("pending", "blocked", "done", "failed")}
    for v in eff.values():
        counts[v] = counts.get(v, 0) + 1
    nxt = _next_step(steps)
    return {"plan_id": plan["plan_id"], "goal": plan["goal"],
            "status": plan["status"], "version": plan["version"],
            "steps": [{"id": s["id"], "tool": s["tool"], "status": eff[s["id"]],
                       "depends_on": s.get("depends_on", [])} for s in steps],
            "counts": counts,
            "next_step": ({"id": nxt["id"], "tool": nxt["tool"]} if nxt else None),
            "updated_at": plan["updated_at"]}


def brain_plan_handler(args: dict) -> dict:
    """brain.plan {goal, steps[]} — create a persisted multi-step plan."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.plan: args must be an object"}
        goal = (args.get("goal") or "").strip()
        if not goal:
            return {"error": "brain.plan: 'goal' is required"}
        steps, err = _validate_steps(args.get("steps"))
        if err:
            return {"error": f"brain.plan: {err}"}
        plan_id = _new_id("plan")
        assert steps is not None
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO plans(plan_id, created_at, updated_at, goal, "
                    "steps_json, status, version) VALUES (?,?,?,?,?,?,1)",
                    (plan_id, _now(), _now(), goal, json.dumps(steps), "active"))
                conn.execute(
                    "INSERT INTO plan_revisions(plan_id, version, created_at, "
                    "update_text, steps_json) VALUES (?,?,?,?,?)",
                    (plan_id, 1, _now(), "plan created", json.dumps(steps)))
                conn.commit()
            finally:
                conn.close()
        return {"plan_id": plan_id, "goal": goal, "version": 1,
                "steps": len(steps), "status": "active"}
    except Exception as exc:
        return {"error": f"brain.plan failed: {exc}"}


def brain_replan_handler(args: dict) -> dict:
    """brain.replan {plan_id, update} — append a revision; history is kept."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.replan: args must be an object"}
        plan_id = args.get("plan_id")
        update = args.get("update")
        if not isinstance(plan_id, str) or not plan_id:
            return {"error": "brain.replan: 'plan_id' is required"}
        if not isinstance(update, dict):
            return {"error": "brain.replan: 'update' must be an object"}
        update_text = str(update.get("note") or update.get("text") or "revision").strip()
        with _LOCK:
            conn = _connect()
            try:
                plan = _load_plan(conn, plan_id)
                if plan is None:
                    return {"error": f"no plan '{plan_id}' (see brain.plan_status)"}
                new_steps = plan["steps"]
                if "steps" in update:
                    validated, err = _validate_steps(update["steps"])
                    if err:
                        return {"error": f"brain.replan: {err}"}
                    assert validated is not None
                    # Carry over done/failed statuses for steps with the same id,
                    # so a revision never silently resurrects finished work.
                    old_by_id = {s["id"]: s for s in plan["steps"]}
                    for s in validated:
                        old = old_by_id.get(s["id"])
                        if old and old.get("status") in ("done", "failed"):
                            s["status"] = old["status"]
                    new_steps = validated
                new_version = plan["version"] + 1
                plan["steps"] = new_steps
                plan["version"] = new_version
                if plan["status"] == "done" and any(
                        s.get("status") == "pending" for s in new_steps):
                    plan["status"] = "active"  # reopened by revision
                _save_plan(conn, plan)
                conn.execute(
                    "INSERT INTO plan_revisions(plan_id, version, created_at, "
                    "update_text, steps_json) VALUES (?,?,?,?,?)",
                    (plan_id, new_version, _now(), update_text[:500],
                     json.dumps(new_steps)))
                conn.commit()
            finally:
                conn.close()
        return {"plan_id": plan_id, "version": new_version,
                "note": update_text[:200],
                "history_note": "previous versions preserved in plan_revisions"}
    except Exception as exc:
        return {"error": f"brain.replan failed: {exc}"}


def brain_plan_status_handler(args: dict) -> dict:
    """brain.plan_status {plan_id?} — one plan's detail or all active plans."""
    try:
        args = args if isinstance(args, dict) else {}
        plan_id = args.get("plan_id")
        with _LOCK:
            conn = _connect()
            try:
                if plan_id:
                    plan = _load_plan(conn, plan_id)
                    if plan is None:
                        return {"error": f"no plan '{plan_id}'"}
                    return {"plan": _plan_summary(plan)}
                rows = conn.execute(
                    "SELECT plan_id, created_at, updated_at, goal, steps_json, status, version "
                    "FROM plans WHERE status='active' ORDER BY updated_at DESC"
                ).fetchall()
                plans = []
                for row in rows:
                    try:
                        steps = json.loads(row[4])
                    except (TypeError, ValueError):
                        steps = []
                    plans.append(_plan_summary({
                        "plan_id": row[0], "created_at": row[1], "updated_at": row[2],
                        "goal": row[3], "steps": steps, "status": row[5], "version": row[6]}))
            finally:
                conn.close()
        return {"active_plans": plans, "count": len(plans)}
    except Exception as exc:
        return {"error": f"brain.plan_status failed: {exc}"}


def brain_run_step_handler(args: dict) -> dict:
    """brain.run_step {plan_id, confirmed?} — run the next runnable step.

    The step executes via the LIVE agent's real registry
    (``agent.registry.call(step.tool, step.args, actor="brain", confirmed=...)``),
    so the normal policy engine gates every step: a high-risk step returns
    ``needs_confirmation`` instead of running. This adds NO new risk surface.
    Without a bound agent this honestly refuses.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.run_step: args must be an object"}
        plan_id = args.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            return {"error": "brain.run_step: 'plan_id' is required"}
        confirmed = bool(args.get("confirmed", False))

        if _AGENT is None:
            return {"error": ("brain.run_step needs the live agent; not bound "
                              "(server.py must call brain_pack.bind_agent(agent)). "
                              "Plans can still be created and inspected offline.")}
        registry = getattr(_AGENT, "registry", None)
        if registry is None or not hasattr(registry, "call"):
            return {"error": ("brain.run_step needs the live agent; not bound "
                              "(the bound agent exposes no .registry.call)")}

        with _LOCK:
            conn = _connect()
            try:
                plan = _load_plan(conn, plan_id)
                if plan is None:
                    return {"error": f"no plan '{plan_id}'"}
                if plan["status"] != "active":
                    return {"error": f"plan '{plan_id}' is {plan['status']} "
                                     "(replan to reopen it)"}
                step = _next_step(plan["steps"])
                if step is None:
                    eff = _effective_statuses(plan["steps"])
                    if any(v == "failed" for v in eff.values()):
                        return {"error": ("no runnable step: a dependency failed — "
                                          "fix with brain.replan"),
                                "plan": _plan_summary(plan)}
                    plan["status"] = "done"
                    _save_plan(conn, plan)
                    conn.commit()
                    return {"plan_id": plan_id, "status": "done",
                            "note": "all steps complete"}
                # Execute through the live registry — policy gates apply.
                tool_name = step["tool"]
                try:
                    res = registry.call(tool_name, step.get("args") or {},
                                        actor="brain", confirmed=confirmed)
                except Exception as exc:
                    res = {"ok": False, "error": f"registry.call raised: {exc}"}
                if not isinstance(res, dict):
                    res = {"ok": False, "error": f"registry returned {type(res).__name__}"}
                if res.get("needs_confirmation"):
                    conn.commit()  # nothing changed; keep the step pending
                    return {"plan_id": plan_id, "step": step["id"], "tool": tool_name,
                            "needs_confirmation": True,
                            "confirm_text": res.get("confirm_text"),
                            "note": ("Policy held this step for confirmation — it was "
                                     "NOT executed. Re-run with confirmed=true after "
                                     "human approval.")}
                if res.get("ok"):
                    step["status"] = "done"
                else:
                    step["status"] = "failed"
                    step["last_error"] = str(res.get("error"))[:500]
                # Plan-level roll-up.
                eff = _effective_statuses(plan["steps"])
                if all(v == "done" for v in eff.values()):
                    plan["status"] = "done"
                _save_plan(conn, plan)
                conn.commit()
            finally:
                conn.close()
        return {"plan_id": plan_id, "step": step["id"], "tool": tool_name,
                "step_status": step["status"], "result": res,
                "plan": "complete" if plan["status"] == "done" else "active"}
    except Exception as exc:
        return {"error": f"brain.run_step failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.decide — heuristic multi-criteria scorecard
# ---------------------------------------------------------------------------
_CRIT_SYNONYMS = {
    "cost": {"cost", "price", "cheap", "expensive", "budget", "afford", "fee", "pay"},
    "price": {"cost", "price", "cheap", "expensive", "budget", "afford", "fee", "pay"},
    "speed": {"speed", "fast", "quick", "rapid", "latency", "slow", "instant"},
    "quality": {"quality", "reliable", "robust", "solid", "premium", "excellent"},
    "ease": {"ease", "easy", "simple", "effort", "convenient", "friction"},
    "risk": {"risk", "risky", "safe", "danger", "uncertain", "volatility"},
    "scale": {"scale", "scalable", "growth", "large", "capacity"},
}


def _criterion_tokens(name: str) -> set[str]:
    toks = _tokens(name)
    for key, syns in _CRIT_SYNONYMS.items():
        if key in toks:
            toks |= syns
    return toks


def brain_decide_handler(args: dict) -> dict:
    """brain.decide {options[], criteria[]} — heuristic scored comparison.

    Criteria are {name, weight}. Each option is scored 0-10 per criterion by
    keyword overlap between the criterion and the option text, adjusted by
    numeric hints in the text. Scores are LABELED heuristic throughout.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.decide: args must be an object"}
        options = args.get("options")
        criteria = args.get("criteria")
        if not isinstance(options, list) or len(options) < 2:
            return {"error": "brain.decide: 'options' must be a list of >= 2"}
        if not isinstance(criteria, list) or not criteria:
            return {"error": "brain.decide: 'criteria' must be a non-empty list"}
        norm_criteria = []
        for c in criteria:
            if not isinstance(c, dict) or not str(c.get("name") or "").strip():
                return {"error": "each criterion must be {name, weight}"}
            try:
                w = float(c.get("weight", 1))
            except (TypeError, ValueError):
                return {"error": f"criterion '{c.get('name')}': weight must be numeric"}
            if w <= 0:
                return {"error": f"criterion '{c.get('name')}': weight must be > 0"}
            norm_criteria.append({"name": str(c["name"]).strip(), "weight": w})

        norm_options = []
        for i, o in enumerate(options):
            if isinstance(o, str):
                norm_options.append({"name": f"option_{i + 1}", "text": o})
            elif isinstance(o, dict):
                text = str(o.get("text") or o.get("description") or "")
                norm_options.append({"name": str(o.get("name") or f"option_{i + 1}"),
                                     "text": text})
            else:
                return {"error": f"option {i + 1} must be a string or {{name, text}}"}

        scorecard: list[dict] = []
        weight_sum = sum(c["weight"] for c in norm_criteria)
        for opt in norm_options:
            opt_toks = _tokens(opt["text"])
            numbers = re.findall(r"\d+(?:\.\d+)?%?", opt["text"])
            per_crit = []
            total = 0.0
            for crit in norm_criteria:
                c_toks = _criterion_tokens(crit["name"])
                overlap = (c_toks & opt_toks) - _STOPWORDS
                base = (len(overlap) / len(c_toks) * 10) if c_toks else 0.0
                # Numeric hints: citing a number is evidence the option speaks
                # to the criterion; small bounded boost, fully disclosed.
                boost = min(2.0, 0.5 * len(numbers)) if overlap and numbers else 0.0
                score = round(min(10.0, base + boost), 2)
                total += score * crit["weight"]
                per_crit.append({
                    "criterion": crit["name"], "weight": crit["weight"],
                    "score": score,
                    "reason": (f"heuristic: {len(overlap)}/{len(c_toks)} criterion "
                               f"terms matched in option text "
                               f"{sorted(overlap) if overlap else '[]'}"
                               + (f"; numeric hints cited: {numbers[:5]}"
                                  if numbers else "; no numeric hints")),
                })
            scorecard.append({"option": opt["name"],
                              "weighted_score": round(total / weight_sum, 2),
                              "per_criterion": per_crit})

        winner = max(scorecard, key=lambda e: e["weighted_score"])
        margin = round(winner["weighted_score"]
                       - max((e["weighted_score"] for e in scorecard
                              if e is not winner), default=0.0), 2)
        why = (f"'{winner['option']}' wins with weighted score "
               f"{winner['weighted_score']} (margin {margin}). "
               + " ".join(f"{p['criterion']}: {p['score']}/10."
                          for p in winner["per_criterion"]))
        result = {"scorecard": scorecard,
                "winner": winner["option"],
                "why": why,
                "scoring_note": ("HEURISTIC: scores are rule-based keyword-overlap "
                                 "0-10 per criterion (plus a bounded numeric-hint "
                                 "boost), weighted by your weights. They reflect "
                                 "what the option TEXT says, not reality.")}
        if args.get("system1"):
            # System 1 fast lane: one JEV forward pass over the options.
            # Lazy import — brain_pack and system1_pack must not import
            # each other at module level.
            from jarvis.tools.builtin import system1_pack
            s1 = system1_pack.choose_handler({
                "text": ("Pick the best option. Criteria: "
                         + "; ".join(c["name"] for c in norm_criteria)),
                "options": {o["name"]: (o["text"] or o["name"])
                            for o in norm_options},
            })
            if s1.get("ok"):
                fast = (s1.get("result") or {}).get("choice")
                result["system1"] = {
                    "winner": fast,
                    "scores": (s1.get("result") or {}).get("scores"),
                    "agree": fast == winner["option"],
                    "note": ("JEV single-forward-pass verdict (laya); independent "
                             "of the heuristic scorecard above.")}
            else:
                result["system1"] = {
                    "winner": None,
                    "agree": None,
                    "note": ("System 1 unavailable: "
                             + str(s1.get("error", "unknown")))}
        return result
    except Exception as exc:
        return {"error": f"brain.decide failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.focus — working memory view
# ---------------------------------------------------------------------------
def brain_focus_handler(args: dict) -> dict:  # noqa: ARG001
    """brain.focus {} — current goal stack from the same sqlite store."""
    try:
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT plan_id, created_at, updated_at, goal, steps_json, status, version "
                    "FROM plans WHERE status='active' ORDER BY updated_at DESC"
                ).fetchall()
                trace_count = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
                lesson_count = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
                pending_lessons = conn.execute(
                    "SELECT COUNT(*) FROM lessons WHERE forwarded=0").fetchone()[0]
            finally:
                conn.close()
        plans_view = []
        blocked: list[dict] = []
        upcoming: list[dict] = []
        for row in rows:
            try:
                steps = json.loads(row[4])
            except (TypeError, ValueError):
                steps = []
            plan = {"plan_id": row[0], "created_at": row[1], "updated_at": row[2],
                    "goal": row[3], "steps": steps, "status": row[5], "version": row[6]}
            eff = _effective_statuses(steps)
            nxt = _next_step(steps)
            done_n = sum(1 for v in eff.values() if v == "done")
            for s in steps:
                if eff[s["id"]] == "blocked":
                    blocked.append({"plan_id": plan["plan_id"], "step": s["id"],
                                    "tool": s["tool"],
                                    "why": "a dependency failed"})
            if nxt:
                upcoming.append({"plan_id": plan["plan_id"], "goal": plan["goal"][:80],
                                 "next_step": nxt["id"], "tool": nxt["tool"]})
            plans_view.append({"plan_id": plan["plan_id"], "goal": plan["goal"],
                               "version": plan["version"],
                               "progress": f"{done_n}/{len(steps)} steps done",
                               "next": ({"step": nxt["id"], "tool": nxt["tool"]}
                                        if nxt else None)})
        return {"active_plans": plans_view, "active_plan_count": len(plans_view),
                "blocked_steps": blocked,
                "whats_next": upcoming,
                "deliberations_recorded": trace_count,
                "lessons_kept": lesson_count,
                "lessons_pending_forward": pending_lessons}
    except Exception as exc:
        return {"error": f"brain.focus failed: {exc}"}


# ---------------------------------------------------------------------------
# brain.reflect — post-action review -> durable lessons
# ---------------------------------------------------------------------------
_LESSON_MARKERS = frozenset(
    "should must always never lesson learned learnt next time avoid remember "
    "worked failed fails mistake better worse important key takeaway because".split()
)


def _extract_lesson_candidates(text: str) -> list[str]:
    """Rule-based: sentences carrying lesson-like markers become candidates."""
    candidates: list[str] = []
    for sent in _sentences(text):
        if not (20 <= len(sent) <= 280):
            continue
        if _tokens(sent) & _LESSON_MARKERS:
            sent = sent.strip().rstrip(".")
            if sent and sent[0].islower():
                sent = sent[0].upper() + sent[1:]
            candidates.append(sent)
        if len(candidates) >= 5:
            break
    return candidates


def _forward_lessons(new_lessons: list[str]) -> dict:
    """Best-effort: forward to memory.learn via the bound agent's registry.

    Never raises, never fails the caller. Returns a per-lesson dict of
    {lesson_text: forwarded_bool} plus a "note"/"skipped" summary.
    """
    ok: dict[str, bool] = {lesson: False for lesson in new_lessons}
    if _AGENT is None:
        return {"ok": ok, "note": "no live agent bound; kept locally"}
    registry = getattr(_AGENT, "registry", None)
    tools = getattr(registry, "tools", {}) if registry is not None else {}
    if registry is None or not hasattr(registry, "call") or "memory.learn" not in tools:
        return {"ok": ok, "note": "memory.learn not available; kept locally"}
    skipped: list[str] = []
    for lesson in new_lessons:
        try:
            res = registry.call("memory.learn",
                                {"text": lesson, "source": "brain.reflect"},
                                actor="brain")
        except Exception as exc:  # noqa: BLE001 — best effort only
            skipped.append(f"{lesson[:60]}: {exc}")
            continue
        if isinstance(res, dict) and res.get("ok"):
            ok[lesson] = True
        else:
            skipped.append(f"{lesson[:60]}: "
                           f"{res.get('error', 'not ok') if isinstance(res, dict) else 'bad result'}")
    forwarded = sum(ok.values())
    return {"ok": ok, "forwarded": forwarded, "skipped": skipped or None,
            "note": ("forwarded to memory.learn" if forwarded
                     else "kept locally")}


def brain_reflect_handler(args: dict) -> dict:
    """brain.reflect {on} — review what happened, keep durable lessons.

    Extracts lesson-like sentences from the description, dedupes them
    against stored lessons (normalized fuzzy match, >= 0.85 similarity is a
    duplicate), stores only genuinely new ones locally. When the live agent
    is bound AND its registry has memory.learn, also forwards new lessons
    there — best effort, never fails this tool.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.reflect: args must be an object"}
        on = str(args.get("on") or "").strip()
        if not on:
            return {"error": "brain.reflect: 'on' (what happened) is required"}

        candidates = _extract_lesson_candidates(on)
        if not candidates:
            return {"lessons": [], "stored": 0,
                    "note": ("No lesson-like sentences found in the description "
                             "(no markers such as 'should', 'lesson', 'next time'). "
                             "Nothing stored.")}

        with _LOCK:
            conn = _connect()
            try:
                existing = [r[0] for r in
                            conn.execute("SELECT normalized FROM lessons").fetchall()]
                new_lessons: list[str] = []
                dupes: list[str] = []
                for cand in candidates:
                    norm = _normalize_lesson(cand)
                    if not norm:
                        continue
                    is_dupe = any(
                        norm == e or SequenceMatcher(None, norm, e).ratio() >= 0.85
                        for e in existing)
                    if is_dupe:
                        dupes.append(cand)
                        continue
                    new_lessons.append(cand)
                    existing.append(norm)
                # Forward first (best-effort), then persist per-lesson
                # forwarded flags so "later forwarding" retries only the gaps.
                fwd = _forward_lessons(new_lessons)
                ok_flags = fwd.get("ok", {})
                stored = 0
                for lesson in new_lessons:
                    try:
                        conn.execute(
                            "INSERT INTO lessons(created_at, text, normalized, source, forwarded)"
                            " VALUES (?,?,?,?,?)",
                            (_now(), lesson, _normalize_lesson(lesson),
                             "brain.reflect", 1 if ok_flags.get(lesson) else 0))
                        stored += 1
                    except sqlite3.IntegrityError:
                        dupes.append(lesson)  # raced an identical insert
                conn.commit()
            finally:
                conn.close()
        return {"lessons": new_lessons, "stored": stored,
                "duplicates_skipped": dupes,
                "forwarding": fwd,
                "extraction": "rule-based: sentences with lesson markers, fuzzy-deduped"}
    except Exception as exc:
        return {"error": f"brain.reflect failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "brain.think",
     "description": ("Structured deliberation over a question: syntactically "
                     "decomposes it into 2-6 sub-questions, weighs evidence "
                     "for/against each from the provided context string, and "
                     "records the full reasoning trace (steps, evidence, "
                     "weights) in sqlite. Returns conclusion + reasons + an "
                     "honest confidence (thin evidence => low confidence). "
                     "Scoring is keyword-overlap heuristic, not understanding. "
                     "Pass instant:true for a JEV System 1 gut verdict per "
                     "sub-question (single batched forward pass) alongside "
                     "the deliberation."),
     "handler": brain_think_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "context?": "string",
                "instant?": "boolean"}},
    {"name": "brain.plan",
     "description": ("Create a multi-step plan: each step is {id, tool, args, "
                     "depends_on[]} naming a registry tool. Persisted in "
                     "sqlite (step statuses pending/blocked/done/failed). "
                     "Returns plan_id. Plans survive restarts."),
     "handler": brain_plan_handler, "risk": "low", "needs_network": False,
     "schema": {"goal": "string", "steps": "array"}},
    {"name": "brain.replan",
     "description": ("Append a revision to a plan (update: {note, steps?}). "
                     "Full version history is kept in plan_revisions and never "
                     "rewritten; done/failed steps carry over across revisions."),
     "handler": brain_replan_handler, "risk": "low", "needs_network": False,
     "schema": {"plan_id": "string", "update": "object"}},
    {"name": "brain.plan_status",
     "description": ("Status of one plan (pass plan_id) or a summary of all "
                     "active plans."),
     "handler": brain_plan_status_handler, "risk": "low", "needs_network": False,
     "schema": {"plan_id?": "string"}},
    {"name": "brain.run_step",
     "description": ("Execute the next pending step whose dependencies are "
                     "done, through the LIVE agent's real registry "
                     "(agent.registry.call(step.tool, step.args, actor='brain', "
                     "confirmed=...)). The normal policy engine gates every "
                     "step: a high-risk step returns needs_confirmation "
                     "instead of running blind — this adds no new risk "
                     "surface. Requires brain_pack.bind_agent(agent) at "
                     "startup; without it, returns an honest error."),
     "handler": brain_run_step_handler, "risk": "medium", "needs_network": False,
     "schema": {"plan_id": "string", "confirmed?": "bool"}},
    {"name": "brain.decide",
     "description": ("Score >=2 options against weighted criteria "
                     "({name, weight}) with a stated, transparently "
                     "HEURISTIC scorecard: rule-based keyword overlap 0-10 "
                     "per criterion plus a bounded numeric-hint boost. "
                     "Returns the full scorecard, the winner, and why. "
                     "Pass system1:true to also get the JEV fast System 1 "
                     "verdict on the same options plus a mechanical agree "
                     "flag (dual-process check)."),
     "handler": brain_decide_handler, "risk": "low", "needs_network": False,
     "schema": {"options": "array", "criteria": "array",
                "system1?": "boolean"}},
    {"name": "brain.focus",
     "description": ("Working memory: the current goal stack — active plans "
                     "with progress, blocked steps, what's next, plus counts "
                     "of recorded deliberations and kept lessons."),
     "handler": brain_focus_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "brain.reflect",
     "description": ("Post-action review of a description of what happened. "
                     "Extracts lesson-like sentences (rule-based markers), "
                     "dedupes them against stored lessons (normalized fuzzy "
                     "match), stores only genuinely new ones locally, and — "
                     "when the live agent is bound and its registry has "
                     "memory.learn — forwards them there too (best-effort, "
                     "never fails the tool)."),
     "handler": brain_reflect_handler, "risk": "low", "needs_network": False,
     "schema": {"on": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(brain_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "brain.think": ("low", False),
    "brain.plan": ("low", False),
    "brain.replan": ("low", False),
    "brain.plan_status": ("low", False),
    "brain.run_step": ("medium", False),
    "brain.decide": ("low", False),
    "brain.focus": ("low", False),
    "brain.reflect": ("low", False),
}


def register(reg) -> None:
    """Wire the eight Brain pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
