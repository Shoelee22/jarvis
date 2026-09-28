"""Phase 30 — the integrity loop (offline).

Covers: all-green brain -> sound; stale cycle -> needs_attention;
tampered trace in the audit sample -> degraded naming the trace;
empty brain -> sound with honest sections; write_handoff false leaves
the handoff file untouched; write_handoff true appends; verdict is
mechanical (same input -> same verdict); registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, cognition_pack, metacog_pack


def _use_db(tmp_path, name):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / f"{name}.db")
    os.environ["JARVIS_AUTOPILOT_DB"] = str(tmp_path / f"{name}-ap.db")
    os.environ["JARVIS_HANDOFF_FILE"] = str(tmp_path / f"{name}-handoff.md")
    brain_pack.bind_agent(None)


def _think(question="Should we raise gym membership prices?"):
    return brain_pack.brain_think_handler({
        "question": question,
        "context": ("Raising gym prices works because members see the value "
                    "in better equipment. However raising prices fails when "
                    "churn spikes after the change.")})


def test_empty_brain_is_sound(tmp_path):
    _use_db(tmp_path, "empty")
    r = cognition_pack.integrity_handler({})
    assert "error" not in r, r
    assert r["verdict"] == "sound"
    assert r["issues"] == []
    assert "verdict: **sound**" in r["report_md"]
    assert r["traces_audited"] == 0


def test_stale_cycle_needs_attention(tmp_path):
    _use_db(tmp_path, "stale")
    c = cognition_pack.cycle_handler({"goal": "Pick a CRM"})
    assert "error" not in c, c
    # age the cycle 9 days
    with brain_pack._LOCK:
        conn = cognition_pack._connect()
        try:
            conn.execute(
                "UPDATE cycles SET created_at=? WHERE cycle_id=?",
                ("2026-09-18T00:00:00+00:00", c["cycle_id"]))
            conn.commit()
        finally:
            conn.close()
    r = cognition_pack.integrity_handler({})
    assert r["verdict"] == "needs_attention", r["issues"]
    assert any("stale" in i for i in r["issues"])


def test_tampered_trace_degraded(tmp_path):
    _use_db(tmp_path, "tamper")
    t = _think()
    # tamper: rewrite the stored conclusion
    with brain_pack._LOCK:
        conn = brain_pack._connect()
        try:
            conn.execute("UPDATE traces SET conclusion=? WHERE trace_id=?",
                         ("HACKED CONCLUSION", t["trace_id"]))
            conn.commit()
        finally:
            conn.close()
    r = cognition_pack.integrity_handler({})
    assert r["verdict"] == "degraded", r
    assert any(t["trace_id"][:12] in i or "trace" in i for i in r["issues"])


def test_clean_traces_stay_sound(tmp_path):
    _use_db(tmp_path, "clean")
    _think()
    _think("Should we hire a designer?")
    r = cognition_pack.integrity_handler({"sample_traces": 5})
    assert "error" not in r, r
    assert r["verdict"] == "sound", r["issues"]
    assert r["traces_audited"] == 2


def test_mechanical_verdict(tmp_path):
    _use_db(tmp_path, "mech")
    _think()
    r1 = cognition_pack.integrity_handler({})
    r2 = cognition_pack.integrity_handler({})
    assert r1["verdict"] == r2["verdict"] == "sound"
    assert r1["issues"] == r2["issues"] == []


def test_handoff_not_touched_by_default(tmp_path):
    _use_db(tmp_path, "nohandoff")
    _think()
    cognition_pack.integrity_handler({})
    assert not os.path.exists(os.environ["JARVIS_HANDOFF_FILE"])


def test_handoff_written_when_asked(tmp_path):
    _use_db(tmp_path, "yeshandoff")
    _think()
    r = cognition_pack.integrity_handler({"write_handoff": True})
    assert "error" not in r, r
    assert r["handoff"] == "report appended to handoff"
    text = open(os.environ["JARVIS_HANDOFF_FILE"]).read()
    assert "integrity loop verdict: sound" in text


def test_report_names_sections(tmp_path):
    _use_db(tmp_path, "sections")
    r = cognition_pack.integrity_handler({})
    for section in ("Accountability review", "Open tensions", "Trace audit",
                    "Calibration", "Approval queue", "Issues"):
        assert section in r["report_md"], section


class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    cognition_pack.register(reg)
    assert "brain.integrity" in reg.tools
    assert "brain.integrity" in cognition_pack.RISK_TABLE_ADDITIONS
