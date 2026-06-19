-- Migration 0020 — Unify the taxonomy on the 8 picker roots (additive enum values)
--
-- Phase SP3 (taxonomy unification), Sub-phase 3. Source of truth:
-- plans/phase-sp3-taxonomy-unification.md + agents/pipeline/categories.py
-- (Python twin) + src/lib/feedBuckets.ts DESIGN_BUCKETS (TS twin).
--
-- Onboarding, "Build your 30", and the reel chip all draw the SAME canonical
-- category set = the 8 onboarding picker roots + the 2 source axes:
--     ai · geopolitics · business · environment · politics · tech · sport · arts
--     · youtube · x
-- The earlier folds (ai→tech_science, geopolitics/politics/environment→world_politics,
-- markets→business, arts→culture) are RETIRED. There is no cross-fold anymore.
--
-- ── WHY THIS FILE IS ENUM-VALUES ONLY (the ADD-VALUE / transaction gotcha) ──────
-- PostgreSQL forbids USING a freshly-added enum value in the SAME transaction that
-- added it (the value isn't visible until commit). `supabase db push` / `psql -f`
-- wrap a single file in one transaction. So the additive `ALTER TYPE … ADD VALUE`
-- statements live HERE (0020), committed on their own; the dependent work that
-- REFERENCES the new values — the `segments` rows + the `stories.story_segment_slug`
-- backfill — lives in the FOLLOW-UP file 0021_taxonomy_8_roots_backfill.sql, which
-- runs after this commit. This mirrors the same split 0014/0015 used. Apply order:
-- 0020 (this) → 0021 (backfill).
--
-- ── ADDITIVE / forward-only · IDEMPOTENT · old values RETAINED ──────────────────
-- Every statement is `ADD VALUE IF NOT EXISTS`, so re-applying is a pure no-op.
-- Postgres CANNOT drop an enum value cheaply; the old folded values
-- (`world_politics, tech_science, culture, markets, wildcard, breaking, podcasts`)
-- are LEFT IN PLACE, unused, for reversibility — nothing writes or reads them after
-- SP3, exactly as 0017 retained `breaking`. No DROP, no enum-swap.

-- ── feed_category enum += the 8 picker roots ──────────────────────────────────
-- Already present from earlier migrations: youtube, x, sport. Adding the rest of
-- the 8 topic roots. (markets/culture/world_politics/tech_science/breaking/podcasts
-- remain as retained-unused legacy values.)
alter type feed_category add value if not exists 'ai';
alter type feed_category add value if not exists 'geopolitics';
alter type feed_category add value if not exists 'business';
alter type feed_category add value if not exists 'environment';
alter type feed_category add value if not exists 'politics';
alter type feed_category add value if not exists 'tech';
alter type feed_category add value if not exists 'arts';

-- ── segment_slug enum (the reel chip's per-story key) += the new roots ─────────
-- Already present from 0001: geopolitics, tech, sport (and legacy markets, wildcard).
-- Adding ai, business, environment, politics, arts so a story can carry any of the
-- 8 roots as its `story_segment_slug`. markets + wildcard stay as retained-unused
-- legacy values (the backfill in 0021 migrates existing rows off them).
alter type segment_slug add value if not exists 'ai';
alter type segment_slug add value if not exists 'business';
alter type segment_slug add value if not exists 'environment';
alter type segment_slug add value if not exists 'politics';
alter type segment_slug add value if not exists 'arts';
