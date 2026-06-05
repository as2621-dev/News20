-- Migration 0009 — Content sources + personalities + archetype catalog (Phase 5b SP1)
--
-- Source of truth: plans/phase-5b-source-data-model-catalog.md SP1 +
-- reference/sources-reuse-map.md §1 (the TL;DW → News20 schema port map). Ports
-- the donor's source stack (`~/TLDW-Phase2/tldw/voice-agent-dashboard/`):
--   001_voice_agent_schema.sql:166-257  → content_sources / user_content_sources /
--                                          content_source_items (RENAMED on port)
--   006_personalities.sql               → personalities / user_personalities /
--                                          personality_appearances + spotlights RPC
--   010_recommendation_seeds.sql:23-48  → topic_tags + popularity_score + GIN
--   014_catalog_personas.sql:34-51      → personas text[] + GIN
-- Plus a NEW `archetypes` table (no donor analog) for the per-archetype catalog
-- the 5c recommendation matcher reads.
--
-- ⚠ MIGRATION NUMBER: this ships as 0009, NOT 0008. When the SP1 brief was written
-- the latest on disk was 0006 and 0008 was assigned. By apply time two sibling
-- migrations had already landed: 0007_entity_registry.sql (Phase 5 picker) and
-- 0008_feed_allocation.sql (Phase 5a "Build your 30"). To keep the forward-only
-- apply order monotonic and avoid a filename/object collision, this migration takes
-- the next free number, 0009. It has NO FK to any 0007/0008 object, so it still
-- applies cleanly on a DB holding only 0001–0006 (the lower bound the brief
-- required) AND on a DB holding 0001–0008.
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0001 (content tables, enums)
-- and 0003 (users + auth mapping). NO DROPs, no destructive ALTERs — only new
-- enums, tables, indexes, policies, and one RPC. ⚠ Forward-only (additive) — no
-- down migration; reversible only by manual drop of the new objects on a
-- disposable/backed-up DB.
--
-- NAMING COLLISION GUARD (per the phase file + reuse-map §1): News20 already has
-- `outlets` (0001 news outlets), `story_sources` (0001 per-story article
-- attribution), and an implicit `sources` namespace. The donor's
-- `sources`/`user_sources`/`content_items` are RENAMED here to
-- `content_sources`/`user_content_sources`/`content_source_items`.
-- `personalities`/`user_personalities` carry over unchanged (no collision).
--
-- KEY DESIGN DECISIONS (surfaced per Rule 7 — conflicting conventions resolved):
--
--  1. USER FK → auth.users(id), NOT public.users(user_id). The shipped convention
--     is split: 0003 references public.users(user_id), but the THREE most recent
--     user-scoped tables — 0005 follows, 0007 user_entity_follows, 0008
--     user_feed_allocation — all reference auth.users(id) directly. Per Rule 7/11
--     (pick the newer/more-tested pattern), the user FK here is auth.users(id) and
--     the owner predicate column is named user_id. NOTE: the SP1 brief said
--     `user_id → users`; this DIVERGES to auth.users(id) to match the shipped
--     trio. Both resolve to the same uuid.
--
--  2. content_source_type is an ENUM (not a CHECK-constrained text like the donor's
--     source_type). News20 models closed editorial sets as enums (cf. segment_slug,
--     coverage_mode, feed_category). The donor used a text+CHECK; the News20 house
--     style (0001/0004/0008) is enums. The donor's deleted-then-re-added
--     `twitter_account` (007_prune_source_types) is re-introduced here as
--     `x_account` per reuse-map §1 + the master-plan x-axis.
--
--  3. content_source_items keeps its own surrogate item_id uuid PK AND a unique
--     (source_id, external_id) — mirroring the donor's content_items, but the
--     dedup key is per-SOURCE (source_id, external_id) rather than the donor's
--     global (source_type, external_id), so the same external_id under two sources
--     can't false-collide. Public-read (it is a global catalog like the donor's,
--     populated by the service-role ingestion worker in Phase 5d).
--
--  4. personality_appearances.content_source_item_id references content_source_items
--     (the renamed content_items). The spotlights RPC is ported faithfully, pointing
--     at content_source_items.source_id (the donor read content_items.source_id).

-- ── Enums ────────────────────────────────────────────────────────────────────
-- content_source_type: the closed set of source axes. youtube_channel / podcast
-- carry over from the donor; x_account re-adds the donor's pruned twitter_account
-- (reuse-map §1); personality lets a personality be addressable as a source axis.
create type content_source_type as enum (
  'youtube_channel',
  'podcast',
  'x_account',
  'personality'
);

-- source_priority: the 3-state follow priority for the control surface (D2 in the
-- reuse-map — donor's user_sources.priority was a plain int; News20 makes it a
-- closed enum). 'off' = followed-but-muted; 'big_stuff' = only high-traction items;
-- 'everything' = ingest all (the follow default).
create type source_priority as enum (
  'off',
  'big_stuff',
  'everything'
);

