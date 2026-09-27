"""Offline knowledge pack (Phase 14, Workstream B).

A fully OFFLINE reference library for the agent: no network, no APIs, no
guessing. Five curated datasets live in ``data/`` next to this module:

    data/elements.json      — 118 elements (symbol, name, atomic number,
                              atomic mass, group, period). Masses are IUPAC
                              standard atomic weights; elements whose mass
                              has ``mass_kind == "isotope"`` carry the mass
                              number of the longest-lived known isotope
                              (the bracket value), because no stable
                              standard weight exists for them.
    data/countries.json     — ~194 countries (name, capital, ISO currency
                              code, continent).
    data/constants.json     — 38 physical & mathematical constants (name,
                              symbol, value, unit; exactness noted where
                              the value is defined rather than measured).
    data/timeline.json      — 122 major world-history events (year, title,
                              summary). BCE years are negative; entries
                              with ``circa == true`` are traditional/
                              approximate dates, honestly flagged.
    data/science_facts.json — 44 human-body / science facts.

IRON RULE (the whole point of this pack): every entry is a fact the author
was genuinely certain of. Uncertain entries were OMITTED rather than
guessed — e.g. Palestine (capital/currency disputed), Bulgaria (currency
transition timing uncertain), the Hubble constant (measurement tension),
Burundi (capital changed 2019, ambiguous listing). A smaller honest
dataset beats a larger invented one.

TOOLS (all low risk, needs_network=False):
    knowledge.ask      {query, n?}      — ranked full-text search across ALL
                                         datasets.
    knowledge.element  {symbol|name|number|q} — exact lookup of one element.
    knowledge.country  {name|q}         — exact/fuzzy lookup of one country.
    knowledge.constant {name|symbol|q}  — lookup by name or symbol.
    knowledge.timeline {from_year, to_year} — events in range, chronological.
    knowledge.stats    {}               — honest counts per dataset + index
                                         status + FTS5 availability.

SEARCH INDEX: on first use, ``knowledge.ask``/``knowledge.stats`` build a
sqlite FTS5 index from the JSON files at
``~/workspace/jarvis/data/knowledge.db`` (override with the
``JARVIS_KNOWLEDGE_DB`` env var; tests use a tmp file). The index is
rebuilt automatically when any JSON is newer than the db. If the local
sqlite was built without FTS5, the pack falls back to a pure-Python
substring ranking — and reports ``engine: "python-fallback"`` in results
and stats instead of pretending FTS5 ran.

SAFETY:
    - Handlers take dict -> return dict and never raise; failures are
      returned as {"error": "..."}.
    - All lookups are read-only against the bundled JSONs; nothing is
      written except the local search index.
    - No live agent is required: no bind_agent needed for this pack.

INTEGRATION NOTE FOR THE PARENT (apply in tools/builtin/__init__.py):
    1. Add ``knowledge_pack`` to the import list, e.g.::

           from . import (..., persona_pack, knowledge_pack)

    2. Add a registration block after the Phase 13 block::

           # --- Phase 14: offline knowledge pack ---
           knowledge_pack.register(reg)
           _policy.RISK_TABLE.update(knowledge_pack.RISK_TABLE_ADDITIONS)

PACKAGING NOTE FOR THE PARENT (Windows build — do NOT edit packaging
files from this workstream): the ``data/*.json`` files live next to this
module (``sidecar/jarvis/tools/builtin/data/``). They MUST be added to the
PyInstaller sidecar spec (``datas=[...]``) and/or the Tauri bundle
resources, or the ``knowledge.*`` tools will fail on the packaged Windows
build because the JSONs won't be beside the module. Flag as a packaging
follow-up.
"""
from __future__ import annotations

import difflib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
JARVIS_DIR = HOME / "workspace" / "jarvis"
DEFAULT_DB_PATH = JARVIS_DIR / "data" / "knowledge.db"

DATA_DIR = Path(__file__).resolve().parent / "data"
DATASETS = ("elements", "countries", "constants", "timeline", "science_facts")

# RLock (not Lock): _ensure_index() holds the lock while calling _all_docs()
# -> _load(), which also acquires it. A plain Lock deadlocks a single
# thread on that re-entrant path.
_LOCK = threading.RLock()
_CACHE: dict[str, list] = {}
_FTS5_OK: bool | None = None  # probed lazily; tests may force False


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _data_path(dataset: str) -> Path:
    return DATA_DIR / f"{dataset}.json"


