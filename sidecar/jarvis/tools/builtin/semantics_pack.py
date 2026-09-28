"""Phase 21 — The semantic brain: real similarity under the deliberation.

Phases 14-20 built the brain's structure (traces, adversarial review,
calibration, closed loop). The scoring underneath was still token overlap:
"gym" vs "gyms", paraphrases, and negation all defeated it. This pack adds
an OPTIONAL embedding backend (sentence-transformers, default off, never
auto-downloaded) and wires cosine similarity into think/decide/duel/survey
— with honest fallback to keyword weights when the backend is missing.

Honesty contract: embeddings are similarity, not comprehension. Negation
("not risky" vs "risky") still fools cosine similarity — the benchmark
fixture includes negation cases as known failures. Every tool reports
honestly when the backend is unavailable; nothing is ever simulated.

Test hook: set_backend_override(fn) injects a fake embed function
fn(texts: list[str]) -> list[list[float]]; clear_backend_override()
restores normal behavior. The override bypasses the enabled-check so
tests never touch the real config or model.
"""
from __future__ import annotations

import math
from pathlib import Path

JARVIS_DIR = Path.home() / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "tools_config.yaml"

INSTALL_HINT = (
    "semantic backend not available. Install it with "
    "`pip install sentence-transformers torch` (downloads ~90MB model on "
    "first use — your explicit opt-in), then set `semantics.enabled: true` "
    "in tools_config.yaml."
)

AGREE_THRESHOLD = 0.85

_backend_override = None
_backend = None
_backend_error = None
_cfg_cache = None
_cfg_mtime = None


# ---------------------------------------------------------------------------
# Backend plumbing
# ---------------------------------------------------------------------------

def set_backend_override(fn) -> None:
    """Inject a fake embed fn for tests: fn(texts) -> list of vectors."""
    global _backend_override
    _backend_override = fn


def clear_backend_override() -> None:
    global _backend_override
    _backend_override = None


def _load_config() -> dict:
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
    return _load_config().get("semantics", {}) or {}


def _enabled() -> tuple[bool, str]:
    cfg = _cfg()
    if not cfg.get("enabled", False):
        return False, ("semantics is disabled. Set `semantics.enabled: true` "
                       "in tools_config.yaml to use the embedding backend.")
    return True, ""


def _load_model():
    """Return (embed_fn, model_name, error). embed_fn maps texts->vectors."""
    global _backend, _backend_error
    if _backend_override is not None:
        return _backend_override, "test-override", None
    if _backend is not None:
        return _backend, _backend[1], None
    if _backend_error is not None:
        return None, "", _backend_error
    ok, why = _enabled()
    if not ok:
        _backend_error = why
        return None, "", why
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        _backend_error = INSTALL_HINT
        return None, "", INSTALL_HINT
    model_name = _cfg().get("model",
                            "sentence-transformers/all-MiniLM-L6-v2")
    try:
        model = SentenceTransformer(model_name)

        def _embed(texts: list[str]) -> list[list[float]]:
            vecs = model.encode(texts, convert_to_numpy=True,
                                normalize_embeddings=False)
            return [list(map(float, v)) for v in vecs]

        _backend = (_embed, model_name)
        return _embed, model_name, None
    except Exception as exc:
        _backend_error = f"could not load model '{model_name}': {exc}"
        return None, "", _backend_error


class _Engine:
    """Thin wrapper: embed() + cosine similarity, or a clean unavailable."""

    def __init__(self, embed_fn, model_name: str):
        self._embed = embed_fn
        self.model_name = model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._embed([str(t) for t in texts])

    def similarity(self, a: str, b: str) -> float:
        va, vb = self.embed([a, b])
        return cosine(va, vb)


