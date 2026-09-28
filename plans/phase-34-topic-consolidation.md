# Phase 34 — topic consolidation

**Status:** BUILT 2026-09-27 (5 tests green)
**Builds on:** Phase 25 (consolidate), Phase 21 (cluster)

## Objective

Chronological consolidation mixes unrelated episodes into one batch
and produces scattered lessons. Phase 34 groups episodes by topic
first, so each batch — and each lesson — is coherent.

## Proposed change

- **`memory.consolidate {by_topic?}`** — when true, episode texts are
  grouped by `brain.cluster` (threshold 0.5) and each cluster becomes
  a batch (cap 10 per batch). Cluster failure falls back to
  chronological with an honest note.

## Design constraints

- Threshold 0.5 (topics, not near-duplicates); disclosed in the method
  string
- Same guarantees as Phase 25: dry-run default, mark-never-delete

## Verification (to write)

- ~6 tests: by_topic groups related episodes into one batch;
  unrelated episodes split; chronological still default; dry-run
  reports topic batches; real run files per-topic lessons and marks;
  cluster failure falls back honestly

## Honest limitations

- Topic quality is only as good as the similarity backend; with the
  keyword fallback, "topics" are word-overlap groups — labeled as such
