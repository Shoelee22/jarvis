"""Phase 16 — JEV: fast System 1 decision layer (Laya).

Laya (github.com/NandhaKishorM/laya) is a non-autoregressive decision engine:
typed choice / score / yes-no decisions over any text in a SINGLE forward
pass (~33 ms), 100+ languages, with a router that picks the right checkpoint
per request. That is exactly what JARVIS's brain was missing: the slow,
deliberative tools (brain.think / brain.decide) are System 2 — careful but
expensive. JEV is System 1 — instant gut triage.

Where it plugs in:
  * autonomy permission pre-checks: "does this request look risky?" in 33 ms
    before the heavier policy path runs;
  * proactive suggestion ranking: score a batch of candidate nudges in one
    forward pass, keep only the top few;
  * inbox triage: yes/no + choice routing over each new message;
  * any fast gate where a keyword heuristic (brain.decide) is too crude.

Honest gates (standing rule — never fake it):
  * `laya` is an OPTIONAL dependency. It needs torch plus a downloaded
    checkpoint (hundreds of MB). It is NOT in the installer's core profile
    and NOT installed by CI. Every tool below returns an honest
    {"ok": False, "error": ...} when laya is missing or disabled — never a
    simulated answer dressed up as a model verdict.
  * Results always carry "backend": "laya" so callers can tell a real
    single-pass verdict from anything else.
  * Enable in tools_config.yaml:
        system1:
          enabled: true
      then:  pip install laya   (downloads the checkpoint on first use)

Tool surface (5 tools):
  system1.ask     yes/no probability for a statement        (laya "noul")
  system1.choose  typed choice among described options      (laya "choice")
  system1.score   ordinal score along a labelled scale      (laya "score")
  system1.batch   several typed questions, ONE forward pass (laya strength)
  system1.status  backend availability / checkpoint / config
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

JARVIS_DIR = Path.home() / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "tools_config.yaml"

_ROUTER = None
_ROUTER_ERROR: str | None = None

_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0
# verdict cache: sha256 key -> (monotonic timestamp, answer dict)
_CACHE: dict[str, tuple[float, dict]] = {}


def _cache_key(qtype: str, state: str, questions: dict) -> str:
    blob = json.dumps({"t": qtype, "text": state, "q": questions},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

INSTALL_HINT = (
    "laya is not installed. Install it with `pip install laya` "
    "(requires torch; downloads a checkpoint on first use), then set "
    "`system1.enabled: true` in tools_config.yaml."
)


def _load_config() -> dict:
    """Read tools_config.yaml at call time; cache by mtime, tolerate failure."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        import yaml
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        raw = {}
    _cfg_cache = raw
    _cfg_mtime = mtime
    return raw


def _cfg() -> dict:
    return _load_config().get("system1", {}) or {}


def _enabled() -> tuple[bool, str]:
    cfg = _cfg()
    if not cfg.get("enabled", False):
        return False, (
            "system1 is disabled. Set `system1.enabled: true` in "
            "tools_config.yaml to use the JEV fast-decision layer."
        )
    return True, ""


def _get_router():
    """Return (router, error). Router is a cached singleton; laya imports lazily."""
    global _ROUTER, _ROUTER_ERROR
    if _ROUTER is not None:
        return _ROUTER, None
    if _ROUTER_ERROR is not None:
        return None, _ROUTER_ERROR
    ok, why = _enabled()
    if not ok:
        return None, why
    try:
        from laya import Router  # type: ignore
    except Exception as e:
        _ROUTER_ERROR = f"{INSTALL_HINT} (import failed: {e})"
        return None, _ROUTER_ERROR
    try:
        cfg = _cfg()
        _ROUTER = Router(preload=bool(cfg.get("preload", False)))
    except Exception as e:
        _ROUTER_ERROR = f"laya Router failed to initialise: {e}"
        return None, _ROUTER_ERROR
    return _ROUTER, None


def reset_router_cache() -> None:
    """Test hook: drop the cached router/singleton state."""
    global _ROUTER, _ROUTER_ERROR
    _ROUTER, _ROUTER_ERROR = None, None
    _CACHE.clear()


