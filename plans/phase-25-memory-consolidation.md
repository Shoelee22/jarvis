# Phase 25 — Memory Consolidation

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 15 (episodic memory), Phase 19 (brain.reflect lessons)

## Objective

Episodic memory grows forever; the brain doesn't — it consolidates.
Old episodes are raw experience; their durable value is the lesson.
Phase 25 rolls episodes older than N days into lessons via
brain.reflect, then marks them consolidated (never deletes: the raw
record stays queryable, it just stops being re-mined).

## Proposed tools

1. **`memory.consolidate {older_than_days?=90, dry_run?=true}`** —
   consolidation pass:
   - find episodes older than the threshold not yet consolidated
   - group them (by week, or by shared tags if present — keep it
     simple: chronological batches of up to 10)
   - run brain.reflect on each batch's combined text; file the lessons
     it returns (tagged source "consolidation")
   - mark the batch's episodes consolidated (a `consolidated` flag on
     the episode row; recall keeps working)
   - default `dry_run:true` — report what WOULD be consolidated and
     the lessons that would be filed; `dry_run:false` commits
2. **Consolidation ledger in the result**: batches processed, episodes
   rolled up, lessons filed, episodes skipped (already consolidated /
   too young)

## Design constraints

- Never delete episodes — consolidation marks, it doesn't burn
- dry_run defaults to true; the first real run is always a conscious
  choice
- Lessons go through the same brain.reflect path as live reflection
  (same dedupe, same honesty about heuristics)
- Consolidated episodes stay in recall; consolidation only stops
  re-mining them
- Bounded: max 50 episodes per run; report if more remain

## Verification

- ~10 new tests: dry_run reports without changing anything; real run
  marks episodes + files lessons; already-consolidated skipped;
  too-young skipped; batching at >10; empty store clean; episode
  recall still finds consolidated episodes; registration contract
- Live: seed old episodes, dry_run then real run, show lessons filed

## Estimated deltas

- +1 static tool
- ~312 static / ~440,658 total addressable
- ~10 new tests

## Honest limitations to keep

- brain.reflect's lesson extraction is heuristic — consolidation
  preserves the raw episodes precisely because the extraction is lossy
- Grouping by week is crude; a real system would cluster by topic
  (could use Phase 21's cluster tool — note as future work, don't build)
- Consolidation doesn't make the brain smarter; it makes the store
  cheaper to re-mine
