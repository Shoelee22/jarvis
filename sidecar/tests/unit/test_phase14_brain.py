"""Phase 14 — Brain pack tests (offline).

Covers: think traces + honest low confidence on thin evidence; plan
persistence across a module "restart" against the same db file; decide
scorecard + winner; reflect dedupe (same lesson twice -> stored once) and
best-effort memory.learn forwarding; run_step's honest error when no agent
is bound; run_step executing through a stub agent's registry.call
(including the needs_confirmation gate); the registration contract.
"""
import importlib
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _use_db(tmp_path):
    """Point the pack at a fresh tmp db and unbind the agent."""
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    brain_pack.bind_agent(None)


def _trace_row(trace_id):
    conn = brain_pack._connect()
    try:
        return conn.execute(
            "SELECT trace_id, conclusion, confidence, decomposition_json "
            "FROM traces WHERE trace_id=?", (trace_id,)).fetchone()
    finally:
        conn.close()


def _lesson_count():
    conn = brain_pack._connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
    finally:
        conn.close()


class FakeRegistry:
    """Minimal stand-in for Registry: only call() matters to run_step."""

    def __init__(self, fail_on=(), gate_on=()):
        self.calls = []
        self.fail_on = set(fail_on)
        self.gate_on = set(gate_on)

    def call(self, name, args, actor="agent", confirmed=False, **kw):
        self.calls.append({"name": name, "args": args,
                           "actor": actor, "confirmed": confirmed})
        if name in self.gate_on and not confirmed:
            return {"ok": False, "needs_confirmation": True,
                    "confirm_text": f"confirm {name}?"}
        if name in self.fail_on:
            return {"ok": False, "error": "boom"}
        return {"ok": True, "result": {"echo": args}}


class FakeAgent:
    def __init__(self, registry):
        self.registry = registry


def _make_plan(goal="do things", steps=None):
    steps = steps or [
        {"id": "s1", "tool": "echo.a", "args": {"x": 1}, "depends_on": []},
        {"id": "s2", "tool": "echo.b", "args": {"y": 2}, "depends_on": ["s1"]},
    ]
    out = brain_pack.brain_plan_handler({"goal": goal, "steps": steps})
    assert "plan_id" in out, out
    return out["plan_id"]


# ---------------------------------------------------------------------------
# brain.think
# ---------------------------------------------------------------------------
def test_think_records_trace_and_conclusion(tmp_path):
    _use_db(tmp_path)
    out = brain_pack.brain_think_handler({
        "question": "Should we ship the release today or wait until Monday?",
        "context": ("Shipping today benefits the team because the tests all pass "
                    "and customers asked for it. However, Friday deploys carry "
                    "risk because nobody is around to fix issues over the weekend.")})
    assert "trace_id" in out and "conclusion" in out
    assert out["reasons"] and out["confidence"]
    assert len(out["sub_questions"]) >= 2
    row = _trace_row(out["trace_id"])
    assert row is not None
    assert row[1] == out["conclusion"]


def test_think_honest_low_confidence_on_thin_evidence(tmp_path):
    _use_db(tmp_path)
    out = brain_pack.brain_think_handler({
        "question": "What is the capital of Assyria?",
        "context": "The weather in Lisbon is pleasant. I like toast."})
    assert out["confidence"] == "low"
    assert "thin" in out["confidence_note"].lower() or "guess" in out["confidence_note"].lower()


def test_think_requires_question(tmp_path):
    _use_db(tmp_path)
    assert "error" in brain_pack.brain_think_handler({})
    assert "error" in brain_pack.brain_think_handler({"question": "   "})


# ---------------------------------------------------------------------------
# brain.plan / brain.replan / brain.plan_status
# ---------------------------------------------------------------------------
def test_plan_survives_module_restart(tmp_path):
    _use_db(tmp_path)
    plan_id = _make_plan(goal="restart me")
    # "Restart": reload the module while the env still points at the same db.
    reloaded = importlib.reload(brain_pack)
    out = reloaded.brain_plan_status_handler({"plan_id": plan_id})
    assert out["plan"]["goal"] == "restart me"
    assert out["plan"]["plan_id"] == plan_id
    assert len(out["plan"]["steps"]) == 2
    assert out["plan"]["status"] == "active"


def test_replan_keeps_history(tmp_path):
    _use_db(tmp_path)
    plan_id = _make_plan()
    out = brain_pack.brain_replan_handler({
        "plan_id": plan_id,
        "update": {"note": "added a third step",
                   "steps": [
                       {"id": "s1", "tool": "echo.a", "args": {}, "depends_on": []},
                       {"id": "s2", "tool": "echo.b", "args": {}, "depends_on": ["s1"]},
                       {"id": "s3", "tool": "echo.c", "args": {}, "depends_on": ["s2"]},
                   ]}})
    assert out["version"] == 2
    st = brain_pack.brain_plan_status_handler({"plan_id": plan_id})
    assert st["plan"]["version"] == 2
    assert len(st["plan"]["steps"]) == 3
    conn = brain_pack._connect()
    try:
        versions = conn.execute(
            "SELECT version FROM plan_revisions WHERE plan_id=? ORDER BY version",
            (plan_id,)).fetchall()
    finally:
        conn.close()
    assert [v[0] for v in versions] == [1, 2]  # history preserved, not rewritten