def _predict(state: str, questions: dict, max_len: int | None = None) -> dict:
    cfg = _cfg()
    ttl = cfg.get("cache_ttl_sec", 0) or 0
    key = _cache_key("predict", state, questions) if ttl > 0 else None
    if key is not None:
        hit = _CACHE.get(key)
        if hit and (time.monotonic() - hit[0]) < ttl:
            out = dict(hit[1])
            out["cached"] = True
            return out
    router, err = _get_router()
    if err:
        return {"ok": False, "error": err, "backend": "laya", "simulated": False}
    kwargs: dict[str, Any] = {}
    if cfg.get("model"):
        kwargs["model"] = cfg["model"]
    if max_len:
        kwargs["max_len"] = max_len
    t0 = time.monotonic()
    try:
        result = router.predict(state, questions, **kwargs)
    except Exception as e:
        return {"ok": False, "error": f"laya predict failed: {e}",
                "backend": "laya", "simulated": False}
    ms = round((time.monotonic() - t0) * 1000, 1)
    out = {"ok": True, "backend": "laya", "simulated": False,
           "latency_ms": ms, "result": result}
    if key is not None:
        _CACHE[key] = (time.monotonic(), dict(out))
    return out


def _question(qtype: str, instructions: str, criteria=None) -> dict:
    q: dict[str, Any] = {"type": qtype, "instructions": instructions}
    if criteria is not None:
        q["criteria"] = criteria
    return q


# ---------------------------------------------------------------- handlers

def ask_handler(args: dict) -> dict:
    """system1.ask — yes/no probability for a statement (laya 'noul')."""
    statement = (args.get("statement") or "").strip()
    if not statement:
        return {"ok": False, "error": "statement is required", "backend": "laya"}
    question = args.get("question") or "Is the statement true / does it hold?"
    out = _predict(statement, {"verdict": _question("noul", question)})
    if not out.get("ok"):
        return out
    ans = out["result"]["answers"]["verdict"]
    p_yes = float(ans.get("noul", 0.0))
    out["result"] = {
        "p_yes": round(p_yes, 4),
        "verdict": "yes" if p_yes >= 0.5 else "no",
        "routing": out["result"].get("routing", {}),
    }
    return out


def choose_handler(args: dict) -> dict:
    """system1.choose — typed choice among described options (laya 'choice')."""
    text = (args.get("text") or "").strip()
    options = args.get("options") or {}
    if not text:
        return {"ok": False, "error": "text is required", "backend": "laya"}
    if not isinstance(options, dict) or len(options) < 2:
        return {"ok": False, "error": "options must be a dict of >=2 {name: description}",
                "backend": "laya"}
    instructions = args.get("instructions") or "Which option best fits the text?"
    out = _predict(text, {"pick": _question("choice", instructions, options)})
    if not out.get("ok"):
        return out
    ans = out["result"]["answers"]["pick"]
    out["result"] = {
        "choice": ans.get("choice"),
        "scores": ans.get("scores") or ans.get("probs"),
        "routing": out["result"].get("routing", {}),
    }
    return out


def score_handler(args: dict) -> dict:
    """system1.score — ordinal score along a labelled scale (laya 'score')."""
    text = (args.get("text") or "").strip()
    scale = args.get("scale") or []
    if not text:
        return {"ok": False, "error": "text is required", "backend": "laya"}
    if not isinstance(scale, list) or len(scale) < 2:
        return {"ok": False, "error": "scale must be a list of >=2 labels, e.g. "
                '["not urgent", "soon", "blocking"]', "backend": "laya"}
    instructions = args.get("instructions") or "Where does the text fall on this scale?"
    out = _predict(text, {"rating": _question("score", instructions, scale)})
    if not out.get("ok"):
        return out
    ans = out["result"]["answers"]["rating"]
    out["result"] = {
        "score": ans.get("score"),
        "label": ans.get("label") or ans.get("choice"),
        "routing": out["result"].get("routing", {}),
    }
    return out


def batch_handler(args: dict) -> dict:
    """system1.batch — several typed questions answered in ONE forward pass.

    questions: {name: {"type": "choice"|"score"|"noul",
                       "instructions": str, "criteria?": dict|list}}
    """
    text = (args.get("text") or "").strip()
    questions = args.get("questions") or {}
    if not text:
        return {"ok": False, "error": "text is required", "backend": "laya"}
    if not isinstance(questions, dict) or not questions:
        return {"ok": False, "error": "questions must be a non-empty dict", "backend": "laya"}
    built: dict[str, dict] = {}
    for name, q in questions.items():
        if not isinstance(q, dict) or q.get("type") not in ("choice", "score", "noul"):
            return {"ok": False,
                    "error": f"question '{name}': type must be choice|score|noul",
                    "backend": "laya"}
        if not q.get("instructions"):
            return {"ok": False,
                    "error": f"question '{name}': instructions is required",
                    "backend": "laya"}
        built[name] = _question(q["type"], q["instructions"], q.get("criteria"))
    out = _predict(text, built,
                   max_len=args.get("max_len") if isinstance(args.get("max_len"), int) else None)
    if not out.get("ok"):
        return out
    answers = out["result"].get("answers", {})
    slim: dict[str, Any] = {}
    for name, ans in answers.items():
        slim[name] = {
            k: ans.get(k) for k in ("choice", "noul", "score", "label", "scores", "probs")
            if ans.get(k) is not None
        } or ans
    out["result"] = {"answers": slim, "routing": out["result"].get("routing", {})}
    return out


