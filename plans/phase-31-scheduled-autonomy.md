# Phase 31 — Scheduled Autonomy

**Status:** BUILT 2026-09-27 (7 tests green)
**Builds on:** Phase 11 (approval queue), Phase 27 (digest)

## Objective

The approval queue is the safety net; nothing feeds it on a schedule.
Phase 31 connects them: recurring schedules that file proposals into
the queue — "every morning, draft my outreach follow-ups" — so
autonomy becomes rhythmic instead of sporadic.

## Proposed tools

1. **`autopilot.schedule {name, every_hours, goal, steps}`** — create
   a recurring schedule (name unique, every_hours ≥ 1). Validates steps
   like propose. Never executes.
2. **`autopilot.schedules {}`** — list schedules with next-due time.
3. **`autopilot.unschedule {name}`** — delete a schedule (its queued
   proposals stay; only future firings stop).
4. **`autopilot.tick {}`** — fire every due schedule: each files one
   proposal into the approval queue and resets its next-due clock.
   Returns what fired and the proposal ids.

## Design constraints

- The sidecar cannot wake itself — the host (shell/cron) calls tick;
  the tool is honest about that in its description
- Tick is idempotent per schedule per period: a schedule fires at most
  once per every_hours window even if tick is called repeatedly
- Firing only *proposes* — execution still needs approval
- A disabled kill switch blocks tick like approve

## Verification

- ~10 new tests: schedule/tick fires once; tick twice doesn't
  double-fire; not-due doesn't fire; unschedule stops future fires;
  past proposals survive unschedule; invalid every_hours rejected;
  duplicate name rejected; bad steps rejected; kill switch blocks;
  registration contract
- Live: schedule an hourly digest, tick, show the queued proposal

## Estimated deltas

- +4 static tools, ~10 new tests

## Honest limitations

- "Every morning at 9am" is really "every 24h from creation" —
  wall-clock cron expressions are future work
- If nobody calls tick, nothing fires — the schedule is a loaded
  gun with no finger on the trigger until the host wires it up
