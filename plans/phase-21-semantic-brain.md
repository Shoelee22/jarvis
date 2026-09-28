# Phase 21 plan — The semantic brain

**Status:** planned (2026-09-27). Not built yet.
**Goal:** replace keyword-overlap scoring with real semantic similarity —
the honest fix for the standing gap "reasoning remains substantially
keyword/heuristic".

## Why this is the right next phase

Phases 14→20 built the brain's *structure*: deliberation traces,
adversarial passes (steelman/premortem), assumption audits, calibration,
and now the closed loop. The *scoring underneath* is still token overlap.
"Gym" vs "gyms", "pricing" vs "prices", paraphrases, and negation all
defeat it. Embeddings fix exactly this class of failure without changing
any of the structure above.

## Design

**Optional backend, honest when missing.** New `semantics_pack.py`, mirroring
how `system1_pack` treats Laya:

- Config in `tools_config.yaml`: `semantics: {enabled: false,
  model: "sentence-transformers/all-MiniLM-L6-v2"}` — default off.
- Lazy load on first use; **never auto-download**. If torch /
  sentence-transformers / the checkpoint is missing, every tool returns
  `{"ok": False, "error": "..."}` with `simulated: false` — never faked,
  never silently fetched.
- Not in the installer core profile, not in CI (same policy as Laya).

**New tools (~5):**

| Tool | Does |
|---|---|
| `brain.embed {text}` | embedding vector for one text (list of floats) |
| `brain.similar {a, b}` | cosine similarity, disclosed as similarity-not-understanding |
| `brain.related {query, texts[]}` | rank texts by similarity to query |
| `brain.cluster {texts[], k?}` | group near-duplicate claims (for contradiction mining) |
| `brain.diverse {texts[], n}` | maximal-marginal-relevance pick — the n most representative distinct texts |

**Integration (the actual advance):**

1. `brain.think` / `brain.decide` gain `semantic: true` flag: evidence
   weights use cosine similarity instead of token overlap when the backend
   is available; falls back to keyword weights with a note when not.
2. `system1.duel`'s `agree` flag becomes semantic (disclosed threshold,
   e.g. ≥ 0.85) instead of mechanical string equality.
3. Research contradiction detection: cluster claims, flag high-similarity
   pairs with opposing stance as candidate contradictions for the
   researcher's heuristic pass to review.
4. `brain.survey` recall: semantic re-ranking of FTS candidates (FTS for
   recall, embeddings for ranking — best of both).

**Benchmark (required, not optional):** a fixture set of ~40
question/context pairs with human-judged expected rankings; the suite
reports keyword-vs-semantic agreement with expected, so the claim
"semantic is better" is measured, not asserted. Where semantic loses or
ties, the docs say so.

## Honest gates (must hold)

- Embeddings are similarity, not comprehension — every description says so.
- Negation ("not risky" vs "risky") still fools cosine similarity; the
  benchmark must include negation cases and report them as known failures.
- ~90MB model download is the user's explicit opt-in, on their hardware.
- No change to the closed loop's accountability: semantic or not, every
  cycle still needs its outcome recorded.

## Tests to write

- Cosine math (identical = 1.0, orthogonal ≈ 0.0); unavailable-backend
  honesty for all five tools; benchmark fixture with expected rankings;
  think/decide `semantic:true` falls back cleanly when backend missing;
  duel semantic-agree threshold behavior on paraphrases.

## Estimated counts

+5 static tools → 309 static, ~440,655 addressable. ~15 new tests.

## After Phase 21

The brain would then be: structured deliberation (14), adversarial review
(19), closed-loop accountability (20), semantic scoring (21). The next
frontier after that is live grounding — the Windows PC smoke test and
Twilio voice bridge are still the biggest *real-world* gaps, and no phase
can substitute for them.