def status_handler(args: dict) -> dict:  # noqa: ARG001
    """system1.status — backend availability, checkpoint, config."""
    cfg = _cfg()
    ok, why = _enabled()
    info: dict[str, Any] = {
        "ok": True, "backend": "laya", "enabled": ok,
        "model": cfg.get("model") or "auto (router picks per request)",
        "preload": bool(cfg.get("preload", False)),
    }
    if not ok:
        info["available"] = False
        info["note"] = why
        return info
    try:
        import laya  # type: ignore
        info["laya_version"] = getattr(laya, "__version__", "unknown")
        info["available"] = True
    except Exception:
        info["available"] = False
        info["note"] = INSTALL_HINT
        return info
    router, err = _get_router()
    info["router_ready"] = router is not None
    if err:
        info["note"] = err
    return info


def triage_handler(args: dict) -> dict:
    """system1.triage — route N items into K buckets, one forward pass each.

    The flagship JEV workload: instant routing of inbox messages, suggestions,
    or permission pre-checks without touching the heavier brain/policy path.
    """
    items = args.get("items")
    buckets = args.get("buckets")
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "items must be a non-empty list of strings",
                "backend": "laya"}
    if not isinstance(buckets, dict) or len(buckets) < 2:
        return {"ok": False, "error": "buckets must be an object with >= 2 entries",
                "backend": "laya"}
    instructions = args.get("instructions") or "Route the item to the best bucket."
    routes: list[dict] = []
    for raw in items:
        text = str(raw or "").strip()
        if not text:
            routes.append({"item": raw, "bucket": None,
                           "error": "empty item text"})
            continue
        out = _predict(f"Route this item: {text}",
                       {"route": _question("choice", instructions,
                                           criteria=buckets)})
        if not out.get("ok"):
            return out  # honest backend error, abort the triage
        ans = out["result"]["answers"]["route"]
        routes.append({"item": text[:200], "bucket": ans.get("choice"),
                       "scores": ans.get("scores"),
                       "cached": out.get("cached", False)})
    return {"ok": True, "backend": "laya", "simulated": False,
            "forward_passes": len(routes), "routes": routes}


