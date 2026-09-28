# Phase 22 — The Accountable Dreamer

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 20 (closed-loop cycles + ledger), sleep_pack (2am dream cycle)

## Objective

Phase 20 built the accountability ledger — open cycles wait for outcomes,
closed cycles file lessons. But nothing *reads* the ledger unless asked.
Phase 22 wires the ledger into the 2am sleep-and-dream cycle: every night,
JARVIS reviews its own open decisions, scores its stale forecasts, and
drafts the accountability section of the morning briefing. The brain that
checks its homework while you sleep.

## Proposed tools / hooks

1. **`brain.review {}`** — nightly accountability pass over the cycles ledger:
   - stale open cycles (> configurable days, default 7) flagged with age
   - forecast hit-rate over recently closed cycles (Brier mean)
   - lessons filed this week, top 3 by recurrence
   - returns a briefing-ready markdown section
2. **Dream-cycle hook** — sleep_pack's 2am run calls `brain.review` and
   merges its output into the morning briefing draft (existing briefing
   path, no new delivery channel)
3. **Stale-cycle nudge** — for open cycles past the stale threshold, queue
   ONE batched nudge in the approval queue ("3 decisions still waiting for
   outcomes: pricing tier, ...") — never nag per-cycle, never act on the
   user's behalf beyond the queue
4. **`brain.calibration` extension** — feed closed-cycle forecasts into the
   existing calibration buckets so the morning briefing shows a real
   calibration trend line over time

## Design constraints

- Review is heuristic (keyword overlap on cycle goals to find related
  cycles), labeled as such; the *statistics* (counts, ages, Brier means)
  are real
- The nudge goes through the existing approval queue — same comfort model
  as everything else: act inside permissions, queue the rest
- No auto-closing cycles: only the outcome reporter closes a cycle; the
  dreamer may *suggest* an outcome ("churn stayed flat per the metrics
  episode on 2026-09-20 — close as success?") as a one-tap approval
- Suggested outcomes must cite the evidence episode; a suggestion with no
  cited evidence is not made
- Keep the dream run bounded: review is O(open cycles), single pass,
  no model calls

## Verification

- ~12 new tests: stale detection thresholds, briefing section shape,
  nudge batching (one queue entry for N stale cycles, not N entries),
  suggested-outcome evidence citation requirement, calibration trend math
- Live: seed 3 cycles (1 fresh open, 1 stale open, 1 closed with forecast),
  run brain.review, show the briefing section
- Full sidecar suite stays green

## Estimated deltas

- +2 static tools (brain.review, plus the dream hook is internal)
- ~311 static / ~440,657 total addressable
- ~12 new tests

## Honest limitations to keep

- The dreamer can't verify outcomes in the real world — it can only match
  them against episodic memory; a wrong episode means a wrong suggestion
- Calibration trend needs ~10 resolved forecasts before it's meaningful
  (Phase 19 rule stands)
- Negation/embedding caveats from Phase 21 apply to any semantic matching
  in the review
