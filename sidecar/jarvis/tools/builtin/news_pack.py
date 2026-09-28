"""News Tool Pack (Phase 10): RSS headlines + digest.

feedparser-gated: ``pip install feedparser`` when missing, else an honest
error. Only fetches URLs you give or one of the built-in presets
(BBC World, Hacker News, The Guardian World). needs_network=True.
Outbound hosts are gated by jarvis/security/egress.py — unallowlisted
feeds return an honest blocked error, never fetched.

No invented headlines: every item comes from the feed response itself.
"""
from __future__ import annotations

import urllib.parse

_FEEDPARSER_HINT = ("feedparser is not installed. Install it with: "
                    "pip install feedparser")

PRESETS = {
    "bbc_world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "hacker_news": "https://hnrss.org/frontpage",
    "guardian_world": "https://www.theguardian.com/world/rss",
}

_ITEM_CAP = 30
_DESC_CAP = 400


# ------------------------------------------------------------ helpers
def _feedparser():
    try:
        import feedparser
        return feedparser
    except ImportError:
        return None


def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _resolve_feed(value) -> tuple[str | None, str | None]:
    """Map preset name or URL -> (url, error)."""
    raw = str(value or "").strip()
    if not raw:
        return None, (f"feed is required: a preset ({', '.join(sorted(PRESETS))}) "
                      "or a feed URL")
    if raw in PRESETS:
        return PRESETS[raw], None
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return None, (f"unknown preset '{raw}'. Presets: "
                      f"{', '.join(sorted(PRESETS))}; or pass a full http(s) URL")
    return raw, None


def _egress_ok(tool: str, url: str) -> tuple[bool, str | None]:
    try:
        from ...security import egress
    except ImportError:
        return True, None  # outside the sidecar: skip egress gate
    host = urllib.parse.urlparse(url).hostname or ""
    if not egress.check(tool, host):
        return False, (f"egress blocked: {host} is not allowlisted for "
                       f"{tool}; add it to ALLOWLIST in "
                       "jarvis/security/egress.py")
    return True, None


def _fetch(tool: str, url: str, limit: int):
    fp = _feedparser()
    if fp is None:
        return {"error": _FEEDPARSER_HINT}
    ok, err = _egress_ok(tool, url)
    if not ok:
        return {"error": err}
    try:
        feed = fp.parse(url)
    except Exception as e:
        return {"error": f"could not fetch/parse feed {url}: "
                         f"{type(e).__name__}: {e}"}
    if getattr(feed, "bozo", 0) and not getattr(feed, "entries", None):
        exc = getattr(feed, "bozo_exception", None)
        return {"error": f"feed unreachable or invalid: {url}"
                         + (f" ({exc})" if exc else "")}
    entries = getattr(feed, "entries", []) or []
    items = []
    for e in entries[:limit]:
        link = getattr(e, "link", "") or ""
        title = (getattr(e, "title", "") or "").strip()
        published = getattr(e, "published", "") or getattr(e, "updated", "") or ""
        desc = (getattr(e, "summary", "") or "").strip()
        if len(desc) > _DESC_CAP:
            desc = desc[:_DESC_CAP] + "..."
        items.append({"title": title, "link": link,
                      "published": published, "summary": desc})
    src = getattr(feed.feed, "title", "") if hasattr(feed, "feed") else ""
    return {"feed_url": url, "source": src, "items": items,
            "count": len(items)}


# ------------------------------------------------ news.headlines
def news_headlines(args: dict) -> dict:
    """Latest headlines (titles + links) from a feed or preset."""
    try:
        url, err = _resolve_feed(args.get("feed"))
        if err:
            return {"error": err}
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            return {"error": "limit must be an integer"}
        limit = max(1, min(limit, _ITEM_CAP))
        res = _fetch("news.headlines", url, limit)
        if "error" in res:
            return res
        return {**res,
                "headlines": [{"title": i["title"], "link": i["link"]}
                              for i in res["items"]]}
    except Exception as e:
        return _fail("news.headlines", e)


# --------------------------------------------------- news.digest
def news_digest(args: dict) -> dict:
    """Digest of N items: titles, links, published dates, short summaries."""
    try:
        url, err = _resolve_feed(args.get("feed"))
        if err:
            return {"error": err}
        try:
            count = int(args.get("count", 5))
        except (TypeError, ValueError):
            return {"error": "count must be an integer"}
        count = max(1, min(count, _ITEM_CAP))
        res = _fetch("news.digest", url, count)
        if "error" in res:
            return res
        digest = [{"n": k + 1, "title": i["title"], "link": i["link"],
                   "published": i["published"], "summary": i["summary"]}
                  for k, i in enumerate(res["items"])]
        return {**res, "digest": digest}
    except Exception as e:
        return _fail("news.digest", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "news.headlines",
     "description": "Latest headlines (titles + links) from an RSS feed or "
                    "preset (bbc_world, hacker_news, guardian_world).",
     "handler": news_headlines, "risk": "low", "needs_network": True,
     "schema": {"feed": "string", "limit": "int?"}},
    {"name": "news.digest",
     "description": "Digest of N feed items: title, link, published, summary.",
     "handler": news_digest, "risk": "low", "needs_network": True,
     "schema": {"feed": "string", "count": "int?"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(news_pack.RISK_TABLE_ADDITIONS)
# Network egress note for the parent: news tools also need entries like
# "news.headlines": ["feeds.bbci.co.uk", "hnrss.org", "www.theguardian.com"]
# in jarvis/security/egress.py ALLOWLIST for live fetching.
RISK_TABLE_ADDITIONS = {
    "news.headlines": ("low", True),
    "news.digest": ("low", True),
}


def register(reg) -> None:
    """Wire the two news tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "news_headlines", "news_digest", "PRESETS"]