def get_engine() -> tuple[_Engine | None, str]:
    """Return (engine, error). Engine is None when backend unavailable."""
    fn, name, err = _load_model()
    if fn is None:
        return None, err or "unknown backend error"
    return _Engine(fn, name), ""


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _need_engine():
    eng, err = get_engine()
    if eng is None:
        return None, {"error": err, "backend": "unavailable",
                      "simulated": False}
    return eng, None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def embed_handler(args: dict) -> dict:
    """brain.embed {text} — embedding vector for one text."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.embed: args must be an object"}
        text = str(args.get("text") or "")
        if not text.strip():
            return {"error": "brain.embed: 'text' is required"}
        eng, errd = _need_engine()
        if errd:
            return errd
        vec = eng.embed([text])[0]
        return {"embedding": vec, "dim": len(vec), "model": eng.model_name,
                "note": "vector of cosine-similarity features, not meaning"}
    except Exception as exc:  # never raise
        return {"error": f"brain.embed failed: {exc}"}


def similar_handler(args: dict) -> dict:
    """brain.similar {a, b} — cosine similarity between two texts."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.similar: args must be an object"}
        a = str(args.get("a") or "")
        b = str(args.get("b") or "")
        if not a.strip() or not b.strip():
            return {"error": "brain.similar: 'a' and 'b' are required"}
        eng, errd = _need_engine()
        if errd:
            return errd
        sim = round(eng.similarity(a, b), 4)
        return {"similarity": sim, "model": eng.model_name,
                "read": ("1.0 = identical direction, 0.0 = unrelated. "
                         "Similarity of wording, not truth or understanding. "
                         "Negation ('not risky' vs 'risky') still scores high "
                         "— known failure mode.")}
    except Exception as exc:  # never raise
        return {"error": f"brain.similar failed: {exc}"}


def related_handler(args: dict) -> dict:
    """brain.related {query, texts[]} — rank texts by similarity to query."""
    try:
        if not isinstance(args, dict):
            return {"error": "brain.related: args must be an object"}
        query = str(args.get("query") or "")
        texts = args.get("texts")
        if not query.strip():
            return {"error": "brain.related: 'query' is required"}
        if not isinstance(texts, list) or not texts:
            return {"error": "brain.related: 'texts' must be a non-empty list"}
        eng, errd = _need_engine()
        if errd:
            return errd
        vecs = eng.embed([query] + [str(t) for t in texts])
        qv = vecs[0]
        ranked = sorted(
            ({"text": str(t)[:300], "similarity": round(cosine(qv, v), 4)}
             for t, v in zip(texts, vecs[1:])),
            key=lambda e: e["similarity"], reverse=True)
        return {"ranking": ranked, "model": eng.model_name}
    except Exception as exc:  # never raise
        return {"error": f"brain.related failed: {exc}"}