def _load(dataset: str) -> list:
    """Load one dataset from the bundled JSON (cached, thread-safe)."""
    with _LOCK:
        if dataset not in _CACHE:
            try:
                _CACHE[dataset] = json.loads(
                    _data_path(dataset).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"knowledge dataset '{dataset}' failed to load: {exc}")
        return _CACHE[dataset]


def _db_path() -> Path:
    override = os.environ.get("JARVIS_KNOWLEDGE_DB")
    return Path(override) if override else DEFAULT_DB_PATH


def _fts5_ok() -> bool:
    """True if this sqlite build supports FTS5. Probed once, cached."""
    global _FTS5_OK
    if _FTS5_OK is None:
        try:
            conn = sqlite3.connect(":memory:")
            try:
                conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
            finally:
                conn.close()
            _FTS5_OK = True
        except Exception:
            _FTS5_OK = False
    return _FTS5_OK


def _year_label(year: int) -> str:
    return f"{-year} BCE" if year < 0 else str(year)


def _doc_text(dataset: str, item: dict) -> tuple[str, str, str]:
    """Return (doc_id, title, text) for one dataset item."""
    if dataset == "elements":
        title = f"{item['name']} ({item['symbol']})"
        text = (f"chemical element symbol {item['symbol']} atomic number "
                f"{item['number']} atomic mass {item['mass']} group "
                f"{item['group']} period {item['period']}")
        return item["symbol"], title, text
    if dataset == "countries":
        title = item["name"]
        text = (f"country capital {item['capital']} currency {item['currency']} "
                f"continent {item['continent']}")
        return item["name"], title, text
    if dataset == "constants":
        title = item["name"]
        text = (f"physical constant symbol {item['symbol']} value "
                f"{item['value']} unit {item['unit']}")
        return item["name"], title, text
    if dataset == "timeline":
        title = f"{_year_label(item['year'])}: {item['title']}"
        text = (f"history event year {item['year']} {item['title']} "
                f"{item['summary']}")
        return f"{item['year']}:{item['title']}", title, text
    # science_facts
    title = item["title"]
    text = f"{item['category']} {item['fact']}"
    return item["title"], title, text


def _all_docs() -> list[dict]:
    """Every searchable document as {dataset, doc_id, title, text}."""
    docs = []
    for dataset in DATASETS:
        for item in _load(dataset):
            doc_id, title, text = _doc_text(dataset, item)
            docs.append({"dataset": dataset, "doc_id": doc_id,
                         "title": title, "text": text})
    return docs


# ---------------------------------------------------------------------------
# sqlite FTS5 index plumbing
# ---------------------------------------------------------------------------
def _ensure_index() -> dict:
    """Build (or rebuild, when JSONs changed) the search index.

    Returns {"engine", "built_at", "documents", "rebuilt"}.
    """
    with _LOCK:
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fts = _fts5_ok()
        engine = "fts5" if fts else "python-fallback"
        max_mtime = max(_data_path(d).stat().st_mtime for d in DATASETS)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS "
                         "meta(key TEXT PRIMARY KEY, value TEXT)")
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            if fts:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS facts USING "
                    "fts5(dataset UNINDEXED, doc_id UNINDEXED, title, text)")
                count = conn.execute(
                    "SELECT COUNT(*) FROM facts").fetchone()[0]
            else:
                count = 0
            stored_mtime = meta.get("source_mtime")
            if (count > 0 and stored_mtime is not None
                    and float(stored_mtime) >= max_mtime
                    and meta.get("engine") == engine):
                return {"engine": engine, "built_at": meta.get("built_at"),
                        "documents": count, "rebuilt": False}
            docs = _all_docs()
            if fts:
                conn.execute("DELETE FROM facts")
                conn.executemany(
                    "INSERT INTO facts(dataset, doc_id, title, text) "
                    "VALUES (?,?,?,?)",
                    [(d["dataset"], d["doc_id"], d["title"], d["text"])
                     for d in docs])
            count = len(docs)
            built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.execute("INSERT OR REPLACE INTO meta(key,value) "
                         "VALUES ('built_at',?)", (built_at,))
            conn.execute("INSERT OR REPLACE INTO meta(key,value) "
                         "VALUES ('source_mtime',?)", (str(max_mtime),))
            conn.execute("INSERT OR REPLACE INTO meta(key,value) "
                         "VALUES ('engine',?)", (engine,))
            conn.execute("INSERT OR REPLACE INTO meta(key,value) "
                         "VALUES ('documents',?)", (str(count),))
            conn.commit()
            return {"engine": engine, "built_at": built_at,
                    "documents": count, "rebuilt": True}
        finally:
            conn.close()


