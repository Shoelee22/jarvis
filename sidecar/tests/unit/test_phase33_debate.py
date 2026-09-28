"""Phase 33 — structured debate (offline).

Covers: two-sided debate returns both conclusions + verdict; three
sides work; 1 side rejected; 5 sides rejected; missing stance
rejected; empty question rejected; all trace ids stored; registration
contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, cognition_pack


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "b.db")
    brain_pack.bind_agent(None)


def _debate(**kw):
    args = {"question": "Should we raise gym membership prices?",
            "sides": [{"name": "for", "stance": "argue FOR raising prices"},
                      {"name": "against",
                       "stance": "argue AGAINST raising prices"}]}
    args.update(kw)
    return cognition_pack.debate_handler(args)


def test_two_sided_debate(tmp_path):
    _use_db(tmp_path)
    r = _debate()
    assert "error" not in r, r
    assert len(r["sides"]) == 2
    assert r["sides"][0]["name"] == "for"
    assert r["sides"][1]["name"] == "against"
    for s in r["sides"]:
        assert s["conclusion"] and s["trace_id"]
    assert r["verdict"] and r["verdict_trace_id"]


def test_three_sides(tmp_path):
    _use_db(tmp_path)
    r = _debate(sides=[{"name": "a", "stance": "for"},
                       {"name": "b", "stance": "against"},
                       {"name": "c", "stance": "neutral, find a middle path"}])
    assert len(r["sides"]) == 3, r
    assert r["verdict"]


def test_side_count_bounds(tmp_path):
    _use_db(tmp_path)
    one = _debate(sides=[{"name": "a", "stance": "for"}])
    assert "error" in one and "2-4" in one["error"]
    five = _debate(sides=[{"name": str(i), "stance": "x"} for i in range(5)])
    assert "error" in five and "2-4" in five["error"]


def test_missing_stance_rejected(tmp_path):
    _use_db(tmp_path)
    r = _debate(sides=[{"name": "a", "stance": "for"},
                       {"name": "b"}])
    assert "error" in r and "stance" in r["error"]


def test_empty_question_rejected(tmp_path):
    _use_db(tmp_path)
    r = _debate(question="  ")
    assert "error" in r


def test_traces_are_real_and_auditable(tmp_path):
    _use_db(tmp_path)
    r = _debate()
    from jarvis.tools.builtin import metacog_pack
    for s in r["sides"]:
        a = metacog_pack.audit_handler({"trace_id": s["trace_id"]})
        assert a.get("verdict") == "clean", (s["name"], a)
    a = metacog_pack.audit_handler({"trace_id": r["verdict_trace_id"]})
    assert a.get("verdict") == "clean"


def test_registration_contract():
    class _FakeReg:
        def __init__(self):
            self.tools = {}

        def register(self, tool):
            self.tools[tool.name] = tool

    reg = _FakeReg()
    cognition_pack.register(reg)
    assert "brain.debate" in reg.tools
    assert "brain.debate" in cognition_pack.RISK_TABLE_ADDITIONS