-- ── content_sources (curated catalog of followable source axes) ──────────────
-- Ports donor public.sources (001:166-185) + the persona/tag/popularity columns
-- folded forward from 010/014. unique (content_source_type, external_id) is the
-- upsert key the Phase 5c/5d resolver/seeder writes against. personas + topic_tags
-- are GIN-indexed for the && ANY-overlap recommendation filters.
create table content_sources (
  source_id            uuid primary key default gen_random_uuid(),
  content_source_type  content_source_type not null,
  external_id          text not null,
  source_name          text not null,
  source_description    text,
  thumbnail_url        text,
  subscriber_count     bigint,
  platform_metadata    jsonb,
  personas             text[] not null default '{}',
  topic_tags           text[] not null default '{}',
  popularity_score     numeric not null default 50,
  is_curated           boolean not null default true,
  last_fetched_at      timestamptz,
  source_created_at    timestamptz not null default now(),
  constraint uq_content_source_type_external unique (content_source_type, external_id)
);
-- Access patterns: persona-filtered catalog browse (5c) via && over personas/tags
-- (GIN), and popularity-ranked ordering within a persona.
create index idx_content_sources_personas   on content_sources using gin (personas);
create index idx_content_sources_topic_tags on content_sources using gin (topic_tags);
create index idx_content_sources_popularity on content_sources (popularity_score desc);

-- ── user_content_sources (per-user follow junction with 3-state priority) ────
-- Ports donor public.user_sources (001:198-208). PK (user_id, source_id) — one
-- follow row per (user, source), idempotent upsert. source_priority defaults
-- 'everything' (a fresh follow ingests all). added_via records the follow origin
-- (onboarding recommendation, manual add, youtube import, …) — free text like the
-- donor (no closed set yet; 5c/5e tighten it).
create table user_content_sources (
  user_id            uuid not null references auth.users (id) on delete cascade,
  source_id          uuid not null references content_sources (source_id) on delete cascade,
  source_priority    source_priority not null default 'everything',
  added_via          text,
  user_source_created_at timestamptz not null default now(),
  constraint pk_user_content_source primary key (user_id, source_id)
);
-- Access pattern: hydrate-by-user (the control surface / ingestion reads a user's
-- whole follow set). PK already leads with user_id; add the explicit by-user index
-- to mirror 0005's idx_follows_user / 0007's idx_user_entity_follows_user.
create index idx_user_content_sources_user on user_content_sources (user_id);