def _matched_field(doc: dict, terms: list[str]) -> str:
    title = doc["title"].lower()
    text = doc["text"].lower()
    in_title = any(t in title for t in terms)
    in_text = any(t in text for t in terms)
    if in_title and in_text:
        return "both"
    if in_title:
        return "title"
    if in_text:
        return "text"
    return "unknown"


def _python_search(terms: list[str], n: int) -> list[dict]:
    """Fallback ranking when FTS5 is unavailable: case-insensitive
    substring scoring (title hits weigh double)."""
    scored = []
    for doc in _all_docs():
        title = doc["title"].lower()
        text = doc["text"].lower()
        score = sum(2 for t in terms if t in title) + sum(
            1 for t in terms if t in text)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda s: (-s[0], s[1]["dataset"], s[1]["title"]))
    hits = []
    for score, doc in scored[:n]:
        text = doc["text"]
        low = text.lower()
        idx = min((low.find(t) for t in terms if t in low), default=-1)
        if idx >= 0:
            start = max(0, idx - 60)
            snippet = ("..." if start > 0 else "") + text[start:idx + 120] \
                + ("..." if idx + 120 < len(text) else "")
        else:
            snippet = text[:160] + ("..." if len(text) > 160 else "")
        hits.append({"dataset": doc["dataset"], "doc_id": doc["doc_id"],
                     "title": doc["title"], "snippet": snippet,
                     "matched_field": _matched_field(doc, terms),
                     "rank": float(-score)})
    return hits


def knowledge_ask_handler(args: dict) -> dict:
    """knowledge.ask {query, n?} — ranked full-text search across ALL datasets."""
    try:
        args = args or {}
        query = args.get("query", "")
        if not isinstance(query, str):
            query = str(query)
        query = query.strip()
        if not query:
            return {"error": "query must be a non-empty string"}
        n = args.get("n", 10)
        try:
            n = int(n)
        except (TypeError, ValueError):
            return {"error": "n must be an integer"}
        n = max(1, min(n, 50))
        info = _ensure_index()
        engine = info["engine"]
        words = [t for t in query.split() if t]
        terms = [t.lower() for t in words]
        hits: list[dict] = []
        if engine == "fts5":
            # Quote each term individually and AND them: multi-word queries
            # match documents containing all terms in any order, while
            # quoting keeps FTS5 syntax characters harmless.
            match_expr = " AND ".join(
                '"' + t.replace('"', '""') + '"' for t in words)
            with _LOCK:
                conn = sqlite3.connect(str(_db_path()))
                try:
                    try:
                        rows = conn.execute(
                            "SELECT dataset, doc_id, title, "
                            "snippet(facts, 3, '<<', '>>', '...', 48), "
                            "bm25(facts) AS rank FROM facts "
                            "WHERE facts MATCH ? ORDER BY rank LIMIT ?",
                            (match_expr, n)).fetchall()
                    except sqlite3.OperationalError as exc:
                        return {"error": f"search query rejected by FTS5: {exc}",
                                "query": query}
                finally:
                    conn.close()
            doc_by_key = {(d["dataset"], d["doc_id"]): d for d in _all_docs()}
            for ds, doc_id, title, snippet, rank in rows:
                doc = doc_by_key.get((ds, doc_id), {"title": title, "text": ""})
                hits.append({"dataset": ds, "doc_id": doc_id, "title": title,
                             "snippet": snippet,
                             "matched_field": _matched_field(doc, terms),
                             "rank": rank})
        else:
            hits = _python_search(terms, n)
        return {"query": query, "engine": engine, "hits": hits,
                "count": len(hits)}
    except Exception as exc:  # belt-and-braces: never raise
        return {"error": f"knowledge.ask failed: {exc}"}


# ---------------------------------------------------------------------------
# Single-item lookups
# ---------------------------------------------------------------------------
def _pick(args: dict, *keys):
    if isinstance(args, dict):
        for key in keys:
            val = args.get(key)
            if val is not None:
                return val
    return None


def _fuzzy(candidates: list[str], query: str, n: int = 3,
           cutoff: float = 0.55) -> list[str]:
    return difflib.get_close_matches(query, candidates, n=n, cutoff=cutoff)


