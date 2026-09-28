# Phase 26 — Trace Auditor

**Status:** planned 2026-09-27, not built
**Builds on:** Phase 14 (think traces), Phase 19 (metacognitive loop)

## Objective

Think traces are the brain's receipts — but nothing ever checks the
receipts. A trace could be hand-edited (conclusion changed, evidence
weights inflated) and every downstream tool would trust it. Phase 26
audits traces: re-derive the verdict from the stored evidence and
verify the recorded conclusion matches what the algorithm would
produce.

## Proposed tools

1. **`brain.audit {trace_id}`** — the receipt checker:
   - load the full trace row (question, evidence, nets, conclusion,
     confidence)
   - structural checks: evidence non-empty; every item has a
     non-empty sentence, weight in [0,1], stance in
     {for,against,neutral}, and a sub_question from the decomposition
   - integrity checks (the real value): recompute for/against nets per
     sub-question from the stored evidence sentences and compare to
     the stored nets (tolerance 0.001); recompute the leading
     sub-question and verify the stored conclusion quotes it
     (conclusions embed the leading reading as `'...'`); verify
     confidence is one of low/medium/high
   - verdict: "clean" when every check passes, else the failing
     checks with observed vs expected values

## Design constraints

- The audit re-derives with the CURRENT algorithm; a trace audited
  after an algorithm change may legitimately mismatch — report the
  mismatch, don't auto-"fix" anything
- Audit never rewrites traces — read-only
- Unknown trace_id is an error, not an empty verdict
- Semantic-mode traces: nets were computed from cosine weights, but
  the stored evidence already carries those weights — recomputation
  is identical; no backend needed

## Verification

- ~10 new tests: clean trace passes; tampered conclusion detected;
  inflated weight detected (nets mismatch); weight out of range
  detected; empty evidence detected; unknown trace_id error;
  bad stance detected; net rounding tolerance respected;
  low-evidence "no reading supported" trace audits clean;
  registration contract
- Live: run think, audit the trace (clean), then tamper a copy and
  show the audit catching it

## Estimated deltas

- +1 static tool
- ~313 static / ~440,659 total addressable
- ~10 new tests

## Honest limitations to keep

- The audit checks internal consistency — a trace whose evidence was
  fabricated at write time still audits "clean"; garbage in, clean
  audit out
- Conclusion-quoting check is string matching on the `'...'` pattern;
  a future conclusion format change breaks the check (report, don't
  crash)
- Confidence low/medium/high is the brain's own heuristic self-grade;
  the audit only checks it's a valid value, not that it's *right*
