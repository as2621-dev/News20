# Phase 5b: Source data model + curated archetype catalog

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L

## Goal
Stand up the **content-source backbone** (YouTube channels / podcasts / X accounts / personalities + follows + archetypes) as additive migrations, port TL;DW's per-archetype catalog seeder, and expose a typed client-side data layer so a user can follow/list sources under RLS — the foundation the recommendation (5c), ingestion (5d), and control surface (5e) phases all build on.

## Why this phase exists
Master plan Decision #12 inherits TL;DW's source stack wholesale. This phase ports the **schema + catalog seed** so every later phase has tables to read/write. It is the lowest-risk way to de-risk the sources axis: schema + curated data first, behavior later.

## Context the sub-agents need
- **Naming collision (critical):** News20 already has `outlets` (news outlets) and `story_sources` (per-story article attribution). The new content-source tables must **not** be named `sources`. Use `content_sources` / `user_content_sources` / `content_source_items` (TL;DW calls them `sources`/`user_sources`/`content_items` — rename on port). `personalities` / `user_personalities` are free (no collision).
- **Donor:** `reference/sources-reuse-map.md` §1–§2 maps the exact TL;DW migrations + seeder to lift: `001_voice_agent_schema.sql:166-257`, `006_personalities.sql`, `010_recommendation_seeds.sql`, `014_catalog_personas.sql`, and `scripts/seed_catalog/`. Donor root: `~/TLDW-Phase2/tldw/voice-agent-dashboard/`.
- **Archetypes:** the draft 12-archetype set + 8-category vectors live in `reference/archetypes.md` (🟡 pending `/cmo`). This phase seeds the **draft** so 5c is unblocked; the set is re-seedable without schema change.
- **Latest migration is `0006`; Phase 5 adds `0007`** → this phase adds **`0008`**. (If Phase 5 hasn't run, `0008` still applies on `0006`; it has no FK to 0007.)
- **Static-export:** the data layer is **client-side Supabase** under RLS (no Next API routes). The seeder is a **Python script** run server-side at seed time (keys server-side).
- **`scripts/` is currently empty** — the seeder is new code under `scripts/seed_catalog/`.

## Sub-phases

### Sub-phase 1: Migration 0008 — content-source + personality + archetype schema
- **Files touched:** `supabase/migrations/0008_content_sources.sql`.
- **What ships:** an additive forward-only migration adding — `content_source_type` enum (`youtube_channel|podcast|x_account|personality`); `source_priority` enum (`off|big_stuff|everything`); `content_sources` (`source_id`, `content_source_type`, `external_id`, `source_name`, `source_description`, `thumbnail_url`, `subscriber_count bigint`, `platform_metadata jsonb`, `personas text[]`, `topic_tags text[]`, `popularity_score numeric`, `is_curated bool`, `last_fetched_at`, **unique `(content_source_type, external_id)`**, GIN indexes on `personas`+`topic_tags`); `user_content_sources` (`user_id`→`users`, `source_id`→`content_sources`, `source_priority default 'everything'`, `added_via text`, PK `(user_id, source_id)`); `content_source_items` (`item_id`, `source_id`→`content_sources`, `external_id`, `item_title`, `item_url`, `author_name`, `published_at`, `raw_transcript text`, `item_summary text`, `processing_status text`, unique `(source_id, external_id)`); `personalities` + `user_personalities` + `personality_appearances` (port `006`); `archetypes` (`archetype_slug` unique, `archetype_label`, `archetype_vector jsonb`). RLS: public-read `content_sources`/`personalities`/`archetypes`/`content_source_items`; owner-all `user_content_sources`/`user_personalities`.
- **Definition of done:** applies cleanly on a DB holding 0001–0006 (and 0007 if present); the unique key rejects a duplicate `(youtube_channel, UC123)` upsert; a GIN query `personas && array['ai-frontier-tech']` uses the index; anon `SELECT` on `content_sources` succeeds while anon `SELECT` on another user's `user_content_sources` returns **zero rows**. ⚠ irreversible (forward-only, additive).
- **Dependencies:** none.

### Sub-phase 2: Archetype definitions + vectors seed
- **Files touched:** `supabase/seed/archetypes.sql`, `reference/archetypes.md` (mark which set is seeded).
- **What ships:** the draft 12 archetypes from `reference/archetypes.md` §3 seeded into `archetypes`, each `archetype_vector` a normalized weight map over the 8 pinned categories (`ai|geopolitics|business|environment|politics|tech|sport|arts`), plus the `balanced-generalist` fallback.
- **Definition of done:** 12 archetype rows seeded; every `archetype_vector` covers all 8 categories and is normalized; a cosine-similarity query of a sample interest vector (e.g. heavy `ai`+`tech`) returns `ai-frontier-tech` as nearest and `balanced-generalist` for a flat vector. SQL/unit assertion.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: Per-archetype catalog seeder (port TL;DW `seed_catalog`)
- **Files touched:** `scripts/seed_catalog/seed_catalog.py`, `scripts/seed_catalog/youtube_resolve.py`, `scripts/seed_catalog/itunes_resolve.py`, `scripts/seed_catalog/data/{channels,podcasts,personalities}.{archetype}.json`, `agents/shared/settings.py` (add `YOUTUBE_API_KEY` if absent).
- **What ships:** the ported seeder reading `{type}.{archetype}.json` (file position = popularity rank) → resolve channels via YouTube `channels.list?forHandle`, podcasts via iTunes (capture `feed_url` into `platform_metadata`), personalities via Wikipedia photo → **upsert** into `content_sources`/`personalities` with `personas` (union across archetype files) + `topic_tags` (8-category-aligned) + `popularity_score`. X handles are stored as `x_account` rows **without** live resolution (no resolver yet — built in 5c/5d).
- **Definition of done:** running the seeder against a test DB upserts ≥10 channels, ≥10 podcasts, ≥10 personalities tagged to archetypes; re-running is **idempotent** (unique-key upsert, no dupes); a channel handle resolves to a real `external_id` + `thumbnail_url` (YouTube/iTunes/Wikipedia **mocked** per CLAUDE.md). Pytest.
- **Dependencies:** Sub-phases 1, 2.

### Sub-phase 4: Typed source data layer + types
- **Files touched:** `src/types/source.ts`, `src/lib/sources.ts`.
- **What ships:** News20 TS types (`ContentSource`, `UserContentSource`, `Personality`, `Archetype`, and a `SOURCE_TYPE_CONFIGS` map ported from TL;DW `src/types/source.ts:100-119`, extended with `x_account`) + a client-side Supabase data layer: `listSourcesByArchetype(personas, kind, limit)`, `getUserSources()`, `followSource(sourceId, priority?)`, `unfollowSource(sourceId)`, `setSourcePriority(sourceId, priority)`.
- **Definition of done:** types match the 0008 columns; `followSource` writes a `user_content_sources` row scoped to `auth.uid()` with default `priority='everything'`; `getUserSources` returns only the caller's rows (RLS); `setSourcePriority` updates the enum. Mock-asserted.
- **Dependencies:** Sub-phase 1.

## Phase-level definition of done
Migration 0008 is live (content-source + personality + archetype schema, RLS); a seeded per-archetype catalog of channels/podcasts/personalities exists (idempotent seeder, external APIs mocked in tests); and a typed client-side data layer lets a user follow/list/prioritize sources under RLS. **Validated by:** migration-applies + unique-key + GIN + RLS assertions; archetype cosine-match test; idempotent seeder test (mocked APIs); the follow/getUserSources RLS test.

## Out of scope
- The recommendation **matcher** + onboarding UI (Phase 5c).
- **Ingestion** of source content (Phase 5d) — `content_source_items` is created here but populated there.
- The **control surface** (Phase 5e).
- The **research agent** that refreshes the catalog (Phase 6).
- Locking the final archetype set (that's `/cmo`; this seeds the draft).

## Open questions
1. **Final archetype set** (10–15) + vectors — pending `/cmo` (`reference/archetypes.md` §5). This phase seeds the draft 12; re-seedable.
2. **`content_source_items` vs `stories`:** raw items land in `content_source_items`, then 5d promotes substance into the existing story pool. Confirm we don't fold raw items directly into `stories` (recommend separate raw table).
3. **Table naming** — `content_sources` chosen to avoid the `outlets`/`story_sources` collision; confirm.

## Self-critique
**Product lens:** PASS — schema + curated catalog is the enabling substrate for the whole sources axis; the per-archetype catalog is exactly "pre-computed recommendations per archetype" (spec §2.2/§3.2). No UX shipped here, by design (lowest-risk first).
**Engineering lens:** PASS — renames to avoid the real `sources`/`outlets`/`story_sources` collision (caught by inspecting existing migrations). Ports donor schema/seeder rather than green-fielding (Decision #12). DoDs are mock-verifiable; the seeder mocks all external APIs (CLAUDE.md). SP4 (types/data layer) comes after the schema is fixed, so it doesn't lock a TS shape prematurely.
**Risk lens:** PASS with flags. ⚠ SP1 forward-only migration (disposable DB first). SP3 writes catalog rows but is **idempotent** (safe to re-run). No within-phase file overlaps (each file in one sub-phase; `agents/shared/settings.py` additive). Test coverage: every sub-phase has a pytest/SQL assertion. Painting-into-a-corner: schema → archetypes → catalog → data layer; 5c can map+recommend immediately after.
**Irreversible sub-phases:** SP1 (forward-only migration). SP3 writes data but is idempotent (not destructive).
