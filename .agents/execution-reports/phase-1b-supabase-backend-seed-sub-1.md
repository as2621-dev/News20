# Phase 1b — SP1: Content-schema migration

**STATUS: APPLIED + VERIFIED against hosted Supabase. DoD: PASS.**

Migration `0001_content_schema.sql` was authored and applied to the hosted DB by the
orchestrator (IPv4 session pooler — direct host is IPv6-only). This report is an
**independent verification** of SP1's Definition of done against the live schema. Nothing
was re-applied; no commit made.

## Implemented (authored)
- `supabase/migrations/0001_content_schema.sql` — 3 content enums (`bias_lean`,
  `segment_slug`, `anchor_speaker`) + 13 content tables (`segments`, `outlets`, `anchors`,
  `stories`, `digests`, `caption_sentences`, `detail_chunks`, `story_trust`,
  `story_timeline`, `story_sources`, `suggested_questions`, `story_qa`, `story_topics`)
  with PK/FK, the `uq_digests_current_per_story` partial unique index, the caption-order
  unique constraint, the `story_blindspot_flag` generated column, and all indexes.
  Columns transcribed **verbatim** from `reference/supabase-schema.md` §1–2. User/auth
  tables correctly DEFERRED to M3.

## Verification results (live hosted DB, via IPv4 session pooler)

### 1. Tables — PASS (13/13, no extras)
`pg_tables` in `public` returned exactly the 13 expected content tables and nothing else
(user/auth tables correctly absent): `anchors`, `caption_sentences`, `detail_chunks`,
`digests`, `outlets`, `segments`, `stories`, `story_qa`, `story_sources`,
`story_timeline`, `story_topics`, `story_trust`, `suggested_questions`.

### 2. Enums — PASS (3/3, labels match)
- `bias_lean` → `left, center, right`
- `segment_slug` → `geopolitics, markets, tech, sport, wildcard`
- `anchor_speaker` → `ALEX, JORDAN`

Label order matches the migration / reference exactly.

### 3. Columns (spot-check vs reference/supabase-schema.md) — PASS, no drift
Checked `segments`, `stories`, `digests`, `caption_sentences` via
`information_schema.columns`. Every column name, type (`udt_name`), nullability, and
default matches `reference/supabase-schema.md` §2 verbatim:
- `segments` (4 cols): `segment_slug` (enum PK), `segment_label`, `segment_accent_hex`,
  `segment_sort_order` (smallint default 0).
- `stories` (17 cols): incl. `story_segment_slug` (enum, NOT NULL), `story_blindspot_lean`
  (enum, nullable), `story_blindspot_flag` — confirmed **generated** ALWAYS as
  `(story_blindspot_lean IS NOT NULL)`; all `_utc`/`_at` are `timestamptz` default `now()`.
- `digests` (9 cols): `digest_audio_url`/`digest_duration_ms` NOT NULL,
  `digest_is_current` bool default true; partial unique index
  `uq_digests_current_per_story` present (`... WHERE digest_is_current`).
- `caption_sentences` (10 cols): `word_tokens` is `jsonb` NOT NULL, `anchor_speaker` enum
  NOT NULL, `sentence_start_ms`/`sentence_end_ms` int4 NOT NULL; `uq_caption_sentence_order`
  unique constraint present.

### 4. Foreign keys — PASS (13 FKs, all validated)
`pg_constraint` (contype='f') returned 13 FKs in `public`, all `convalidated = t`, zero
NOT VALID. Key FKs confirmed:
- `digests.digest_story_id → stories` ✓
- `caption_sentences.caption_digest_id → digests` ✓
- `caption_sentences.caption_story_id → stories` ✓
- `stories.story_segment_slug → segments` ✓
- `stories.story_primary_outlet_id → outlets` ✓
- Plus `detail_chunks`, `story_qa`, `story_sources` (×2), `story_timeline`,
  `story_topics`, `story_trust`, `suggested_questions` → all resolve to `stories`/`outlets`.

### 5. Repo regression — PASS
- `npm run lint` (biome check) → exit 0, "Checked 35 files. No fixes applied."
- `npx tsc --noEmit` → exit 0, no errors.

## Definition of done — PASS
"all 13 content tables + 3 enums exist with the documented columns and FKs resolve."
Confirmed against the live hosted DB: 13 tables, 3 enums (correct labels), columns match
reference verbatim on the seed/feed-critical tables, and all 13 FKs are present and valid.

## Concerns
- None for SP1 structure. Verified read-only against the hosted DB as the DB owner
  (pooler), which is appropriate for schema/structure checks (RLS-as-anon is SP2's DoD).
- Migration is forward-only (no down migration) — already flagged in the phase file as
  irreversible; not a regression.
