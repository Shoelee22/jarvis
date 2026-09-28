"""Swarm pack (Phase 9): run multiple specialist sub-agents in parallel on one
goal, then merge their replies.

Wiring (mirrors delegate_tool.py):
- The live Agent is bound once by the IPC server via bind_agent().
- Outside a live session every tool returns an honest error instead of
  faking parallelism.

Tools:
- swarm.launch {goal, specialists: [1-4 role names], merge: "concat"|"vote"}
- swarm.status {run_id}
- swarm.results {run_id}

All handlers take dict -> return dict and never raise.
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# --- module-level binding + run registry ----------------------------------

_AGENT = None


def bind_agent(agent) -> None:
    """Called once by the IPC server after the main Agent is constructed."""
    global _AGENT
    _AGENT = agent


_RUNS: dict = {}          # run_id -> run record
_RUNS_LOCK = threading.Lock()

_MAX_SPECIALISTS = 4
_PER_SPECIALIST_TIMEOUT_S = 120
_DELEGATE_STEPS = 8


def _get_runs() -> dict:
    with _RUNS_LOCK:
        return dict(_RUNS)


def _record_run(run_id: str, record: dict) -> None:
    with _RUNS_LOCK:
        _RUNS[run_id] = record


# --- specialist execution --------------------------------------------------


def _run_specialist(role: str, goal: str) -> dict:
    """One specialist pass via Agent.delegate. Never raises."""
    try:
        out = _AGENT.delegate(role, goal, _DELEGATE_STEPS)
    except Exception as e:  # defensive: a fake/mock agent must not kill the swarm
        return {"role": role, "ok": False,
                "error": f"{type(e).__name__}: {e}"}
    if not isinstance(out, dict):
        return {"role": role, "ok": False,
                "error": f"delegate returned non-dict: {type(out).__name__}"}
    out.setdefault("role", role)
    return out


def _specialist_reply_text(result: dict) -> str | None:
    reply = result.get("reply")
    if isinstance(reply, str) and reply.strip():
        return reply
    return None


# --- merge strategies ------------------------------------------------------


def _merge_concat(specialists: list[str], results: list[dict]) -> str:
    parts = []
    for role, result in zip(specialists, results):
        text = _specialist_reply_text(result)
        if text is None:
            text = f"(no reply — {result.get('error', 'unknown error')})"
        parts.append(f"## {role}\n{text}")
    return "\n\n".join(parts)


def _merge_vote(specialists: list[str], results: list[dict]) -> str:
    normalized: dict[str, list[str]] = {}  # normalized reply -> roles that said it
    for role, result in zip(specialists, results):
        text = _specialist_reply_text(result)
        key = (text or "").casefold().strip()
        normalized.setdefault(key, []).append(role)
    winner = max(normalized.items(), key=lambda kv: len(kv[1]))[0]
    winning_roles = normalized[winner]
    dissent = len(specialists) - len(winning_roles)
    header = (f"{len(winning_roles)}/{len(specialists)} specialists agreed "
              f"({', '.join(winning_roles)}); {dissent} dissented.")
    return f"{header}\n\n{winner}"


# --- handlers ---------------------------------------------------------------


def swarm_launch(args: dict) -> dict:
    try:
        return _swarm_launch(args or {})
    except Exception as e:
        return {"error": f"swarm.launch failed: {type(e).__name__}: {e}"}


def _swarm_launch(args: dict) -> dict:
    if _AGENT is None:
        return {"error": "swarm unavailable: agent not bound (start the sidecar)"}
    goal = (args.get("goal") or "").strip()
    if not goal:
        return {"error": "goal is required"}
    raw = args.get("specialists")
    if not isinstance(raw, (list, tuple)) or not raw:
        return {"error": "specialists must be a non-empty list of 1-4 role names"}
    if len(raw) > _MAX_SPECIALISTS:
        return {"error": f"at most {_MAX_SPECIALISTS} specialists per swarm "
                         f"(got {len(raw)})"}
    merge = str(args.get("merge", "concat")).strip().lower() or "concat"
    if merge not in ("concat", "vote"):
        return {"error": "merge must be 'concat' or 'vote'"}

    from ...agent.roles import get_role, role_names
    specialists: list[str] = []
    for name in raw:
        if not isinstance(name, str) or not name.strip():
            return {"error": f"invalid specialist name {name!r} — "
                             f"choose from: {', '.join(role_names())}"}
        role = get_role(name)
        if role is None:
            return {"error": f"unknown role '{name}' — choose from: "
                             f"{', '.join(role_names())}"}
        specialists.append(role.name)

    run_id = uuid.uuid4().hex[:12]
    record: dict = {
        "run_id": run_id,
        "status": "running",
        "goal": goal,
        "specialists": list(specialists),
        "merge": merge,
        "results": [],
        "merged": None,
        "error": None,
        "started_at": time.time(),
    }
    _record_run(run_id, record)

    results: list[dict] = [None] * len(specialists)
    with ThreadPoolExecutor(max_workers=min(_MAX_SPECIALISTS, len(specialists))) as pool:
        futures = {pool.submit(_run_specialist, role, goal): i
                   for i, role in enumerate(specialists)}
        for future, i in futures.items():
            try:
                results[i] = future.result(timeout=_PER_SPECIALIST_TIMEOUT_S)
            except TimeoutError:
                results[i] = {"role": specialists[i], "ok": False,
                              "error": "specialist timed out"}
            except Exception as e:  # future itself died; never raise
                results[i] = {"role": specialists[i], "ok": False,
                              "error": f"{type(e).__name__}: {e}"}

    # A specialist "failed" if it contributed no usable reply text (this also
    # covers explicit {"ok": False} results and ones without an "ok" key).
    failed = sum(1 for r in results if _specialist_reply_text(r or {}) is None)
    merged = _merge_vote(specialists, results) if merge == "vote" \
        else _merge_concat(specialists, results)
    if failed == len(specialists):
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "done"
    record.update({"status": status, "results": results, "merged": merged,
                   "finished_at": time.time()})
    _record_run(run_id, record)
    return {"run_id": run_id, "specialists": specialists, "merge": merge,
            "status": status}


def swarm_status(args: dict) -> dict:
    try:
        run_id = str((args or {}).get("run_id") or "").strip()
        if not run_id:
            return {"error": "run_id is required"}
        with _RUNS_LOCK:
            record = _RUNS.get(run_id)
            if record is None:
                return {"error": f"unknown run_id '{run_id}'"}
            record = dict(record)
        results = record.get("results") or []
        total = len(record.get("specialists") or [])
        # launch is synchronous, so results are stored all at once; done counts
        # non-None entries for the (future) case of a live-running record.
        done = sum(1 for r in results if r is not None)
        return {"run_id": run_id, "status": record.get("status"),
                "goal": record.get("goal"),
                "specialists_done": done, "specialists_total": total}
    except Exception as e:
        return {"error": f"swarm.status failed: {type(e).__name__}: {e}"}


def swarm_results(args: dict) -> dict:
    try:
        run_id = str((args or {}).get("run_id") or "").strip()
        if not run_id:
            return {"error": "run_id is required"}
        with _RUNS_LOCK:
            record = _RUNS.get(run_id)
            if record is None:
                return {"error": f"unknown run_id '{run_id}'"}
            record = dict(record)
        return {
            "run_id": record["run_id"],
            "status": record.get("status"),
            "goal": record.get("goal"),
            "specialists": record.get("specialists"),
            "merge": record.get("merge"),
            "results": record.get("results"),
            "merged": record.get("merged"),
            "error": record.get("error"),
        }
    except Exception as e:
        return {"error": f"swarm.results failed: {type(e).__name__}: {e}"}


# --- registration contract --------------------------------------------------

def register(reg) -> None:
    """Wire the three swarm tools into a Registry (call from builtin.__init__)."""
    from ..base import Tool
    reg.register(Tool(
        name="swarm.launch",
        description=("Run 1-4 specialist sub-agents (researcher/coder/writer/planner) "
                     "in parallel on one goal and merge their replies."),
        schema={"goal": "string", "specialists": "list",
                "merge": "string?"},
        handler=swarm_launch, risk="medium", needs_network=False))
    reg.register(Tool(
        name="swarm.status",
        description="Status of a swarm run: status, goal, specialists done/total.",
        schema={"run_id": "string"},
        handler=swarm_status, risk="low", needs_network=False))
    reg.register(Tool(
        name="swarm.results",
        description="Full record of a swarm run: per-specialist replies plus merged output.",
        schema={"run_id": "string"},
        handler=swarm_results, risk="low", needs_network=False))


RISK_TABLE_ADDITIONS = {
    "swarm.launch": ("medium", False),
    "swarm.status": ("low", False),
    "swarm.results": ("low", False),
}
