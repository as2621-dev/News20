-- Migration 0023 — Mint the 8 depth-0 ROOT interest nodes + re-parent picker leaves
--
-- Phase FSR-M2R (root interest nodes). Source of truth for the 8 roots:
-- agents/pipeline/categories.py::TOPIC_CATEGORIES =
--     ai · geopolitics · business · environment · politics · tech · sport · arts
-- and its category_for_slug() — the screen category of any interest slug is fixed
-- by the slug's ROOT segment (the depth-0 part before the first '.').
--
-- ── WHY THIS FILE EXISTS ────────────────────────────────────────────────────────
-- M2 (theme→category tagging) is blocked: ranking.py::assign_category DROPS any
-- story tag whose interest_id is not a known interest node, so a theme-derived
-- depth-0 ROOT tag silently collapses to DEFAULT_CATEGORY unless a depth-0 interest
-- row exists for that root. Today only 3 of the 8 roots exist as interest rows
-- (business, tech, sport — supabase/seed/interests.sql). The other 5 (ai,
-- geopolitics, environment, politics, arts) live only in the `segments` table and
-- in the slug-namespacing of depth-1 picker leaves (interests_picker_topics.sql),
-- whose leaves are currently parented to LEGACY depth-0 roots (ai.* under `tech`,
-- geopolitics.*/politics.* under `world`, environment.* under `climate`, arts.*
-- under `entertainment`). This migration mints the missing roots and re-homes the
-- leaves so each leaf's slug-root equals its actual parent interest's slug.
--
-- ── ADDITIVE · IDEMPOTENT · NON-DESTRUCTIVE ─────────────────────────────────────
--  * MINT roots with `on conflict (interest_slug) do nothing` → the 3 existing
--    roots (business/tech/sport) are RECONCILED, not duplicated or overwritten.
--  * RE-PARENT only flips `parent_interest_id` — it never changes interest_id,
--    interest_slug, or depth_level, so every user_interest_profile / story_interests
--    FK (which references interest_id, not the parent pointer) stays intact.
--  * The legacy depth-0 rows (world, climate, entertainment, health, lifestyle,
--    crypto, science) are LEFT IN PLACE (no DROP, no rename) — additive only. After
--    re-parenting they simply have no children among the 8-root picker leaves.
--  * Re-parent has a `where parent <> root` guard → a re-apply matches zero rows
--    (pure no-op). The mapping is keyed on the slug's root segment, fully
--    deterministic, so it never moves a settled row.
--
-- DEPENDS ON: 0003 (interests table + ck_interest_depth) and the seed rows from
-- supabase/seed/interests.sql + interests_picker_topics.sql. Safe to apply before
-- the seed runs too (then it only mints roots; re-parent matches nothing yet) — but
-- in practice the seed lands first.
--
-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ──────────────────────
-- This migration is reversible by data, not by drop:
--   1. Re-parent the leaves back to their legacy roots (inverse of section 2):
--        update interests set parent_interest_id =
--          (select interest_id from interests where interest_slug = 'tech')
--        where split_part(interest_slug,'.',1) = 'ai' and depth_level = 1;
--        -- geopolitics.*/politics.* -> 'world'; environment.* -> 'climate';
--        -- arts.* -> 'entertainment'.  (business/tech/sport leaves never moved.)
--   2. Delete the 5 newly-minted roots IFF they have no remaining children:
--        delete from interests
--        where interest_slug in ('ai','geopolitics','environment','politics','arts')
--          and depth_level = 0
--          and not exists (select 1 from interests c
--                          where c.parent_interest_id = interests.interest_id);
--      (Do NOT delete business/tech/sport — they pre-existed this migration.)
--   Down is only safe if M2-SP3 ingest tagging has NOT yet written
--   user_interest_profile / story_interests rows that reference the new root ids.

-- ── 1. MINT the 8 depth-0 root interest nodes ───────────────────────────────────
-- depth_level=0, parent NULL (satisfies ck_interest_depth). interest_segment_slug
-- ties each root to its `segments` row (0021 created all 8). Labels mirror the
-- segments labels (0021). `on conflict (interest_slug) do nothing` reconciles the 3
-- that already exist (business/tech/sport) WITHOUT touching their existing
-- label/sort/segment — they keep their Phase-1e identity; only the 5 missing roots
-- are inserted. interest_kind='taxonomy' (the table default; these are taxonomy
-- roots, same as every seeded interest).
insert into interests
  (interest_slug, interest_label, depth_level, parent_interest_id, interest_segment_slug, interest_sort_order)
values
  ('ai',          'AI',          0, null, 'ai',          1),
  ('geopolitics', 'Geopolitics', 0, null, 'geopolitics', 2),
  ('business',    'Business',    0, null, 'business',    3),
  ('environment', 'Environment', 0, null, 'environment', 4),
  ('politics',    'Politics',    0, null, 'politics',    5),
  ('tech',        'Tech',        0, null, 'tech',        6),
  ('sport',       'Sport',       0, null, 'sport',       7),
  ('arts',        'Arts',        0, null, 'arts',        8)
on conflict (interest_slug) do nothing;

-- ── 2. RE-PARENT every depth-1 picker leaf under its true ROOT ──────────────────
-- Generic, slug-driven rule == categories.category_for_slug's root-segment logic:
-- for every depth-1 interest whose slug root (split_part on '.') is one of the 8
-- roots, point its parent at THAT root's interest_id. The `where` guards make this:
--   * scoped to depth-1 leaves only — depth-2 leaves (sport.cricket.india,
--     business.equities.semis, tech.ai.llms) keep their depth-1 parent, untouched;
--   * a no-op on re-apply (parent already equals the root → `parent <> root` false);
--   * a no-op for business/tech/sport leaves already parented correctly;
--   * never touching interest_id / interest_slug / depth_level, so FKs are intact.
-- Joining `roots` to the row's own slug-root means one statement re-homes ai.* ->
-- ai, geopolitics.* -> geopolitics, environment.* -> environment, politics.* ->
-- politics, arts.* -> arts (and is inert for the rest) — no per-root branches.
update interests AS leaf
set parent_interest_id = root.interest_id
from interests AS root
where root.depth_level = 0
  and root.interest_slug = split_part(leaf.interest_slug, '.', 1)
  and root.interest_slug in
      ('ai','geopolitics','business','environment','politics','tech','sport','arts')
  and leaf.depth_level = 1
  and leaf.interest_id <> root.interest_id
  and (leaf.parent_interest_id is distinct from root.interest_id);

-- Verification (run after apply — see the execution report / phase DoD):
--   -- all 8 roots exist exactly once, depth 0, parent NULL:
--   select interest_slug, count(*) from interests
--   where depth_level = 0
--     and interest_slug in ('ai','geopolitics','business','environment',
--                           'politics','tech','sport','arts')
--   group by interest_slug;  -- expect 8 rows, each count = 1
--   -- every depth-1 picker leaf's parent slug == its own slug-root:
--   select count(*) from interests leaf join interests p
--     on p.interest_id = leaf.parent_interest_id
--   where leaf.depth_level = 1
--     and split_part(leaf.interest_slug,'.',1) in
--         ('ai','geopolitics','business','environment','politics','tech','sport','arts')
--     and p.interest_slug <> split_part(leaf.interest_slug,'.',1);  -- expect 0
