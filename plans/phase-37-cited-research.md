# Phase 37 — Stronger Cited Deep Research

**Status:** BUILT 2026-09-27 (4 tests green)

## Problem
`research.investigate` already attached a source URL to each claim, but
there was no readable cited report — citations were data, not delivery.

## Build
- `research.cite {id}` in `research_pack.py`
  - Loads a persisted briefing from sqlite.
  - Numbers unique sources in first-seen order; every claim gets a
    numbered `[n]` citation pointing at its source URL.
  - Claims with no source go under **Unverified** and are NEVER cited.
  - Returns a markdown report: Findings (cited) → Unverified →
    Contradictions (heuristic, labeled) → Sources.
  - Stats: claims_cited, claims_unverified, sources.
  - Honest error for unknown ids.

## Honesty
- Citations are mechanical mappings from claim → source URL the
  investigation actually fetched. Sources are never invented; if nothing
  was verified, the report says so.
- Read-only, low risk, no network.

## Verified
- `test_phase37_cite.py`: 4 passed — citation numbering + source dedup,
  report marks unverified without citation markers, unknown-id honesty,
  registration contract.