def test_plan_status_lists_active_plans(tmp_path):
    _use_db(tmp_path)
    _make_plan(goal="alpha")
    _make_plan(goal="beta")
    out = brain_pack.brain_plan_status_handler({})
    assert out["count"] == 2
    goals = {p["goal"] for p in out["active_plans"]}
    assert goals == {"alpha", "beta"}


def test_plan_rejects_bad_steps(tmp_path):
    _use_db(tmp_path)
    assert "error" in brain_pack.brain_plan_handler({"goal": "g", "steps": []})
    assert "error" in brain_pack.brain_plan_handler(
        {"goal": "g", "steps": [{"id": "s1", "tool": "x", "depends_on": ["nope"]}]})
    assert "error" in brain_pack.brain_plan_handler(
        {"goal": "g", "steps": [{"id": "s1", "tool": "x"}, {"id": "s1", "tool": "y"}]})
    assert "error" in brain_pack.brain_replan_handler(
        {"plan_id": "plan-nope", "update": {"note": "x"}})


# ---------------------------------------------------------------------------
# brain.run_step
# ---------------------------------------------------------------------------
def test_run_step_without_agent_is_honest_error(tmp_path):
    _use_db(tmp_path)
    brain_pack.bind_agent(None)
    out = brain_pack.brain_run_step_handler({"plan_id": "plan-x"})
    assert "error" in out
    assert "not bound" in out["error"]
    assert "bind_agent" in out["error"]


def test_run_step_executes_through_stub_registry(tmp_path):
    _use_db(tmp_path)
    reg = FakeRegistry()
    brain_pack.bind_agent(FakeAgent(reg))
    plan_id = _make_plan()

    first = brain_pack.brain_run_step_handler({"plan_id": plan_id})
    assert first["step"] == "s1" and first["step_status"] == "done"
    # s2 depends on s1: it must not run before s1 is done.
    assert [c["name"] for c in reg.calls] == ["echo.a"]
    assert reg.calls[0]["actor"] == "brain"

    second = brain_pack.brain_run_step_handler({"plan_id": plan_id})
    assert second["step"] == "s2" and second["step_status"] == "done"
    assert second["plan"] == "complete"

    st = brain_pack.brain_plan_status_handler({"plan_id": plan_id})
    assert st["plan"]["status"] == "done"
    assert st["plan"]["counts"]["done"] == 2


def test_run_step_failure_marks_step_and_blocks_dependents(tmp_path):
    _use_db(tmp_path)
    reg = FakeRegistry(fail_on={"echo.a"})
    brain_pack.bind_agent(FakeAgent(reg))
    plan_id = _make_plan()

    out = brain_pack.brain_run_step_handler({"plan_id": plan_id})
    assert out["step_status"] == "failed"
    st = brain_pack.brain_plan_status_handler({"plan_id": plan_id})
    by_id = {s["id"]: s["status"] for s in st["plan"]["steps"]}
    assert by_id["s1"] == "failed"
    assert by_id["s2"] == "blocked"  # dependency failed

    focus = brain_pack.brain_focus_handler({})
    assert any(b["step"] == "s2" for b in focus["blocked_steps"])


def test_run_step_policy_gate_stops_without_executing(tmp_path):
    _use_db(tmp_path)
    reg = FakeRegistry(gate_on={"echo.a"})
    brain_pack.bind_agent(FakeAgent(reg))
    plan_id = _make_plan()

    gated = brain_pack.brain_run_step_handler({"plan_id": plan_id})
    assert gated.get("needs_confirmation") is True
    assert "confirm_text" in gated

    st = brain_pack.brain_plan_status_handler({"plan_id": plan_id})
    by_id = {s["id"]: s["status"] for s in st["plan"]["steps"]}
    assert by_id["s1"] == "pending"  # policy gate: nothing executed

    # With human confirmation, the step may proceed.
    done = brain_pack.brain_run_step_handler({"plan_id": plan_id, "confirmed": True})
    assert done["step_status"] == "done"


# ---------------------------------------------------------------------------
# brain.decide
# ---------------------------------------------------------------------------
def test_decide_scorecard_and_winner(tmp_path):
    _use_db(tmp_path)
    out = brain_pack.brain_decide_handler({
        "options": [
            {"name": "train", "text": "The train is cheap, costs $40, and reliable"},
            {"name": "taxi", "text": "The taxi is fast and quick, but costs $120"},
        ],
        "criteria": [
            {"name": "cost", "weight": 3},
            {"name": "speed", "weight": 1},
        ]})
    assert "scorecard" in out and "winner" in out and "why" in out
    assert out["winner"] == "train"  # cost weight 3 dominates
    assert len(out["scorecard"]) == 2
    for entry in out["scorecard"]:
        assert len(entry["per_criterion"]) == 2
        for pc in entry["per_criterion"]:
            assert 0 <= pc["score"] <= 10
            assert pc["reason"]
    assert "HEURISTIC" in out["scoring_note"]


