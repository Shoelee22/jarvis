# Phase 33 — structured debate

**Status:** BUILT 2026-09-27 (7 tests green)
**Builds on:** Phase 10 (think traces)

## Objective

One brain playing devil's advocate with itself is better than none,
but the passes blur together. Phase 33 structures the argument:
each side gets its own recorded think pass, primed with its stance,
then a judging pass reads only the recorded conclusions.

## Proposed tools

1. **`brain.debate {question, sides[{name, stance}]}`** — 2–4 sides,
   one think per side, one judging think. Returns per-side
   conclusions + verdict + all trace ids.

## Design constraints

- Honest framing: one mechanical brain, multiple primed passes —
  not independent minds; the tool's description says so
- The judge sees only recorded conclusions, never the sides'
  internals — so thin arguments judge thin
- Every pass is a real stored trace (auditable by Phase 26)

## Verification (to write)

- ~7 tests: two-sided debate returns both conclusions + verdict;
  three sides work; 1 side rejected; 5 sides rejected; missing
  stance rejected; all trace ids stored and auditable; judge
  verdict present; registration contract

## Honest limitations

- Stance priming is cue-word level — the "sides" share the same
  evidence and the same heuristics; expect correlated arguments,
  not genuine disagreement
