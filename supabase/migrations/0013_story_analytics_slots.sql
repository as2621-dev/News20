-- Migration 0013 — story_analytics 1:1 → 1:N (category-specific Detail panels)
--
-- Source of truth: plans/write-a-detailed-plan-squishy-bubble.md (Phase 1).
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0004 (which created
-- story_analytics with analytic_story_id UNIQUE = strictly 1:1). The
-- category-specific Detail page needs up to THREE ordered analytic panels per
-- story (e.g. Markets = Market Impact + By the Numbers; sources = 3 panels), so
-- the 1:1 constraint is relaxed to a per-slot composite unique.
--
-- WHY drop-then-recompose rather than a destructive table rebuild: the only
-- change is the uniqueness grain (per-story → per-(story, slot)). Existing rows
-- keep working — they all land at analytic_slot_index 0 (the DEFAULT below), so
-- a pre-migration story still resolves to a single slot-0 panel.

-- ── Relax the 1:1 uniqueness ─────────────────────────────────────────────────
-- 0004 declared `analytic_story_id text not null unique` INLINE, so Postgres
-- auto-named the constraint `story_analytics_analytic_story_id_key` (verified
-- against 0004 line 63 — the standard <table>_<column>_key form for an inline
-- column UNIQUE). Dropping it also drops its backing index.
alter table story_analytics
  drop constraint story_analytics_analytic_story_id_key;

-- ── Ordered slot within the story ────────────────────────────────────────────
-- 0-based panel order on the Detail page (slot 0 = the second tab, slot 1 = the
-- third tab, …). DEFAULT 0 so existing single-panel rows backfill in place.
alter table story_analytics
  add column analytic_slot_index smallint not null default 0;

-- ── New uniqueness grain: one panel per (story, slot) ────────────────────────
alter table story_analytics
  add constraint uq_story_analytics_slot unique (analytic_story_id, analytic_slot_index);

-- Read path is "all panels for a story, in slot order" (fetchStoryDetail orders
-- by analytic_slot_index). The composite unique already provides a usable index,
-- but an explicit one documents the access pattern.
create index idx_story_analytics_story_slot
  on story_analytics (analytic_story_id, analytic_slot_index);
