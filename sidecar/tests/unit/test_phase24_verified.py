"""Phase 24 — Verified execution (offline).

Covers: expect clause validation at plan creation; ok/contains/field
clauses passing and failing; multi-clause AND semantics; missing field =
failure; steps without expect unchanged; run_step end-to-end through a
stub registry (pass -> done+verified, fail -> failed + replan_hint);
verification visible in plan_status.
"""
import os
import sys

sys.path.insert(0, "sidecar")

from jarvis.tools.builtin import brain_pack


def _use_db(tmp_path):
    os.environ["JARVIS_BRAIN_COG_DB"] = str(tmp_path / "cog.db")
    brain_pack.bind_agent(None)


class _Reg:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def call(self, name, args, actor="agent", confirmed=False, **kw):
        self.calls.append(name)
        return self._result


class _Agent:
    def __init__(self, reg):
        self.registry = reg


def _bind(result):
    brain_pack.bind_agent(_Agent(_Reg(result)))


# ---------------------------------------------------------------------------
# clause evaluation (pure)
# ---------------------------------------------------------------------------

def test_ok_clause_pass_and_fail():
    res = {"ok": True, "result": {"n": 1}}
    v = brain_pack._verify_step([{"ok": True}], res)
    assert v["passed"] and len(v["checks"]) == 1
    v2 = brain_pack._verify_step([{"ok": True}], {"ok": False, "error": "x"})
    assert not v2["passed"]
    assert "ok=False" in v2["checks"][0]["detail"]


def test_contains_clause():
    res = {"ok": True, "result": {"msg": "invoice created"}}
    v = brain_pack._verify_step([{"contains": "invoice"}], res)
    assert v["passed"]
    v2 = brain_pack._verify_step([{"contains": "refund"}], res)
    assert not v2["passed"]
    assert "NOT found" in v2["checks"][0]["detail"]


def test_field_clause_dotted_path():
    res = {"ok": True, "result": {"stats": {"n": 5}}}
    v = brain_pack._verify_step(
        [{"field": "result.stats.n", "equals": 5}], res)
    assert v["passed"]
    v2 = brain_pack._verify_step(
        [{"field": "result.stats.n", "equals": 6}], res)
    assert not v2["passed"]
    assert "MISMATCH" in v2["checks"][0]["detail"]


def test_field_clause_missing_key_fails():
    res = {"ok": True, "result": {}}
    v = brain_pack._verify_step(
        [{"field": "result.missing", "equals": 1}], res)
    assert not v["passed"]
    assert "missing" in v["checks"][0]["detail"]


def test_field_clause_list_index():
    res = {"ok": True, "result": {"items": ["a", "b"]}}
    v = brain_pack._verify_step(
        [{"field": "result.items.1", "equals": "b"}], res)
    assert v["passed"]


def test_multi_clause_and_semantics():
    res = {"ok": True, "result": {"msg": "invoice created"}}
    v = brain_pack._verify_step(
        [{"ok": True}, {"contains": "invoice"}], res)
    assert v["passed"]
    v2 = brain_pack._verify_step(
        [{"ok": True}, {"contains": "refund"}], res)
    assert not v2["passed"]


def test_no_expect_returns_none():
    assert brain_pack._verify_step(None, {"ok": True}) is None


# ---------------------------------------------------------------------------
# plan creation validation
# ---------------------------------------------------------------------------

def test_plan_rejects_bad_expect(tmp_path):
    _use_db(tmp_path)
    bad = [
        {"expect": "yes"},
        {"expect": [{"nope": 1}]},
        {"expect": [{"ok": True, "contains": "x"}]},
        {"expect": [{"field": "a.b"}]},  # no equals
        {"expect": [{"contains": 5}]},
    ]
    for i, b in enumerate(bad):
        r = brain_pack.brain_plan_handler(
            {"goal": "g", "steps": [{"id": f"s{i}", "tool": "t", **b}]})
        assert "error" in r, (b, r)


def test_plan_accepts_good_expect(tmp_path):
    _use_db(tmp_path)
    r = brain_pack.brain_plan_handler({
        "goal": "g",
        "steps": [{"id": "s1", "tool": "t",
                   "expect": [{"ok": True},
                              {"field": "result.n", "equals": 3}]}]})
    assert "plan_id" in r, r


# ---------------------------------------------------------------------------
# run_step end-to-end
# ---------------------------------------------------------------------------

def test_run_step_verifies_pass(tmp_path):
    _use_db(tmp_path)
    _bind({"ok": True, "result": {"n": 3}})
    p = brain_pack.brain_plan_handler({
        "goal": "g",
        "steps": [{"id": "s1", "tool": "echo",
                   "expect": [{"field": "result.n", "equals": 3}]}]})
    r = brain_pack.brain_run_step_handler({"plan_id": p["plan_id"]})
    assert "error" not in r, r
    assert r["step_status"] == "done"
    assert r["verification"]["passed"] is True
    assert r["replan_hint"] is None


def test_run_step_verifies_fail_marks_failed_with_hint(tmp_path):
    _use_db(tmp_path)
    _bind({"ok": True, "result": {"n": 4}})
    p = brain_pack.brain_plan_handler({
        "goal": "g",
        "steps": [{"id": "s1", "tool": "echo",
                   "expect": [{"field": "result.n", "equals": 3}]}]})
    r = brain_pack.brain_run_step_handler({"plan_id": p["plan_id"]})
    assert "error" not in r, r
    assert r["step_status"] == "failed"
    assert r["verification"]["passed"] is False
    assert "MISMATCH" in (r["replan_hint"] or "")
    st = brain_pack.brain_plan_status_handler({"plan_id": p["plan_id"]})
    s1 = st["plan"]["steps"][0]
    assert s1["verified"] is False
    assert s1["expect"] is True


def test_run_step_without_expect_unchanged(tmp_path):
    _use_db(tmp_path)
    _bind({"ok": True, "result": {"n": 9}})
    p = brain_pack.brain_plan_handler({
        "goal": "g", "steps": [{"id": "s1", "tool": "echo"}]})
    r = brain_pack.brain_run_step_handler({"plan_id": p["plan_id"]})
    assert "error" not in r, r
    assert r["step_status"] == "done"
    assert r["verification"] is None
    st = brain_pack.brain_plan_status_handler({"plan_id": p["plan_id"]})
    assert st["plan"]["steps"][0]["verified"] is None


def test_failed_verification_blocks_dependents(tmp_path):
    _use_db(tmp_path)
    _bind({"ok": True, "result": {"n": 1}})
    p = brain_pack.brain_plan_handler({
        "goal": "g",
        "steps": [{"id": "s1", "tool": "echo",
                   "expect": [{"contains": "nope"}]},
                  {"id": "s2", "tool": "echo", "depends_on": ["s1"]}]})
    brain_pack.brain_run_step_handler({"plan_id": p["plan_id"]})
    r2 = brain_pack.brain_run_step_handler({"plan_id": p["plan_id"]})
    assert "error" in r2
    assert "dependency failed" in r2["error"]
