# Phase 1b — SP4: Typed feed data-access layer

**STATUS: CODE + CONTRACT TEST COMPLETE. Offline + LIVE-DB validation PASS. DoD: PASS.**

## Implemented
- `src/lib/supabase/client.ts` — lazy singleton browser anon client from
  `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY`; throws with `fix_suggestion`
  if missing.
- `src/lib/feed/supabaseFeed.ts` — `getFeed(client?): Promise<Story[]>`, the drop-in sibling
  of `fixtureFeed.ts` (identical return contract). One PostgREST embedded select:
  `stories` ⋈ `segments` ⋈ `digests!inner` (current) ⋈ `caption_sentences`, ordered by
  `story_id`, `.returns<StoryRow[]>()` (no `any`). Maps DB rows → `Story` (`src/types/feed.ts`):
  - captions sorted by `sentence_index`; `word_tokens` JSONB passed through.
  - `anchors` derived from caption speakers (no single column).
  - `speech_end_ms` derived = last caption's `sentence_end_ms` (no `digests` column).
  - throws if a story lacks a current digest or segment (fail loud).
  - injectable client → tests mock at the boundary.
- `tests/lib/feed/supabaseFeed.test.ts` — Zod parse against the `Story` contract (DoD gate),
  caption-sort + derived anchors/speech_end_ms, missing-digest throws, query-error throws.

## Contract reconciliation (Rule 7, phase Open Q3)
`src/types/feed.ts` (authoritative; already-shipped Phase 1 seam) and
`reference/supabase-schema.md` agree on every stored column. `reference/api-contracts.md`
describes a different, older `Story` (mp4 + bias_breakdown) and is **superseded by
`src/types/feed.ts`** for the reel — chosen, not blended. Two `Story` fields are DERIVED
(documented in source): `anchors` (caption speakers) and `speech_end_ms` (last caption end).

## Fix applied during this verification (in SP4 scope)
**Bug:** `getFeed()` threw `Story "s1" is missing its segment` against the LIVE seeded DB,
though the offline mocked test passed. **Root cause:** the `segments` embed is a
**many-to-one** relationship (`stories.story_segment_slug` FK → one `segments` row), so
PostgREST returns it as a **single object**, not an array. The code typed it as
`SegmentRow[]` and read `row.segments[0]`, which is `undefined` on an object → throw. The
offline test mock supplied `segments` as an array `[{...}]`, masking the mismatch.
**Fix (`src/lib/feed/supabaseFeed.ts`):** typed `segments` as `SegmentRow | SegmentRow[] | null`
and normalized with `Array.isArray(row.segments) ? row.segments[0] : row.segments`. Robust to
both PostgREST's object form (live) and the array form (offline mock). `digests` remains a
one-to-many array (correct, unchanged). Single attempt; verified live + offline after.

## Validation
### Offline (the committed contract guarantee) — PASS
- `npx vitest run` → **81 passed / 81** (8 files), including the `supabaseFeed` Zod contract test.
- `npm run lint` (Biome, 35 files) → 0 errors. `npx tsc --noEmit` → exit 0.

### Live integration check (one-time SP4 gate, against hosted seeded DB) — PASS
Temporary `tmp_verifyFeedLive.ts` built an anon client from the public env vars, called
`getFeed()` against the live DB, asserted each DoD item, then was **deleted** (not committed).
Per-assertion results:
- `story_count == 5` → PASS (story ids: **s1, s2, s3, s4, s5**, ascending)
- every story **Zod-parses** against `src/types/feed.ts` → PASS (no column→field drift)
- all `caption_sentences[].word_tokens[]` carry numeric `start_ms`/`end_ms` → PASS
- `anchors` is a 2-tuple of ALEX/JORDAN for all 5 stories → PASS
- `segment_key`/`segment_label`/`segment_accent_hex` populated → PASS
- `audio_duration_ms`/`speech_end_ms` present (numeric) → PASS
- audio + poster URLs absolute and HTTP **200** for every story:
  - s1 audio 200, poster 200
  - s2 audio 200, poster 200
  - s3 audio 200, poster 200
  - s4 audio 200, poster 200
  - s5 audio 200, poster 200
- OVERALL: **PASS**

## Definition of done — PASS
- [x] Against the SEEDED DB, `getFeed()` returns **5** stories that **validate against
  `src/types/feed.ts`** (live Zod parse passes; offline contract test also passes).
- [x] caption `word_tokens` carry numeric `start_ms`/`end_ms`.
- [x] `digest_audio_url` + `poster_url` are absolute URLs and resolve **HTTP 200** (all 10 checks).
- [x] contract test fails on any column→type drift (the offline Zod gate).

## Files modified this run
- `src/lib/feed/supabaseFeed.ts` — segment-embed normalization fix (in SP4 scope).
- `.agents/execution-reports/phase-1b-supabase-backend-seed-sub-4.md` — this report.
- Temp scripts `tmp_verifyFeedLive.ts` / `tmp_debugRaw.ts` created and **deleted**; not committed.

## Concerns
- The offline mock used an **array** for the `segments` embed, which did not match PostgREST's
  live many-to-one **object** shape — it masked the bug and let the offline suite pass while
  live failed. The fix accepts both, but the offline mock still exercises only the array path.
  Consider adding an offline test case feeding `segments` as a single object to lock the live
  shape into the contract test (not done here — out of this verification's surgical scope; flag
  for a follow-up).
- `digest_audio_url` 200s came back via `fetch(GET)`; storage objects served HTTP 200.
