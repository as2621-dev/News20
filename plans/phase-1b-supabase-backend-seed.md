# Phase 1b: Supabase backend — content schema, storage, seed, data layer

**Milestone:** M1 — Audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** L

## Goal
A Supabase project whose **content schema** (per `reference/supabase-schema.md`) holds the 5 M0 digests — story content + word-timed captions + audio + posters — exposed through a typed `getFeed()` data layer that returns the **exact `Story` shape Phase 1 consumes**, so Phase 1c can swap fixtures for live data with zero reel changes.

## Context the sub-agents need
- **Schema is already designed** in `reference/supabase-schema.md` (22 tables + 5 enums). This phase implements the **content half only**; user/auth tables (`users`, `follows`, `saves`, `player_signals`, `play_sessions`, `interests`, `user_interest_profile`, …) are **deferred to M3** (M1 is an anonymous passive reel).
- **The feed contract is `src/types/feed.ts`** from Phase 1 SP2 — the data layer here must conform to it (and to `reference/api-contracts.md`; reconcile if they differ, Rule 7).
- **Seed inputs:** story content from `prototype/News20 Prototype/data.js` (headline, dek, segment, anchors, `detail_chunks`, `trust{coverage,outlet_count,blindspot,timeline,opposing_view}`, `suggested_questions`, `answers`→`story_qa`, `topics`→`story_topics`, `citations`→`story_sources`); captions from `agents/m0/output/captions/digest-{1..5}.captions.json` (flat `words[]` → `caption_sentences.word_tokens`, seconds→ms); audio `agents/m0/output/audio/digest-{1..5}.mp3`; posters `assets/m0/digest-{1..5}/poster.png`.
- **Reuse pattern** (`reference/reuse-map.md`): TLDW `supabase/` migrations are the **PATTERN** (the News20 schema is new). Supabase version pins in `reference/stack-notes.md` (`@supabase/supabase-js` 2.49+, `@supabase/ssr` 0.6+).

## Sub-phases

### Sub-phase 1: Content-schema migration
- **Files touched:** `supabase/config.toml`, `supabase/migrations/0001_content_schema.sql` (enums `bias_lean`, `segment_slug`, `anchor_speaker`; content tables `segments`, `outlets`, `anchors`, `stories`, `digests`, `caption_sentences`, `detail_chunks`, `story_trust`, `story_timeline`, `story_sources`, `suggested_questions`, `story_qa`, `story_topics` — DDL transcribed from `reference/supabase-schema.md`).
- **What ships:** a forward migration that builds the content schema with PK/FK, enums, indexes.
- **Definition of done:** `supabase db reset` (or `supabase migration up`) applies cleanly on a local Supabase; a SQL assertion (or `\dt` capture) confirms all **13 content tables + 3 enums** exist with the documented columns and FKs resolve. ⚠ irreversible (forward-only migration).
- **Dependencies:** none

### Sub-phase 2: Storage buckets + public-read RLS
- **Files touched:** `supabase/migrations/0002_storage_and_rls.sql` (storage buckets `digest-audio`, `story-posters`; `ENABLE ROW LEVEL SECURITY` on all content tables; public-read `SELECT` policies; **no** public write policies; public-read storage objects).
- **What ships:** storage for audio + posters and RLS that lets the anon client read content but not mutate it.
- **Definition of done:** both buckets exist; an **anon-key** `SELECT` on `stories` returns rows while an anon `INSERT` is **rejected** by RLS (assert both); a stored object is publicly readable via its URL. ⚠ irreversible (policies/migration).
- **Dependencies:** Sub-phase 1

### Sub-phase 3: Seed the 5 M0 digests
- **Files touched:** `supabase/seed/seedM0Digests.ts` (Node + `supabase-js` service-role): reads `data.js` content + M0 caption JSON (transform flat `words[]`→`caption_sentences` with `word_tokens` incl. `start_ms`/`end_ms`) + uploads the 5 mp3s + posters to storage; inserts `segments`/`outlets`/`anchors` lookups + per-story rows; `package.json` `seed` script; `tests/seed/seedMapping.test.ts`.
- **What ships:** the 5 fully-populated stories in Supabase — content + captions + audio + posters.
- **Definition of done:** after `npm run seed`, `stories` = 5 rows, each with ≥1 `digest` (audio URL resolves HTTP 200), ≥6 `caption_sentences` with `word_tokens`, `detail_chunks`, a `story_trust` row, `suggested_questions`, `story_qa`; `poster_url` resolves HTTP 200; **exactly one** highlight word per caption sentence preserved from M0. A test asserts the seeded `caption_sentences` reconstruct the same verbatim word sequence as the source M0 JSON. ⚠ writes data + uploads files.
- **Dependencies:** Sub-phase 1, Sub-phase 2

