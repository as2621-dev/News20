-- Migration 0022 — Source clusters + cluster members (Phase FSR-M1 SP1)
--
-- Source of truth: plans/phase-fsr-m1-catalog-clusters-nodup.md SP1.
-- Adds the net-new editorial CLUSTER grouping over the EXISTING catalog from
-- 0009_content_sources.sql (content_sources / personalities). A cluster is a
-- hand-authored, named group of followable rows within ONE of the 8 topic roots
-- (per PRD Decision #6: a cluster is net-new — NOT an archetype, NOT a persona).
-- M1 adds ONLY these two tables; it does NOT touch any 0009 object.
--
-- ⚠ MIGRATION NUMBER: ships as 0022 — the next free number after the latest on
-- disk, 0021_taxonomy_8_roots_backfill.sql. FKs only to 0009 objects
-- (content_sources, personalities), so it applies cleanly on top of them.
--
-- ⚠ ADDITIVE / forward-only — no down migration. NO DROPs, no destructive ALTERs:
-- only two new tables, their indexes, and public-read RLS policies. Reversible
-- only by a manual drop of the new objects on a disposable/backed-up DB.
--
-- THE 8 CLUSTER CATEGORIES are the topic roots in agents/pipeline/categories.py
-- TOPIC_CATEGORIES (ai · geopolitics · business · environment · politics · tech ·
-- sport · arts) — the same vocabulary as content_sources.topic_tags[0]. The
-- youtube/x source axes are NOT cluster categories; the cluster_category CHECK
-- below lists EXACTLY these 8 roots. (Mirrored as a set-equality assertion in
-- tests/supabase/test_migration_0022_source_clusters.py so a drift fails loud.)
--
-- RLS tier (mirror 0009): PUBLIC-READ on both tables (anon onboarding reads them),
-- service-role writes only — identical to content_sources (`for select using
-- (true)`; NO write policy, so only the service-role key, which bypasses RLS,
-- seeds/curates them).

-- ── source_clusters (named editorial groupings within one topic root) ─────────
-- cluster_slug is the stable seed key (the SP3 editorial seed upserts on it).
-- cluster_category is constrained to the 8 topic roots (see header). is_curated
-- mirrors the 0009 catalog flag (curated rows are the ones surfaced).
create table source_clusters (
  cluster_id          uuid primary key default gen_random_uuid(),
  cluster_slug        text not null unique,
  cluster_label       text not null,
  cluster_category    text not null
    check (cluster_category in ('ai','geopolitics','business','environment','politics','tech','sport','arts')),
  cluster_sort_order  smallint not null default 0,
  is_curated          boolean not null default true,
  cluster_created_at  timestamptz not null default now()
);
-- Access pattern: clusters-for-one-category, ordered (the resolver's first filter).
create index idx_source_clusters_category on source_clusters (cluster_category);

-- ── source_cluster_members (ordered members of a cluster) ─────────────────────
-- A member references EXACTLY ONE of content_sources(source_id) or
-- personalities(personality_id) — the XOR `check ((source_id is not null) <>
-- (personality_id is not null))` (a boolean <> is XOR: exactly one non-null). Both
-- FKs cascade-delete with their parent. member_sort_order is the ordered render
-- position. member_created_at is plain timestamptz per the SP1 contract of record
-- (no default — the seed/writer sets it).
create table source_cluster_members (
  cluster_member_id   uuid primary key default gen_random_uuid(),
  cluster_id          uuid not null references source_clusters (cluster_id) on delete cascade,
  source_id           uuid references content_sources (source_id) on delete cascade,
  personality_id      uuid references personalities (personality_id) on delete cascade,
  member_sort_order   smallint not null,
  member_created_at   timestamptz,
  constraint ck_source_cluster_member_exactly_one
    check ((source_id is not null) <> (personality_id is not null))
);
-- Partial-unique guards: the same (cluster_id, source_id) / (cluster_id,
-- personality_id) pair can't be inserted twice. Partial (WHERE … is not null) so
-- the NULL leg of each XOR member does not collide on the unique.
create unique index uq_source_cluster_member_source
  on source_cluster_members (cluster_id, source_id)
  where source_id is not null;
create unique index uq_source_cluster_member_personality
  on source_cluster_members (cluster_id, personality_id)
  where personality_id is not null;
-- Access pattern: members-of-a-cluster in render order (the resolver's per-cluster read).
create index idx_source_cluster_members_cluster_order
  on source_cluster_members (cluster_id, member_sort_order);

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- PUBLIC-READ reference/catalog tables (mirror 0009 content_sources EXACTLY): anon
-- SELECT via `using (true)`; NO write policy, so only the service-role key (which
-- bypasses RLS) seeds/curates. These are non-sensitive shared editorial data.
alter table source_clusters enable row level security;
create policy source_clusters_public_read on source_clusters
  for select using (true);

alter table source_cluster_members enable row level security;
create policy source_cluster_members_public_read on source_cluster_members
  for select using (true);