def cluster_handler(args: dict) -> dict:
    """brain.cluster {texts[], threshold?} — group near-duplicate claims.

    Greedy: each text joins the first cluster whose representative scores
    >= threshold (default 0.85), else starts a new cluster. For grouping
    paraphrased claims before contradiction mining.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.cluster: args must be an object"}
        texts = args.get("texts")
        if not isinstance(texts, list) or not texts:
            return {"error": "brain.cluster: 'texts' must be a non-empty list"}
        try:
            threshold = float(args.get("threshold", AGREE_THRESHOLD))
        except (TypeError, ValueError):
            return {"error": "brain.cluster: 'threshold' must be numeric"}
        if not (0.0 < threshold <= 1.0):
            return {"error": "brain.cluster: 'threshold' must be in (0, 1]"}
        eng, errd = _need_engine()
        if errd:
            return errd
        strs = [str(t) for t in texts]
        vecs = eng.embed(strs)
        clusters: list[dict] = []  # {rep_idx, members:[idx]}
        for i, v in enumerate(vecs):
            placed = False
            for cl in clusters:
                if cosine(v, vecs[cl["rep_idx"]]) >= threshold:
                    cl["members"].append(i)
                    placed = True
                    break
            if not placed:
                clusters.append({"rep_idx": i, "members": [i]})
        return {"clusters": [
            {"representative": strs[cl["rep_idx"]][:200],
             "members": [strs[m][:200] for m in cl["members"]],
             "size": len(cl["members"])}
            for cl in clusters],
            "threshold": threshold, "model": eng.model_name}
    except Exception as exc:  # never raise
        return {"error": f"brain.cluster failed: {exc}"}


def diverse_handler(args: dict) -> dict:
    """brain.diverse {texts[], n} — the n most mutually-different texts.

    Farthest-point sampling in embedding space: start from the first text,
    repeatedly add the text with the largest minimum distance to the picked
    set. For picking a representative-but-varied shortlist.
    """
    try:
        if not isinstance(args, dict):
            return {"error": "brain.diverse: args must be an object"}
        texts = args.get("texts")
        if not isinstance(texts, list) or not texts:
            return {"error": "brain.diverse: 'texts' must be a non-empty list"}
        try:
            n = int(args.get("n", 3))
        except (TypeError, ValueError):
            return {"error": "brain.diverse: 'n' must be an integer"}
        n = max(1, min(n, len(texts)))
        eng, errd = _need_engine()
        if errd:
            return errd
        strs = [str(t) for t in texts]
        vecs = eng.embed(strs)
        picked = [0]
        while len(picked) < n:
            best, best_d = -1, -1.0
            for i, v in enumerate(vecs):
                if i in picked:
                    continue
                d = min(1.0 - cosine(v, vecs[p]) for p in picked)
                if d > best_d:
                    best, best_d = i, d
            picked.append(best)
        return {"picks": [strs[i][:300] for i in picked],
                "model": eng.model_name}
    except Exception as exc:  # never raise
        return {"error": f"brain.diverse failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "brain.embed",
     "description": ("Embedding vector for one text (cosine-similarity "
                     "features, not meaning). Requires the optional semantic "
                     "backend: `semantics.enabled: true` in tools_config.yaml "
                     "plus sentence-transformers + model (~90MB, your opt-in "
                     "download). Honest error when unavailable — never "
                     "simulated."),
     "handler": embed_handler, "risk": "low", "needs_network": False,
     "schema": {"text": "string"}},
    {"name": "brain.similar",
     "description": ("Cosine similarity between two texts (1.0 = identical "
                     "direction, 0.0 = unrelated). Similarity of wording, not "
                     "truth. Known failure: negation still scores high. Same "
                     "optional-backend honesty as brain.embed."),
     "handler": similar_handler, "risk": "low", "needs_network": False,
     "schema": {"a": "string", "b": "string"}},
    {"name": "brain.related",
     "description": ("Rank a list of texts by cosine similarity to a query. "
                     "Same optional-backend honesty as brain.embed."),
     "handler": related_handler, "risk": "low", "needs_network": False,
     "schema": {"query": "string", "texts": "array"}},
    {"name": "brain.cluster",
     "description": ("Group near-duplicate claims: greedy clustering at a "
                     "cosine threshold (default 0.85). For grouping "
                     "paraphrased claims before contradiction mining. Same "
                     "optional-backend honesty as brain.embed."),
     "handler": cluster_handler, "risk": "low", "needs_network": False,
     "schema": {"texts": "array", "threshold?": "number"}},
    {"name": "brain.diverse",
     "description": ("Pick the n most mutually-different texts "
                     "(farthest-point sampling in embedding space). For a "
                     "representative-but-varied shortlist. Same "
                     "optional-backend honesty as brain.embed."),
     "handler": diverse_handler, "risk": "low", "needs_network": False,
     "schema": {"texts": "array", "n": "int?"}},
]

RISK_TABLE_ADDITIONS = {spec["name"]: (spec["risk"], spec["needs_network"])
                        for spec in TOOL_DEFS}


def register(reg) -> None:
    """Wire the five Phase 21 semantic tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
