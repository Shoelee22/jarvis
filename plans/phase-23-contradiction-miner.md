# Phase 23 — Contradiction Miner

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 19 (adversarial metacognition), Phase 21 (semantic backend)

## Objective

Deliberation is only as good as the disagreements it notices. Today the
brain weighs evidence for/against but never spots two claims that
directly contradict each other. Phase 23 mines contradictions: cluster
claims by topic, flag opposite-stance pairs inside a cluster, and hand
them to deliberation as explicit tensions to resolve.

## Proposed tools

1. **`brain.contradictions {claims[]}`** — the miner:
   - topic-cluster claims (cosine ≥ 0.6 with the embedding backend when
     available, else Jaccard ≥ 0.25 on content tokens — method disclosed)
   - within each cluster, pair claims with opposing stance
     (for vs against via the existing cue-word stance detector)
   - return pairs with severity = the pair's topic similarity, plus the
     cluster topic and both claim texts
2. **Survey integration** — `brain.survey {contradictions:true}` runs the
   miner over the brief's episode + lesson texts and appends a
   "Tensions to resolve" section to `brief_text` (opt-in; off by default
   to keep the base survey cheap)

## Design constraints

- Stance detection stays the cue-word heuristic (labeled); the miner
  finds *candidate* contradictions, never verdicts
- Neutral-stance claims never pair — only for-vs-against
- Same-cluster threshold disclosed in every result (`method` field)
- Empty/duplicate claim lists return clean empties, not errors
- No backend → keyword path, same as Phase 21's honesty contract

## Verification

- ~12 new tests: opposite-stance pair found in one cluster; same-stance
  not paired; neutral never pairs; cross-cluster not paired; empty input;
  survey opt-in appends the tensions section; survey default off;
  keyword fallback path with no backend; registration contract
- Live: run over a mixed claim set with the fake backend, show pairs

## Estimated deltas

- +1 static tool (+1 schema flag on brain.survey)
- ~311 static / ~440,657 total addressable
- ~12 new tests

## Honest limitations to keep

- Cue-word stance is crude — "not bad" reads as against; sarcasm invisible
- Topic clustering is similarity, not understanding (Phase 21 caveats)
- The miner surfaces tensions; resolving them is still deliberation's job
