# Phase 38 — Persistent Multi-Day Coding Workspace

**Status:** BUILT 2026-09-27 (4 tests green)

## Problem
Project files persisted, but *context* evaporated between days: what was
I doing, what's left, what did I decide.

## Build
- `build.note {project, text}` in `builder_pack.py` — appends a dated
  entry to `<project>/.session.md` (plain markdown, human-readable,
  survives restarts and reinstalls).
- `build.resume {project}` — pick up where work stopped:
  - files modified in the last 7 days (newest first),
  - open TODOs/issues from the static `build.review`,
  - the last 10 session notes,
  - a `next_suggested_step` pointing at the first open item.
- Both low risk, read-only except the note append; containment jail
  reused from the existing project-name validation.

## Honesty
- The suggested step is a pointer (first open item), not a plan.
- Review data is static — parse only, never executed.

## Verified
- `test_phase38_session.py`: 4 passed — note→resume roundtrip,
  TODO surfacing + next-step pointer, unknown-project honesty,
  registration contract.
