"""Roles + tasks.delegate tests (offline)."""
from __future__ import annotations

import tempfile

from jarvis.agent.core import Agent, FakeLLM
from jarvis.agent.audit import AuditLog
from jarvis.agent.roles import ROLES, get_role, role_names
from jarvis.tools.builtin import build_registry
from jarvis.tools.builtin import delegate_tool


def _agent():
    d = tempfile.mkdtemp()
    reg = build_registry(d)
    return Agent(FakeLLM(), reg, AuditLog(d + "/a.db")), reg


def test_four_roles_defined():
    assert role_names() == ["coder", "planner", "researcher", "writer"]


def test_role_prompts_nonempty_and_distinct():
    prompts = [r.system_prompt for r in ROLES.values()]
    assert all(len(p) > 100 for p in prompts)
    assert len(set(prompts)) == 4


def test_preferred_tools_all_exist():
    agent, reg = _agent()
    missing = {t for r in ROLES.values() for t in r.preferred_tools} - set(reg.tools)
    assert not missing, f"role tools not registered: {missing}"


def test_get_role_case_insensitive():
    assert get_role("Researcher").name == "researcher"
    assert get_role("nope") is None


def test_delegate_unknown_role():
    agent, _ = _agent()
    out = agent.delegate("janitor", "clean up")
    assert out["ok"] is False and "unknown role" in out["error"]


def test_delegate_empty_task():
    agent, _ = _agent()
    out = agent.delegate("coder", "   ")
    assert out["ok"] is False


def test_delegate_runs_subloop_with_role_tools():
    # Scripted LLM: one tool call then done.
    llm = FakeLLM(script=[
        {"thought": "check", "tool_calls": [{"name": "calc.eval", "args": {"expression": "2+2"}}],
         "text": "checking"},
        {"thought": "done", "tool_calls": [], "text": "Four."},
    ])
    d = tempfile.mkdtemp()
    reg = build_registry(d)
    agent = Agent(llm, reg, AuditLog(d + "/a.db"))
    out = agent.delegate("planner", "what is 2+2", max_steps=4)
    assert out["ok"] is True and out["role"] == "planner"
    assert out["steps_used"] == 1
    assert out["timeline"][0]["tool"] == "calc.eval"


def test_delegate_max_steps_bounded():
    agent, _ = _agent()
    out = agent.delegate("writer", "x", max_steps=999)
    assert out["ok"] is True  # FakeLLM finishes immediately; bound just must not crash


def test_delegate_tool_unbound_honest_error():
    delegate_tool._AGENT = None
    r = delegate_tool.delegate_handler({"role": "coder", "task": "hi"})
    assert r == {"error": "delegation is only available inside a live agent session"}


def test_delegate_tool_bound_end_to_end():
    agent, reg = _agent()
    delegate_tool.bind_agent(agent)
    try:
        r = reg.call("tasks.delegate", {"role": "researcher", "task": "say hi"},
                     actor="t")
        assert r["ok"] is True and r["result"]["ok"] is True
        assert r["result"]["role"] == "researcher"
    finally:
        delegate_tool._AGENT = None


def test_tasks_delegate_in_registry_with_medium_risk():
    _, reg = _agent()
    assert "tasks.delegate" in reg.tools
    assert reg.tools["tasks.delegate"].risk == "medium"
