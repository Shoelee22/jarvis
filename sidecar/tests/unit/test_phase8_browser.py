"""Unit tests for the Phase 8 browser control pack. ALL OFFLINE:
sys.modules['playwright'] is replaced with a recording fake; no real
browser is ever launched.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import browser_pack  # noqa: E402


# ------------------------------------------------------------- fakes
class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakePage:
    def __init__(self):
        self.calls: list = []
        self.url = "about:blank"
        self._text = ""
        self.closed = False

    def goto(self, url, timeout=None):
        self.calls.append(("goto", url, timeout))
        self.url = url
        return FakeResponse()

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append(("wait_for_load_state", state))

    def title(self):
        return "Fake Page Title"

    def inner_text(self, selector, timeout=None):
        return self._text

    def fill(self, selector, text, timeout=None):
        self.calls.append(("fill", selector, text))

    def click(self, selector, timeout=None):
        self.calls.append(("click", selector))

    def screenshot(self, path, timeout=None):
        self.calls.append(("screenshot", path))
        Path(path).write_bytes(b"\x89PNG-fake")

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pw):
        self._pw = pw
        self.page = FakePage()
        self.closed = False
        self.launch_kwargs = {}

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakePW:
    instance = None  # last created, for assertions

    def __init__(self):
        self.context = None
        self.stopped = False
        self.chromium = self

    def __call__(self):
        return self

    def start(self):
        FakePW.instance = self
        return self

    def launch_persistent_context(self, user_data_dir=None, headless=True):
        self.context = FakeContext(self)
        self.context.launch_kwargs = {"user_data_dir": user_data_dir,
                                      "headless": headless}
        return self.context

    def stop(self):
        self.stopped = True


def _install_fake_playwright(monkeypatch):
    fake = types.SimpleNamespace(
        sync_api=types.SimpleNamespace(sync_playwright=lambda: FakePW())
    )
    monkeypatch.setitem(sys.modules, "playwright", fake)
    return fake


# ------------------------------------------------------------- fixtures
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_session(monkeypatch, tmp_path):
    browser_pack._session = None
    FakePW.instance = None
    monkeypatch.setattr(browser_pack, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(browser_pack, "SHOTS_DIR", tmp_path / "shots")
    yield
    browser_pack._session = None


@pytest.fixture()
def pw(monkeypatch):
    return _install_fake_playwright(monkeypatch)


# ------------------------------------------------------------- URL validation
def test_open_rejects_ftp_scheme(pw):
    out = browser_pack.browser_open({"url": "ftp://x"})
    assert "error" in out and "http/https" in out["error"]
    assert FakePW.instance is None  # never launched a browser


def test_open_rejects_missing_and_empty_url(pw):
    assert "error" in browser_pack.browser_open({})
    assert "error" in browser_pack.browser_open({"url": "   "})


def test_open_rejects_non_bool_headless(pw):
    out = browser_pack.browser_open({"url": "https://example.com", "headless": "yes"})
    assert "error" in out


# ------------------------------------------------------------- open happy path
def test_open_returns_title_url_status(pw, tmp_path):
    out = browser_pack.browser_open({"url": "https://example.com"})
    assert out["title"] == "Fake Page Title"
    assert out["url"] == "https://example.com"
    assert out["status"] == 200
    assert (tmp_path / "profile").is_dir()


def test_open_reuses_existing_session(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    first = FakePW.instance
    browser_pack.browser_open({"url": "https://other.example/"})
    assert FakePW.instance is first  # no second browser launched


# ------------------------------------------------------------- no-session errors
def test_fill_click_read_shot_require_open_page(pw):
    assert "error" in browser_pack.browser_fill({"selector": "#a", "text": "x"})
    assert "error" in browser_pack.browser_click({"selector": "#a"})
    assert "error" in browser_pack.browser_read_text({})
    assert "error" in browser_pack.browser_shot({})


def test_fill_click_validate_args(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    assert "error" in browser_pack.browser_fill({"selector": "", "text": "x"})
    assert "error" in browser_pack.browser_fill({"selector": "#a"})
    assert "error" in browser_pack.browser_click({"selector": ""})


# ------------------------------------------------------------- read_text
def test_read_text_truncates(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    browser_pack._session["page"]._text = "z" * 20_000
    out = browser_pack.browser_read_text({"max_chars": 500})
    assert len(out["text"]) == 500
    assert out["truncated"] is True
    assert out["length"] == 20_000


def test_read_text_default_limit(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    browser_pack._session["page"]._text = "short"
    out = browser_pack.browser_read_text({})
    assert out["text"] == "short" and out["truncated"] is False


def test_read_text_rejects_bad_max_chars(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    assert "error" in browser_pack.browser_read_text({"max_chars": -1})


# ------------------------------------------------------------- fill / click
def test_fill_and_click_record_calls(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    page = browser_pack._session["page"]
    assert browser_pack.browser_fill({"selector": "#q", "text": "hello"})["ok"] is True
    assert browser_pack.browser_click({"selector": "#go"})["ok"] is True
    kinds = [c[0] for c in page.calls]
    assert ("fill", "#q", "hello") in page.calls
    assert ("click", "#go") in page.calls


def test_page_errors_become_error_dicts(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    page = browser_pack._session["page"]

    def boom(*a, **k):
        raise RuntimeError("selector gone")

    page.fill = boom
    out = browser_pack.browser_fill({"selector": "#q", "text": "x"})
    assert out == {"error": "RuntimeError: selector gone"}


# ------------------------------------------------------------- shot
def test_shot_writes_file_via_fake(pw, tmp_path):
    browser_pack.browser_open({"url": "https://example.com"})
    target = tmp_path / "custom.png"
    out = browser_pack.browser_shot({"path": str(target)})
    assert out["path"] == str(target)
    assert target.exists() and target.read_bytes().startswith(b"\x89PNG")


def test_shot_default_path_under_shots_dir(pw, tmp_path):
    browser_pack.browser_open({"url": "https://example.com"})
    out = browser_pack.browser_shot({})
    p = Path(out["path"])
    assert p.parent == tmp_path / "shots"
    assert p.suffix == ".png" and p.exists()


# ------------------------------------------------------------- close
def test_close_releases_everything(pw):
    browser_pack.browser_open({"url": "https://example.com"})
    pw_inst = FakePW.instance
    out = browser_pack.browser_close({})
    assert out == {"closed": True}
    assert browser_pack._session is None
    assert pw_inst.context.closed is True
    assert pw_inst.stopped is True


def test_close_is_safe_with_no_session(pw):
    assert browser_pack.browser_close({}) == {"closed": True}


def test_close_survives_partial_failures(pw):
    browser_pack.browser_open({"url": "https://example.com"})

    def boom():
        raise OSError("already dead")

    browser_pack._session["context"].close = boom
    out = browser_pack.browser_close({})
    assert out["closed"] is True
    assert browser_pack._session is None  # slot released despite the failure


# ------------------------------------------------------------- TOOL_DEFS metadata
def test_tool_defs_count_and_risks():
    defs = browser_pack.TOOL_DEFS
    assert len(defs) == 6
    by_name = {d["name"]: d for d in defs}
    assert set(by_name) == {"browser.open", "browser.read_text", "browser.fill",
                            "browser.click", "browser.shot", "browser.close"}
    assert by_name["browser.fill"]["risk"] == "medium"
    assert by_name["browser.click"]["risk"] == "medium"
    for name in ("browser.open", "browser.read_text", "browser.shot", "browser.close"):
        assert by_name[name]["risk"] == "low", name
    assert all(d["needs_network"] is True for d in defs)
    assert all(callable(d["handler"]) and d["description"] for d in defs)


# ------------------------------------------------------------- missing playwright
def test_missing_playwright_every_tool_errors(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    handlers = [browser_pack.browser_open, browser_pack.browser_read_text,
                browser_pack.browser_fill, browser_pack.browser_click,
                browser_pack.browser_shot]
    args = [{"url": "https://example.com"}, {}, {"selector": "#a", "text": "x"},
            {"selector": "#a"}, {}]
    for handler, arg in zip(handlers, args):
        out = handler(arg)
        assert out["error"].startswith("playwright not installed"), handler.__name__
    # close() needs no playwright: it must stay robust, not error
    assert browser_pack.browser_close({}) == {"closed": True}
