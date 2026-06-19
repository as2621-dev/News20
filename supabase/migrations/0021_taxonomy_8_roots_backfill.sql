-- Migration 0021 — Taxonomy 8-roots: segments rows + story_segment_slug backfill
--
-- Phase SP3 (taxonomy unification), Sub-phase 3 — the FOLLOW-UP to 0020. This file
-- REFERENCES the enum values 0020 added, so it MUST run in a separate transaction
-- (after 0020 commits) — see 0020's header for the ADD-VALUE/txn gotcha. Apply
-- order: 0020 → 0021.
--
-- Source of truth for labels + accents: src/lib/feedBuckets.ts `DESIGN_BUCKETS`
-- (the onboarding-chip palette) — the reel chip MUST render the same label + color
-- as the onboarding chip for each root (the phase's core "everything matches" goal).
--
-- ── IDEMPOTENT throughout ──────────────────────────────────────────────────────
--  * The segments upsert uses ON CONFLICT DO UPDATE → re-running re-writes the same
--    label/accent, a no-op.
--  * The story backfill UPDATE has a WHERE guard so once every row is on a new root,
--    a re-run matches zero rows. Mapping is deterministic (keyed on the row's
--    current legacy segment), so re-running never changes a settled row.

-- ── 1. segments rows for the 8 picker roots (label + accent == DESIGN_BUCKETS) ──
-- The `stories.story_segment_slug` FK references `segments(segment_slug)`, so a row
-- must exist for every root BEFORE the backfill can point stories at it. Labels and
-- accent_hex are verbatim from src/lib/feedBuckets.ts DESIGN_BUCKETS so the reel
-- chip's label+color EQUAL the onboarding chip. Upsert (idempotent): inserts the
-- missing roots, and realigns any pre-existing row (e.g. geopolitics/tech/sport) to
-- the canonical DESIGN_BUCKETS label/accent. sort_order mirrors DESIGN_BUCKETS order.
insert into segments (segment_slug, segment_label, segment_accent_hex, segment_sort_order) values
  ('ai',          'AI',          '#3B82F6', 0),
  ('geopolitics', 'Geopolitics', '#EF4444', 1),
  ('business',    'Business',    '#22C55E', 2),
  ('environment', 'Environment', '#34D399', 3),
  ('politics',    'Politics',    '#A78BFA', 4),
  ('tech',        'Tech',        '#22D3EE', 5),
  ('sport',       'Sport',       '#F59E0B', 6),
  ('arts',        'Arts',        '#E8B7BC', 7)
on conflict (segment_slug) do update
  set segment_label      = excluded.segment_label,
      segment_accent_hex = excluded.segment_accent_hex,
      segment_sort_order = excluded.segment_sort_order;

-- ── 2. Backfill stories.story_segment_slug onto the new roots ──────────────────
-- Deterministic legacy→root map (owner decision 2026-06-18):
--   markets  → business   (old markets fold collapses into business)
--   wildcard → arts        (old culture/long-tail accent → arts)
-- Roots that keep their own name (geopolitics, tech, sport) need no move — they are
-- already valid 8-root values. No existing story carries a signal that splits into
-- ai / politics / environment (story_detail_category only discriminates the legacy
-- buckets world/markets/tech/sport/culture, which align 1:1 with the segment slug),
-- so the deterministic map on the current segment fully covers all rows. The WHERE
-- guard makes each UPDATE a no-op once applied (idempotent + re-runnable).
update stories set story_segment_slug = 'business' where story_segment_slug = 'markets';
update stories set story_segment_slug = 'arts'     where story_segment_slug = 'wildcard';

-- Verification (run after apply — see the execution report):
--   select count(*) from stories
--   where story_segment_slug is null
--      or story_segment_slug::text not in
--         ('ai','geopolitics','business','environment','politics','tech','sport','arts');
--   -- expected: 0