def knowledge_element_handler(args: dict) -> dict:
    """knowledge.element {symbol|name|number|q} — exact lookup of one element."""
    try:
        raw = _pick(args or {}, "symbol", "name", "number", "q")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return {"error": "provide one of: symbol, name, number (e.g. "
                             "{'symbol': 'Au'}, {'name': 'gold'}, "
                             "{'number': 79})"}
        elements = _load("elements")
        # Atomic number: int or digit string.
        num = None
        if isinstance(raw, int):
            num = raw
        elif isinstance(raw, str) and raw.strip().lstrip("+-").isdigit():
            num = int(raw.strip())
        if num is not None:
            for el in elements:
                if el["number"] == num:
                    return {"element": el}
            return {"error": f"no element with atomic number {num} "
                             "(valid range 1-118)"}
        s = str(raw).strip()
        # Symbol: 1-2 letters, case-insensitive.
        if len(s) <= 2 and s.isalpha():
            want = s[0].upper() + s[1:].lower() if len(s) == 2 else s.upper()
            for el in elements:
                if el["symbol"] == want:
                    return {"element": el}
        # Name: case-insensitive.
        low = s.lower()
        for el in elements:
            if el["name"].lower() == low:
                return {"element": el}
        pool = [el["name"] for el in elements] + [el["symbol"] for el in elements]
        suggestions = _fuzzy(pool, s)
        return {"error": f"no element matching '{s}'",
                "suggestions": suggestions}
    except Exception as exc:
        return {"error": f"knowledge.element failed: {exc}"}


def knowledge_country_handler(args: dict) -> dict:
    """knowledge.country {name|q} — exact/fuzzy lookup of one country."""
    try:
        raw = _pick(args or {}, "name", "q")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return {"error": "provide a country name, e.g. {'name': 'France'}"}
        name = str(raw).strip()
        countries = _load("countries")
        low = name.lower()
        for c in countries:
            if c["name"].lower() == low:
                return {"country": c}
        names = [c["name"] for c in countries]
        suggestions = _fuzzy([n.lower() for n in names], low)
        if suggestions:
            best = suggestions[0]
            ratio = difflib.SequenceMatcher(None, low, best).ratio()
            match = next(c for c in countries if c["name"].lower() == best)
            if ratio >= 0.8:
                return {"country": match, "matched": match["name"],
                        "note": "fuzzy match — no exact country name found"}
            pretty = [next(c["name"] for c in countries
                            if c["name"].lower() == s) for s in suggestions]
            return {"error": f"no country named '{name}'",
                    "suggestions": pretty}
        return {"error": f"no country named '{name}'", "suggestions": []}
    except Exception as exc:
        return {"error": f"knowledge.country failed: {exc}"}


def knowledge_constant_handler(args: dict) -> dict:
    """knowledge.constant {name|symbol|q} — lookup by name or symbol."""
    try:
        raw = _pick(args or {}, "name", "symbol", "q")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return {"error": "provide a constant name or symbol, e.g. "
                             "{'name': 'speed of light'} or {'symbol': 'c'}"}
        q = str(raw).strip()
        constants = _load("constants")
        # Exact symbol first (case-sensitive), then case-insensitive.
        for c in constants:
            if c["symbol"] == q:
                return {"constant": c}
        low = q.lower()
        for c in constants:
            if c["symbol"].lower() == low:
                return {"constant": c}
        for c in constants:
            if c["name"].lower() == low:
                return {"constant": c}
        # Substring containment: "speed of light" -> "Speed of light in vacuum".
        subs = [c for c in constants
                if low in c["name"].lower() or low in c["symbol"].lower()]
        if subs:
            best = max(subs, key=lambda c: difflib.SequenceMatcher(
                None, low, c["name"].lower()).ratio())
            return {"constant": best, "matched": best["name"],
                    "note": "substring match — no exact constant found"}
        pool = [c["name"] for c in constants] + [c["symbol"] for c in constants]
        suggestions = _fuzzy([p.lower() for p in pool], low)
        if suggestions:
            best = suggestions[0]
            ratio = difflib.SequenceMatcher(None, low, best).ratio()
            match = next(c for c in constants
                         if c["name"].lower() == best
                         or c["symbol"].lower() == best)
            if ratio >= 0.8:
                return {"constant": match, "matched": match["name"],
                        "note": "fuzzy match — no exact constant found"}
            pretty = [next(c["name"] for c in constants
                            if c["name"].lower() == s
                            or c["symbol"].lower() == s) for s in suggestions]
            return {"error": f"no constant matching '{q}'",
                    "suggestions": pretty}
        return {"error": f"no constant matching '{q}'", "suggestions": []}
    except Exception as exc:
        return {"error": f"knowledge.constant failed: {exc}"}


