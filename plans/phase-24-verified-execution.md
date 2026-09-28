# Phase 24 — Verified Execution

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 7/15 (plans + steps), Phase 19 (deliberation)

## Objective

Plans execute blind: a step runs, its result is recorded, and nobody
checks whether the step actually did what it promised. Phase 24 gives
every plan step a checkable expectation — and verifies it mechanically
after the step runs.

## Proposed design

1. **`brain.plan` steps accept `expect` clauses** (optional per step):
   - `{"ok": true}` — the step's result must not carry an error
   - `{"contains": "substring"}` — the step's JSON result must contain
     the substring somewhere
   - `{"field": "path.to.key", "equals": value}` — simple dotted-path
     equality on the result JSON
   - multiple clauses allowed; ALL must pass
2. **`brain.run_step` auto-verifies**: after executing a step, evaluate
   its `expect` clauses against the recorded result and store
   `verification: {passed: bool, checks: [...]}` on the step record.
   A failed check marks the step `failed` and appends a
   "replan hint" — which clause failed and on what value — instead of
   silently continuing.
3. **`brain.plan` gains a `plan.verify_report`-style summary? No — keep
   it lean:** verification lives on each step record and on the plan's
   `steps` listing; a caller (or the morning review) reads it.

## Design constraints

- Checks are mechanical string/JSON comparisons — never LLM judgment;
  the verifier cannot be "convinced" by flowery output
- `expect` is optional; steps without it behave exactly as today
- Verification failures never auto-retry or auto-replan — they surface
  with the failing clause and the observed value, and stop the chain's
  "all green" illusion
- No new permissions: verification reads step results the plan already
  recorded

## Verification

- ~10 new tests: ok-clause passes/fails; contains-clause passes/fails;
  field-equals passes/fails (dotted path, missing key = fail);
  multi-clause AND semantics; step without expect unchanged; failed
  verification marks step failed with replan hint; real plan run_step
  end-to-end through the registry
- Live: a two-step plan where step 2's expectation fails, show the hint

## Estimated deltas

- +0 new static tools (schema extension on brain.plan steps + behavior
  change in brain.run_step)
- static count unchanged (~311)
- ~10 new tests

## Honest limitations to keep

- A check passing means the output *looked right*, not that the world
  changed — {"contains": "invoice"} does not prove money moved
- The verifier checks the step's own recorded result; a lying tool
  passes its checks
- `equals` on floats is exact-match — use it for strings/ints/bools
