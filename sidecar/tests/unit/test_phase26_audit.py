"""Phase 26 — Trace auditor (offline).

Covers: a real think trace audits clean; tampered conclusion detected;
inflated evidence weight detected (nets mismatch); out-of-range weight
detected; bad stance detected; empty evidence detected; unknown
trace_id is an error; the honest no-evidence conclusion audits clean;
net rounding tolerance respected; registration contract.
"""
import json
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, metacog_pack


def _use_db(tmp_path, name):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / f"{name}.db")
    brain_pack.bind_agent(None)


def _think():
    return brain_pack.brain_think_handler({
        "question": "Should we raise gym membership prices?",
        "context": ("Raising gym prices works because members see the value "
                    "in better equipment. However raising prices fails when "
                    "churn spikes after the change. Gym pricing is proven to "
                    "increase revenue when owners warn members first.")})


def _copy_trace(src_id, new_id, mutate):
    conn = brain_pack._connect()
    try:
        row = conn.execute(
            "SELECT trace_id, created_at, question, context_excerpt,"
            " decomposition_json, evidence_json, nets_json, conclusion,"
            " reasons_json, confidence, confidence_note FROM traces"
            " WHERE trace_id=?", (src_id,)).fetchone()
        assert row is not None
        cols = list(row)
        cols[0] = new_id
        data = {"decomposition": json.loads(cols[4]),
                "evidence": json.loads(cols[5]),
                "nets": json.loads(cols[6])}
        mutate(data)
        cols[4] = json.dumps(data["decomposition"])
        cols[5] = json.dumps(data["evidence"])
        cols[6] = json.dumps(data["nets"])
        conn.execute(
            "INSERT INTO traces(trace_id, created_at, question,"
            " context_excerpt, decomposition_json, evidence_json, nets_json,"
            " conclusion, reasons_json, confidence, confidence_note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", cols)
        if mutate.__name__ == "_mut_conclusion":
            conn.execute("UPDATE traces SET conclusion=? WHERE trace_id=?",
                         (mutate.new_conclusion, new_id))
        conn.commit()
    finally:
        conn.close()


def _mut_conclusion(data):
    pass
_mut_conclusion.new_conclusion = "The moon is made of cheese."


def _mut_weight(data):
    if data["evidence"]:
        data["evidence"][0]["weight"] = 0.99


def _mut_weight_out_of_range(data):
    if data["evidence"]:
        data["evidence"][0]["weight"] = 5.0


def _mut_stance(data):
    if data["evidence"]:
        data["evidence"][0]["stance"] = "maybe"


def _mut_empty(data):
    data["evidence"] = []


def _verdict_checks(res):
    assert "error" not in res, res
    return res["verdict"], {c["check"]: c["passed"] for c in res["checks"]}


def test_real_trace_audits_clean(tmp_path):
    _use_db(tmp_path, "clean")
    t = _think()
    assert "trace_id" in t, t
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": t["trace_id"]}))
    assert v == "clean", checks
    assert all(checks.values())


def test_tampered_conclusion_detected(tmp_path):
    _use_db(tmp_path, "tamper")
    t = _think()
    _copy_trace(t["trace_id"], "tampered-1", _mut_conclusion)
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": "tampered-1"}))
    assert v == "issues_found"
    assert checks["conclusion_matches_leading"] is False
    assert checks["nets_match_recomputed"] is True  # only conclusion tampered


def test_inflated_weight_detected(tmp_path):
    _use_db(tmp_path, "weight")
    t = _think()
    _copy_trace(t["trace_id"], "tampered-2", _mut_weight)
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": "tampered-2"}))
    assert v == "issues_found"
    assert checks["nets_match_recomputed"] is False


def test_out_of_range_weight_detected(tmp_path):
    _use_db(tmp_path, "range")
    t = _think()
    _copy_trace(t["trace_id"], "tampered-3", _mut_weight_out_of_range)
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": "tampered-3"}))
    assert v == "issues_found"
    assert checks["evidence_items_well_formed"] is False


def test_bad_stance_detected(tmp_path):
    _use_db(tmp_path, "stance")
    t = _think()
    _copy_trace(t["trace_id"], "tampered-4", _mut_stance)
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": "tampered-4"}))
    assert v == "issues_found"
    assert checks["evidence_items_well_formed"] is False


def test_empty_evidence_detected(tmp_path):
    _use_db(tmp_path, "empty")
    t = _think()
    _copy_trace(t["trace_id"], "tampered-5", _mut_empty)
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": "tampered-5"}))
    assert v == "issues_found"
    assert checks["evidence_non_empty"] is False


def test_unknown_trace_id_is_error(tmp_path):
    _use_db(tmp_path, "unknown")
    r = metacog_pack.audit_handler({"trace_id": "nope"})
    assert "error" in r
    r2 = metacog_pack.audit_handler({})
    assert "error" in r2


def test_no_evidence_conclusion_audits_clean(tmp_path):
    _use_db(tmp_path, "noev")
    t = brain_pack.brain_think_handler({
        "question": "Should we expand to Mars?",
        "context": "The cafeteria served lentils on Tuesday."})
    assert "trace_id" in t, t
    v, checks = _verdict_checks(
        metacog_pack.audit_handler({"trace_id": t["trace_id"]}))
    assert v == "clean", checks


def test_read_discloses_limits(tmp_path):
    _use_db(tmp_path, "read")
    t = _think()
    r = metacog_pack.audit_handler({"trace_id": t["trace_id"]})
    assert "tampered with after writing" in r["read"]
    assert "fabricated at write time" in r["read"]


class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    metacog_pack.register(reg)
    assert "brain.audit" in reg.tools
    assert "brain.audit" in metacog_pack.RISK_TABLE_ADDITIONS
