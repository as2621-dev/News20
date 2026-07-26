-- Migration 0034 — stories.story_resolved_category (Slice #73)
--
-- Source of truth: GitHub issue #73 ("persist the resolved category on `stories` so
-- on-demand assembly stops re-deriving it"), recorded as AC #4 of #72 and deliberately
-- not built there.
--
-- WHY: the batch resolves ONE category verdict per story (`compute_category_verdicts`,
-- issue #70) from tags PLUS the theme (aboutness) side-channel, and that verdict drives
-- the founder chip, the produce cap bucket and the persisted `story_segment_slug`. The
-- worker's on-demand assembly (`_load_ready_story_pool`, behind POST /feed/assemble-for-user
-- and /feed/assemble-mine) rebuilds `CanonicalStory` objects from this table WITHOUT
-- `canonical_themes`, so it can never see that side-channel: a story with no
-- taxonomy-resolvable tag falls to the `arts` default instead of its aboutness, and an
-- equal-depth cross-root tie degrades to alphabetical slug order. The same story could
-- therefore bucket differently on the two paths. This column makes the batch's verdict
-- DURABLE so the read path consumes it instead of re-deriving it (the "resolve once,
-- consume — don't re-derive" doctrine, docs/solutions/architecture-patterns/
-- resolve-once-consume-dont-re-derive-plus-total-sort-key.md).
--
-- ⚠ MIGRATION NUMBER: ships as 0034 — the next free number after the latest on disk,
-- 0033_user_source_clusters.sql. Depends only on 0001's `stories` table and the
-- `feed_category` enum (0008, extended by 0010 + 0020), so it applies cleanly on top.
--
-- ⚠ APPLY BEFORE DEPLOY. The worker's ready-pool loader SELECTs this column. Redeploying
-- the worker before this migration is applied breaks POST /feed/assemble-for-user and
-- /feed/assemble-mine with a PostgREST "column does not exist" error. Ordering is:
--   1. apply this migration to prod
--   2. THEN deploy the worker
--
-- ⚠ ADDITIVE / forward-only — ONE nullable column, no index, no constraint, no backfill.
-- NO DROPs, no destructive ALTERs, no rewrite of existing rows (a nullable add with no
-- default does not rewrite the table). Reversible by
--   `alter table stories drop column story_resolved_category;`
-- which loses only the verdicts written after the apply.
--
-- NULL IS THE SAFE DEFAULT: every row that predates this migration stays NULL, and a NULL
-- verdict means the read path classifies that story EXACTLY as it does today (the loader
-- only builds an override entry for a non-NULL value). A backfill of historical rows is
-- therefore optional and is left as a follow-up.
--
-- RLS: none added — `stories` already carries its policies from 0002 (public-read
-- content); a new column inherits the table's existing tier.

-- ── stories.story_resolved_category (the batch's resolve-once verdict) ────────
-- Nullable `feed_category` — the SAME enum `user_feed_allocation.allocation_category`
-- uses, so a verdict is storable verbatim and a typo is a Postgres error, not a silent
-- miss. Deliberately NOT `not null` and deliberately NOT defaulted: the absence of a
-- verdict has to remain distinguishable from a real one.
alter table stories
  add column if not exists story_resolved_category feed_category;

comment on column stories.story_resolved_category is
  'The batch resolve-once category verdict for this story (compute_category_verdicts, '
  'issue #70), persisted on the same seam as story_segment_slug so the worker''s '
  'on-demand feed assembly consumes it instead of re-deriving a category without the '
  'theme side-channel (issue #73). NULL = no verdict recorded (pre-0034 rows, or a '
  'direct caller outside the batch) — the read path then classifies from tags exactly '
  'as it did before this column existed.';
