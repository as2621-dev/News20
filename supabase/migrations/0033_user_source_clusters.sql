-- Migration 0033 — user → source_cluster follow refs (Slice #20)
--
-- Source of truth: GitHub issue #20 (in-chat YouTube grid + X cluster pickers) +
-- plans/prd.md §7 persistence mapping ("cluster follows → user_content_sources rows
-- for members + cluster ref (new user link to source_clusters) for sweep scheduling
-- + theme attribution").
--
-- When a user follows an X cluster in onboarding, its members expand into individual
-- user_content_sources / user_personalities rows (existing, migration 0009). This
-- table adds the MISSING piece: a per-user link to the CLUSTER ITSELF, so the shared
-- once-daily X cluster sweep (migration 0031 / slice #23) can schedule "which clusters
-- have at least one follower" and theme reels can attribute back to the followed
-- cluster. Without it a cluster follow is indistinguishable from N unrelated member
-- follows.
--
-- ⚠ MIGRATION NUMBER: ships as 0033 — the next free number after the latest on disk,
-- 0032_daily_feeds_x_theme_rung.sql. FK only to 0022's source_clusters + auth.users,
-- so it applies cleanly on top of them.
--
-- ⚠ ADDITIVE / forward-only — one new table, its by-user index, and an owner-all RLS
-- policy. NO DROPs, no destructive ALTERs. Reversible only by a manual drop of the new
-- object on a disposable/backed-up DB.
--
-- RLS tier (mirror 0009 user_content_sources EXACTLY): OWNER-ALL — every row is pinned
-- to auth.uid() for select/insert/update/delete. Private per-user follow state.

-- ── user_source_clusters (per-user cluster follow ref) ────────────────────────
-- PK (user_id, cluster_id) — one follow row per (user, cluster), idempotent upsert.
-- added_via records the follow origin (onboarding_chat, manual, …) — free text like
-- user_content_sources.added_via (no closed set yet).
create table user_source_clusters (
  user_id                  uuid not null references auth.users (id) on delete cascade,
  cluster_id               uuid not null references source_clusters (cluster_id) on delete cascade,
  added_via                text,
  user_cluster_created_at  timestamptz not null default now(),
  constraint pk_user_source_cluster primary key (user_id, cluster_id)
);
-- Access pattern: (a) hydrate-by-user (a user's followed cluster set) — the PK already
-- leads with user_id; (b) followers-of-a-cluster (the sweep scheduler: "which clusters
-- have a follower") — add the by-cluster index.
create index idx_user_source_clusters_cluster on user_source_clusters (cluster_id);

-- ── RLS (mirror 0009 user_content_sources_owner_all EXACTLY) ──────────────────
alter table user_source_clusters enable row level security;
create policy user_source_clusters_owner_all on user_source_clusters
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
