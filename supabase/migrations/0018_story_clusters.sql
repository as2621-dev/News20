-- Migration 0018 — Story clusters (M3a Stage-C persistence)
--
-- Source of truth: reference/shared-pool-pipeline.md §3 (with the 2026-06-18
-- owner-approved execution deltas banner) + plans/phase-m3a-clustering-foundations.md
-- (Sub-phase 3).
--
-- Context: this is the Stage (C) persistence layer of the shared-pool clusterer
-- (spec §2C/§3). It stores the ROLLING CENTROIDS of the custom online
-- assign-or-spawn engine so a cluster seen yesterday can be re-matched today
-- (cross-day continuity). `cluster_centroid` is the running-mean of each member
-- article's Gemini `text-embedding-004` text embedding — a 768-dimension,
-- L2-normalized vector (owner delta: Gemini API embeddings, NOT local MiniLM/384;
-- see spec §3 banner). Cosine similarity over normalized vectors == dot product.
--
-- This migration is ADDITIVE and ISOLATED:
--   * It does NOT touch, replace, or reference `stories` or `story_url_aliases`.
--   * `cluster_id` (a synthetic text id minted by the engine) bridges to
--     `stories.story_id` later, via `story_url_aliases`, inside the BATCH (M3b) —
--     reusing `daily_batch.build_story_id_resolver`. No FK to `stories` here on
--     purpose: clusters span URLs/reprints, and the bridge is resolved in code.
--   * It REUSES the existing `feed_category` enum (defined in
--     0008_feed_allocation.sql:57, extended in 0010). It does NOT redefine it.
--
-- ⚠ irreversible (live DB schema). LIVE APPLY IS DEFERRED to the human checkpoint
-- (batched with 0017). This file is AUTHORED ONLY. All statements are guarded with
-- `if not exists`, so a re-run is safe and idempotent.

-- pgvector fallback: if the `vector` extension is unavailable on the Supabase
-- instance, `cluster_centroid` can instead be declared `real[]` and cosine
-- similarity computed in Python at match time (daily volume is small enough that
-- an in-process scan is cheap). DEFAULT to the pgvector path below; the `real[]`
-- fallback is the documented escape hatch only, applied at the live-apply
-- checkpoint if `create extension ... vector` fails.

create extension if not exists vector;

-- ── story_clusters — one row per rolling cluster (running-mean centroid) ───────
create table if not exists story_clusters (
  cluster_id             text primary key,
  cluster_centroid       vector(768),                 -- running-mean Gemini text-embedding-004 centroid (768-d, L2-normalized)
  cluster_category       feed_category not null,       -- reuses the existing enum (0008/0010)
  cluster_subcategory    text,
  cluster_reel_format    text not null default 'event',
  cluster_member_count   int  not null default 1,
  cluster_outlet_count   int  not null default 1,
  cluster_first_seen_utc timestamptz not null,
  cluster_last_seen_utc  timestamptz not null,
  cluster_importance     real,
  cluster_velocity       real,
  cluster_status         text not null default 'active'
);

-- ANN index for centroid nearest-neighbour lookups (assign-or-spawn match step).
create index if not exists story_clusters_centroid_idx
  on story_clusters using ivfflat (cluster_centroid vector_cosine_ops);

-- Window-scan index for "active clusters in category X since T" (cross-day load).
create index if not exists story_clusters_cat_lastseen_idx
  on story_clusters (cluster_category, cluster_last_seen_utc);

-- ── story_cluster_members — the member URLs that rolled into each centroid ─────
create table if not exists story_cluster_members (
  cluster_id      text not null references story_clusters(cluster_id) on delete cascade,
  member_url      text not null,
  member_outlet   text,
  member_seen_utc timestamptz not null,
  primary key (cluster_id, member_url)
);

-- ── Apply + verify (HUMAN CHECKPOINT ONLY — do NOT run from this sub-phase) ────
-- Apply via the IPv4 session pooler (:6543), batched with 0017:
--   supabase db push --db-url "$SUPABASE_SESSION_POOLER_URL"
-- Smoke query after a fresh apply (expected: 0):
--   select count(*) from story_clusters;
