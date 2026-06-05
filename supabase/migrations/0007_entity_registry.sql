-- Migration 0007 — Entity registry + entity follows (Phase 5 SP1, recursive interest picker)
--
-- Source of truth: onboarding_interest_picker_spec.md §2 (topic vs entity), §5
-- (data model), §6 (registry list/search contract), §7 (follow payload + `source`
-- weighting) + interest_picker.html (the approved prototype + full seed dataset).
-- Phase file: plans/phase-5-recursive-interest-picker.md SP1.
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0001 (content + segments) and
-- 0003 (interests / users / auth mapping) and 0005 (follows pattern). NO DROPs, no
-- destructive ALTERs. The lone ALTER on `interests` adds a nullable-with-default
-- column, which is safe and back-compatible. ⚠ Forward-only (additive registry +
-- seed) — no down migration; reversible only by manual drop of the new objects.
--
-- Adds:
--   1 ext   — pg_trgm (trigram index for Add-your-own / Show-more entity search)
--   2 enums — entity_kind (the §2/§5 kind taxonomy), entity_follow_source (§7)
--   2 table — entities (public-read registry), user_entity_follows (owner-all)
--   1 ALTER — interests.interest_node_type text default 'topic' (so the picker can
--             render the existing topic taxonomy as `type:'topic'` nodes; §2)
--   RLS     — public-read entities (reference data, mirrors 0002 content tables);
--             owner-all user_entity_follows (mirrors 0005 follows_owner_all exactly)
--
-- KEY DESIGN DECISIONS (surfaced per Rule 7 — conflicting conventions resolved):
--
--  1. entity_id is `text`, NOT uuid. `interests` (0003) keys rows by a uuid PK with a
--     separate `interest_slug text unique`. We DIVERGE here and use a text PK equal to
--     the path-derived id, because (a) reference/supabase-schema.md §0 explicitly
--     sanctions "short text PKs where the prototype already uses a stable human-readable
--     slug — preserving them keeps the design payload portable and the seed legible";
--     (b) the spec §5/§7 keys nodes/follows by exactly this path-derived id
--     (e.g. business/corporate-news/what-to-track/earnings/companies-to-track/nvidia);
--     (c) it makes the seed deterministic + idempotent with no UUID juggling, and lets
--     the §7 `followId` BE the entity_id. So entity_id == entity_slug == prototype chip id.
--
--  2. entity_id is PATH-DERIVED (faithful to interest_picker.html's chip id =
--     idBase + '/' + slug(label)), NOT identity-derived. Consequence: a label reachable
--     via multiple paths (Nvidia under AI-hardware, Business-earnings, Tech-semis) is
--     THREE registry rows with three ids, all carrying ticker NVDA. The spec §11 / SP3
--     "dedupe to one underlying id with both paths" is a FOLLOW/PAYLOAD-layer concern
--     (SP3/SP4): the picker dedupes selections on a canonical identity (label+ticker/kind)
--     before persisting. The registry intentionally stays 1:1 with the prototype so
--     listEntities({parent}) (SP2) can page a parent's children by entity_parent_slug.
--     (See "Concerns for SP2" in the SP1 execution report.)
--
--  3. user_entity_follows.follow_user_id references auth.users(id) — mirroring the MOST
--     RECENT shipped user-scoped table, 0005 follows (which uses auth.users(id) directly),
--     rather than 0003's public.users(user_id). Both resolve to the same uuid; we pick the
--     newer/tested pattern (Rule 7). entity_id FK is `text` to match entities.entity_id.

-- ── Extension — pg_trgm (not created by any earlier migration; verified absent) ──
-- Powers the trigram GIN index that searchEntities (SP2) queries via ILIKE / similarity.
create extension if not exists pg_trgm;

-- ── Enums ────────────────────────────────────────────────────────────────────
-- entity_kind: the closed taxonomy from spec §2/§5. The UI uses `kind` for affordances
-- (companies render a ticker, etc.). Values transcribed VERBATIM from the spec list.
create type entity_kind as enum (
  'company', 'team', 'person', 'league', 'org', 'asset',
  'event', 'brand', 'franchise', 'conflict', 'genre', 'product'
);

-- entity_follow_source (spec §7): where a follow came from. 'custom' (the user typed it)
-- is higher-intent than a seed tap and is weighted more heavily in ranking (SP4 consumes
-- this). 'seed' = a default chip; 'more' = revealed via Show-more; 'custom' = Add-your-own.
create type entity_follow_source as enum ('seed', 'more', 'custom');

-- ── entities (public-read registry of followable dynamic things) ─────────────
-- entity_id == entity_slug == the prototype's path-derived chip id (see decision #1/#2).
-- entity_parent_slug is the set-id this node was seeded under (the listEntities scope).
-- entity_search_query is the searchable haystack (label + ticker) for the trigram index.
-- entity_is_curated marks v1 seed rows (vs. future user-discovered/registry-grown rows).
create table entities (
  entity_id            text primary key,
  entity_slug          text not null unique,
  entity_label         text not null,
  entity_kind          entity_kind not null,
  entity_ticker        text,
  entity_parent_slug   text,
  entity_search_query  text not null,
  entity_is_curated    boolean not null default true,
  entity_created_at    timestamptz not null default now()
);
-- Access patterns:
--   listEntities({parent, kind}) — keyset page a parent's children (SP2).
create index idx_entities_parent_kind on entities (entity_parent_slug, entity_kind, entity_id);
--   searchEntities({q}) — fuzzy/substring match over the haystack (SP2). A trigram GIN
--   index serves both ILIKE '%q%' and similarity()/word_similarity() ranking.
create index idx_entities_search_trgm on entities using gin (entity_search_query gin_trgm_ops);