def test_decide_validates_input(tmp_path):
    _use_db(tmp_path)
    assert "error" in brain_pack.brain_decide_handler({"options": ["a"], "criteria": []})
    assert "error" in brain_pack.brain_decide_handler(
        {"options": ["a", "b"], "criteria": [{"name": "cost", "weight": 0}]})


# ---------------------------------------------------------------------------
# brain.focus
# ---------------------------------------------------------------------------
def test_focus_reports_goal_stack(tmp_path):
    _use_db(tmp_path)
    reg = FakeRegistry()
    brain_pack.bind_agent(FakeAgent(reg))
    plan_id = _make_plan(goal="stack me")
    brain_pack.brain_run_step_handler({"plan_id": plan_id})  # s1 done
    brain_pack.brain_think_handler({"question": "q?", "context": "q context"})

    focus = brain_pack.brain_focus_handler({})
    assert focus["active_plan_count"] == 1
    plan = focus["active_plans"][0]
    assert plan["goal"] == "stack me"
    assert plan["progress"] == "1/2 steps done"
    assert focus["whats_next"][0]["next_step"] == "s2"
    assert focus["deliberations_recorded"] == 1


# ---------------------------------------------------------------------------
# brain.reflect
# ---------------------------------------------------------------------------
_REPORT = ("We learned that deploying on Friday should be avoided because "
           "nobody is around to fix breakage. Always run the full test suite "
           "before a release; it catches regressions early.")


def test_reflect_dedupes_same_lesson_twice(tmp_path):
    _use_db(tmp_path)
    first = brain_pack.brain_reflect_handler({"on": _REPORT})
    assert first["stored"] >= 1
    assert _lesson_count() == first["stored"]

    second = brain_pack.brain_reflect_handler({"on": _REPORT})
    assert second["stored"] == 0
    assert len(second["duplicates_skipped"]) >= 1
    assert _lesson_count() == first["stored"]  # stored once, not twice


def test_reflect_marks_unforwarded_without_agent(tmp_path):
    _use_db(tmp_path)
    brain_pack.bind_agent(None)
    brain_pack.brain_reflect_handler({"on": _REPORT})
    conn = brain_pack._connect()
    try:
        rows = conn.execute("SELECT forwarded FROM lessons").fetchall()
    finally:
        conn.close()
    assert rows and all(r[0] == 0 for r in rows)  # kept locally, marked pending


def test_reflect_forwards_to_memory_learn_best_effort(tmp_path):
    _use_db(tmp_path)

    class LearnRegistry(FakeRegistry):
        def __init__(self):
            super().__init__()
            self.tools = {"memory.learn": object()}

    reg = LearnRegistry()
    brain_pack.bind_agent(FakeAgent(reg))
    out = brain_pack.brain_reflect_handler({"on": _REPORT})
    assert out["stored"] >= 1
    assert out["forwarding"]["forwarded"] == out["stored"]
    learn_calls = [c for c in reg.calls if c["name"] == "memory.learn"]
    assert len(learn_calls) == out["stored"]
    conn = brain_pack._connect()
    try:
        rows = conn.execute("SELECT forwarded FROM lessons").fetchall()
    finally:
        conn.close()
    assert all(r[0] == 1 for r in rows)


def test_reflect_no_lesson_markers_stores_nothing(tmp_path):
    _use_db(tmp_path)
    out = brain_pack.brain_reflect_handler({"on": "The sky was blue. Lunch was rice."})
    assert out["stored"] == 0
    assert _lesson_count() == 0


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_register_contract_and_risk_table(tmp_path):
    _use_db(tmp_path)
    from jarvis.tools.base import Registry
    reg = Registry()
    brain_pack.register(reg)
    expected = {
        "brain.think": "low", "brain.plan": "low", "brain.replan": "low",
        "brain.plan_status": "low", "brain.run_step": "medium",
        "brain.decide": "low", "brain.focus": "low", "brain.reflect": "low",
    }
    for name, risk in expected.items():
        assert name in reg.tools, name
        assert reg.tools[name].risk == risk, (name, reg.tools[name].risk)
        assert reg.tools[name].needs_network is False, name
    # Later phases extend brain_pack (milestones, metacog, integrity);
    # assert the originals keep their contract, and every entry is well-formed.
    for name, entry in brain_pack.RISK_TABLE_ADDITIONS.items():
        assert name in reg.tools, name
        assert isinstance(entry, tuple) and len(entry) == 2, name
        assert isinstance(entry[1], bool), name


def test_handlers_never_raise_on_bad_input(tmp_path):
    _use_db(tmp_path)
    assert "error" in brain_pack.brain_think_handler(None)
    assert "error" in brain_pack.brain_plan_handler(None)
    assert "error" in brain_pack.brain_replan_handler(None)
    assert "error" in brain_pack.brain_run_step_handler(None)
    assert "error" in brain_pack.brain_decide_handler(None)
    assert "error" in brain_pack.brain_reflect_handler(None)
    assert "error" in brain_pack.brain_reflect_handler({})
