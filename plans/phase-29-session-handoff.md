# Phase 29 — Session Handoff

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 20 (cycles), Phase 27 (approval queue), Phase 28 (lessons)

## Objective

Every conversation starts cold: the brain knows its stores but never
summarizes "where did we leave things?" for the next session. Phase 29
writes a compact carry-forward file — open cycles, active plans with
their next step, pending approvals, unresolved forecasts, recent
lessons, calibration snapshot — and reads it back as a briefing. The
morning starts with continuity, not amnesia.

## Proposed tools

1. **`brain.handoff {note?}`** — write the carry-forward file:
   - open cycles (id, goal, age_days)
   - active plans (goal, next runnable step id + tool)
   - pending approval count + their goals
   - unresolved forecast count
   - 5 most recent lessons
   - calibration snapshot (resolved count + Brier, when available)
   - optional free-text `note` from the caller (e.g. "user is
     mid-decision on X")
   - path: `~/workspace/jarvis/data/handoff.md`, overridable via
     `JARVIS_HANDOFF_FILE`; the file is plain markdown, human-readable
2. **`brain.resume {}`** — read the handoff file back as a session
   briefing; honest "no handoff recorded yet" when the file is
   missing (never invents state)

## Design constraints

- The file is a snapshot, not a live view — it says when it was
  written, and resume reports that timestamp
- Everything in it is re-derivable from the stores; the file is a
  convenience, not a second source of truth
- `note` is stored verbatim — the one place the caller's words go in
  unprocessed
- Low risk, no network, no execution

## Verification

- ~8 new tests: handoff writes the file with all sections; resume
  reads it back; resume with no file is honest; note stored verbatim;
  open cycle appears with age; active plan shows next step; empty
  stores produce an honest "all clear" handoff; registration contract
- Live: seed a cycle + plan, run handoff, cat the file, resume

## Estimated deltas

- +2 static tools
- ~317 static / ~440,663 total addressable
- ~8 new tests

## Honest limitations to keep

- A stale handoff is worse than none — the timestamp is prominent,
  and nothing auto-refreshes it (a future sleep-cycle hook could;
  note as future work)
- The handoff summarizes state; it doesn't restore in-progress tool
  calls or conversation context
- `note` is trusted verbatim — including if it's wrong
