"""Phase 32 — milestone plans (offline).

Covers: milestones created with plan; invalid step id rejected;
early mark refused naming undone steps; mark works after steps done;
re-mark idempotent; unknown plan/milestone errors; plan_status shows
milestone progress; registration contract.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "b.db")
    brain_pack.bind_agent(None)


def _mkplan(**kw):
    args = {"goal": "Launch the gym site",
            "steps": [{"id": "s1", "tool": "memory.remember",
                       "args": {"text": "copy done"}},
                      {"id": "s2", "tool": "memory.remember",
                       "args": {"text": "design done"}}]}
    args.update(kw)
    return brain_pack.brain_plan_handler(args)


def _mark_done(plan_id, step_id):
    # flip a step to done directly (simulates run_step success)
    with brain_pack._LOCK:
        conn = brain_pack._connect()
        try:
            plan = brain_pack._load_plan(conn, plan_id)
            for s in plan["steps"]:
                if s["id"] == step_id:
                    s["status"] = "done"
            brain_pack._save_plan(conn, plan)
            conn.commit()
        finally:
            conn.close()


def test_milestones_created_with_plan(tmp_path):
    _use_db(tmp_path)
    r = _mkplan(milestones=[{"id": "m1", "title": "Content ready",
                             "step_ids": ["s1", "s2"], "due": "Friday"}])
    assert r.get("milestones") == 1, r
    st = brain_pack.brain_plan_status_handler({"plan_id": r["plan_id"]})
    ms = st["plan"]["milestones"]
    assert len(ms) == 1 and ms[0]["title"] == "Content ready"
    assert ms[0]["done"] is False and ms[0]["due"] == "Friday"


def test_invalid_step_id_rejected(tmp_path):
    _use_db(tmp_path)
    r = _mkplan(milestones=[{"title": "Bad", "step_ids": ["nope"]}])
    assert "error" in r and "nope" in r["error"]


def test_early_mark_refused(tmp_path):
    _use_db(tmp_path)
    r = _mkplan(milestones=[{"id": "m1", "title": "T",
                             "step_ids": ["s1", "s2"]}])
    m = brain_pack.brain_milestone_handler(
        {"plan_id": r["plan_id"], "milestone_id": "m1"})
    assert "error" in m and "s1" in m["error"] and "s2" in m["error"]


def test_mark_after_steps_done(tmp_path):
    _use_db(tmp_path)
    r = _mkplan(milestones=[{"id": "m1", "title": "T",
                             "step_ids": ["s1"]}])
    _mark_done(r["plan_id"], "s1")
    m = brain_pack.brain_milestone_handler(
        {"plan_id": r["plan_id"], "milestone_id": "m1",
         "note": "copy signed off"})
    assert m.get("ok"), m
    assert m["milestones_remaining"] == 0
    st = brain_pack.brain_plan_status_handler({"plan_id": r["plan_id"]})
    assert st["plan"]["milestones"][0]["done"] is True


def test_remark_idempotent(tmp_path):
    _use_db(tmp_path)
    r = _mkplan(milestones=[{"id": "m1", "title": "T", "step_ids": ["s1"]}])
    _mark_done(r["plan_id"], "s1")
    brain_pack.brain_milestone_handler(
        {"plan_id": r["plan_id"], "milestone_id": "m1"})
    m2 = brain_pack.brain_milestone_handler(
        {"plan_id": r["plan_id"], "milestone_id": "m1"})
    assert m2.get("ok") and m2.get("already") is True


def test_unknown_ids_error(tmp_path):
    _use_db(tmp_path)
    assert "error" in brain_pack.brain_milestone_handler(
        {"plan_id": "plan_nope", "milestone_id": "m1"})
    r = _mkplan()
    assert "error" in brain_pack.brain_milestone_handler(
        {"plan_id": r["plan_id"], "milestone_id": "m_nope"})


def test_plan_without_milestones_still_works(tmp_path):
    _use_db(tmp_path)
    r = _mkplan()
    assert r.get("milestones") == 0
    st = brain_pack.brain_plan_status_handler({"plan_id": r["plan_id"]})
    assert st["plan"]["milestones"] == []


def test_registration_contract():
    class _FakeReg:
        def __init__(self):
            self.tools = {}

        def register(self, tool):
            self.tools[tool.name] = tool

    reg = _FakeReg()
    brain_pack.register(reg)
    assert "brain.milestone" in reg.tools
    assert "brain.milestone" in brain_pack.RISK_TABLE_ADDITIONS
