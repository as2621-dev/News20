-- Migration 0024 — Collapse user_interest_profile pointers to their depth-0 ROOTS
--
-- Phase FSR-M5 (top-level-category onboarding + interest collapse), Sub-phase 3.
--
-- ⚠ IRREVERSIBLE DATA MIGRATION. It repoints every deep user_interest_profile row to
-- its depth-0 root interest and DELETES the rows that dedup away on collision. There
-- is no automatic down (forward-only repo); the recovery note is at the foot of this
-- file. Verified offline against ephemeral Postgres 16 before any live apply.
--
-- ── WHY THIS FILE EXISTS ────────────────────────────────────────────────────────
-- M5 makes the onboarding interest picker ROOTS-ONLY (TopicTree renders only the 8
-- depth-0 category roots), so a fresh onboarding can only ever create a ROOT-level
-- user_interest_profile row. This migration folds the HISTORICAL deep rows (created by
-- the old recursive picker — sport.soccer.epl, business.equities.semis, …) up to the
-- same depth-0 roots, so the stored interest graph matches what the picker now emits.
-- After this runs, every user_interest_profile.profile_interest_id references a
-- depth-0 interest (the 8 roots minted by 0023_root_interest_nodes.sql).
--
-- ── THE TWIN INVARIANT (Rule 7) ─────────────────────────────────────────────────
-- The deep→root rule here is the SQL TWIN of agents/pipeline/categories.py
-- ::collapse_profile_rows_to_roots (which itself uses category_for_slug). Both map an
-- interest slug to its ROOT via the slug's first dotted segment
-- (split_part(interest_slug,'.',1)  ==  slug.split('.',1)[0]), and both dedup a
-- per-user collision by KEEPING THE HIGHER profile_weight (NOT sum, NOT average),
-- carrying that kept row's profile_source. tests/agents/pipeline/test_interest_collapse
-- .py::TestCollapseParityWithCategoryForSlug pins the Python side; this header pins the
-- SQL side to the same rule. If one changes, change BOTH.
--
-- Note on the root lookup: the depth-0 interest whose interest_slug == a leaf's root
-- segment IS that leaf's category root (0023 minted exactly one depth-0 interest per
-- topic root, interest_slug == the category key). So joining on
-- root.interest_slug = split_part(leaf_target.interest_slug,'.',1) is the SQL form of
-- category_for_slug for every slug whose root segment is one of the 8 canonical roots.
-- Legacy alias roots (world/markets/climate/…) that category_for_slug remaps are NOT
-- re-pointed here by design: their depth-0 rows already exist as valid interest nodes
-- (0023 left them in place additively) and a profile row on them is already root-level
-- (depth_level = 0), so the depth>0 guard below leaves them untouched — no orphan, no
-- FK break. (The Python transform's alias remap matters for ingest-time slug tagging,
-- not for repointing an already-depth-0 profile pointer.)
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * Every statement is guarded on "the target interest is deeper than depth-0" — once
--    every profile row points at a depth-0 interest, a re-run matches ZERO rows.
--  * The dedup deletes only the strictly-lower-weight duplicates within a post-collapse
--    (user, root) group; after one apply each group has a single row, so a re-run
--    deletes nothing.
--  * Deterministic throughout (keyed on slug root segment + weight), so it never moves
--    or drops a settled row.
--
-- DEPENDS ON: 0003 (interests + user_interest_profile + uq_user_interest) and 0023
-- (the 8 depth-0 root interest nodes — the repoint targets). Apply order: 0023 → 0024.

begin;

-- ── 1. Resolve each profile row's destination root, then DEDUP keeping max weight ──
-- collapse_target: for every profile row, the depth-0 root interest it WILL land on.
--   * If its current interest is already depth-0 → it stays on that same interest
--     (root.interest_id = cur.interest_id), so already-root rows participate in the
--     dedup grouping but are never "moved".
--   * If its current interest is deep (depth_level > 0) → the depth-0 interest whose
--     interest_slug == the current interest's slug ROOT segment.
-- We DELETE the losers FIRST (before the repoint UPDATE) so the UPDATE can never
-- violate uq_user_interest (profile_user_id, profile_interest_id): after this delete
-- there is at most one surviving row per (user, target_root_id).
with collapse_target as (
  select
    uip.user_interest_profile_id,
    uip.profile_user_id,
    uip.profile_weight,
    -- The depth-0 root interest id this row collapses onto.
    root.interest_id as target_root_interest_id
  from user_interest_profile uip
  join interests cur on cur.interest_id = uip.profile_interest_id
  join interests root
    on root.depth_level = 0
   and root.interest_slug = case
         when cur.depth_level = 0 then cur.interest_slug
         else split_part(cur.interest_slug, '.', 1)
       end
),
ranked as (
  -- Rank rows within each (user, target root): the WINNER is the highest weight; ties
  -- break deterministically on the row id so a re-run picks the same winner. The
  -- migration keeps rank 1 and deletes the rest of each group.
  select
    user_interest_profile_id,
    row_number() over (
      partition by profile_user_id, target_root_interest_id
      order by profile_weight desc, user_interest_profile_id asc
    ) as weight_rank
  from collapse_target
)
delete from user_interest_profile uip
using ranked
where ranked.user_interest_profile_id = uip.user_interest_profile_id
  and ranked.weight_rank > 1;

-- ── 2. REPOINT every surviving deep row to its depth-0 root interest ──────────────
-- Only rows whose CURRENT interest is deeper than depth-0 are moved (the depth>0
-- guard = idempotency: an already-root row matches nothing). After step 1 removed the
-- dedup losers, no survivor's move can collide on uq_user_interest. profile_weight and
-- profile_source are left exactly as the surviving (max-weight) row carried them.
update user_interest_profile uip
set profile_interest_id = root.interest_id
from interests cur, interests root
where cur.interest_id = uip.profile_interest_id
  and cur.depth_level > 0
  and root.depth_level = 0
  and root.interest_slug = split_part(cur.interest_slug, '.', 1)
  and uip.profile_interest_id <> root.interest_id;

commit;

-- Verification (run after apply — see the execution report / phase DoD):
--   -- every profile row now points at a depth-0 interest:
--   select count(*) from user_interest_profile uip
--   join interests i on i.interest_id = uip.profile_interest_id
--   where i.depth_level <> 0;                                  -- expect 0
--   -- no duplicate (user, interest) pairs remain (the unique constraint holds):
--   select profile_user_id, profile_interest_id, count(*)
--   from user_interest_profile
--   group by profile_user_id, profile_interest_id having count(*) > 1;  -- expect 0 rows
--
-- ── ROLLBACK / DOWN (none — IRREVERSIBLE) ───────────────────────────────────────
-- This migration DELETES the deduped-away rows and OVERWRITES profile_interest_id with
-- the root id; the original deep pointers and the dropped rows are not recoverable from
-- this table. There is no safe forward down. Recovery is from a pre-apply backup /
-- point-in-time restore of user_interest_profile ONLY. (The deep `interests` taxonomy
-- itself is untouched — it still backs DepthMatch — so a restore re-links cleanly.)
