-- Seed — interests taxonomy (Phase 1e SP1)
--
-- Source of truth: plans/phase-1e-auth-onboarding-interest-profile.md
--   "Proposed depth-0 taxonomy" table + the four example 3-level chains.
-- Depends on migration 0003 (interests table + ck_interest_depth) and 0001
-- (segments enum/table — interest_segment_slug FKs segments.segment_slug).
--
-- IDEMPOTENT: every INSERT is `on conflict (interest_slug) do nothing`, so
-- re-running is safe. Parent links resolve by slug subselect (no fragile
-- hardcoded UUIDs) — depth-0 rows insert first, so depth-1 finds its parent,
-- then depth-2. Respects ck_interest_depth (depth 0 ⇒ parent NULL; depth > 0 ⇒
-- parent NOT NULL). segment_slug enum has only 5 values
-- (geopolitics|markets|tech|sport|wildcard); multiple interests map to one
-- accent, wildcard is the long-tail catch-all.

-- ── Depth-0 categories (~10) — parent NULL, carry interest_segment_slug accent ─
insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_segment_slug, interest_sort_order)
values
  ('world',         'Geopolitics',             0, null, 'geopolitics', 10),
  ('business',      'Business & Markets',      0, null, 'markets',     20),
  ('tech',          'Tech & Science',          0, null, 'tech',        30),
  ('sport',         'Sport',                   0, null, 'sport',       40),
  ('health',        'Health & Wellbeing',      0, null, 'wildcard',    50),
  ('entertainment', 'Entertainment & Culture', 0, null, 'wildcard',    60),
  ('climate',       'Climate & Environment',   0, null, 'geopolitics', 70),
  ('lifestyle',     'Lifestyle & Travel',      0, null, 'wildcard',    80),
  ('crypto',        'Crypto & Web3',           0, null, 'markets',     90),
  ('science',       'Space & Hard Science',    0, null, 'tech',        100)
on conflict (interest_slug) do nothing;

-- ── Depth-1 subcategories — parent resolved by parent slug subselect ─────────
-- (depth-1/2 rows leave interest_segment_slug NULL per the §3 seed example.)
insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_sort_order)
select v.interest_slug, v.interest_label, 1, p.interest_id, v.interest_sort_order
from (values
  ('sport.cricket',       'Cricket',        'sport',    10),
  ('sport.soccer',        'Soccer',         'sport',    20),
  ('business.equities',   'Equities',       'business', 10),
  ('tech.ai',             'AI',             'tech',     10)
) as v (interest_slug, interest_label, parent_slug, interest_sort_order)
join interests p on p.interest_slug = v.parent_slug
on conflict (interest_slug) do nothing;

-- ── Depth-2 leaves — parent resolved by parent slug subselect; carry a query ──
insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_search_query, interest_sort_order)
select v.interest_slug, v.interest_label, 2, p.interest_id, v.interest_search_query, v.interest_sort_order
from (values
  ('sport.cricket.india',         'India',           'sport.cricket',     'India cricket team BCCI news',              10),
  ('sport.soccer.arsenal',        'Arsenal',         'sport.soccer',      'Arsenal FC news Premier League',            10),
  ('business.equities.semis',     'Semiconductors',  'business.equities', 'semiconductor stocks NVIDIA TSMC news',     10),
  ('tech.ai.llms',                'LLMs',            'tech.ai',           'large language models OpenAI Anthropic news', 10)
) as v (interest_slug, interest_label, parent_slug, interest_search_query, interest_sort_order)
join interests p on p.interest_slug = v.parent_slug
on conflict (interest_slug) do nothing;
