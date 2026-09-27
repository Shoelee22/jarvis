"""Offline tests for the Phase 12 Teach pack (macros + local file search).

Macros are exercised against a REAL Registry: teach_pack is registered
alongside stub tools, a fake agent object exposing .registry is bound, and
recording is driven through the real note_call() hook the agent core would
call. File search runs against tmp dirs with a tmp sqlite DB.
"""
from __future__ import annotations

import os
import sys
import types
import zipfile

import pytest

sys.path.insert(0, "sidecar")

import jarvis.tools.builtin.teach_pack as tp
from jarvis.agent import policy
from jarvis.tools.base import Registry, Tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    db = tmp_path / "teach.db"
    monkeypatch.setenv("JARVIS_TEACH_DB", str(db))
    monkeypatch.setattr(tp, "_AGENT", None)
    monkeypatch.setattr(tp, "_RECORDING", None)
    yield db


def _echo_tool(calls):
    def h(a):
        calls.append(("test.echo", dict(a)))
        return {"echoed": a}

    return Tool(name="test.echo", description="low-risk echo test tool",
                schema={}, handler=h, risk="low", needs_network=False)


def _danger_tool(calls):
    def h(a):
        calls.append(("test.danger", dict(a)))
        return {"boom": True}

    return Tool(name="test.danger", description="high-risk test tool",
                schema={}, handler=h, risk="high", needs_network=False)


@pytest.fixture
def live(tmpdb, monkeypatch):
    """REAL Registry + teach_pack + stub tools, fake agent bound."""
    calls: list = []
    reg = Registry()
    tp.register(reg)
    reg.register(_echo_tool(calls))
    reg.register(_danger_tool(calls))
    monkeypatch.setitem(policy.RISK_TABLE, "test.echo", ("low", False))
    monkeypatch.setitem(policy.RISK_TABLE, "test.danger", ("high", False))
    for tool_name, (risk, net) in tp.RISK_TABLE_ADDITIONS.items():
        monkeypatch.setitem(policy.RISK_TABLE, tool_name, (risk, net))
    tp.bind_agent(types.SimpleNamespace(registry=reg))
    return reg, calls


def _regcall(reg, name, args, confirmed=False):
    """Call through the real registry + policy engine; unwrap handler output."""
    out = reg.call(name, args or {}, actor="agent", confirmed=confirmed)
    assert out.get("ok"), f"{name} failed: {out}"
    return out["result"]


# ---------------------------------------------------------------------------
# register() wiring
# ---------------------------------------------------------------------------
def test_register_wires_all_tools_and_risk_table(tmpdb):
    reg = Registry()
    tp.register(reg)
    expected = {"macro.record", "macro.stop", "macro.play", "macro.list",
                "files.index", "files.search"}
    assert set(reg.tools) == expected
    assert set(tp.RISK_TABLE_ADDITIONS) == expected
    for spec in tp.TOOL_DEFS:
        for key in ("name", "description", "handler", "risk",
                    "needs_network", "schema"):
            assert key in spec, f"TOOL_DEF missing {key}: {spec['name']}"
        assert spec["needs_network"] is False
        assert callable(spec["handler"])
    assert tp.RISK_TABLE_ADDITIONS["macro.play"] == ("medium", False)
    assert tp.RISK_TABLE_ADDITIONS["macro.record"] == ("low", False)


# ---------------------------------------------------------------------------
# Recording API
# ---------------------------------------------------------------------------
def test_recording_api_lifecycle(tmpdb):
    assert tp.is_recording() is False
    tp.recording_start("demo")
    assert tp.is_recording() is True
    tp.note_call("test.echo", {"a": 1}, "ok")
    steps = tp.recording_stop()
    assert tp.is_recording() is False
    assert len(steps) == 1
    assert steps[0]["tool"] == "test.echo"
    assert steps[0]["args"] == {"a": 1}
    # No-op when idle: no crash, no state.
    assert tp.note_call("test.echo", {}, "x") is None
    assert tp.is_recording() is False


def test_macro_control_calls_never_recorded(tmpdb):
    tp.recording_start("selfsafe")
    tp.note_call("macro.list", {}, "listed")
    tp.note_call("macro.play", {"name": "x"}, "played")
    tp.note_call("test.echo", {"a": 1}, "ok")
    steps = tp.recording_stop()
    assert [s["tool"] for s in steps] == ["test.echo"]


def test_double_record_is_an_honest_error(tmpdb):
    tp.macro_record_handler({"name": "one"})
    out = tp.macro_record_handler({"name": "two"})
    assert "error" in out and "already recording" in out["error"]
    tp.macro_stop_handler({})


def test_stop_with_no_session_is_honest(tmpdb):
    out = tp.macro_stop_handler({})
    assert out["recording"] is False


def test_bad_macro_name_rejected(tmpdb):
    assert "error" in tp.macro_record_handler({"name": "Bad Name!"})
    assert "error" in tp.macro_record_handler({"name": "x"})