-- ── content_source_items (per-item raw store; global, no user_id) ────────────
-- Ports donor public.content_items (001:234-257). GLOBAL catalog (no user_id) —
-- populated by the service-role ingestion worker in Phase 5d; Phase 5b only
-- creates the shell. Dedup key is per-source unique (source_id, external_id).
-- raw_transcript holds captions/transcript; item_summary the LLM summary;
-- processing_status the pipeline state (free text like the donor's CHECK-text).
create table content_source_items (
  item_id            uuid primary key default gen_random_uuid(),
  source_id          uuid not null references content_sources (source_id) on delete cascade,
  external_id        text not null,
  item_title         text not null,
  item_url           text,
  author_name        text,
  published_at       timestamptz,
  raw_transcript     text,
  item_summary       text,
  processing_status  text not null default 'pending',
  item_created_at    timestamptz not null default now(),
  constraint uq_content_source_item unique (source_id, external_id)
);
-- Access patterns: by-source listing (the per-source item feed) and by-published
-- ordering (the freshness window the spotlights RPC + ingestion read).
create index idx_content_source_items_source    on content_source_items (source_id);
create index idx_content_source_items_published on content_source_items (published_at desc);

-- ── personalities (curated named-creator catalog) ────────────────────────────
-- Ports donor public.personalities (006:23-58) UNCHANGED in name (no collision),
-- with the 010/014 forward folds (topic_tags, personas, display_name unique)
-- inlined since this is the FIRST migration to create the table.
create table personalities (
  personality_id       uuid primary key default gen_random_uuid(),
  display_name         text not null,
  aliases              text[] not null default '{}',
  bio                  text,
  photo_url            text,
  youtube_channel_ids  text[] not null default '{}',
  personas             text[] not null default '{}',
  topic_tags           text[] not null default '{}',
  popularity_score     numeric not null default 50,
  is_curated           boolean not null default true,
  personality_created_at timestamptz not null default now(),
  constraint uq_personality_display_name unique (display_name)
);
-- GIN on aliases (the hunt adapter's alias-contains match) + personas/topic_tags
-- (the && recommendation filters); a plain index on is_curated like the donor.
create index idx_personalities_is_curated on personalities (is_curated);
create index idx_personalities_aliases    on personalities using gin (aliases);
create index idx_personalities_personas   on personalities using gin (personas);
create index idx_personalities_topic_tags on personalities using gin (topic_tags);

-- ── user_personalities (per-user personality follow junction) ────────────────
-- Ports donor public.user_personalities (006:52-58). PK (user_id, personality_id).
create table user_personalities (
  user_id            uuid not null references auth.users (id) on delete cascade,
  personality_id     uuid not null references personalities (personality_id) on delete cascade,
  is_active          boolean not null default true,
  user_personality_created_at timestamptz not null default now(),
  constraint pk_user_personality primary key (user_id, personality_id)
);
create index idx_user_personalities_user on user_personalities (user_id);

-- ── personality_appearances (hunt + cross-mention links into the item store) ─
-- Ports donor public.personality_appearances (006:72-81). Links a personality to a
-- content_source_item (host/guest/mention/quote) detected by the Phase 5d hunt
-- adapter. unique (personality_id, content_source_item_id) — one link per pair.
create table personality_appearances (
  appearance_id           uuid primary key default gen_random_uuid(),
  personality_id          uuid not null references personalities (personality_id) on delete cascade,
  content_source_item_id  uuid not null references content_source_items (item_id) on delete cascade,
  appearance_type         text not null check (appearance_type in ('host', 'guest', 'mention', 'quote')),
  match_method            text not null check (match_method in ('alias_regex', 'llm', 'search_query')),
  confidence              numeric not null,
  detected_at             timestamptz not null default now(),
  constraint uq_personality_appearance unique (personality_id, content_source_item_id)
);
create index idx_personality_appearances_personality on personality_appearances (personality_id);
create index idx_personality_appearances_item        on personality_appearances (content_source_item_id);
create index idx_personality_appearances_detected_at on personality_appearances (detected_at desc);

-- ── archetypes (NEW — per-archetype interest vector catalog) ─────────────────
-- No donor analog. Each archetype is a normalized weight map (archetype_vector
-- jsonb) over the 8 pinned categories (reuse-map §C1); 5c matches a user's
-- interest vector against these. archetype_slug is the stable seed key (5b SP2
-- re-seeds the draft set without schema change). Public-read reference data.
create table archetypes (
  archetype_id       uuid primary key default gen_random_uuid(),
  archetype_slug     text not null unique,
  archetype_label    text not null,
  archetype_vector   jsonb not null default '{}'::jsonb,
  archetype_created_at timestamptz not null default now()
);

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- PUBLIC-READ reference/catalog tables (mirror the 0002 content tables: anon
-- SELECT via `using (true)`; NO write policy, so only the service-role key, which
-- bypasses RLS, seeds/curates/ingests). These are non-sensitive shared catalog
-- data. NOTE the donor used `for select to authenticated` (its app is auth-gated);
-- News20's catalog/reference tables are public-read (anon onboarding reads them),
-- matching the 0002/0003/0004/0007 News20 convention.
alter table content_sources enable row level security;
create policy content_sources_public_read on content_sources
  for select using (true);

alter table content_source_items enable row level security;
create policy content_source_items_public_read on content_source_items
  for select using (true);

alter table personalities enable row level security;
create policy personalities_public_read on personalities
  for select using (true);

alter table personality_appearances enable row level security;
create policy personality_appearances_public_read on personality_appearances
  for select using (true);

alter table archetypes enable row level security;
create policy archetypes_public_read on archetypes
  for select using (true);

-- OWNER-ALL per-user tables (mirror 0005 follows_owner_all / 0007
-- user_entity_follows_owner_all / 0008 user_feed_allocation_owner_all EXACTLY).
-- Both the USING (read/delete) and WITH CHECK (insert/update) predicates pin the
-- row to the caller — another user's follow rows return zero.
alter table user_content_sources enable row level security;
create policy user_content_sources_owner_all on user_content_sources
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

alter table user_personalities enable row level security;
create policy user_personalities_owner_all on user_personalities
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ── user_personality_spotlights RPC (port donor 006:112-151) ─────────────────
-- Returns a user's followed personalities with their last-window appearance +
-- distinct-source counts. Aggregation lives in SQL because PostgREST cannot
-- express a HAVING across joined tables in a single GET. Ported faithfully, with
-- content_items → content_source_items and ci.source_id preserved (the item store
-- still carries source_id). SECURITY DEFINER + filtered by p_user_id, so a caller
-- can only retrieve their own spotlights; search_path pinned to public.
create or replace function public.user_personality_spotlights(
  p_user_id uuid,
  p_window_hours int default 24
)
returns table (
  personality_id        uuid,
  display_name          text,
  appearance_count      bigint,
  distinct_source_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    p.personality_id,
    p.display_name,
    count(distinct pa.content_source_item_id) as appearance_count,
    count(distinct ci.source_id)              as distinct_source_count
  from public.personalities p
  join public.user_personalities up
    on up.personality_id = p.personality_id
  join public.personality_appearances pa
    on pa.personality_id = p.personality_id
  join public.content_source_items ci
    on ci.item_id = pa.content_source_item_id
  where up.user_id = p_user_id
    and up.is_active = true
    and pa.detected_at >= now() - make_interval(hours => p_window_hours)
  group by p.personality_id, p.display_name
  order by distinct_source_count desc, appearance_count desc;
$$;

-- SECURITY DEFINER hardening (mirror donor 006:150-151): revoke the implicit
-- PUBLIC execute grant and re-grant only to authenticated + service_role, so an
-- unauthenticated caller cannot invoke the definer-privileged function.
revoke all on function public.user_personality_spotlights(uuid, int) from public;
grant execute on function public.user_personality_spotlights(uuid, int) to authenticated, service_role;