### Sub-phase 4: Typed feed data-access layer
- **Files touched:** `src/lib/supabase/client.ts` (anon client, `@supabase/ssr`), `src/lib/feed/supabaseFeed.ts` (`getFeed(): Promise<Story[]>` — joins `stories`+`digests`+`caption_sentences`+`segments` + storage URLs into the Phase-1 `Story` contract, ordered by `feed_position`), `.env.example` (`NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY`), `tests/lib/supabaseFeed.test.ts`.
- **What ships:** a Supabase-backed `getFeed()` returning the identical typed `Story[]` shape the Phase-1 reel already consumes.
- **Definition of done:** against the seeded DB, `getFeed()` returns 5 stories that **validate against `src/types/feed.ts`** (a Zod parse passes); caption `word_tokens` carry `start_ms`/`end_ms`; audio/poster URLs are absolute and resolve. The contract test fails if any column→type mapping drifts from the Phase-1 contract.
- **Dependencies:** Sub-phase 3

## Phase-level definition of done
A Supabase (local and/or hosted) has the content schema applied, the 5 M0 digests seeded (content + captions + audio + posters in storage), public-read RLS enforced, and `getFeed()` returns 5 fully-typed stories matching `src/types/feed.ts` with resolvable audio/poster URLs and per-word caption timings. **Validated by:** migration applies cleanly; RLS read-allowed/write-denied assertions; seed-count + URL-resolve checks; the `getFeed` Zod contract test. ⚠ contains forward-only migrations + data writes.

## Out of scope
- User/auth tables (`users`, `follows`, `saves`, `player_signals`, `play_sessions`, `interests`, `user_interest_profile`) — M3 with auth.
- The daily ingestion pipeline that fills the feed at scale (Phase 1d).
- Capacitor / iOS (Phase 1c).
- Detail/trust/Q&A **UI** (M2 reads these tables, but no UI is built here).

## Open questions
1. **Local vs hosted Supabase** — recommend local (`supabase start`) for the migration/seed/test loop, then push to a hosted project before Phase 1c device testing. Confirm a hosted project exists or create one.
2. Caption `word_tokens` are seeded from M0's **time-sliced** timings — same limitation as Phase 1 (master-plan Open Q7).
3. **`api-contracts.md` alignment:** SP4 must conform `getFeed` to both `src/types/feed.ts` and `reference/api-contracts.md` — reconcile if they differ (Rule 7).

## Self-critique

**Product lens:** PASS. Pure enabling infra for the reel MVP — ships no user-facing feature beyond the data the reel needs, and no M2/M3 surface. Scoped to content tables only (no auth), matching M1 as an anonymous passive reel; pulling user tables in would be M3 creep, correctly avoided.

**Engineering lens:** PASS. All inside the stack (Supabase migrations + `supabase-js` + the TS contract). DoDs are concrete + fresh-context-verifiable: migration-applies, RLS allow/deny assertions, seed counts + HTTP-200 URL checks, a Zod contract test on `getFeed`. SP4 (the data layer) is last and locks the `getFeed` contract — but that contract was already fixed in Phase 1 SP2; SP4 **conforms** to it rather than inventing it, so nothing flexible is cemented late. Seed (SP3), schema (SP1), and storage/RLS (SP2) are distinct concerns.

**Risk lens:** PASS with flags. ⚠ irreversible: SP1/SP2 are forward-only migrations and SP3 writes data + uploads — flagged so `/run-phase` runs them against a disposable local DB first. File boundaries disjoint (each migration a separate numbered file; seed and data-layer separate). Test coverage present on the verifiable seams (seed word-sequence reconstruction; `getFeed` Zod contract); migration/RLS verified by assertions. Painting-into-a-corner: SP1→4 leaves a contract-satisfying `getFeed` that Phase 1c plugs into directly.

**Irreversible sub-phases:** SP1, SP2 (forward-only migrations), SP3 (data writes/uploads).
