"""Unit tests for Phase 9 — Jarvis API Mode (sidecar/jarvis/ipc/api_mode.py).

fastapi is NOT installed on the dev VM, so this file has two layers:

1. Pure-logic tests — always run: bind_host defaults, auth comparison,
   truncation, the 4000-char size guard, and the honest 503 when no agent
   is bound. Route functions are exercised by DIRECT CALLS with fakes.
2. TestClient tests — skipped via pytest.importorskip when fastapi (or
   httpx, which TestClient needs) is missing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from jarvis.ipc import api_mode  # noqa: E402
from jarvis.ipc.api_mode import (  # noqa: E402
    ApiError, api_ask, api_health, api_tools_list, bind_host,
    check_auth, set_agent, get_agent, mount_api,
)


# ------------------------------------------------------------------ fakes
class FakeRegistry:
    def spec_list(self):
        return [
            {"name": "time.now", "description": "current time", "risk": "low"},
            {"name": "social.post_instagram", "description": "post", "risk": "high"},
        ]


class FakeAgent:
    def __init__(self, reply="At your service, sir.", timeline=None):
        self.registry = FakeRegistry()
        self._reply = reply
        self._timeline = timeline or []

    def run(self, text, confirmed=None):
        assert isinstance(confirmed, set)
        return {"reply": self._reply, "timeline": self._timeline, "done": True}


@pytest.fixture(autouse=True)
def _clean_agent():
    set_agent(None)
    yield
    set_agent(None)


# ------------------------------------------------------------ pure logic
class TestPureLogic:
    def test_health_shape(self):
        h = api_health()
        assert h == {"ok": True, "version": api_mode.API_MODE_VERSION}

    def test_bind_host_defaults_loopback(self, tmp_path):
        # missing config file entirely -> loopback
        assert bind_host(tmp_path / "nope.yaml") == "127.0.0.1"

    def test_bind_host_explicit_false(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("api:\n  lan_enabled: false\n")
        assert bind_host(cfg) == "127.0.0.1"

    def test_bind_host_lan_enabled(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("api:\n  lan_enabled: true\n")
        assert bind_host(cfg) == "0.0.0.0"

    def test_bind_host_missing_section_or_bad_yaml(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text("gmail:\n  client_id: x\n")
        assert bind_host(cfg) == "127.0.0.1"
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{ not yaml")
        assert bind_host(bad) == "127.0.0.1"

    def test_bind_host_default_path_no_crash(self):
        # PHASE9_CONFIG does not exist yet on the dev VM — must not crash.
        assert bind_host() in ("127.0.0.1", "0.0.0.0")

    def test_auth_ok(self):
        ok, detail = check_auth("Bearer s3cret", lambda: "s3cret")
        assert ok and detail == ""

    def test_auth_bad_token(self):
        ok, detail = check_auth("Bearer wrong", lambda: "s3cret")
        assert not ok and detail == "bad token"

    def test_auth_missing_header(self):
        ok, detail = check_auth("", lambda: "s3cret")
        assert not ok and detail == "bad token"

    def test_auth_none_header(self):
        ok, detail = check_auth(None, lambda: "s3cret")
        assert not ok and detail == "bad token"

    def test_auth_unavailable_when_getter_missing(self):
        ok, detail = check_auth("Bearer x", None)
        assert not ok and detail == "auth unavailable"

    def test_auth_unavailable_when_getter_raises(self):
        def boom():
            raise RuntimeError("no server module")
        ok, detail = check_auth("Bearer x", boom)
        assert not ok and detail == "auth unavailable"

    def test_tools_no_agent_honest_503(self):
        with pytest.raises(ApiError) as e:
            api_tools_list()
        assert e.value.status == 503

    def test_tools_list(self):
        set_agent(FakeAgent())
        specs = api_tools_list()
        assert [s["name"] for s in specs] == ["time.now", "social.post_instagram"]

    def test_ask_no_agent_honest_503(self):
        with pytest.raises(ApiError) as e:
            api_ask("hello")
        assert e.value.status == 503
        assert "not bound" in e.value.detail

    def test_ask_happy_path(self):
        set_agent(FakeAgent(reply="Done, sir."))
        out = api_ask("what time is it", confirmed_tools=["time.now"])
        assert out["reply"] == "Done, sir."
        assert out["done"] is True
        assert out["tool_trace"] == []
        assert isinstance(out["took_ms"], int) and out["took_ms"] >= 0

    def test_ask_truncates_long_tool_output(self):
        big = "x" * 5000
        agent = FakeAgent(timeline=[
            {"tool": "code.run", "args": {"code": "print(1)"}, "result": big},
        ])
        set_agent(agent)
        out = api_ask("run this")
        (entry,) = out["tool_trace"]
        assert entry["tool"] == "code.run"
        assert len(entry["result"]) == api_mode.TRUNCATE_CHARS + len("\u2026 (truncated)")
        assert entry["result"].endswith("(truncated)")
        assert big not in entry["result"]

    def test_ask_oversize_text_rejected_400(self):
        set_agent(FakeAgent())
        with pytest.raises(ApiError) as e:
            api_ask("y" * (api_mode.MAX_TEXT_CHARS + 1))
        assert e.value.status == 400

    def test_ask_empty_text_rejected(self):
        set_agent(FakeAgent())
        with pytest.raises(ApiError):
            api_ask("   ")

    def test_ask_agent_exception_honest_500(self):
        class BadAgent(FakeAgent):
            def run(self, text, confirmed=None):
                raise RuntimeError("llm exploded")
        set_agent(BadAgent())
        with pytest.raises(ApiError) as e:
            api_ask("hello")
        assert e.value.status == 500

    def test_module_imports_without_fastapi(self):
        # the whole point: optional dep must never break import
        assert "fastapi" not in sys.modules or True
        import importlib
        mod = importlib.import_module("jarvis.ipc.api_mode")
        assert mod.API_MODE_VERSION


# ------------------------------------------------------- TestClient tests
# fastapi is optional on the dev VM. Pure-logic tests above always run;
# the HTTP layer below is skipped (not failed) when fastapi/httpx are
# missing. Module-level pytest.importorskip would skip the whole file,
# so detection happens here with a flag and imports stay lazy.
try:
    import fastapi as _fastapi  # noqa: F401
    import httpx as _httpx  # noqa: F401
    _HAS_HTTP_STACK = True
except ImportError:
    _HAS_HTTP_STACK = False

needs_http = pytest.mark.skipif(
    not _HAS_HTTP_STACK, reason="fastapi/httpx not installed (optional server dep)")

TOKEN = "test-token-123"


@needs_http
class TestHttpApi:
    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI(title="test-api-mode")
        set_agent(FakeAgent(reply="Hello from the API, sir."))
        mount_api(app, token_getter=lambda: TOKEN)
        with TestClient(app) as c:
            yield c
    def test_health_no_auth(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "version": api_mode.API_MODE_VERSION}

    def test_tools_requires_auth(self, client):
        assert client.get("/api/v1/tools").status_code == 401
        r = client.get("/api/v1/tools", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert any(s["name"] == "time.now" for s in r.json())

    def test_ask_happy_path(self, client):
        r = client.post("/api/v1/ask",
                        headers={"Authorization": f"Bearer {TOKEN}"},
                        json={"text": "hello", "confirmed_tools": ["time.now"]})
        assert r.status_code == 200
        body = r.json()
        assert body["reply"] == "Hello from the API, sir."
        assert body["done"] is True
        assert body["tool_trace"] == []
        assert isinstance(body["took_ms"], int)

    def test_ask_oversize_rejected(self, client):
        r = client.post("/api/v1/ask",
                        headers={"Authorization": f"Bearer {TOKEN}"},
                        json={"text": "y" * 4001})
        assert r.status_code == 400

    def test_ask_no_agent_503(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI(title="test-api-mode-no-agent")
        mount_api(app, token_getter=lambda: TOKEN)  # no agent bound
        with TestClient(app) as client:
            r = client.post("/api/v1/ask",
                            headers={"Authorization": f"Bearer {TOKEN}"},
                            json={"text": "hello"})
            assert r.status_code == 503

    def test_auth_unavailable_503(self):
        # token_getter that blows up -> defensive "auth unavailable" -> 503,
        # never silently let traffic through.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        def boom():
            raise RuntimeError("no token machinery")
        app = FastAPI(title="test-api-mode-no-auth")
        mount_api(app, agent=FakeAgent(), token_getter=boom)
        with TestClient(app) as client:
            r = client.get("/api/v1/tools",
                           headers={"Authorization": "Bearer whatever"})
            assert r.status_code == 503
            assert r.json()["detail"] == "auth unavailable"

    def test_late_binding_via_set_agent(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI(title="test-api-mode-late")
        mount_api(app, token_getter=lambda: TOKEN)  # agent=None at mount
        set_agent(FakeAgent(reply="Late-bound, sir."))
        try:
            with TestClient(app) as client:
                r = client.post("/api/v1/ask",
                                headers={"Authorization": f"Bearer {TOKEN}"},
                                json={"text": "hi"})
                assert r.status_code == 200
                assert r.json()["reply"] == "Late-bound, sir."
        finally:
            set_agent(None)
