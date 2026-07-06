-- Migration 0032 — daily_feeds X theme-of-the-day rung metadata (Slice #24)
--
-- Phase FSR (source reels), slice #24 (X theme-of-the-day reels + honest ladder).
-- Source of truth: GitHub issue #24 + plans/prd.md stories #29/#30/#32/#33 +
-- reference/source-reels-spec.md §3 (the X theme ladder: theme → second theme →
-- roundup-of-takes → news; "the rung that filled each slot is stamped in daily_feeds
-- row metadata for the honest UI label"). Produced by
-- agents/pipeline/feed_assembly.assemble_niche_feed (X-slot theme fill); consumed by
-- src/lib/reel/sectionChips.ts (the honest X-theme chip + attribution credit).
--
-- ── WHAT THIS ADDS (2 columns on daily_feeds) ────────────────────────────────────
--  1. feed_x_theme_rung — nullable text. Which rung of the X theme ladder filled this
--     slot: 'theme' (theme-of-the-day), 'second_theme', or 'roundup' (roundup-of-takes).
--     NULL on every non-X-theme slot (news floor, interest, source, youtube). THIS is
--     the honesty stamp: a news-floor X slot carries NULL (real news, never a faked
--     theme); a roundup-rung slot says so. Mirrors feed_fallback_source_level's role
--     for the niche ladder (slice #7).
--  2. feed_x_theme_attribution — nullable jsonb. The theme's attribution the reel
--     credits: { "theme_summary": text, "supporting_handles": [text,...],
--     "supporting_tweet_urls": [text,...] }. Load-bearing: an X theme reel MUST credit
--     the handles/tweets it was drawn from (PRD story #29, source-reels-spec §3
--     "Attribution"). NULL on non-X-theme slots.
--
-- ── EXPAND/CONTRACT — mid-deploy safety (migration lens) ─────────────────────────
--  * Both changes are ADDITIVE: two NULLABLE columns with no default. OLD writer code
--    (assemble_niche_feed's write_daily_feed before this slice) that inserts without
--    these columns keeps working — both default to NULL, the truthful value for every
--    non-X-theme slot (which is every slot on a pre-slice-#24 feed).
--  * OLD reader code never selects these columns, so it is unaffected. No enum,
--    constraint, PK, or index is touched.
--  * No backfill needed: every historical daily_feeds row is a non-X-theme slot whose
--    honest metadata is exactly (NULL rung, NULL attribution) — the column defaults.
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * Both column adds are `if not exists`, so re-applying the whole file is a no-op.
--
-- DEPENDS ON: 0003 (daily_feeds). Independent of 0027's section columns and of
-- 0031's x_cluster_sweeps (this only touches 0003's daily_feeds).
-- Apply order: … → 0031 → 0032.
--
-- ⚠ forward-only (repo convention). ROLLBACK is documented at the bottom (manual).

-- ── 1. The X theme rung (the honesty stamp) ──────────────────────────────────────
alter table daily_feeds
  add column if not exists feed_x_theme_rung text;

-- ── 2. The theme attribution (handles + tweet urls the reel credits) ──────────────
alter table daily_feeds
  add column if not exists feed_x_theme_attribution jsonb;

comment on column daily_feeds.feed_x_theme_rung is
  'Which rung of the X theme ladder filled this slot: theme / second_theme / roundup. '
  'NULL on every non-X-theme slot (news floor, interest, source) — a news-floor X slot '
  'stays real news, never a faked theme (slice #24).';
comment on column daily_feeds.feed_x_theme_attribution is
  'The X theme attribution the reel credits: {theme_summary, supporting_handles, '
  'supporting_tweet_urls}. NULL on non-X-theme slots (slice #24).';

-- RLS: unchanged. The 0003 daily_feeds_owner_select policy is row-level and already
-- pins every row (including the new columns) to the authed user; the service-role
-- pipeline writer bypasses RLS as before. No new policy needed.

-- Verification (run after apply):
--   select column_name, is_nullable, data_type
--   from information_schema.columns
--   where table_name = 'daily_feeds'
--     and column_name in ('feed_x_theme_rung','feed_x_theme_attribution');
--
-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ───────────────────────
--   alter table daily_feeds drop column if exists feed_x_theme_attribution;
--   alter table daily_feeds drop column if exists feed_x_theme_rung;
