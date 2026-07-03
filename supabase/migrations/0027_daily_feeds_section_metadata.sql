-- Migration 0027 — daily_feeds section metadata (honest fallback-ladder assembly)
--
-- Phase FSR (interview onboarding revamp), slice #7 (fallback-ladder feed assembly).
-- Source of truth: plans/prd.md Technical Foundation Decisions #6/#7 (the honesty
-- decision — silent substitution is the rejected failure mode). Produced by
-- agents/pipeline/feed_assembly.assemble_niche_feed; consumed downstream by slice #8
-- (section rendering) via src/lib/feed/supabaseFeed.ts.
--
-- ── WHAT THIS ADDS (3 columns on daily_feeds) ────────────────────────────────────
--  1. feed_section_label — nullable user-vocabulary section header (e.g. "IPL"), the
--     reserved "Beyond your bubble" label on a beyond-bubble slot, or NULL for a
--     legacy/coarse (roots-only) or followed-source slot. Drives the feed section header.
--  2. feed_section_interest_id — nullable FK to interests(interest_id): the interest node
--     the SECTION is named for (the followed leaf). Distinct from feed_matched_interest_id,
--     which is the node a slot was actually FILLED from — they differ exactly when the
--     ladder climbed (section = IPL leaf, matched = cricket parent). NULL on coarse /
--     beyond-bubble / source slots. `on delete set null` keeps the row if the node is
--     pruned (mirrors feed_matched_interest_id's existing on-delete behaviour, 0003).
--  3. feed_fallback_source_level — NOT NULL default 0: how far the fill climbed the
--     ladder to reach this story for its section (0 = direct/leaf fill, 1 = one level up
--     / parent, 2 = grandparent). THIS is the honesty stamp: a value > 0 means the UI
--     must say so ("Nothing new in IPL today — here's cricket"). 0 on direct / source /
--     beyond-bubble slots.
--
-- ── EXPAND/CONTRACT — mid-deploy safety (migration lens) ─────────────────────────
--  * All three changes are ADDITIVE: two NULLABLE columns with no default and one
--    NOT-NULL column WITH a default (0). OLD writer code (assemble_user_feed's
--    write_daily_feed before this slice) that inserts without these columns keeps
--    working — the two nullable columns default to NULL and feed_fallback_source_level
--    defaults to 0 (== "direct fill", the truthful value for the pre-revamp coarse path).
--  * OLD reader code (src/lib/feed/supabaseFeed.ts before slice #8) never selects these
--    columns, so it is unaffected. No enum, constraint, PK, or index is touched.
--  * No backfill needed: existing daily_feeds rows are coarse/source slots whose honest
--    metadata is exactly (NULL label, NULL section interest, level 0) — the column
--    defaults. So every historical row reads correctly with zero data migration.
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * All three column adds are `if not exists`, so re-applying the whole file is a no-op.
--
-- DEPENDS ON: 0003 (daily_feeds + feed_matched_interest_id), 0003 (interests).
-- Apply order: … → 0026 → 0027. (0022/0024 are deliberately unapplied to prod; this
-- migration does NOT depend on them — it only touches 0003's daily_feeds + interests.)
--
-- ⚠ forward-only (repo convention). ROLLBACK is documented at the bottom (manual).

-- ── 1. Section label (user vocabulary / "Beyond your bubble" / NULL) ──────────────
alter table daily_feeds
  add column if not exists feed_section_label text;

-- ── 2. Section interest node (the followed leaf the section is named for) ──────────
alter table daily_feeds
  add column if not exists feed_section_interest_id uuid
    references interests (interest_id) on delete set null;

-- ── 3. Fallback climb level (the honesty stamp) ──────────────────────────────────
alter table daily_feeds
  add column if not exists feed_fallback_source_level smallint not null default 0;

comment on column daily_feeds.feed_section_label is
  'User-vocabulary section header (e.g. "IPL"), the reserved "Beyond your bubble" '
  'label, or NULL for a coarse/roots-only or followed-source slot (slice #7).';
comment on column daily_feeds.feed_section_interest_id is
  'The interest node the SECTION is named for (the followed leaf). Differs from '
  'feed_matched_interest_id when the ladder climbed. NULL on coarse/beyond/source slots.';
comment on column daily_feeds.feed_fallback_source_level is
  'How far the fill climbed the ladder for this section: 0 direct/leaf, 1 parent, '
  '2 grandparent. > 0 means the UI must label the substitution honestly (slice #7).';

-- RLS: unchanged. The 0003 daily_feeds_owner_select policy is row-level and already
-- pins every row (including the new columns) to the authed user; the service-role
-- pipeline writer bypasses RLS as before. No new policy needed.

-- Verification (run after apply — see the acceptance-criteria check):
--   select column_name, is_nullable, data_type, column_default
--   from information_schema.columns
--   where table_name = 'daily_feeds'
--     and column_name in
--       ('feed_section_label','feed_section_interest_id','feed_fallback_source_level');
--
-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ───────────────────────
--   alter table daily_feeds drop column if exists feed_fallback_source_level;
--   alter table daily_feeds drop column if exists feed_section_interest_id;
--   alter table daily_feeds drop column if exists feed_section_label;
