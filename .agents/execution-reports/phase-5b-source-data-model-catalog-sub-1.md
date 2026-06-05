# Phase 5b — Sub-phase 1 execution report

**Sub-phase:** Migration — content-source + personality + archetype schema
**Status:** SUCCESS
**Date:** 2026-06-05

## What was implemented

An additive, forward-only migration creating the content-source backbone:

- **2 enums:** `content_source_type` (`youtube_channel|podcast|x_account|personality`), `source_priority` (`off|big_stuff|everything`).
- **7 tables:**
  - `content_sources` — curated catalog; `source_id uuid` PK, `content_source_type`, `external_id`, `source_name`, `source_description`, `thumbnail_url`, `subscriber_count bigint`, `platform_metadata jsonb`, `personas text[]`, `topic_tags text[]`, `popularity_score numeric`, `is_curated bool`, `last_fetched_at`. **Unique `(content_source_type, external_id)`**. **GIN** on `personas` + `topic_tags` (+ a `popularity_score desc` btree).
  - `user_content_sources` — follow junction; `user_id`→`auth.users(id)`, `source_id`→`content_sources`, `source_priority default 'everything'`, `added_via text`, **PK `(user_id, source_id)`**.
  - `content_source_items` — per-item raw store (global, no user_id); `item_id uuid` PK, `source_id`→`content_sources`, `external_id`, `item_title`, `item_url`, `author_name`, `published_at`, `raw_transcript`, `item_summary`, `processing_status`. **Unique `(source_id, external_id)`**.
  - `personalities` + `user_personalities` + `personality_appearances` — ported from TL;DW `006_personalities.sql` (010/014 persona/tag/display-name folds inlined). `personality_appearances` links into `content_source_items` (the renamed `content_items`).
  - `archetypes` — NEW; `archetype_slug` unique, `archetype_label`, `archetype_vector jsonb`.
- **RLS:** public-read on `content_sources`/`content_source_items`/`personalities`/`personality_appearances`/`archetypes`; owner-all (`= auth.uid()` USING + WITH CHECK, idiom copied verbatim from 0005) on `user_content_sources`/`user_personalities`.
- **1 RPC:** `user_personality_spotlights(p_user_id, p_window_hours)` — ported faithfully from the donor (SECURITY DEFINER, `search_path = public`, revoke-from-public + grant to authenticated/service_role), repointed at `content_source_items`.

## Files created

- `supabase/migrations/0009_content_sources.sql`
- `.agents/execution-reports/phase-5b-source-data-model-catalog-sub-1.md` (this file)

No other files touched.

## Divergences from the SP1 brief (surfaced per Rule 7/11/12)

1. **Migration number: 0009, NOT 0008 (CRITICAL — orchestrator must note).**
   The brief said "latest on disk is 0006, use 0008, 0007 is reserved." By execution
   time, **two sibling migrations had already landed**: `0007_entity_registry.sql`
   (Phase 5 picker) and `0008_feed_allocation.sql` (Phase 5a "Build your 30"). Using
   0008 would collide on filename and on objects. I took the next free number, **0009**.
   The migration has **no FK to any 0007/0008 object** (verified — the only mentions of
   0007/0008 names are in explanatory comments), so it still applies cleanly on the
   0001–0006 lower bound the brief required, AND on 0001–0008.

