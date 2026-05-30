# Phase 1b — SP3: Seed the 5 M0 digests

**STATUS: SEEDED + verified against the hosted DB. Mapping test authored + passing. DoD: PASS.** (Not committed.)

## Seed run
- `npm run seed` against the hosted Supabase project. **First run FAILED**, root-caused + fixed (one change, in-scope), re-ran **green**, then re-ran a third time to confirm idempotency (clean).
- **Failure + fix (in `supabase/seed/seedM0Digests.ts`):** the digest write used
  `upsert(..., { onConflict: "digest_story_id" })`, but the schema's
  `uq_digests_current_per_story` is a **PARTIAL** unique index
  (`create unique index ... on digests (digest_story_id) where digest_is_current`).
  PostgREST's `onConflict` cannot target a partial index → Postgres error
  `there is no unique or exclusion constraint matching the ON CONFLICT specification`.
  Replaced the upsert with an explicit, idempotent **select-current → update-in-place
  else insert** keyed on `(digest_story_id, digest_is_current=true)`. M0 has exactly
  one current digest per story, so this is deterministic. Documented with a `// Reason:`.

## Implemented / changed files
- `supabase/seed/seedM0Digests.ts` — digest-write fix above (rest of the script unchanged).
- `tests/seed/seedMapping.test.ts` — NEW mapping test (see below).
- `vitest.config.ts` — extended the `include` glob from `tests/lib/**` to also cover
  `tests/seed/**` so the mandated `tests/seed/` test is actually collected (Rule 12 — a
  silently-uncollected test is worse than the out-of-scope edit). Python tree under
  `tests/agents/**` is still excluded. **Flagged as out of SP3's stated file scope.**
- `package.json` `seed` script (`tsx supabase/seed/seedM0Digests.ts`) — already present, unchanged.

## Verified counts (psql, DB owner, against the live seeded DB)
| metric | s1 | s2 | s3 | s4 | s5 |
|---|---|---|---|---|---|
| `stories` total | **5** rows | | | | |
| current `digests` per story | 1 | 1 | 1 | 1 | 1 |
| `caption_sentences` per story | 11 | 10 | 10 | 11 | 10 |
| `detail_chunks` per story | 3 | 3 | 3 | 3 | 3 |
| `story_trust` per story | 1 | 1 | 1 | 1 | 1 |
| `suggested_questions` per story | 3 | 3 | 3 | 3 | 3 |
| `story_qa` per story | 3 | 3 | 3 | 3 | 3 |

- All **52** caption sentences: `word_tokens` non-empty, every token carries `start_ms`+`end_ms`,
  and **exactly one** highlight token per sentence (0 sentences violate; 52/52 have exactly one).
- **Audio URL HTTP 200** for all 5 (`content-type: audio/mpeg`; s1 `content-length` 1216304 == local file).
- **Poster URL HTTP 200** for all 5 (`digest_ambient_poster_url` and `story_ambient_poster_url`;
  `content-type: image/png`; s1 `content-length` 2443601 == local poster).

## New test — `tests/seed/seedMapping.test.ts` (Vitest, 16 tests, PASS)
Validates the seed's caption mapping deterministically with **no live-DB dependency**: the seed
builds each `caption_sentences.word_tokens` row by running `normalizeM0Captions` over the exact
`agents/m0/output/captions/digest-{1..5}.captions.json` files and inserts the result verbatim, so
the test asserts that transform against the source JSON. Per digest it asserts:
1. **Verbatim word-sequence fidelity (the karaoke contract):** flattening the produced
   `word_tokens` in sentence order yields the source `words[]` **1:1 — count + order + text.**
   Encodes WHY: a dropped/re-tokenized/reordered word desyncs the on-screen caption from the audio;
   the test fails the moment that mapping drifts.
2. **Exactly one highlight per sentence**, and the highlighted token equals the word M0 tagged for
   that sentence (cross-checked against source `words[].is_highlight`).
3. Every token carries integer `start_ms`/`end_ms` with `end_ms >= start_ms`.
Plus one suite-level test asserting each digest produces **≥6** sentences (the DoD floor) and that the
produced count equals M0's declared `sentence_count`.

## Validation
- **Biome lint:** PASS (35 files, no fixes).
- **`tsc --noEmit`:** PASS (exit 0).
- **`vitest run`:** PASS — **8 files / 81 tests** (was 7 files / 65 before; new file adds 16).

## Definition of done — PASS
- [x] `stories` = exactly 5.
- [x] each story ≥1 current `digest`; audio URL resolves HTTP 200.
- [x] each story ≥6 `caption_sentences`, each with non-empty `word_tokens` carrying start_ms/end_ms.
- [x] each story has `detail_chunks`, a `story_trust` row, `suggested_questions`, `story_qa`.
- [x] each story's `poster_url` resolves HTTP 200.
- [x] exactly one highlight word per caption sentence (52/52), preserved from M0.
- [x] mapping test asserts seeded captions reconstruct the verbatim source word sequence.

## Concerns
- **Out-of-scope edit:** `vitest.config.ts` `include` glob was extended to collect `tests/seed/**`.
  Required for the SP3-mandated test path to actually run; flagged for the orchestrator.
- The digest-write fix changed an `upsert` to a select-then-update/insert. It is still idempotent and
  M0-deterministic (one current digest/story), but it is **not** concurrency-safe (a TOCTOU race if two
  seeders ran at once). Fine for a single-process seed CLI; noted in case the schema later gets a
  non-partial unique constraint that would let a true upsert replace it.
- Two highlight-related invariants (verbatim words, one-highlight) are also covered by the existing
  `tests/lib/normalizeM0.test.ts` against the same normalizer. The new test is intentionally seed-scoped
  (all 5 digests + the ≥6 floor + source-tag cross-check) rather than a duplicate.
