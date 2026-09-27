"""Web Tool Pack (Phase 6 / Pack 2): search, fetch, summarize, price check,
RSS, bookmarks, URL unshortening, and page screenshots.

Every handler takes a dict and returns a dict, and NEVER raises — errors come
back as {"error": ...} so the tool loop stays alive.

Wiring: this module is NOT imported by
jarvis.tools.builtin.__init__ (off-limits). Call web_pack.register(reg) from
the composition layer to add these tools; it REPLACES the net_tools.web_search
stub for "web.search".

Per-tool toggles come from tools_config.yaml under tools.<name>.enabled
(reusing creator._enabled). Web tunables (timeouts, size caps) come from the
"web" section of tools_config.yaml — see
packaging/config_fragments/web_pack.yaml for the documented fragment.
"""
from __future__ import annotations

import html as _html
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from ...config import DATA_DIR
from ...security import egress
from ..base import Registry, Tool
from .creator import _load_config, _enabled, _output_dir

_UA = "Mozilla/5.0 (compatible; JarvisSidecar/1.0; +https://localhost)"


# ------------------------------------------------------------- config
def _web_cfg() -> dict:
    """Tunables from tools_config.yaml 'web' section; sane defaults."""
    return _load_config().get("web", {}) or {}


def _fetch_timeout() -> int:
    return max(1, int(_web_cfg().get("fetch_timeout", 20)))


def _max_bytes() -> int:
    return max(1024, int(_web_cfg().get("max_bytes", 2_000_000)))


def _default_count() -> int:
    return max(1, int(_web_cfg().get("search_count_default", 8)))


