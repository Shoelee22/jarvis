"""Unit tests for the Phase 6 / Pack 2 Web Tool Pack. ALL OFFLINE:
urllib.request.urlopen is faked, shutil.which/subprocess are monkeypatched,
bookmarks use a real sqlite db in tmp_path."""
import re
import sqlite3
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, "sidecar")

from jarvis.tools.base import Registry  # noqa: E402
from jarvis.tools.builtin import web_pack  # noqa: E402


# ------------------------------------------------------------- fakes
class FakeResp:
    def __init__(self, body: bytes, url="https://example.com/", headers=None):
        self._body = body
        self._url = url
        self.headers = headers or {"Content-Length": str(len(body))}

    def read(self, n=-1):
        if n is None or n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


DDG_HTML = b"""<html><body>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x">Example A</a>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb&rut=y">Example B</a>
<a href="https://duckduckgo.com/?q=test">More results</a>
</body></html>"""

PAGE_HTML = b"""<html><head><title>Test Page</title>
<style>.x{color:red}</style><script>var evil=1;</script></head>
<body><h1>Hello world</h1><p>This is visible text about widgets.</p></body></html>"""

PRODUCT_HTML = ("<html><head><title>Gadget</title></head><body>"
                "<span>\u20b91,299</span><span>\u20b91,299</span>"
                "<div>$49.99</div><div>no price here</div></body></html>").encode()

RSS_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>RSS Title</title><link>https://r.example/1</link>
<pubDate>Sat, 27 Sep 2026 10:00:00 +0530</pubDate></item>
<item><title>Second</title><link>https://r.example/2</link></item>
</channel></rss>"""

ATOM_XML = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom Title</title><link href="https://a.example/1"/>
<updated>2026-09-27T10:00:00+05:30</updated></entry>
</feed>"""


def _offline(monkeypatch, body: bytes, url="https://example.com/"):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: FakeResp(body, url))


def _base_cfg(monkeypatch):
    monkeypatch.setattr(
        web_pack, "_load_config",
        lambda: {"web": {"fetch_timeout": 5, "max_bytes": 100000,
                         "search_count_default": 8},
                 "tools": {}})
    monkeypatch.setattr(web_pack.egress, "check", lambda tool, host: True)


# ------------------------------------------------------------- web.search
def test_web_search_parses_ddg_lite(monkeypatch):
    _base_cfg(monkeypatch)
    _offline(monkeypatch, DDG_HTML)
    r = web_pack.web_search({"query": "test", "count": 5})
    assert "error" not in r, r
    assert r["query"] == "test"
    urls = [x["url"] for x in r["results"]]
    assert urls == ["https://example.com/a", "https://example.org/b"]
    assert r["results"][0]["title"] == "Example A"


def test_web_search_egress_blocked(monkeypatch):
    _base_cfg(monkeypatch)
    monkeypatch.setattr(web_pack.egress, "check", lambda tool, host: False)
    r = web_pack.web_search({"query": "test"})
    assert "error" in r and "Settings" in r["error"]


def test_web_search_needs_query(monkeypatch):
    _base_cfg(monkeypatch)
    r = web_pack.web_search({})
    assert r["error"] == "query is required"


def test_web_search_offline(monkeypatch):
    _base_cfg(monkeypatch)
    def boom(*a, **k):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = web_pack.web_search({"query": "test"})
    assert r["error"].startswith("network unavailable")


# ------------------------------------------------------------- web.fetch
def test_web_fetch_title_and_text(monkeypatch):
    _base_cfg(monkeypatch)
    _offline(monkeypatch, PAGE_HTML)
    r = web_pack.web_fetch({"url": "https://example.com/"})
    assert "error" not in r, r
    assert r["title"] == "Test Page"
    assert "Hello world" in r["text"]
    assert "visible text about widgets" in r["text"]
    assert "evil" not in r["text"] and "color:red" not in r["text"]
    assert r["truncated"] is False


def test_web_fetch_rejects_non_http(monkeypatch):
    _base_cfg(monkeypatch)
    r = web_pack.web_fetch({"url": "ftp://example.com/x"})
    assert "error" in r


def test_web_fetch_never_raises(monkeypatch):
    _base_cfg(monkeypatch)
    r = web_pack.web_fetch({})  # missing url
    assert isinstance(r, dict) and "error" in r


# ------------------------------------------------------------- web.summarize
def test_web_summarize_ranks_by_query(monkeypatch):
    _base_cfg(monkeypatch)
    text = ("The sky is blue. A widget powers many gadgets. Rain falls in "
            "April. A widget factory opened in Surat. Birds migrate south.")
    r = web_pack.web_summarize({"text": text, "query": "widget factory", "n": 2})
    assert "error" not in r, r
    assert len(r["summary"]) == 2
    assert all("widget" in s.lower() for s in r["summary"])
    # original order preserved
    assert text.index(r["summary"][0]) < text.index(r["summary"][1])


def test_web_summarize_empty_text(monkeypatch):
    _base_cfg(monkeypatch)
    r = web_pack.web_summarize({"text": "", "query": "x"})
    assert r["summary"] == []


# ------------------------------------------------------------- price.check
def test_price_check_extracts_and_dedupes(monkeypatch):
    _base_cfg(monkeypatch)
    _offline(monkeypatch, PRODUCT_HTML)
    r = web_pack.price_check({"url": "https://shop.example/gadget"})
    assert "error" not in r, r
    assert "\u20b91,299" in r["prices"]
    assert "$49.99" in r["prices"]
    assert len(r["prices"]) == len(set(
        re.sub(r"[\s,]", "", p) for p in r["prices"]))  # deduped
    assert "heuristic" in r["note"]


