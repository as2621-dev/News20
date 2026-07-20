-- Migration 0031 — shared X cluster sweeps + extracted themes (Slice #23)
--
-- Source of truth: GitHub issue #23 (X cluster sweep + theme extraction — once
-- daily, shared, original-only) + plans/prd.md stories #31/#35/#38.
--
-- The SHARED X data layer. Each followed cluster (source_clusters, 0022) is swept
-- ONCE PER DAY in a single batched xAI x_search call, and its extracted themes are
-- stored keyed by (cluster_id, sweep_date) — NOT per user — so cost stays flat as
-- users grow: N users following the same cluster share ONE sweep row. This mirrors
-- the "shared, interest-keyed, produced once" model of the stories pipeline (a
-- story is ingested once and fanned out), applied to X clusters.
--
-- ⚠ MIGRATION NUMBER: ships as 0031 — the next free number after the latest on
-- disk, 0030_user_deferred_questions.sql. FK only to 0022's source_clusters, so it
-- applies cleanly on top of it.
--
-- ⚠ ADDITIVE / forward-only — one new table, its indexes, and a public-read RLS
-- policy. NO DROPs, no destructive ALTERs. Reversible only by a manual drop of the
-- new object on a disposable/backed-up DB.
--
-- RLS tier (mirror 0022 source_clusters): PUBLIC-READ (anon feed reads the themes),
-- service-role writes only — there is NO write policy, so only the service-role key
-- (which bypasses RLS) writes sweeps. Non-sensitive shared editorial data.

-- ── x_cluster_sweeps (one row per cluster per day — the shared sweep + themes) ──
-- One sweep of a cluster's followed X handles on one calendar day. The
-- (cluster_id, sweep_date) UNIQUE is the once-per-day idempotency key: the sweeper
-- upserts on it, so multiple users following the same cluster still yield exactly
-- ONE sweep per cluster per day. A silent cluster is stored HONESTLY — the row
-- exists with original_post_count = 0 and themes = '[]' (never a fabricated theme).
--
-- themes jsonb: an array of extracted-theme objects, each of shape
--   { "theme_summary": text,
--     "supporting_handles": [text, ...],      -- >= 2 distinct handles (multi-handle gate)
--     "supporting_tweet_urls": [text, ...] }  -- the real posts the theme is drawn from
-- Attribution is load-bearing: a theme MUST trace to >= 2 handles' real posts, so a
-- single loud handle's thread never mints a theme (enforced in code, not the model).
create table x_cluster_sweeps (
  sweep_id             uuid primary key default gen_random_uuid(),
  cluster_id           uuid not null references source_clusters (cluster_id) on delete cascade,
  sweep_date           date not null,
  swept_at             timestamptz not null default now(),
  handle_count         smallint not null default 0,
  raw_post_count       smallint not null default 0,
  original_post_count  smallint not null default 0,
  themes               jsonb not null default '[]'::jsonb,
  -- Once-per-day idempotency: the sweeper upserts on this pair, so a re-run (or a
  -- second follower's feed build) reads the existing row instead of re-sweeping.
  constraint uq_x_cluster_sweep_cluster_date unique (cluster_id, sweep_date)
);
-- Access pattern: themes-for-a-cluster-today (#24 X theme-of-day reels reads this),
-- and the sweeper's own once-per-day existence check — both served by the index the
-- uq_x_cluster_sweep_cluster_date UNIQUE already creates on (cluster_id, sweep_date),
-- so no separate index is added.

-- ── RLS (mirror 0022 source_clusters EXACTLY) ─────────────────────────────────
-- PUBLIC-READ shared editorial data: anon SELECT via `using (true)`; NO write
-- policy, so only the service-role key (which bypasses RLS) writes sweeps.
alter table x_cluster_sweeps enable row level security;
create policy x_cluster_sweeps_public_read on x_cluster_sweeps
  for select using (true);