# ---------------------------------------------------------------------------
# Record -> play round trip through the REAL registry
# ---------------------------------------------------------------------------
def test_record_play_roundtrip_in_order(live):
    reg, calls = live
    assert _regcall(reg, "macro.record", {"name": "greet"})["recording"] == "greet"
    # Simulate the agent core: registry call, then the note_call hook.
    for msg in ("hello", "world"):
        res = _regcall(reg, "test.echo", {"msg": msg})
        tp.note_call("test.echo", {"msg": msg}, res.get("echoed"))
    stop = _regcall(reg, "macro.stop", {})
    assert stop["saved"] is True and stop["steps"] == 2

    listed = _regcall(reg, "macro.list", {})
    assert listed["count"] == 1
    assert listed["macros"][0] == {
        "name": "greet", "steps": 2,
        "created_at": listed["macros"][0]["created_at"]}

    calls.clear()
    out = _regcall(reg, "macro.play", {"name": "greet"})
    assert out["steps_executed"] == 2
    assert [c[1]["msg"] for c in calls] == ["hello", "world"]
    assert [s["tool"] for s in out["completed_steps"]] == ["test.echo",
                                                           "test.echo"]
    assert out["completed_steps"][0]["result"]["result"]["echoed"] == {"msg": "hello"}


def test_empty_recording_is_not_saved(live):
    reg, _ = live
    _regcall(reg, "macro.record", {"name": "nothing"})
    out = _regcall(reg, "macro.stop", {})
    assert out["recording"] is False
    assert _regcall(reg, "macro.list", {})["count"] == 0


def test_play_respects_policy_gates(live):
    """A high-risk step inside a macro still needs confirmation at replay."""
    reg, calls = live
    _regcall(reg, "macro.record", {"name": "risky"})
    # The agent ran it earlier with confirmation; we only record the note.
    tp.note_call("test.danger", {"target": "x"}, "boom")
    _regcall(reg, "macro.stop", {})

    calls.clear()
    out = _regcall(reg, "macro.play", {"name": "risky"})
    assert out["stopped_at_step"] == 1
    assert out["reason"] == "confirmation gate"
    assert calls == [], "high-risk step must not execute without confirmation"
    step_res = out["completed_steps"][0]["result"]
    assert step_res.get("needs_confirmation") is True


def test_play_unknown_macro_errors(live):
    reg, _ = live
    out = _regcall(reg, "macro.play", {"name": "nope"})
    assert "error" in out and "no macro" in out["error"]


def test_play_unbound_is_honest_error(tmpdb):
    tp.bind_agent(None)
    tp.macro_record_handler({"name": "lonely"})
    tp.note_call("test.echo", {"a": 1}, "ok")
    tp.macro_stop_handler({})
    out = tp.macro_play_handler({"name": "lonely"})
    assert "error" in out
    assert "not bound" in out["error"]


# ---------------------------------------------------------------------------
# Arg templating
# ---------------------------------------------------------------------------
def test_arg_templating_substitution(live):
    reg, calls = live
    _regcall(reg, "macro.record", {"name": "tmpl"})
    tp.note_call("test.echo", {"text": {"var": "word"},
                               "nested": {"deep": {"var": "word"}}}, "ok")
    _regcall(reg, "macro.stop", {})

    calls.clear()
    out = _regcall(reg, "macro.play", {"name": "tmpl",
                                       "args": {"word": "hello"}})
    assert out["steps_executed"] == 1
    assert calls[0][1] == {"text": "hello", "nested": {"deep": "hello"}}


def test_missing_template_var_errors(live):
    reg, calls = live
    _regcall(reg, "macro.record", {"name": "tmpl2"})
    tp.note_call("test.echo", {"text": {"var": "word"}}, "ok")
    _regcall(reg, "macro.stop", {})

    calls.clear()
    out = _regcall(reg, "macro.play", {"name": "tmpl2"})
    assert "error" in out and "word" in out["error"]
    assert calls == [], "nothing may run when a variable is unfilled"


# ---------------------------------------------------------------------------
# File indexing + FTS search
# ---------------------------------------------------------------------------
def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_index_and_search_ranking(tmp_path, tmpdb):
    root = tmp_path / "docs"
    _write(root / "a.txt", "the march invoice is due soon")
    _write(root / "b.txt", "March invoice March totals March summary")
    _write(root / "c.md", "# notes\nnothing relevant here")

    out = tp.files_index_handler({"roots": [str(root)]})
    assert out["indexed"] == 3
    assert out["skipped_count"] == 0

    res = tp.files_search_handler({"query": "march"})
    assert res["count"] == 2
    # The doc mentioning March three times must outrank the one mentioning it once.
    assert res["hits"][0]["path"].endswith("b.txt")
    assert res["hits"][1]["path"].endswith("a.txt")
    assert "<<" in res["hits"][0]["snippet"]  # snippet marks the match
    assert res["hits"][0]["rank"] < res["hits"][1]["rank"]


