# Phase 27 — Approval Digest

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 11 (autopilot approval queue)

## Objective

The approval queue is the user's decision bottleneck — and his stated
preference is batching, not a stream of interruptions. Today
autopilot.queue lists proposals flat; the user must open each one to
judge it. Phase 27 digests the queue: group pending proposals by risk
profile, show what's waiting and for how long, and recommend the batch
action per group — so the morning approval becomes one screen, one
decision per group.

## Proposed tools

1. **`autopilot.digest {}`** — the one-screen queue summary:
   - lists all pending proposals with age, step count, tools involved
   - max step risk per proposal (from the bound registry's tool risk
     labels, falling back to the policy RISK_TABLE, else "unknown" —
     source disclosed per proposal)
   - groups: `all_low` (safe to batch-approve), `has_medium`
     (review first), `has_high_or_unknown` (careful review)
   - a markdown digest ready to show the user, plus the structured
     groups with proposal ids for one-tap `autopilot.approve`
   - read-only: never approves, never executes

## Design constraints

- The digest is advisory — risk labels come from the registry/policy,
  not from the digest's own judgment; "unknown" is a real bucket, not
  a guess
- Empty queue returns a clean "nothing waiting" — not an error
- Age computed from created_at honestly; unparseable timestamps say
  "unknown age" rather than 0
- No new permissions, no execution surface

## Verification

- ~10 new tests: empty queue; all-low group recommendation; medium
  group flagged; high/unknown group flagged; unknown tool risk when
  no registry bound; age math; digest markdown contains the groups;
  digest is read-only (queue unchanged after); proposal ids usable
  for approve; registration contract
- Live: seed three proposals (low / medium / unknown tool), show the
  digest

## Estimated deltas

- +1 static tool
- ~314 static / ~440,660 total addressable
- ~10 new tests

## Honest limitations to keep

- A risk label is a static tag, not a guarantee — "all_low" means
  "labeled low by the registry", not "provably harmless"
- The digest can't see proposal *content* semantics (a low-risk tool
  with a malicious argument still reads low)
- Recommendation is a sorting aid; the approval decision stays human
