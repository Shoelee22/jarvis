"""Offline tests for the proactive suggestions + standing rules pack (Phase 11).

Uses tmp sqlite DBs via JARVIS_PROACTIVE_DB / JARVIS_AUTOPILOT_DB /
JARVIS_AUTONOMY_DB; a stub Agent whose registry records every call
(tool, args, actor); and the REAL sibling packs (autopilot_pack for the
proposal queue, autonomy_pack for the kill switch) — both exist in the tree.

The core invariant under test: NOTHING executes a medium-or-higher-risk
action. Suggestions land in the autopilot queue; rules may only run low-risk
reads or create proposals.
"""
from __future__ import annotations

import pytest

import jarvis.tools.builtin.autopilot_pack as autopilot_pack
import jarvis.tools.builtin.autonomy_pack as autonomy_pack
import jarvis.tools.builtin.proactive_pack as pp

# Tools the pack is allowed to invoke through the registry: low-risk reads
# only (plus workers.list for the briefing). Anything else with
# actor="proactive" would be an escalation.
ALLOWED_READS = {"inbox.triage", "calendar.read", "workers.logs", "workers.list"}


class StubRegistry:
    """Mimics Registry.call(name, args, actor=...). Records every call and
    returns canned handler-style dicts (no {"ok","result"} envelope)."""

    def __init__(self, results: dict | None = None):
        self.calls: list[dict] = []
        self.results = results or {}

    def call(self, name, args, actor="agent", **kwargs):
        self.calls.append({"tool": name, "args": dict(args or {}), "actor": actor})
        if name in self.results:
            return self.results[name]
        return {"error": f"tool '{name}' not stubbed"}


class StubAgent:
    def __init__(self, results: dict | None = None):
        self.registry = StubRegistry(results)


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_PROACTIVE_DB", str(tmp_path / "proactive.db"))
    monkeypatch.setenv("JARVIS_AUTOPILOT_DB", str(tmp_path / "autopilot.db"))
    monkeypatch.setenv("JARVIS_AUTONOMY_DB", str(tmp_path / "autonomy.db"))
    return tmp_path


@pytest.fixture
def agent(monkeypatch):
    fa = StubAgent()
    monkeypatch.setattr(pp, "_AGENT", fa)
    yield fa
    monkeypatch.setattr(pp, "_AGENT", None)


def _queued() -> list[dict]:
    """Pending proposals in the real autopilot queue."""
    return autopilot_pack.queue_handler({})["pending"]


def _add_rule(when, then, name="test rule"):
    return pp.add_handler({"name": name, "when": when, "then": then})


def _assert_reads_only(reg: StubRegistry):
    """Every registry call was a low-risk read made as actor='proactive' —
    i.e. nothing executed beyond observation."""
    assert reg.calls, "expected at least one registry call"
    for c in reg.calls:
        assert c["actor"] == "proactive", f"unexpected actor: {c}"
        assert c["tool"] in ALLOWED_READS, f"non-read tool called: {c}"


# ------------------------------------------------- rules.add validation
def test_add_rejects_unknown_trigger(tmpdb):
    res = _add_rule({"trigger": "price_watch"},
                    [{"action": "propose", "goal": "g", "steps": []}])
    assert "error" in res
    assert "unknown trigger" in res["error"]
    assert "inbox_triage" in res["error"]  # catalog is listed honestly


def test_add_rejects_medium_risk_then_tool(tmpdb):
    res = _add_rule({"trigger": "inbox_triage"},
                    [{"action": "tool", "tool": "mail.send", "args": {}}])
    assert res == {"error": ("rules cannot execute 'mail.send' (risk=high): "
                             "rules may only run low-risk reads or create proposals")}


def test_add_rejects_unknown_then_tool(tmpdb):
    res = _add_rule({"trigger": "inbox_triage"},
                    [{"action": "tool", "tool": "teleport.do", "args": {}}])
    assert "error" in res
    assert "risk=unknown" in res["error"]


def test_add_rejects_unknown_action_kind(tmpdb):
    res = _add_rule({"trigger": "inbox_triage"},
                    [{"action": "execute", "goal": "x"}])
    assert "error" in res
    assert "unknown action" in res["error"]


