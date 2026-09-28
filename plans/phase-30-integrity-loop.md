# Phase 30 — The Integrity Loop (CAPSTONE — planned, not built)

**Status:** BUILT 2026-09-27 (9 tests green)
**Builds on:** Phases 22–29 (the whole accountability stack)

## Objective

Phases 22–29 built seven accountability instruments, each answering one
question: what's stale (review), what's tense (contradictions), what's
verified (expect clauses), what's consolidated (memory), what's sound
(audit), what's waiting (digest), what was I wrong about (correct),
where did we leave off (handoff). Phase 30 is the capstone: one call
that runs the whole loop and produces a single "state of the brain"
report — the instrument the 2am sleep cycle and the morning briefing
both read.

## Proposed tools

1. **`brain.integrity {sample_traces?: int, write_handoff?: bool}`** —
   the integrity loop:
   - runs `brain.review` (stale cycles + Brier trend + weekly lessons)
   - runs `brain.contradictions` (open claim tensions)
   - audits the N most recent think traces (`sample_traces`, default 5)
     with `brain.audit`
   - pulls `brain.calibration` (Brier + buckets)
   - pulls `autopilot.digest` (queue risk groups)
   - composes one markdown report with a top-line verdict:
     `sound` / `needs_attention` / `degraded`, with the evidence behind
     each section
   - if `write_handoff` (default false): appends the report to the
     handoff file via `brain.handoff {note}`
   - read-only unless `write_handoff` is set — and even then it only
     writes the handoff file, never mutates cycles/traces/lessons
2. **Sleep-cycle wiring:** `sleep_pack` calls `brain.integrity` after
   `brain.review` in the 2am cycle and appends the verdict line to the
   morning briefing draft (same pattern as Phase 22's review hook)

## Design constraints

- The verdict is mechanical, not moral: `degraded` means "N checks
  failed", with N and the failures listed — never a vibe
- The loop reuses the existing tools by calling their handlers, not
  by reimplementing them — one definition of each check
- Audit sampling is bounded (default 5) so the 2am cycle stays fast;
  the sample is "most recent", disclosed as such, not "representative"
- No new stores, no new permissions

## Verification (when built)

- ~12 new tests: all-green run → `sound`; one stale cycle →
  `needs_attention`; tampered trace in sample → `degraded` with the
  trace named; empty brain → `sound` with honest "nothing to check"
  sections; `write_handoff:false` leaves the handoff file untouched;
  `write_handoff:true` appends; sleep-cycle wiring calls integrity
  after review; verdict is mechanical (same input → same verdict);
  registration contract
- Live: run integrity on the real stores, show the report

## Estimated deltas

- +1 static tool
- ~220 static / ~440,566 total addressable
- ~12 new tests

## Honest limitations to keep

- `sound` means "every check passed", not "the brain is right" —
  fabricated-but-consistent evidence still audits clean (Phase 26's
  limit, inherited)
- The loop can't see what it can't see: unlogged decisions,
  off-the-books corrections, and anything outside the stores
- A mechanical verdict can be gamed by a tool that lies consistently
  — the loop trusts its instruments, and says so
