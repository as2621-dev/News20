-- Migration 0004 — Detail analytics (Phase 2c SP1)
--
-- Source of truth: reference/supabase-schema.md §1 (enums coverage_mode/analytic_kind),
-- §2 (detail_key_points, story_analytics, story_trust ALTERs, outlets.outlet_domain),
-- §6 (public-read RLS for the two new content tables) + the phase-2c design decisions.
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0001 (content tables +
-- bias_lean/segment_slug enums + outlets), 0002 (content RLS), and 0003
-- (personalization). NO DROPs, no destructive ALTERs — only new enums, tables,
-- columns, indexes, and policies (master-plan reversibility note).
--
-- Adds:
--   2 enums   — coverage_mode ('partisan'|'reach'), analytic_kind
--               ('market_impact'|'ripple'|'impact'|'stakes'|'why_it_matters')
--   2 content — detail_key_points (the 5 at-a-glance bullets),
--               story_analytics (the 1:1 segment-skinned "second analytic" tab)
--   ALTERs    — story_trust (+4 reach-mode coverage columns),
--               outlets (+ outlet_domain, so a GDELT domain resolves to a lean)
--   RLS       — public-read SELECT on detail_key_points + story_analytics
--
-- Column names transcribed VERBATIM from supabase-schema.md.

-- ── Enums (supabase-schema.md §1) ────────────────────────────────────────────
-- How the Detail "Coverage" tab is framed. partisan = L·C·R + blindspot
-- (contested stories); reach = covered-by-N + momentum + who-broke-it.
create type coverage_mode as enum ('partisan', 'reach');

-- The segment-skinned "second analytic" tab. Chosen DETERMINISTICALLY from
-- story_segment_slug (Decision #2), never by the LLM:
--   geopolitics→market_impact, markets→ripple, tech→impact,
--   sport→stakes, wildcard→why_it_matters.
create type analytic_kind as enum ('market_impact', 'ripple', 'impact', 'stakes', 'why_it_matters');

-- ── outlets ALTER (supabase-schema.md §2) ────────────────────────────────────
-- GDELT returns *domains* ("foxnews.com"), not outlet names, so the coverage
-- census resolves domain→lean via this column (SP2). Partial unique index so
-- many existing rows can stay NULL while seeded domains stay unique.
alter table outlets add column outlet_domain text;
create unique index uq_outlets_domain on outlets (outlet_domain) where outlet_domain is not null;

-- ── detail_key_points (supabase-schema.md §2) ────────────────────────────────
-- The 5 at-a-glance bullets shown above "Read the full article". Distinct from
-- detail_chunks (the long-form body behind the button). 0-based display order,
-- one row per bullet, unique per (story, index).
create table detail_key_points (
  detail_key_point_id  uuid primary key default gen_random_uuid(),
  key_point_story_id   text not null references stories (story_id) on delete cascade,
  key_point_index      smallint not null,
  key_point_text       text not null,
  constraint uq_detail_key_point_order unique (key_point_story_id, key_point_index)
);
create index idx_detail_key_points_story on detail_key_points (key_point_story_id, key_point_index);

-- ── story_analytics (supabase-schema.md §2) ──────────────────────────────────
-- The variable middle Detail tab (Market Impact / Ripple / Impact / Stakes /
-- Why It Matters). 1:1 per story (analytic_story_id UNIQUE). analytic_kind drives
-- the tab label + accent. analytic_rows is JSONB (like caption_sentences.word_tokens):
-- a variable-length array consumed whole by the client renderer — each element is
-- validated against the AnalyticRow Pydantic model before insert (§0 types).
-- analytic_is_grounded records whether numeric row values were verified vs source.
create table story_analytics (
  story_analytic_id      uuid primary key default gen_random_uuid(),
  analytic_story_id      text not null unique references stories (story_id) on delete cascade,
  analytic_kind          analytic_kind not null,
  analytic_tab_label     text not null,
  analytic_headline      text not null,
  analytic_summary_text  text not null,
  analytic_rows          jsonb not null default '[]'::jsonb,
  analytic_is_grounded   boolean not null default false,
  analytic_created_at    timestamptz not null default now()
);

-- ── story_trust ALTER (supabase-schema.md §2) ────────────────────────────────
-- Adaptive Coverage tab. partisan mode uses the existing L/C/R counts +
-- blindspot_lean; reach mode uses coverage_outlet_count + the three new columns.
-- coverage_mode defaults 'partisan' to preserve existing rows' behaviour.
alter table story_trust
  add column coverage_mode                    coverage_mode not null default 'partisan',
  add column coverage_momentum                text,
  add column coverage_originating_outlet_name text,
  add column coverage_notable_outlet_names    text[] not null default '{}';

-- ── RLS (supabase-schema.md §6) ──────────────────────────────────────────────
-- detail_key_points + story_analytics are public-read content (SELECT using true;
-- no write policy → only the service-role key, which bypasses RLS, can write).
alter table detail_key_points enable row level security;
create policy detail_key_points_public_read on detail_key_points
  for select using (true);

alter table story_analytics enable row level security;
create policy story_analytics_public_read on story_analytics
  for select using (true);