def test_add_requires_worker_name_param(tmpdb):
    res = _add_rule({"trigger": "worker_results"},
                    [{"action": "propose", "goal": "g", "steps": []}])
    assert "error" in res
    assert "when.params.name" in res["error"]


def test_add_accepts_low_risk_read_plus_propose(tmpdb):
    res = _add_rule({"trigger": "inbox_triage"},
                    [{"action": "propose", "goal": "Draft replies", "steps": ["read inbox"]},
                     {"action": "tool", "tool": "calendar.read", "args": {"days": 1}}])
    assert res["ok"] is True
    assert isinstance(res["id"], int)
    listed = pp.list_handler({})["rules"]
    assert [r["id"] for r in listed] == [res["id"]]
    assert listed[0]["when"]["trigger"] == "inbox_triage"
    assert listed[0]["then"][0]["action"] == "propose"
    assert listed[0]["then"][1]["tool"] == "calendar.read"


def test_remove(tmpdb):
    res = _add_rule({"trigger": "calendar_today"},
                    [{"action": "propose", "goal": "g", "steps": []}])
    rid = res["id"]
    out = pp.remove_handler({"id": rid})
    assert out == {"ok": True, "id": rid, "removed": True}
    assert pp.list_handler({})["rules"] == []
    assert "error" in pp.remove_handler({"id": rid})


# ------------------------------------------------- proactive.scan
def test_scan_needs_bound_agent(tmpdb):
    assert "error" in pp.scan_handler({})


def test_scan_kill_switch_skips(tmpdb, agent, monkeypatch):
    autonomy_pack.off_handler({})
    try:
        assert autonomy_pack.is_killed() is True
        out = pp.scan_handler({})
        assert out["skipped"] == "autonomy off"
        assert out["rules_checked"] == 0
        assert agent.registry.calls == []  # no reads happened at all
        assert _queued() == []
    finally:
        autonomy_pack.on_handler({})


def test_scan_proceeds_when_not_killed(tmpdb, agent):
    assert autonomy_pack.is_killed() is False
    _add_rule({"trigger": "calendar_today"},
              [{"action": "propose", "goal": "g", "steps": []}])
    agent.registry.results["calendar.read"] = {"events": [], "count": 0}
    out = pp.scan_handler({})
    assert out["rules_checked"] == 1
    assert out["proposals_created"] == 0
    assert out["fired"] == []


def test_scan_trigger_fires_and_queues_proposal(tmpdb, agent):
    rid = _add_rule(
        {"trigger": "inbox_triage"},
        [{"action": "propose", "goal": "Reply to urgent client mail",
          "steps": ["draft reply", "queue for approval"]}],
        name="client mail watch")["id"]
    agent.registry.results["inbox.triage"] = {
        "messages": [{"channel": "gmail",
                      "subject_or_first_line": "Contract question",
                      "snippet": "…", "urgency": 75}],
        "channels": {"gmail": "ok (1)"}}

    out = pp.scan_handler({})

    assert out["rules_checked"] == 1
    assert out["fired"] == [rid]
    assert out["proposals_created"] == 1
    # The suggestion landed in the REAL autopilot queue, labeled proactive…
    pending = _queued()
    assert len(pending) == 1
    assert pending[0]["goal"].startswith("[proactive] ")
    assert "Reply to urgent client mail" in pending[0]["goal"]
    assert all(s["tool"] == "manual" for s in pending[0]["steps"])
    # …and nothing else was executed: only low-risk reads as actor=proactive.
    _assert_reads_only(agent.registry)
    assert [c["tool"] for c in agent.registry.calls] == ["inbox.triage"]


def test_scan_then_tool_read_executes_low_risk_only(tmpdb, agent):
    _add_rule(
        {"trigger": "inbox_triage"},
        [{"action": "tool", "tool": "calendar.read", "args": {"days": 1}}])
    agent.registry.results["inbox.triage"] = {
        "messages": [{"channel": "gmail", "subject_or_first_line": "Hi",
                      "urgency": 10}],
        "channels": {"gmail": "ok (1)"}}
    agent.registry.results["calendar.read"] = {"events": [], "count": 0}

    out = pp.scan_handler({})
    assert out["proposals_created"] == 0  # reads don't create proposals
    _assert_reads_only(agent.registry)
    tools = [c["tool"] for c in agent.registry.calls]
    assert tools == ["inbox.triage", "calendar.read"]


