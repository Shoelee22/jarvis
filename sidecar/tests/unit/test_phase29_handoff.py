"""Phase 29 — Session handoff (offline).

Covers: handoff writes the file with all sections; resume reads it
back; resume with no file is honest; note stored verbatim; open cycle
appears with age; active plan shows its next step; empty stores give
an honest all-clear handoff; registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack, cognition_pack


def _use_db(tmp_path, name):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / f"{name}.db")
    os.environ["JARVIS_AUTOPILOT_DB"] = str(tmp_path / f"{name}-ap.db")
    os.environ["JARVIS_HANDOFF_FILE"] = str(tmp_path / f"{name}-handoff.md")
    brain_pack.bind_agent(None)


def test_resume_without_handoff_is_honest(tmp_path):
    _use_db(tmp_path, "nofile")
    r = cognition_pack.resume_handler({})
    assert "error" not in r, r
    assert r["handoff"] is None
    assert "No handoff recorded yet" in r["note"]


def test_empty_stores_all_clear(tmp_path):
    _use_db(tmp_path, "clear")
    r = cognition_pack.handoff_handler({})
    assert "error" not in r, r
    assert r["open_cycles"] == 0
    assert r["active_plans"] == 0
    assert r["pending_approvals"] == 0
    text = cognition_pack.resume_handler({})["handoff"]
    assert "every decision has its outcome" in text
    assert "queue is clear" in text
    assert "none filed yet" in text


def test_handoff_roundtrip(tmp_path):
    _use_db(tmp_path, "round")
    r = cognition_pack.handoff_handler({"note": "user is mid-decision on the gym launch"})
    assert "error" not in r, r
    assert r["path"] == os.environ["JARVIS_HANDOFF_FILE"]
    text = cognition_pack.resume_handler({})["handoff"]
    assert text is not None
    assert "user is mid-decision on the gym launch" in text  # verbatim note
    assert "written " in text  # timestamp
    for section in ("Open cycles", "Active plans", "Pending approvals",
                    "Unresolved forecasts", "Calibration", "Recent lessons"):
        assert section in text, section
    # resume reports the file's own timestamp
    assert cognition_pack.resume_handler({})["file_mtime"]


def test_cycle_and_plan_in_handoff(tmp_path):
    _use_db(tmp_path, "full")
    cyc = cognition_pack.cycle_handler({"goal": "Choose a CRM"})
    assert "error" not in cyc, cyc
    plan = brain_pack.brain_plan_handler({
        "goal": "Launch the gym site",
        "steps": [{"id": "a", "tool": "memory.recall",
                   "args": {}, "depends_on": []}]})
    assert "error" not in plan, plan
    r = cognition_pack.handoff_handler({})
    assert r["open_cycles"] == 1
    assert r["active_plans"] == 1
    text = cognition_pack.resume_handler({})["handoff"]
    assert cyc["cycle_id"] in text
    assert "d old" in text  # cycle age
    assert plan["plan_id"] in text
    assert "a (memory.recall)" in text  # next step


def test_lesson_in_handoff(tmp_path):
    _use_db(tmp_path, "lesson")
    brain_pack.brain_reflect_handler(
        {"on": "Lesson learned: always confirm the venue. This was a lesson."})
    r = cognition_pack.handoff_handler({})
    assert "error" not in r, r
    text = cognition_pack.resume_handler({})["handoff"]
    assert "confirm the venue" in text


class _FakeReg:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


def test_registration_contract():
    reg = _FakeReg()
    cognition_pack.register(reg)
    assert "brain.handoff" in reg.tools
    assert "brain.resume" in reg.tools
    assert "brain.handoff" in cognition_pack.RISK_TABLE_ADDITIONS
    assert "brain.resume" in cognition_pack.RISK_TABLE_ADDITIONS
