"""Phase 28 — Correction learning (offline).

Covers: correction filed and retrievable; unknown trace_id rejected;
empty fields rejected; duplicate correction stored once; lesson source
is "correction"; lesson text carries both fields verbatim; survey
surfaces the correction for a similar goal; the original trace is
untouched (audit still clean); registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, cognition_pack, metacog_pack


def _use_db(tmp_path, name):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / f"{name}.db")
    brain_pack.bind_agent(None)


def _think():
    return brain_pack.brain_think_handler({
        "question": "Should we raise gym membership prices?",
        "context": ("Raising gym prices works because members see the value "
                    "in better equipment. However raising prices fails when "
                    "churn spikes after the change.")})


def _lessons():
    conn = brain_pack._connect()
    try:
        return conn.execute(
            "SELECT text, source FROM lessons ORDER BY id").fetchall()
    finally:
        conn.close()


def test_correction_filed(tmp_path):
    _use_db(tmp_path, "filed")
    t = _think()
    r = metacog_pack.correct_handler({
        "trace_id": t["trace_id"],
        "what_was_wrong": "it leaned toward raising prices",
        "right_answer": "Never raise prices without warning members first"})
    assert "error" not in r, r
    assert r["stored"] is True
    assert r["trace_id"] == t["trace_id"]
    rows = _lessons()
    assert len(rows) == 1
    text, source = rows[0]
    assert source == "correction"
    assert "Never raise prices without warning members first" in text
    assert "it leaned toward raising prices" in text
    assert t["trace_id"] in text


def test_unknown_trace_rejected(tmp_path):
    _use_db(tmp_path, "unknown")
    r = metacog_pack.correct_handler({
        "trace_id": "nope", "what_was_wrong": "x", "right_answer": "y"})
    assert "error" in r
    assert "unknown trace_id" in r["error"]


def test_empty_fields_rejected(tmp_path):
    _use_db(tmp_path, "empty")
    t = _think()
    for args in ({"trace_id": t["trace_id"], "what_was_wrong": "",
                  "right_answer": "y"},
                 {"trace_id": t["trace_id"], "what_was_wrong": "x",
                  "right_answer": ""},
                 {}):
        r = metacog_pack.correct_handler(args)
        assert "error" in r, args


def test_duplicate_correction_stored_once(tmp_path):
    _use_db(tmp_path, "dupe")
    t = _think()
    args = {"trace_id": t["trace_id"],
            "what_was_wrong": "it leaned toward raising prices",
            "right_answer": "Never raise prices without warning members first"}
    r1 = metacog_pack.correct_handler(args)
    assert r1["stored"] is True
    r2 = metacog_pack.correct_handler(args)
    assert r2["stored"] is False
    assert "duplicate" in r2["note"]
    assert len(_lessons()) == 1


def test_trace_untouched_after_correction(tmp_path):
    _use_db(tmp_path, "untouched")
    t = _think()
    metacog_pack.correct_handler({
        "trace_id": t["trace_id"],
        "what_was_wrong": "wrong lean",
        "right_answer": "the opposite is true"})
    a = metacog_pack.audit_handler({"trace_id": t["trace_id"]})
    assert a["verdict"] == "clean", a["checks"]


def test_survey_surfaces_correction(tmp_path):
    _use_db(tmp_path, "survey")
    t = _think()
    metacog_pack.correct_handler({
        "trace_id": t["trace_id"],
        "what_was_wrong": "it leaned toward raising prices",
        "right_answer": "Never raise membership prices without warning"})
    s = cognition_pack.survey_handler({"goal": "gym membership prices"})
    assert "error" not in s, s
    assert any("Correction" in le["lesson"] for le in s["lessons"]), s["lessons"]


def test_trust_note_present(tmp_path):
    _use_db(tmp_path, "trust")
    t = _think()
    r = metacog_pack.correct_handler({
        "trace_id": t["trace_id"],
        "what_was_wrong": "wrong lean",
        "right_answer": "the opposite is true"})
    assert "believed completely" in r["note"]


class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    metacog_pack.register(reg)
    assert "brain.correct" in reg.tools
    assert "brain.correct" in metacog_pack.RISK_TABLE_ADDITIONS