# ------------------------------------------------------------- rss.read
def test_rss_read_rss2(monkeypatch):
    _base_cfg(monkeypatch)
    _offline(monkeypatch, RSS_XML)
    r = web_pack.rss_read({"url": "https://example.com/feed", "limit": 5})
    assert "error" not in r, r
    assert len(r["items"]) == 2
    assert r["items"][0]["title"] == "RSS Title"
    assert r["items"][0]["link"] == "https://r.example/1"
    assert "2026" in r["items"][0]["published"]


def test_rss_read_atom(monkeypatch):
    _base_cfg(monkeypatch)
    _offline(monkeypatch, ATOM_XML)
    r = web_pack.rss_read({"url": "https://example.com/atom"})
    assert "error" not in r, r
    assert r["items"][0]["title"] == "Atom Title"
    assert r["items"][0]["link"] == "https://a.example/1"
    assert "2026" in r["items"][0]["published"]


def test_rss_read_bad_xml(monkeypatch):
    _base_cfg(monkeypatch)
    _offline(monkeypatch, b"<html>not a feed</oops>")
    r = web_pack.rss_read({"url": "https://example.com/x"})
    assert "error" in r


# ------------------------------------------------------------- bookmarks
def test_bookmarks_save_and_list(monkeypatch, tmp_path):
    _base_cfg(monkeypatch)
    monkeypatch.setattr(web_pack, "DATA_DIR", tmp_path)
    r1 = web_pack.bookmarks_save(
        {"url": "https://example.com/a", "title": "A", "tags": ["news", "tech"]})
    assert "error" not in r1, r1
    assert isinstance(r1["id"], int)
    r2 = web_pack.bookmarks_save({"url": "https://example.com/b", "tags": "news"})
    assert r2["id"] != r1["id"]
    listed = web_pack.bookmarks_list({})
    assert len(listed["bookmarks"]) == 2
    assert listed["bookmarks"][0]["url"] == "https://example.com/b"  # newest first
    filtered = web_pack.bookmarks_list({"tag": "tech"})
    assert len(filtered["bookmarks"]) == 1
    assert filtered["bookmarks"][0]["id"] == r1["id"]
    # real sqlite file on disk
    assert (tmp_path / "bookmarks.db").exists()
    con = sqlite3.connect(tmp_path / "bookmarks.db")
    assert con.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 2
    con.close()


def test_bookmarks_save_needs_url(monkeypatch, tmp_path):
    _base_cfg(monkeypatch)
    monkeypatch.setattr(web_pack, "DATA_DIR", tmp_path)
    assert "error" in web_pack.bookmarks_save({})


# ------------------------------------------------------------- link.unshorten
class _FakeOpener:
    def __init__(self, recorder, final_url, hops):
        self._rec, self._final, self._hops = recorder, final_url, hops

    def open(self, req, timeout=None):
        self._rec.hops.extend(self._hops)
        return FakeResp(b"", self._final)


def test_link_unshorten_follows_hops(monkeypatch):
    _base_cfg(monkeypatch)
    hops = ["https://mid.example/x", "https://final.example/page"]
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda rec: _FakeOpener(rec, "https://final.example/page", hops))
    r = web_pack.link_unshorten({"url": "https://short.example/abc"})
    assert "error" not in r, r
    assert r["original"] == "https://short.example/abc"
    assert r["final"] == "https://final.example/page"
    assert r["hops"] == hops


# ------------------------------------------------------------- webshot.capture
def test_webshot_no_browser(monkeypatch):
    _base_cfg(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = web_pack.webshot_capture({"url": "https://example.com/"})
    assert r["error"] == "no headless browser found on this machine"


def test_webshot_with_browser(monkeypatch, tmp_path):
    _base_cfg(monkeypatch)
    monkeypatch.setattr(web_pack, "_output_dir", lambda: tmp_path)
    monkeypatch.setattr("shutil.which",
                        lambda name: "/fake/chrome" if name == "chrome" else None)

    class _Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        shot = [a for a in cmd if a.startswith("--screenshot=")][0].split("=", 1)[1]
        Path(shot).write_bytes(b"\x89PNG fake")
        return _Proc()

    monkeypatch.setattr("subprocess.run", fake_run)
    r = web_pack.webshot_capture({"url": "https://example.com/", "width": 800})
    assert "error" not in r, r
    assert Path(r["path"]).exists()
    assert r["width"] == 800


# ------------------------------------------------------------- registration
def test_register_adds_all_nine_tools(monkeypatch):
    _base_cfg(monkeypatch)
    reg = Registry()
    web_pack.register(reg)
    expected = {"web.search": ("medium", True), "web.fetch": ("low", True),
                "web.summarize": ("low", False), "price.check": ("medium", True),
                "rss.read": ("low", True), "bookmarks.save": ("low", False),
                "bookmarks.list": ("low", False),
                "link.unshorten": ("low", True),
                "webshot.capture": ("low", False)}
    for name, (risk, net) in expected.items():
        assert name in reg.tools, name
        assert reg.tools[name].risk == risk, name
        assert reg.tools[name].needs_network == net, name
    assert reg.tools["web.search"].handler is web_pack.web_search
