"""Phase 9 Tool Forging pack tests (offline).

All forge/test/list/retire tests run against a tmp forged dir via the
JARVIS_FORGED_DIR env override, so the real forged/ dir is never polluted.
Network is mocked: forge_pack.p_http_get is monkeypatched per-test.
"""
from __future__ import annotations

import json

import pytest

from jarvis.tools.builtin import forge_pack


@pytest.fixture(autouse=True)
def forged_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_FORGED_DIR", str(tmp_path / "forged"))
    yield


def _forge_titles():
    return forge_pack.forge({
        "name": "fetch_titles",
        "description": "Fetch a web page and extract its titles.",
        "intent": "fetch website url http and extract titles",
    })


# ---------------------------------------------------------------------------
# forge validation
# ---------------------------------------------------------------------------

def test_forge_rejects_bad_names():
    for bad in ["Bad-Name!", "ab", "1abc", "a b", "x" * 40, ""]:
        out = forge_pack.forge({"name": bad, "description": "d",
                                "intent": "fetch url"})
        assert "error" in out, bad


def test_forge_rejects_unmatched_intent():
    out = forge_pack.forge({"name": "symphony_writer", "description": "d",
                            "intent": "compose a symphony in c minor with feeling"})
    assert "error" in out and "safe primitive" in out["error"]


def test_forge_picks_primitives_in_pipeline_order():
    out = _forge_titles()
    assert out["name"] == "fetch_titles"
    assert out["primitives"] == ["p_http_get", "p_regex_extract"]
    assert "inactive" in out["note"].lower()
    # generated file exists and defines the contract
    src = open(out["path"], encoding="utf-8").read()
    assert "def run(args)" in src
    assert "DESCRIPTION" in src
    assert "exec(" not in src and "eval(" not in src
    # manifest entry is inactive
    listed = forge_pack.list_forged({})
    assert listed["count"] == 1
    assert listed["tools"][0]["active"] is False


def test_forge_rejects_collision():
    first = _forge_titles()
    assert "error" not in first
    dup = _forge_titles()
    assert "error" in dup and "already exists" in dup["error"]


# ---------------------------------------------------------------------------
# test_forged + activation
# ---------------------------------------------------------------------------

def test_test_forged_activates(monkeypatch):
    _forge_titles()
    html = "<html><head><title>Alpha</title><title>Beta</title></head></html>"

    def fake_get(args):
        assert args["url"] == "http://example.com"
        return {"ok": True, "text": html, "bytes": len(html),
                "truncated": False, "url": args["url"]}

    monkeypatch.setattr(forge_pack, "p_http_get", fake_get)
    out = forge_pack.test_forged({"name": "fetch_titles",
                                  "example_input": {"url": "http://example.com"},
                                  "activate": True})
    assert out["ok"] is True
    matches = out["output"]["result"]["matches"]
    assert "Alpha" in matches and "Beta" in matches
    assert "ACTIVE" in out.get("note", "")
    # manifest flipped to active
    assert forge_pack.list_forged({})["tools"][0]["active"] is True


def test_test_forged_stays_inactive_without_activate(monkeypatch):
    _forge_titles()
    monkeypatch.setattr(forge_pack, "p_http_get",
                        lambda a: {"ok": True, "text": "<title>T</title>",
                                   "bytes": 18, "truncated": False,
                                   "url": a.get("url")})
    out = forge_pack.test_forged({"name": "fetch_titles",
                                  "example_input": {"url": "http://x"}})
    assert out["ok"] is True
    assert forge_pack.list_forged({})["tools"][0]["active"] is False


def test_test_forged_unknown_name():
    out = forge_pack.test_forged({"name": "nope_tool", "example_input": {}})
    assert "error" in out


def test_test_forged_honest_failure(monkeypatch):
    _forge_titles()
    monkeypatch.setattr(forge_pack, "p_http_get",
                        lambda a: {"error": "URLError: boom"})
    out = forge_pack.test_forged({"name": "fetch_titles",
                                  "example_input": {"url": "http://x"},
                                  "activate": True})
    assert out["ok"] is False
    assert "error" in out["output"]
    assert forge_pack.list_forged({})["tools"][0]["active"] is False


# ---------------------------------------------------------------------------
# retire_forged
# ---------------------------------------------------------------------------

def test_retire_removes_file_and_manifest(tmp_path):
    forged_dir = tmp_path / "forged"
    out = _forge_titles()
    path = out["path"]
    retired = forge_pack.retire_forged({"name": "fetch_titles"})
    assert retired["ok"] is True
    assert retired["retired"] == "fetch_titles"
    import os
    assert not os.path.exists(path)
    assert forge_pack.list_forged({})["count"] == 0
    again = forge_pack.retire_forged({"name": "fetch_titles"})
    assert "error" in again


# ---------------------------------------------------------------------------
# primitive guards
# ---------------------------------------------------------------------------

def test_primitive_guards():
    assert "error" in forge_pack.p_http_get({"url": "ftp://x"})
    assert "error" in forge_pack.p_http_get({"url": ""})
    assert "error" in forge_pack.p_sqlite_select({"db": "jarvis.db",
                                                  "query": "DROP TABLE t"})
    assert "error" in forge_pack.p_sqlite_select({"db": "../etc/x.db",
                                                  "query": "SELECT 1"})
    assert "error" in forge_pack.p_regex_extract({"text": "abc", "pattern": "("})
    assert "error" in forge_pack.p_regex_extract({"text": "abc", "pattern": "x" * 201})
    assert "error" in forge_pack.p_file_head({"path": "/etc/passwd"})
    assert "error" in forge_pack.p_json_path({"data": "not json", "path": "a"})
    assert "error" in forge_pack.p_json_path({"data": {"a": 1}, "path": "b.c"})
    n = forge_pack.p_notify({"text": "hello"})
    assert isinstance(n, dict) and "delivered" in n  # never raises, never lies
    assert forge_pack.p_json_path({"data": {"a": {"b": 2}},
                                   "path": "a.b"})["value"] == 2


def test_match_intent_caps_at_three():
    prims = forge_pack.match_intent(
        "fetch url json api from database sqlite query and notify alert me")
    assert len(prims) <= 3
    assert prims == sorted(prims, key=forge_pack._PRIM_ORDER.index)
