"""Phase 9 — Swarm pack tests (offline).

Covers: parallel launch via a fake agent, concat/vote merging, role
validation, unbound-agent honesty, unknown run_id errors, registration
contract, and a loose parallelism assertion.
"""
import sys
import time

sys.path.insert(0, "sidecar")

from jarvis.agent import roles as roles_mod
from jarvis.tools.builtin import swarm_pack


class FakeAgent:
    """Mimics Agent.delegate: sleeps briefly, returns a per-role reply."""

    def __init__(self, delay=0.05, replies=None):
        self.delay = delay
        self.replies = replies or {}
        self.calls = []

    def delegate(self, role, task, max_steps=8):
        time.sleep(self.delay)
        self.calls.append((role, task, max_steps))
        if role in self.replies:
            return {"reply": self.replies[role]}
        return {"reply": f"{role}:{task}"}


def _rebind(fake=None):
    # Reset module state between tests.
    with swarm_pack._RUNS_LOCK:
        swarm_pack._RUNS.clear()
    swarm_pack.bind_agent(fake)


def test_unbound_returns_honest_error():
    _rebind(None)
    out = swarm_pack.swarm_launch(
        {"goal": "g", "specialists": ["researcher"]})
    assert "error" in out and "not bound" in out["error"]


def test_launch_concat_all_replies_and_parallelism():
    fake = FakeAgent(delay=0.05)
    _rebind(fake)
    t0 = time.monotonic()
    out = swarm_pack.swarm_launch(
        {"goal": "the goal",
         "specialists": ["researcher", "coder", "writer"],
         "merge": "concat"})
    elapsed = time.monotonic() - t0
    assert out["status"] == "done"
    assert out["specialists"] == ["researcher", "coder", "writer"]
    assert out["merge"] == "concat"
    run_id = out["run_id"]

    rec = swarm_pack.swarm_results({"run_id": run_id})
    assert rec["status"] == "done"
    assert len(rec["results"]) == 3
    for role, res in zip(["researcher", "coder", "writer"], rec["results"]):
        assert res["reply"] == f"{role}:the goal", res
        assert role in res["role"]

    merged = rec["merged"]
    assert "researcher:the goal" in merged
    assert "coder:the goal" in merged
    assert "writer:the goal" in merged
    assert "## researcher" in merged and "## coder" in merged and "## writer" in merged

    # All delegate calls happened with max_steps=8.
    assert all(call[2] == 8 for call in fake.calls)

    # Loose parallelism: 3 x 0.05s sleeps must beat a generous bound of
    # the sequential time (0.15s). Parallel ~0.05s; sequential would be >=0.15s.
    assert elapsed < 0.14, f"looks sequential: {elapsed:.3f}s"

    # Status endpoint agrees.
    st = swarm_pack.swarm_status({"run_id": run_id})
    assert st["status"] == "done"
    assert st["goal"] == "the goal"
    assert st["specialists_done"] == 3 and st["specialists_total"] == 3


def test_vote_merge_picks_majority_and_notes_dissent():
    fake = FakeAgent(replies={
        "researcher": "  YES  ",   # normalised to "yes"
        "coder": "yes",
        "writer": "no",
    })
    _rebind(fake)
    out = swarm_pack.swarm_launch(
        {"goal": "agree?", "specialists": ["researcher", "coder", "writer"],
         "merge": "vote"})
    rec = swarm_pack.swarm_results({"run_id": out["run_id"]})
    merged = rec["merged"]
    assert "2/3" in merged
    assert "1 dissented" in merged
    assert "yes" in merged.split("\n\n")[-1]


def test_invalid_role_names_valid_roles():
    _rebind(FakeAgent())
    out = swarm_pack.swarm_launch(
        {"goal": "g", "specialists": ["nope"]})
    assert "error" in out
    for name in roles_mod.role_names():
        assert name in out["error"]


def test_too_many_specialists_rejected():
    _rebind(FakeAgent())
    out = swarm_pack.swarm_launch(
        {"goal": "g", "specialists": ["researcher", "coder", "writer",
                                      "planner", "researcher"]})
    assert "error" in out and "4" in out["error"]


def test_bad_merge_rejected():
    _rebind(FakeAgent())
    out = swarm_pack.swarm_launch(
        {"goal": "g", "specialists": ["researcher"], "merge": "majority"})
    assert "error" in out and "concat" in out["error"]


def test_empty_goal_rejected():
    _rebind(FakeAgent())
    out = swarm_pack.swarm_launch({"goal": "   ", "specialists": ["researcher"]})
    assert "error" in out and "goal" in out["error"]


def test_unknown_run_id_errors():
    _rebind(FakeAgent())
    assert "error" in swarm_pack.swarm_status({"run_id": "nope123"})
    assert "error" in swarm_pack.swarm_results({"run_id": "nope123"})
    assert "error" in swarm_pack.swarm_status({"run_id": ""})


def test_timeout_records_error_and_partial_status():
    class SlowAgent:
        def delegate(self, role, task, max_steps=8):
            if role == "coder":
                time.sleep(0.6)  # still completes, fine
            return {"reply": f"{role}:{task}"}

        def __init__(self):
            self.calls = 0

    _rebind(SlowAgent())
    # Shrink the per-future timeout to force a timeout without a slow suite.
    old = swarm_pack._PER_SPECIALIST_TIMEOUT_S
    swarm_pack._PER_SPECIALIST_TIMEOUT_S = 0.2
    try:
        out = swarm_pack.swarm_launch(
            {"goal": "g", "specialists": ["researcher", "coder", "writer"]})
    finally:
        swarm_pack._PER_SPECIALIST_TIMEOUT_S = old
    rec = swarm_pack.swarm_results({"run_id": out["run_id"]})
    assert rec["status"] == "partial"
    by_role = {r["role"]: r for r in rec["results"]}
    assert by_role["coder"]["error"] == "specialist timed out"
    assert by_role["researcher"]["reply"] == "researcher:g"
    assert "(no reply — specialist timed out)" in rec["merged"]


def test_handler_never_raises_on_bad_input():
    _rebind(FakeAgent())
    assert "error" in swarm_pack.swarm_launch(None)      # type: ignore[arg-type]
    assert "error" in swarm_pack.swarm_launch({})
    assert "error" in swarm_pack.swarm_launch({"specialists": "researcher"})
    assert "error" in swarm_pack.swarm_status(None)      # type: ignore[arg-type]
    assert "error" in swarm_pack.swarm_results(None)     # type: ignore[arg-type]


def test_register_contract_and_risk_table():
    _rebind(FakeAgent())
    from jarvis.tools.base import Registry
    reg = Registry()
    swarm_pack.register(reg)
    for name, risk in (("swarm.launch", "medium"),
                       ("swarm.status", "low"),
                       ("swarm.results", "low")):
        assert name in reg.tools, name
        assert reg.tools[name].risk == risk, (name, reg.tools[name].risk)
    assert swarm_pack.RISK_TABLE_ADDITIONS == {
        "swarm.launch": ("medium", False),
        "swarm.status": ("low", False),
        "swarm.results": ("low", False),
    }


def test_bind_agent_rebindable():
    _rebind(None)
    assert "error" in swarm_pack.swarm_launch(
        {"goal": "g", "specialists": ["researcher"]})
    _rebind(FakeAgent())
    out = swarm_pack.swarm_launch({"goal": "g", "specialists": ["researcher"]})
    assert out["status"] == "done"
