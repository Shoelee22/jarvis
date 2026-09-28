# Phase 36 — Email Triage with Drafted Replies

**Status:** BUILT 2026-09-27 (4 tests green)

## Problem
Inbox triage ranks messages, but the user still has to write every reply.
The safe middle ground: draft replies mechanically, and put every draft
in the approval queue — never send directly.

## Build
- `inbox.triage_replies {limit?}` in `inbox_pack.py`
  - Runs the existing `inbox.triage` (all channels, honest unavailable
    per channel).
  - `_needs_reply` heuristic: urgency >= 40 AND a question/request cue
    ("?", "please", "could you", "deadline", ...). Documented as a
    heuristic, not comprehension.
  - `_draft_reply`: mechanical template — greets by first name, quotes
    the subject, restates the ask, asks about a deadline. Always carries
    a `[DRAFT — review before sending]` marker. Never written to sound
    like a finished human reply.
  - Files each draft via `autopilot.propose` with one step:
    `{tool: inbox.reply, args: {id, text: draft}, why: ...}`.
  - Returns drafts + proposal ids + channels.

## Safety
- Risk **medium** (writes proposals), needs_network False.
- NOTHING IS SENT by this tool. Sending happens only through the
  existing approval flow: the user reviews the draft text in the batch
  queue, then approves. `inbox.reply` itself stays high-risk.
- Rejected drafts leave no trace beyond the pending proposal.

## Verified
- `test_phase36_replies.py`: 4 passed — heuristic true/false cases,
  draft personalization + DRAFT marker, proposals filed (2 pending, steps
  reference inbox.reply with draft text), registration contract.