def test_scan_worker_results_fires_on_errors(tmpdb, agent):
    _add_rule(
        {"trigger": "worker_results", "params": {"name": "nightly"}},
        [{"action": "propose", "goal": "Fix nightly worker",
          "steps": ["inspect logs"]}])
    agent.registry.results["workers.logs"] = {
        "logs": [{"kind": "error", "text": "disk full", "ts": 1},
                 {"kind": "run", "text": "ok", "ts": 2}]}
    out = pp.scan_handler({})
    assert out["proposals_created"] == 1
    assert len(_queued()) == 1
    _assert_reads_only(agent.registry)


# ------------------------------------------------- proactive.suggest
def test_suggest_queues_proposal_never_executes(tmpdb, agent):
    out = pp.suggest_handler({
        "trigger": "observed: client opened the demo link twice today",
        "proposal": {"goal": "Follow up with the client",
                     "steps": ["draft a check-in note"]}})
    assert out["ok"] is True
    assert out["note"].startswith("queued")
    pending = _queued()
    assert len(pending) == 1
    assert pending[0]["goal"].startswith("[proactive] ")
    assert "Follow up with the client" in pending[0]["goal"]
    assert agent.registry.calls == []  # suggest makes no tool calls at all


def test_suggest_rejects_bad_proposal(tmpdb, agent):
    assert "error" in pp.suggest_handler({"trigger": "x", "proposal": {"goal": ""}})
    assert "error" in pp.suggest_handler({"trigger": "", "proposal": {"goal": "g"}})


# ------------------------------------------------- proactive.briefing
def _briefing_results():
    return {
        "inbox.triage": {
            "messages": [
                {"channel": "gmail", "subject_or_first_line": "Invoice overdue",
                 "urgency": 80},
                {"channel": "telegram", "subject_or_first_line": "Hi!",
                 "urgency": 20}],
            "channels": {"gmail": "ok (1)", "telegram": "ok (1)"}},
        "calendar.read": {
            "events": [{"title": "Dentist", "start_iso": "2026-09-27T10:00:00",
                        "end_iso": "2026-09-27T10:30:00"}],
            "count": 1, "from": "2026-09-27", "to": "2026-09-28"},
        "workers.list": {"workers": [
            {"name": "nightly", "status": "active"}]},
        "workers.logs": {"logs": [
            {"kind": "error", "text": "disk full", "ts": 1}]},
    }


def test_briefing_composes_digest_and_queues(tmpdb, agent):
    agent.registry.results.update(_briefing_results())
    out = pp.briefing_handler({})

    digest = out["digest"]
    assert "## Inbox" in digest and "Invoice overdue" in digest
    assert "## Calendar" in digest and "Dentist" in digest
    assert "## Workers" in digest and "nightly" in digest
    # 2 inbox items + 1 calendar prep + 1 worker error = 4 proposals
    assert out["proposals_created"] == 4
    pending = _queued()
    assert len(pending) == 4
    assert all(p["goal"].startswith("[proactive] ") for p in pending)
    # Only low-risk reads were made, all as actor='proactive'.
    _assert_reads_only(agent.registry)


def test_briefing_needs_bound_agent(tmpdb):
    assert "error" in pp.briefing_handler({})


def test_briefing_marks_unavailable_source(tmpdb, agent):
    agent.registry.results.update(_briefing_results())
    del agent.registry.results["calendar.read"]
    out = pp.briefing_handler({})
    assert "_unavailable" in out["digest"]
    assert "Calendar" in out["digest"]
    assert out["proposals_created"] >= 1  # inbox + worker items still queued


def test_briefing_all_sources_down_is_honest_error(tmpdb, agent):
    out = pp.briefing_handler({})
    assert "error" in out
    assert "inbox" in out["error"] and "calendar" in out["error"]
