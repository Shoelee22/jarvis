# Phase 28 — Correction Learning

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 14 (traces), Phase 19 (lessons)

## Objective

The brain learns from outcomes (Phase 20 cycles, Phase 19 forecasts)
but never from being *told it was wrong*. When the user corrects a
deliberation — "no, the right answer was X" — that correction should
become durable knowledge, linked to the trace it corrects, so future
surveys surface it. Phase 28 closes that loop with one tool.

## Proposed tools

1. **`brain.correct {trace_id, what_was_wrong, right_answer}`** —
   file a correction:
   - verifies the trace exists (unknown trace_id is an error —
     corrections attach to real deliberations, not vibes)
   - composes a lesson: `[Correction] {right_answer} (trace {trace_id}
     concluded otherwise: {what_was_wrong})`
   - dedupes against stored lessons with the same rule as
     brain.reflect (normalized fuzzy match ≥ 0.85) — the same
     correction twice stores once
   - inserts with source `correction` so it's distinguishable from
     reflected lessons; survey's existing lesson ranking surfaces it
     for similar goals with no new machinery

## Design constraints

- Both fields required and non-empty — a correction without the
  right answer is just a complaint
- The lesson text is the user's words, lightly framed — no
  paraphrasing by the brain (no invention at the point of learning)
- Dedupe is identical to reflect's; no second standard
- Never edits the original trace — the trace stays as the record of
  what the brain thought; the correction sits alongside it

## Verification

- ~9 new tests: correction filed and retrievable; unknown trace_id
  error; empty fields rejected; duplicate correction stored once;
  lesson source is "correction"; lesson text contains both fields;
  survey surfaces the correction for a similar goal; original trace
  untouched (audit still clean); registration contract
- Live: think, correct it, survey the same goal, show the correction
  in the brief

## Estimated deltas

- +1 static tool
- ~315 static / ~440,661 total addressable
- ~9 new tests

## Honest limitations to keep

- A correction is trusted completely — the brain doesn't verify the
  user is right; a wrong correction becomes a wrong lesson
- Survey ranking is keyword overlap; a correction surfaces only for
  goals that share its words
- No contradiction handling between corrections (a later correction
  can silently coexist with an opposite earlier one — Phase 23's
  miner would flag it, but nothing auto-resolves it)