def knowledge_timeline_handler(args: dict) -> dict:
    """knowledge.timeline {from_year, to_year} — events in range, chronological.

    Years are integers; BCE years are negative (e.g. -490 for 490 BCE).
    """
    try:
        args = args or {}
        try:
            from_year = int(args["from_year"])
            to_year = int(args["to_year"])
        except (KeyError, TypeError, ValueError):
            return {"error": "from_year and to_year must both be integers "
                             "(BCE years are negative, e.g. -490)"}
        if from_year > to_year:
            return {"error": "from_year must be <= to_year"}
        events = [e for e in _load("timeline")
                  if from_year <= e["year"] <= to_year]
        events.sort(key=lambda e: e["year"])
        out = [{"year": e["year"], "year_label": _year_label(e["year"]),
                "circa": e["circa"], "title": e["title"],
                "summary": e["summary"]} for e in events]
        return {"from_year": from_year, "to_year": to_year,
                "count": len(out), "events": out}
    except Exception as exc:
        return {"error": f"knowledge.timeline failed: {exc}"}


def knowledge_stats_handler(args: dict) -> dict:  # noqa: ARG001
    """knowledge.stats {} — honest counts per dataset + index status."""
    try:
        counts = {d: len(_load(d)) for d in DATASETS}
        info = _ensure_index()
        return {"datasets": counts,
                "total_documents": sum(counts.values()),
                "db_path": str(_db_path()),
                "fts5_available": _fts5_ok(),
                "index": {"engine": info["engine"],
                          "built_at": info["built_at"],
                          "documents_indexed": info["documents"],
                          "rebuilt_this_call": info["rebuilt"]},
                "note": ("Counts are the exact number of entries in the "
                         "bundled JSONs — nothing is inflated.")}
    except Exception as exc:
        return {"error": f"knowledge.stats failed: {exc}"}


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
TOOL_DEFS = [
    {"name": "knowledge.ask",
     "description": ("Ranked full-text search across ALL offline knowledge "
                     "datasets (elements, countries, constants, timeline, "
                     "science facts). Builds a local sqlite FTS5 index on "
                     "first use (rebuilt when the JSONs change); falls back "
                     "to pure-Python substring ranking if FTS5 is unavailable "
                     "and says so. Returns ranked hits with dataset, title, "
                     "snippet and matched field. Fully offline."),
     "handler": knowledge_ask_handler, "risk": "low", "needs_network": False,
     "schema": {"query": "string", "n?": "int"}},
    {"name": "knowledge.element",
     "description": ("Exact lookup of one chemical element by symbol, name, "
                     "or atomic number (e.g. {'symbol': 'Au'}, "
                     "{'name': 'gold'}, {'number': 79}). Returns symbol, "
                     "name, atomic number, atomic mass, group and period. "
                     "Fuzzy suggestions when nothing matches. Fully offline."),
     "handler": knowledge_element_handler, "risk": "low", "needs_network": False,
     "schema": {"symbol?": "string", "name?": "string", "number?": "int",
                "q?": "string"}},
    {"name": "knowledge.country",
     "description": ("Lookup of one country by name (exact, case-insensitive; "
                     "fuzzy fallback with suggestions). Returns name, capital, "
                     "ISO currency code and continent. Fully offline."),
     "handler": knowledge_country_handler, "risk": "low", "needs_network": False,
     "schema": {"name": "string", "q?": "string"}},
    {"name": "knowledge.constant",
     "description": ("Lookup of one physical or mathematical constant by name "
                     "or symbol (e.g. {'name': 'speed of light'} or "
                     "{'symbol': 'c'}). Returns name, symbol, value and unit. "
                     "Fully offline."),
     "handler": knowledge_constant_handler, "risk": "low", "needs_network": False,
     "schema": {"name?": "string", "symbol?": "string", "q?": "string"}},
    {"name": "knowledge.timeline",
     "description": ("World-history events within an inclusive year range, "
                     "returned in chronological order. Years are integers; "
                     "BCE years are negative (e.g. from_year=-500, "
                     "to_year=1500). Approximate dates are flagged circa. "
                     "Fully offline."),
     "handler": knowledge_timeline_handler, "risk": "low", "needs_network": False,
     "schema": {"from_year": "int", "to_year": "int"}},
    {"name": "knowledge.stats",
     "description": ("Honest stats for the knowledge pack: exact entry counts "
                     "per dataset, the search-index path, engine and build "
                     "status, and whether FTS5 is available. No inflated "
                     "claims — counts are the JSON entry counts."),
     "handler": knowledge_stats_handler, "risk": "low", "needs_network": False,
     "schema": {}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(knowledge_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "knowledge.ask": ("low", False),
    "knowledge.element": ("low", False),
    "knowledge.country": ("low", False),
    "knowledge.constant": ("low", False),
    "knowledge.timeline": ("low", False),
    "knowledge.stats": ("low", False),
}


def register(reg) -> None:
    """Wire the six Knowledge pack tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))
