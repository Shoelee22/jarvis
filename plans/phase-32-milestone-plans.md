# Phase 32 — milestone plans

**Status:** BUILT 2026-09-27 (8 tests green)
**Builds on:** Phase 10/24 (plans, expect clauses)

## Objective

Plans die when the session ends. Milestones make long-horizon work
visible and trustworthy across sessions: named checkpoints bound to
step ids, earned only when their steps are done.

## Proposed tools

1. **`brain.plan {goal, steps[], milestones?[]}`** — optional
   `milestones: [{id?, title, step_ids[], due?}]`. Milestone step ids
   must exist in the plan.
2. **`brain.milestone {plan_id, milestone_id, note?}`** — mark done.
   Refuses unless every bound step is done.
3. **`brain.plan_status`** — extended with a `milestones[]` section
   (steps_done/steps_total per milestone).

## Design constraints

- Milestones live in `plan_milestones`, never rewritten history —
  done flags only
- A milestone with an unknown step id is rejected at plan time
- `due` is a free string ("Friday"), not parsed — no false precision

## Verification (to write)

- ~8 tests: milestone created with plan; invalid step id rejected;
  early mark refused naming the undone steps; mark after steps done
  works; idempotent re-mark; unknown plan/milestone errors;
  plan_status shows progress; registration contract

## Honest limitations

- Milestones don't auto-detect completion — someone must call
  brain.milestone (or the agent does it as part of its loop)
- `due` is decorative; nothing enforces or reminds