2. **User FK → `auth.users(id)`, NOT `users(user_id)` (as the brief's "`user_id → users`" implied).**
   The shipped convention is split: `0003` references `public.users(user_id)`, but the
   **three most recent** user-scoped tables — `0005 follows`, `0007 user_entity_follows`,
   `0008 user_feed_allocation` — all reference `auth.users(id)` directly. Per Rule 7/11
   (pick the newer/more-tested pattern), I used `auth.users(id)`. Both resolve to the same
   uuid. The owner-RLS predicate column is named `user_id` (matching the donor + the
   `= auth.uid()` idiom). **Flagged for the orchestrator** in case the data layer (SP4)
   assumed a `public.users` join.

3. **`content_source_type` / `source_priority` are ENUMs**, where the donor used
   text+CHECK. News20 house style (0001/0004/0008) models closed editorial sets as enums.
   `processing_status` and `added_via` are kept as free text (no closed set locked yet;
   5c/5e tighten them) — matching the donor's looseness there.

4. **Public-read (not `to authenticated`).** The donor gated catalog reads behind
   `authenticated`; News20 onboarding reads the catalog anonymously, so the public-read
   policies use `for select using (true)` — matching the 0002/0003/0004/0007 News20
   convention, not the donor's.

## Self-review findings + fixes

| # | Check | Result |
|---|---|---|
| 1 | No enum-name collision vs 0001–0008 | PASS (`content_source_type`, `source_priority` unique) |
| 2 | No table-name collision; `sources`/`outlets`/`story_sources` NOT reused | PASS |
| 3 | No index-name collision vs 0001–0008 | PASS (14 idx names all unique) |
| 4 | No policy-name collision | PASS |
| 5 | No function-name collision | PASS (`user_personality_spotlights` absent from disk) |
| 6 | Every FK target resolves (within-migration order or `auth.users`) | PASS |
| 7 | FK column types match targets (all `uuid` → `uuid` PKs) | PASS |
| 8 | RLS enabled on all 7 new tables | PASS |
| 9 | A policy exists on all 7 new tables | PASS |
| 10 | Unique key on `content_sources (type, external_id)` + `content_source_items (source_id, external_id)` | PASS |
| 11 | GIN on `content_sources.personas` + `topic_tags` (+ personalities) | PASS |
| 12 | No DDL dependency on a 0007/0008 object | PASS (only comment mentions) |
| 13 | Dollar-quote / paren balance / statement terminators | PASS (2 `$$`, 73=73 parens) |

No critical/high issues found during review; nothing required a fix beyond the
number/FK decisions made up front.

## Validation results

- `npm run lint` (biome): **PASS** — `Checked 105 files. No fixes applied.` exit 0.
  SQL is outside biome's surface, confirming the TS/JS lint surface is undisturbed.
- Static SQL correctness pass: paren balance 73=73, dollar-quotes balanced (2),
  7 `create table`, 2 enum closers, 9 `^);` closers all accounted for, FK ordering
  correct (referenced objects defined before use).
- **No live DB available** (no `supabase/config.toml`, no psql/Docker) — runtime
  assertions are DEFERRED-offline (see DoD table). I did **not** run or fake them.

## Definition of done (SP1)

| DoD item | Verdict | Justification |
|---|---|---|
| Schema present (enums, 7 tables, archetypes) | **PASS** | By inspection — all objects created. |
| FKs resolvable + correct types | **PASS** | All FK targets resolve to in-migration objects or `auth.users`; all `uuid`→`uuid`. |
| RLS on every new table + correct policies | **PASS** | 7/7 tables `enable row level security` + a policy each; owner-all idiom copied from 0005. |
| Unique keys + GIN indexes present | **PASS** | `(content_source_type, external_id)`, `(source_id, external_id)` uniques; GIN on personas/topic_tags. |
| Applies cleanly on a DB holding 0001–0006 (and 0007/0008 if present) | **DEFERRED — offline** | No DB to apply against; verified statically (no FK to 0007/0008, no name collisions, correct intra-file ordering). Verify on manual apply. |
| Unique key rejects a duplicate `(youtube_channel, UC123)` upsert | **DEFERRED — offline** | Constraint is written; requires a live DB to assert the reject. |
| GIN query `personas && array['ai-frontier-tech']` uses the index | **DEFERRED — offline** | GIN index written; requires `EXPLAIN` on a live DB. |
| Anon `SELECT` on `content_sources` succeeds; anon SELECT on another user's `user_content_sources` returns zero rows | **DEFERRED — offline** | RLS policies written (public-read vs owner-all); requires a live DB + two auth sessions to assert. |

## Concerns for the orchestrator

1. **Renumber awareness (action required):** This is `0009`, not `0008`. Any later
   phase doc / sibling that hard-codes "0008 = content sources" is now wrong — content
   sources are `0009`; `0008` is `feed_allocation`. SP2's `supabase/seed/archetypes.sql`
   and SP3's seeder target the `archetypes`/`content_sources`/`personalities` tables this
   migration creates (table names unchanged), so they are unaffected by the number.

2. **User-FK divergence (see Divergence #2):** SP4's data layer (`src/lib/sources.ts`)
   should scope `user_content_sources`/`user_personalities` via `auth.uid()` directly
   (RLS does this server-side); there is no `public.users` join on these junctions.

3. **`personality_appearances` is public-read**, diverging from the donor's
   `to authenticated`. If News20 later wants appearances hidden from anon, tighten to a
   policy — but per the News20 public-read catalog convention this is consistent today.
   Not populated until Phase 5d (the hunt adapter).

4. **`content_source_items` is created but empty** — Phase 5d populates it; 5b only
   builds the shell (matches open-question #2 in the phase file: raw items land here, not
   folded into `stories`).