def duel_handler(args: dict) -> dict:
    """system1.duel — System 1 instant verdict vs System 2 deliberation.

    Runs the JEV fast verdict AND the brain's slow heuristic path on the same
    question and reports both side by side with a mechanical agreement flag.
    Dual-process cognition: gut feeling checked against deliberation.
    """
    question = (args.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": "question is required", "backend": "laya"}
    context = str(args.get("context") or "")
    options = args.get("options")

    # --- System 1: the fast gut verdict ---
    if options:
        s1 = choose_handler({"text": question, "options": options})
        s1_verdict = (s1.get("result", {}) or {}).get("choice") if s1.get("ok") else None
    else:
        s1 = ask_handler({"statement": question,
                          "question": "Is the statement true given the context?"})
        s1_verdict = (s1.get("result", {}) or {}).get("verdict") if s1.get("ok") else None
    system1_part = {"ok": s1.get("ok", False),
                    "verdict": s1_verdict,
                    "detail": (s1.get("result") or s1.get("error")),
                    "simulated": False}

    # --- System 2: the slow deliberative path (lazy import, no cycle) ---
    from jarvis.tools.builtin import brain_pack
    if options:
        s2 = brain_pack.brain_decide_handler({
            "options": [{"name": k, "text": v} for k, v in options.items()]
            if isinstance(options, dict) else options,
            "criteria": [{"name": "overall fit", "weight": 1}],
        })
        s2_verdict = s2.get("winner") if not s2.get("error") else None
    else:
        s2 = brain_pack.brain_think_handler({"question": question,
                                             "context": context})
        s2_verdict = None if s2.get("error") else s2.get("conclusion")
    system2_part = {"ok": not s2.get("error"),
                    "verdict": s2_verdict,
                    "detail": (s2.get("error") or
                               {k: s2.get(k) for k in ("confidence", "why",
                                                      "trace_id")
                                if k in s2}),
                    "scoring": s2.get("scoring", "heuristic")}

    if options and s1.get("ok") and not s2.get("error"):
        agree: bool | None = (s1_verdict == s2_verdict)
    else:
        agree = None  # different verdict types — no mechanical comparison

    # Phase 21: semantic agreement — cosine similarity between the chosen
    # option DESCRIPTIONS, so paraphrased-but-distinct keys still agree.
    # Only for the options case; yes/no vs conclusion text is meaningless.
    agree_semantic = None
    if options and s1.get("ok") and not s2.get("error"):
        try:
            from jarvis.tools.builtin import semantics_pack as _sem
            _eng, _err = _sem.get_engine()
            if _eng is not None:
                def _vtext(v):
                    if isinstance(options, dict):
                        return str(options.get(v, v))
                    return str(v)
                _sim = _eng.similarity(_vtext(s1_verdict), _vtext(s2_verdict))
                agree_semantic = {
                    "similarity": round(_sim, 3),
                    "agree": _sim >= _sem.AGREE_THRESHOLD,
                    "threshold": _sem.AGREE_THRESHOLD,
                    "model": _eng.model_name,
                }
        except Exception:
            agree_semantic = None
    return {"ok": True, "question": question[:200],
            "system1": system1_part, "system2": system2_part,
            "agree": agree, "agree_semantic": agree_semantic,
            "note": ("agree compares fast choice vs deliberative winner only "
                     "when both reduce to a single option; otherwise None. "
                     "agree_semantic is cosine similarity between the chosen "
                     "option descriptions (threshold 0.85) when the embedding "
                     "backend is available; None otherwise.")}


TOOL_DEFS = [
    {"name": "system1.ask",
     "description": ("JEV fast yes/no: probability that a statement holds, in a "
                     "single Laya forward pass (~33 ms), 100+ languages. Use for "
                     "instant triage gates (risky? urgent? needs approval?) before "
                     "the heavier brain/policy path. Honest error when laya is "
                     "not installed or system1.enabled is false — never fakes."),
     "handler": ask_handler, "risk": "low", "needs_network": False,
     "schema": {"statement": "string", "question?": "string"}},
    {"name": "system1.choose",
     "description": ("JEV fast typed choice: pick one option from a dict of "
                     "{name: description} for a text, single Laya forward pass. "
                     "Use for instant routing (which department / which action). "
                     "Honest error when laya is unavailable."),
     "handler": choose_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string", "options": "object", "instructions?": "string"}},
    {"name": "system1.score",
     "description": ("JEV fast ordinal score: place a text on a labelled scale "
                     "(e.g. [\"not urgent\", \"soon\", \"blocking\"]), single Laya "
                     "forward pass. Use for instant ranking/prioritisation. "
                     "Honest error when laya is unavailable."),
     "handler": score_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string", "scale": "array", "instructions?": "string"}},
    {"name": "system1.batch",
     "description": ("JEV batch triage: several typed questions (choice/score/noul) "
                     "answered in ONE Laya forward pass over the same text — the "
                     "fast path for inbox triage and suggestion ranking. questions "
                     "is {name: {type, instructions, criteria?}}. Honest error "
                     "when laya is unavailable."),
     "handler": batch_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string", "questions": "object", "max_len?": "integer"}},
    {"name": "system1.status",
     "description": ("JEV backend status: whether laya is installed and enabled, "
                     "which checkpoint routing is configured, router readiness. "
                     "Check this before relying on system1.* verdicts."),
     "handler": status_handler, "risk": "low", "needs_network": False,
     "schema": {}},
    {"name": "system1.triage",
     "description": ("JEV bulk router: send N items, get each routed into one of "
                     "K named buckets (one fast forward pass per item). The "
                     "flagship System 1 workload — instant inbox triage, "
                     "suggestion ranking, permission pre-checks. Honest error "
                     "when laya is unavailable."),
     "handler": triage_handler, "risk": "low", "needs_network": False,
     "schema": {"items": "array", "buckets": "object",
                "instructions?": "string"}},
    {"name": "system1.duel",
     "description": ("Dual-process verdict: JEV fast System 1 gut verdict AND "
                     "the brain's slow System 2 deliberation on the same "
                     "question, side by side, with a mechanical agree flag. "
                     "Use when a fast call needs a deliberative second opinion."),
     "handler": duel_handler, "risk": "low", "needs_network": False,
     "schema": {"question": "string", "context?": "string",
                "options?": "object"}},
]

RISK_TABLE_ADDITIONS = {
    "system1.ask": ("low", False),
    "system1.choose": ("low", False),
    "system1.score": ("low", False),
    "system1.batch": ("low", False),
    "system1.status": ("low", False),
    "system1.triage": ("low", False),
    "system1.duel": ("low", False),
}


def register(reg) -> None:
    """Wire the five JEV System 1 tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