def _get(url: str, timeout: int | None = None, max_bytes: int | None = None,
         method: str = "GET") -> tuple[bytes, str, bool]:
    """Fetch a URL. Returns (body_bytes_capped, final_url, truncated).

    Raises urllib.error.URLError / OSError / socket.timeout on network failure;
    callers translate these to {"error": "network unavailable: ..."}.
    """
    timeout = _fetch_timeout() if timeout is None else timeout
    max_bytes = _max_bytes() if max_bytes is None else max_bytes
    req = urllib.request.Request(url, headers={"User-Agent": _UA}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final = resp.geturl()
        try:
            declared = int(resp.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            declared = 0
        if declared > max_bytes * 4:  # clearly too big; don't even start
            return b"", final, True
        body = resp.read(max_bytes + 1)
    truncated = len(body) > max_bytes
    return body[:max_bytes], final, truncated


def _net_error(e: BaseException) -> dict:
    return {"error": f"network unavailable: {type(e).__name__}: {e}"}


def _http_only(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme in ("http", "https")


# ------------------------------------------------------------- HTML text
class _TextExtractor(HTMLParser):
    """Pull title + visible text out of HTML; skip script/style/noscript."""

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            self.text_parts.append(data)

    def title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.title_parts)).strip()

    def text(self) -> str:
        raw = " ".join(self.text_parts)
        return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\s*\n\s*", "\n", raw)).strip()


def _html_to_text(body: bytes) -> tuple[str, str]:
    """Decode HTML bytes (charset sniff, utf-8 fallback) -> (title, text)."""
    head = body[:4096].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?([\w-]+)', head, re.I)
    enc = (m.group(1) if m else "utf-8")
    try:
        text = body.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        text = body.decode("utf-8", errors="replace")
    ext = _TextExtractor()
    try:
        ext.feed(text)
    except Exception:
        pass
    return ext.title(), ext.text()


# ------------------------------------------------------------- web.search
_DDG_ANCHOR_RE = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _ddg_real_url(href: str) -> str | None:
    """Resolve a lite.duckduckgo.com result href to the real target URL."""
    href = _html.unescape(href)
    if "uddg=" in href:  # DDG redirect wrapper: uddg=<encoded target>
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        for val in q.get("uddg", []):
            if _http_only(val):
                return val
        return None
    if _http_only(href):
        host = urllib.parse.urlsplit(href).netloc.lower()
        if host and not host.endswith("duckduckgo.com"):
            return href
    return None


def web_search(args: dict) -> dict:
    """Real web search via DuckDuckGo Lite. Offline -> error dict."""
    blocked = _enabled("web.search")
    if blocked:
        return blocked
    if not egress.check("web.search", "lite.duckduckgo.com"):
        return {"error": "web search is disabled — enable it in Settings first"}
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        count = int(args.get("count", _default_count()))
    except (TypeError, ValueError):
        count = _default_count()
    count = max(1, min(count, 25))
    try:
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(
            "https://lite.duckduckgo.com/lite/",
            data=data, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_fetch_timeout()) as resp:
            body = resp.read(_max_bytes() + 1)
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        return _net_error(e)
    except Exception as e:
        return {"error": f"search failed: {type(e).__name__}: {e}"}
    results, seen = [], set()
    for href, inner in _DDG_ANCHOR_RE.findall(body.decode("utf-8", "replace")):
        url = _ddg_real_url(href)
        if not url or url in seen:
            continue
        title = re.sub(r"\s+", " ", _TAG_RE.sub("", inner)).strip()
        if not title:
            continue
        seen.add(url)
        results.append({"title": title, "url": url})
        if len(results) >= count:
            break
    return {"query": query, "results": results}


# ------------------------------------------------------------- web.fetch
def web_fetch(args: dict) -> dict:
    """Fetch a page and return its title + visible text (2MB cap)."""
    blocked = _enabled("web.fetch")
    if blocked:
        return blocked
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    if not _http_only(url):
        return {"error": "only http(s) URLs are supported"}
    try:
        body, final, truncated = _get(url)
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        return _net_error(e)
    except Exception as e:
        return {"error": f"fetch failed: {type(e).__name__}: {e}"}
    title, text = _html_to_text(body)
    return {"url": final, "title": title, "text": text, "truncated": truncated}


# ------------------------------------------------------------- web.summarize
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z0-9']+")


def web_summarize(args: dict) -> dict:
    """Extractive summarizer (pure stdlib, offline): score sentences by
    keyword overlap with the query, return the top n in original order."""
    text = (args.get("text") or "")
    query = (args.get("query") or "")
    try:
        n = int(args.get("n", 5))
    except (TypeError, ValueError):
        n = 5
    n = max(1, n)
    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return {"summary": [], "query": query}
    qwords = {w.lower() for w in _WORD.findall(query) if len(w) >= 3}
    scored = []
    for i, s in enumerate(sentences):
        swords = {w.lower() for w in _WORD.findall(s)}
        score = len(swords & qwords)
        # tiny bonus for early sentences so ties read naturally
        scored.append((score, -i / 1e6, i, s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = sorted(scored[:n], key=lambda t: t[2])
    return {"summary": [s for _, _, _, s in top], "query": query}


# ------------------------------------------------------------- price.check
_PRICE_RE = re.compile(r"""
    (?P<sym>[₹$€£])\s?(?P<amt>
        \d{1,3}(?:[,\s]\d{3})+(?:\.\d{1,2})?   # 1,299 / 12,34,567.00
      | \d+(?:\.\d{1,2})?                      # 999 / 49.99
    )
""", re.VERBOSE)


def price_check(args: dict) -> dict:
    """Heuristic price extraction from a product page. Honest about being
    heuristic — the note says to verify on the page."""
    blocked = _enabled("price.check")
    if blocked:
        return blocked
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    if not _http_only(url):
        return {"error": "only http(s) URLs are supported"}
    try:
        body, final, truncated = _get(url)
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        return _net_error(e)
    except Exception as e:
        return {"error": f"fetch failed: {type(e).__name__}: {e}"}
    page = _html.unescape(body.decode("utf-8", errors="replace"))
    prices, seen = [], set()
    for m in _PRICE_RE.finditer(page):
        candidate = f"{m.group('sym')}{m.group('amt')}"
        norm = re.sub(r"[\s,]", "", candidate)
        if norm not in seen:
            seen.add(norm)
            prices.append(candidate)
        if len(prices) >= 20:
            break
    return {"url": final, "prices": prices,
            "note": "heuristic extraction — verify on the page",
            "truncated": truncated}


# ------------------------------------------------------------- rss.read
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def rss_read(args: dict) -> dict:
    """Fetch and parse an RSS/Atom feed with xml.etree.ElementTree."""
    blocked = _enabled("rss.read")
    if blocked:
        return blocked
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    if not _http_only(url):
        return {"error": "only http(s) URLs are supported"}
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    try:
        body, final, truncated = _get(url)
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        return _net_error(e)
    except Exception as e:
        return {"error": f"fetch failed: {type(e).__name__}: {e}"}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return {"error": f"not a parseable feed: {e}"}
    except Exception as e:
        return {"error": f"feed parse failed: {type(e).__name__}: {e}"}
    items = []
    for node in root.iter():
        kind = _local(node.tag)
        if kind not in ("item", "entry"):
            continue
        fields = {}
        for child in node:
            name = _local(child.tag)
            if name in ("title", "link", "pubDate", "published", "updated"):
                fields[name] = (child.text or "").strip()
        # atom links are usually <link href="..."/> attributes
        for child in node:
            if _local(child.tag) == "link" and not fields.get("link"):
                fields["link"] = (child.attrib.get("href") or "").strip()
        items.append({
            "title": fields.get("title", ""),
            "link": fields.get("link", ""),
            "published": fields.get("pubDate") or fields.get("published")
                        or fields.get("updated") or "",
        })
        if len(items) >= limit:
            break
    return {"url": final, "items": items, "truncated": truncated}


# ------------------------------------------------------------- bookmarks
def _bookmarks_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "bookmarks.db")
    db.execute("""CREATE TABLE IF NOT EXISTS bookmarks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL, title TEXT, tags TEXT,
        created_at INTEGER NOT NULL)""")
    db.commit()
    return db


def _norm_tags(tags) -> str:
    if isinstance(tags, str):
        parts = [t.strip() for t in tags.split(",")]
    elif isinstance(tags, (list, tuple)):
        parts = [str(t).strip() for t in tags]
    else:
        parts = []
    return ",".join(p for p in parts if p)


def bookmarks_save(args: dict) -> dict:
    """Save a bookmark to DATA_DIR/bookmarks.db. Returns {id}."""
    blocked = _enabled("bookmarks.save")
    if blocked:
        return blocked
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    title = (args.get("title") or "").strip()
    tags = _norm_tags(args.get("tags"))
    try:
        db = _bookmarks_db()
        cur = db.execute(
            "INSERT INTO bookmarks(url, title, tags, created_at) VALUES(?,?,?,?)",
            (url, title, tags, int(time.time())))
        bid = cur.lastrowid
        db.commit()
        db.close()
    except Exception as e:
        return {"error": f"could not save bookmark: {type(e).__name__}: {e}"}
    return {"id": bid, "url": url}


def bookmarks_list(args: dict) -> dict:
    """List bookmarks, optionally filtered by tag."""
    blocked = _enabled("bookmarks.list")
    if blocked:
        return blocked
    tag = (args.get("tag") or "").strip()
    try:
        limit = int(args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        db = _bookmarks_db()
        if tag:
            rows = db.execute(
                "SELECT id, url, title, tags, created_at FROM bookmarks "
                "WHERE ',' || tags || ',' LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%,{tag},%", limit)).fetchall()
        else:
            rows = db.execute(
                "SELECT id, url, title, tags, created_at FROM bookmarks "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        db.close()
    except Exception as e:
        return {"error": f"could not list bookmarks: {type(e).__name__}: {e}"}
    return {"bookmarks": [
        {"id": r[0], "url": r[1], "title": r[2],
         "tags": [t for t in (r[3] or "").split(",") if t],
         "created_at": r[4]} for r in rows]}


# ------------------------------------------------------------- link.unshorten
class _HopRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.hops: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def link_unshorten(args: dict) -> dict:
    """Follow redirects (HEAD, falling back to GET) and report the chain."""
    blocked = _enabled("link.unshorten")
    if blocked:
        return blocked
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    if not _http_only(url):
        return {"error": "only http(s) URLs are supported"}
    recorder = _HopRecorder()
    opener = urllib.request.build_opener(recorder)
    final, hops = url, []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
        try:
            with opener.open(req, timeout=_fetch_timeout()) as resp:
                final = resp.geturl()
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308, 400, 403, 405):
                raise
            # HEAD not supported by the server — cheap GET instead
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with opener.open(req, timeout=_fetch_timeout()) as resp:
                resp.read(1)
                final = resp.geturl()
        hops = list(recorder.hops)
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        return _net_error(e)
    except Exception as e:
        return {"error": f"unshorten failed: {type(e).__name__}: {e}"}
    return {"original": url, "final": final, "hops": hops}


# ------------------------------------------------------------- webshot.capture
_BROWSERS = ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge")


def webshot_capture(args: dict) -> dict:
    """Screenshot a URL with a headless browser if one is installed."""
    blocked = _enabled("webshot.capture")
    if blocked:
        return blocked
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    if not _http_only(url):
        return {"error": "only http(s) URLs are supported"}
    try:
        width = max(320, min(int(args.get("width", 1280)), 3840))
    except (TypeError, ValueError):
        width = 1280
    exe = None
    for candidate in _BROWSERS:
        exe = shutil.which(candidate)
        if exe:
            break
    if not exe:
        return {"error": "no headless browser found on this machine"}
    outdir = _output_dir() / "webshots"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"shot-{int(time.time() * 1000)}.png"
    try:
        proc = subprocess.run(
            [exe, "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", f"--window-size={width},900",
             f"--screenshot={out}", url],
            capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"error": "screenshot timed out"}
    except Exception as e:
        return {"error": f"screenshot failed: {type(e).__name__}: {e}"}
    if proc.returncode != 0 or not out.exists():
        return {"error": f"screenshot failed: {proc.stderr.strip()[-300:] or 'unknown error'}"}
    return {"path": str(out), "url": url, "width": width}


# ------------------------------------------------------------- registry wiring
WEB_PACK_TOOLS: list[Tool] = [
    Tool("web.search", "Web search via DuckDuckGo Lite (opt-in).",
         {"query": "string", "count": "int?"}, web_search,
         risk="medium", needs_network=True),
    Tool("web.fetch", "Fetch a page; return title + visible text (2MB cap).",
         {"url": "string"}, web_fetch, risk="low", needs_network=True),
    Tool("web.summarize", "Extractive summary of text, ranked by query overlap.",
         {"text": "string", "query": "string", "n": "int?"}, web_summarize,
         risk="low"),
    Tool("price.check", "Heuristic price extraction from a product page.",
         {"url": "string"}, price_check, risk="medium", needs_network=True),
    Tool("rss.read", "Read an RSS/Atom feed.",
         {"url": "string", "limit": "int?"}, rss_read, risk="low",
         needs_network=True),
    Tool("bookmarks.save", "Save a bookmark to the local sqlite store.",
         {"url": "string", "title": "string?", "tags": "string|list?"},
         bookmarks_save, risk="low"),
    Tool("bookmarks.list", "List saved bookmarks, optionally by tag.",
         {"tag": "string?", "limit": "int?"}, bookmarks_list, risk="low"),
    Tool("link.unshorten", "Follow redirects; report the final URL and hops.",
         {"url": "string"}, link_unshorten, risk="low", needs_network=True),
    Tool("webshot.capture", "Screenshot a URL with a headless browser if present.",
         {"url": "string", "width": "int?"}, webshot_capture, risk="low"),
]


def register(reg: Registry) -> Registry:
    """Register the web pack on a Registry. Replaces the net_tools.web_search
    stub for 'web.search'. Returns the registry."""
    for tool in WEB_PACK_TOOLS:
        reg.register(tool)
    return reg