def test_search_empty_query_errors(tmpdb):
    assert "error" in tp.files_search_handler({"query": "   "})
    assert "error" in tp.files_search_handler({})


def test_index_excludes(tmp_path, tmpdb, monkeypatch):
    root = tmp_path / "docs"
    _write(root / "ok.txt", "keep me")
    _write(root / ".git" / "config.txt", "git internals")
    _write(root / "node_modules" / "x" / "a.txt", "bundled dep")
    _write(root / ".hidden" / "a.txt", "hidden dir")
    _write(root / "venv" / "lib" / "a.txt", "virtualenv")
    _write(root / "app.log", "log noise")
    big = _write(root / "big.txt", "x" * 64)
    monkeypatch.setattr(tp, "MAX_FILE_BYTES", 10)

    out = tp.files_index_handler({"roots": [str(root)]})
    assert out["indexed"] == 1
    skipped = {s["path"]: s["reason"] for s in out["skipped"]}
    # File-level excludes are reported...
    log_reasons = [r for p, r in skipped.items() if p.endswith("app.log")]
    assert log_reasons and "log" in log_reasons[0]
    big_reasons = [r for p, r in skipped.items() if p.endswith("big.txt")]
    assert big_reasons and "excluded" in big_reasons[0]
    # ...while pruned dirs (.git, node_modules, hidden, venv) are silently
    # skipped at walk time — but their content must never be searchable.
    for q in ("internals", "bundled", "hidden", "virtualenv", "noise"):
        assert tp.files_search_handler({"query": q})["count"] == 0, q

    res = tp.files_search_handler({"query": "keep"})
    assert res["count"] == 1 and res["hits"][0]["path"].endswith("ok.txt")


def test_index_replaces_previous_entries_for_root(tmp_path, tmpdb):
    root = tmp_path / "docs"
    _write(root / "a.txt", "alpha content here")
    assert tp.files_index_handler({"roots": [str(root)]})["indexed"] == 1
    (root / "a.txt").write_text("totally different wording", encoding="utf-8")
    assert tp.files_index_handler({"roots": [str(root)]})["indexed"] == 1
    res = tp.files_search_handler({"query": "alpha"})
    assert res["count"] == 0, "stale entries must not survive re-index"


def test_missing_pypdf_skips_pdf_without_crash(tmp_path, tmpdb):
    try:
        import pypdf  # noqa: F401
        have_pypdf = True
    except ImportError:
        have_pypdf = False
    root = tmp_path / "docs"
    (root).mkdir(parents=True, exist_ok=True)
    (root / "scan.pdf").write_bytes(b"%PDF-1.4 fake pdf bytes")
    _write(root / "ok.txt", "plain text still indexed")

    out = tp.files_index_handler({"roots": [str(root)]})
    assert out["indexed"] == 1  # the txt file; the pdf never crashes the run
    skipped = {s["path"]: s["reason"] for s in out["skipped"]}
    pdf_reasons = [r for p, r in skipped.items() if p.endswith("scan.pdf")]
    assert pdf_reasons, "pdf must be reported as skipped"
    if not have_pypdf:
        assert "pypdf" in pdf_reasons[0]
    # Index stays usable.
    assert tp.files_search_handler({"query": "plain"})["count"] == 1


def _write_minimal_docx(path) -> None:
    """Build a valid minimal .docx with plain zipfile (no deps)."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>")
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>")
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>hello docx world unique</w:t></w:r></w:p></w:body>"
        "</w:document>")
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)


def test_docx_indexed_and_searchable(tmp_path, tmpdb):
    docx = pytest.importorskip("docx")
    assert docx is not None
    root = tmp_path / "docs"
    root.mkdir(parents=True, exist_ok=True)
    _write_minimal_docx(root / "memo.docx")

    out = tp.files_index_handler({"roots": [str(root)]})
    assert out["indexed"] == 1
    res = tp.files_search_handler({"query": "unique"})
    assert res["count"] == 1
    assert res["hits"][0]["path"].endswith("memo.docx")


def test_index_rejects_bad_roots(tmpdb):
    out = tp.files_index_handler({"roots": ["/does/not/exist/xyz"]})
    assert out["indexed"] == 0
    assert out["skipped"] and "not a directory" in out["skipped"][0]["reason"]
    assert "error" in tp.files_index_handler({"roots": []})
    assert "error" in tp.files_index_handler({})


# ---------------------------------------------------------------------------
# Handlers never raise
# ---------------------------------------------------------------------------
def test_handlers_never_raise(tmpdb, monkeypatch):
    monkeypatch.setattr(tp, "_AGENT", None)
    for spec in tp.TOOL_DEFS:
        out = spec["handler"]({"unexpected": object()})
        assert isinstance(out, dict), spec["name"]
