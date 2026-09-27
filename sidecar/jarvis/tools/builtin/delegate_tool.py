"""tasks.delegate tool: hand a task to a specialist role sub-agent.

The live Agent is bound at server startup via bind_agent(). Outside a live
session the tool returns an honest error instead of faking delegation.
"""
from __future__ import annotations

_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


def delegate_handler(args: dict) -> dict:
    if _AGENT is None:
        return {"error": "delegation is only available inside a live agent session"}
    role = (args.get("role") or "").strip()
    task = (args.get("task") or "").strip()
    try:
        max_steps = int(args.get("max_steps", 8))
    except (TypeError, ValueError):
        max_steps = 8
    return _AGENT.delegate(role, task, max_steps=max_steps)
