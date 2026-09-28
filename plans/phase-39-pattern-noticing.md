# Phase 39 — Pattern Noticing / Proactive Preparation

**Status:** BUILT 2026-09-27 (3 tests green)

## Problem
The proactive pack had rules, scans, and briefings — but nothing that
*noticed* recurring behavior on its own. "You run inbox.triage every
morning" should surface without being asked.

## Build
- `proactive.patterns {days?=14, min_repeats?=3}` in `proactive_pack.py`
  - Mines the audit log (`<data_dir>/jarvis.db`): a tool counts as a
    pattern at >= min_repeats uses across >= 2 distinct days (a same-day
    burst is not a pattern).
  - Notes weekday affinity when >= 60% of uses fall on one weekday.
  - Each pattern becomes a `proactive.suggest` proposal: "you run X
    often — want a standing rule/schedule?" Queued, never executed.
- Honest when the log is empty: "No audit log yet — no patterns."

## Honesty
- Frequency analysis — counts, not understanding. Says so in the read.
- Suggests only. Creating a rule or schedule is always the user's call.

## Verified
- `test_phase39_patterns.py`: 3 passed — pattern found + suggestion
  queued as a `[proactive]` proposal, same-day burst ignored,
  empty-log honesty, registration contract.