-- ── user_entity_follows (per-user persistent entity follow set) ──────────────
-- PK (user_id, entity_id): one follow row per (user, entity) — idempotent toggle.
-- follow_path text[] holds the ancestry path the user took (spec §7 `path`), enabling the
-- feed to use both the specific follow and its ancestry for fallback/related content; it
-- is also where cross-path dedupe lands (one entity_id, multiple recorded paths if needed).
-- follow_weight is the ranking weight (SP4 sets custom > more/seed per the §7 intent signal).
create table user_entity_follows (
  follow_user_id      uuid not null references auth.users (id) on delete cascade,
  entity_id           text not null references entities (entity_id) on delete cascade,
  follow_path         text[] not null default '{}',
  follow_source       entity_follow_source not null,
  follow_weight       numeric not null default 1.0,
  follow_created_at   timestamptz not null default now(),
  constraint pk_user_entity_follow primary key (follow_user_id, entity_id)
);
-- Access pattern: hydrate-by-user (the picker/ranker reads a user's whole follow set).
-- The PK already leads with follow_user_id, but add the explicit by-user index to mirror
-- 0005's idx_follows_user convention for the batched hydrate read.
create index idx_user_entity_follows_user on user_entity_follows (follow_user_id);

-- ── ALTER interests — add interest_node_type so the picker renders topic nodes ──
-- The existing taxonomy (0003 `interests`, seeded in supabase/seed/interests.sql) holds the
-- TOPIC half of the picker tree. Tagging every interest row with type 'topic' lets the
-- recursive engine (SP3) render topic nodes uniformly alongside entity nodes (spec §2/§5).
-- Nullable-with-default ⇒ additive and back-compatible (existing readers ignore it).
alter table interests
  add column interest_node_type text not null default 'topic';

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- entities: PUBLIC-READ reference data (mirrors the content tables in 0002 — anon SELECT
-- via `using (true)`; NO write policy, so only the service-role key, which bypasses RLS,
-- seeds/curates it). The registry is non-sensitive shared catalog data.
alter table entities enable row level security;
create policy entities_public_read on entities
  for select using (true);

-- user_entity_follows: OWNER-ALL scoped to auth.uid() (mirrors 0005 follows_owner_all
-- EXACTLY). Both the USING (read/delete) and WITH CHECK (insert/update) predicates pin the
-- row to the caller — a leaked follow set is the worst failure, so the predicate is on the
-- user-id column on both sides. No cross-user read: another user's rows return zero.
alter table user_entity_follows enable row level security;
create policy user_entity_follows_owner_all on user_entity_follows
  for all using (follow_user_id = auth.uid()) with check (follow_user_id = auth.uid());

-- ── search_entities RPC (Phase 5 SP2 — Add-your-own / fuzzy registry search) ──
-- Backs searchEntities({q, kind?, parent?, limit}) in src/lib/entities.ts (spec §6
-- `search`). The client cannot run a trigram ILIKE + word_similarity ranking through
-- PostgREST's filter grammar cleanly, so the search is a STABLE SQL function over the
-- GIN trigram index idx_entities_search_trgm.
--
--   • Matches entity_search_query ILIKE '%' || q || '%' (the substring path the GIN
--     trigram index serves) and ranks best matches first via word_similarity(q, ...).
--   • Optional filters: entity_kind = k and entity_parent_slug = p are applied ONLY
--     when the arg is non-null (k is null / p is null short-circuits the predicate),
--     so the same function serves global and scoped search.
--   • Returns the §6 projection columns (entity_id, entity_label, entity_ticker,
--     entity_kind) — exactly what mapEntityRow() in entities.ts consumes.
--   • PARAM NAMING: args are q / k / p / lim (single-letter, NOT entity_*), so they
--     can never collide with the entities columns referenced unqualified in the body
--     — avoids the classic Postgres "column reference is ambiguous" bug.
--   • SECURITY INVOKER (the default): the function runs with the CALLER's privileges,
--     so the public-read RLS on entities still applies (anon/authed can search; the
--     function does NOT escalate). language sql + stable ⇒ no side effects, planner-
--     cacheable within a statement.
create or replace function search_entities(
  q text,
  k entity_kind default null,
  p text default null,
  lim int default 20
)
returns table (
  entity_id     text,
  entity_label  text,
  entity_ticker text,
  entity_kind   entity_kind
)
language sql
stable
security invoker
as $$
  select e.entity_id, e.entity_label, e.entity_ticker, e.entity_kind
  from entities e
  where e.entity_search_query ilike '%' || q || '%'
    and (k is null or e.entity_kind = k)
    and (p is null or e.entity_parent_slug = p)
  order by word_similarity(q, e.entity_search_query) desc, e.entity_label asc
  limit lim
$$;
