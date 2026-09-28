# Phase 35 — Explainability: "Why Did You Do That?"

**Status:** BUILT 2026-09-27 (4 tests green)

## Problem
As JARVIS acts more autonomously, the user needs a simple way to ask
"why did you do that?" — without digging through logs.

## Build
- `sidecar/jarvis/tools/builtin/agent_pack.py` (new pack)
- `agent.why {tool?, limit?=10}` — reads the agent's existing audit log
  (`<data_dir>/jarvis.db`, written by `jarvis/agent/audit.py`), newest
  first: who called what, with which args, what it returned, risk label.
- Filter by tool-name substring.
- Honest when empty: "No audit records yet — the audit log is written
  as the agent acts."
- Secrets are already redacted at audit write time; the tool only reads.

## Honesty
- Explains WHAT happened and with what — the WHY lives in the plans
  and approval proposals that authorized the action, not in the log.
- Read-only, low risk, no network.

## Verified
- `test_phase35_why.py`: 4 passed (ordering, filter, empty-log honesty,
  registration contract).
