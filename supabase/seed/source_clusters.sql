-- Seed — editorial source clusters (Phase FSR-M1 SP3)
--
-- Source of truth: plans/phase-fsr-m1-catalog-clusters-nodup.md SP3. Authors the
-- launch set of named CLUSTERS (a net-new editorial grouping per PRD Decision #6 —
-- NOT an archetype, NOT a persona) over the EXISTING content_sources / personalities
-- catalog from 0009. Each cluster sits in ONE of the 8 topic roots; its ordered
-- members reference REAL catalog rows resolved by their natural keys (never uuids).
--
-- Depends on migration 0022 (source_clusters / source_cluster_members) AND on the
-- 0009 catalog having been seeded (scripts/seed_catalog — content_sources keyed by
-- unique (content_source_type, external_id); personalities keyed by unique
-- display_name). Run AFTER both migrations and the catalog seed.
--
-- IDEMPOTENT (mirrors supabase/seed/archetypes.sql):
--   * source_clusters         → `on conflict (cluster_slug) do update` re-applies
--     label / category / sort order, so an editorial re-author converges in place.
--   * source_cluster_members  → `on conflict do nothing` (the migration's
--     partial-unique guards on (cluster_id, source_id) / (cluster_id,
--     personality_id) make a re-seed a no-op, not a duplicate).
--
-- MEMBER RESOLUTION (no hardcoded uuids — Rule 3/11): every member row is a SELECT
-- that resolves the cluster_id by cluster_slug and the followable by its natural
-- key, guarded by `where exists (...)`. The guard makes a MISSING catalog row a
-- no-op insert rather than a NULL-FK failure — so the seed is robust to a partial
-- catalog (a category whose real rows were never seeded simply gets zero members,
-- which is exactly why the offline structural test does not require live rows).
--
-- NOTE (LIVE-E2E residual): this seed targets PLAUSIBLE real keys lifted from the
-- curated catalog data (scripts/seed_catalog/data/*.json) — x_account external_ids
-- are the bare handles and personality display_names are verbatim. youtube_channel
-- external_ids are RESOLVED channel ids (UC…) not present at author time, so the
-- members below deliberately use the deterministic x_account / personality axes.
-- Confirming real per-category coverage against the live DB is the named LIVE-E2E
-- residual (the #1-risk live check); a thin live catalog yields fewer members, not
-- an error.
--
-- member_sort_order starts at 0 within each cluster (the render order). The
-- migration's member_created_at has NO default, so every member sets now() to
-- mirror the catalog's created-at convention.

-- ════════════════════════════════════════════════════════════════════════════
-- CLUSTERS — slug + label + category (∈ the 8 topic roots) + sort order
-- ════════════════════════════════════════════════════════════════════════════
insert into source_clusters (cluster_slug, cluster_label, cluster_category, cluster_sort_order)
values
  -- AI
  ('ai-lab-researchers', 'Leading AI-lab researchers', 'ai', 0),
  ('ai-founders',        'AI founders',                'ai', 1),
  -- GEOPOLITICS
  ('geo-world-desks',    'World news desks',           'geopolitics', 0),
  ('geo-world-leaders',  'World leaders',              'geopolitics', 1),
  -- BUSINESS
  ('biz-markets-desks',  'Markets & macro desks',      'business', 0),
  ('biz-investors',      'Investors & central bankers','business', 1),
  -- ENVIRONMENT
  ('env-climate-voices', 'Climate voices',             'environment', 0),
  ('env-climate-orgs',   'Climate institutions',       'environment', 1),
  -- POLITICS
  ('pol-us-desks',       'US politics desks',          'politics', 0),
  ('pol-us-anchors',     'US political anchors',       'politics', 1),
  -- TECH
  ('tech-leaders',       'Big-tech leaders',           'tech', 0),
  ('tech-reviewers',     'Tech reviewers & media',     'tech', 1),
  -- SPORT
  ('sport-footballers',  'Football stars',             'sport', 0),
  ('sport-leagues',      'Leagues & competitions',     'sport', 1),
  -- ARTS
  ('arts-figures',       'Film & music figures',       'arts', 0),
  ('arts-institutions',  'Arts institutions & awards', 'arts', 1)
on conflict (cluster_slug) do update
  set cluster_label    = excluded.cluster_label,
      cluster_category = excluded.cluster_category,
      cluster_sort_order = excluded.cluster_sort_order;

-- All 8 topic roots carry ≥2 clusters at seed; no THIN CATALOG gaps.
-- (If a future re-author can only supply 1 cluster for a root, add a line:
--  `-- THIN CATALOG: <category> — only 1 cluster at seed` so the gap is loud and
--  the structural test still accounts for that root.)

-- ════════════════════════════════════════════════════════════════════════════
-- MEMBERS — ordered, resolved by natural key, guarded by `where exists`
-- ════════════════════════════════════════════════════════════════════════════
-- Helper shape (repeated below):
--   insert into source_cluster_members (cluster_id, <ref>, member_sort_order, member_created_at)
--   select (select cluster_id from source_clusters where cluster_slug = '<slug>'),
--          (select <id> from <catalog> where <natural key>),
--          <ord>, now()
--   where exists (select 1 from <catalog> where <natural key>)
--   on conflict do nothing;

-- ── AI: ai-lab-researchers (personalities) ───────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'ai-lab-researchers'),
       (select personality_id from personalities where display_name = 'Andrej Karpathy'),
       0, now()
where exists (select 1 from personalities where display_name = 'Andrej Karpathy')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'ai-lab-researchers'),
       (select personality_id from personalities where display_name = 'Demis Hassabis'),
       1, now()
where exists (select 1 from personalities where display_name = 'Demis Hassabis')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'ai-lab-researchers'),
       (select personality_id from personalities where display_name = 'Yann LeCun'),
       2, now()
where exists (select 1 from personalities where display_name = 'Yann LeCun')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'ai-lab-researchers'),
       (select personality_id from personalities where display_name = 'Geoffrey Hinton'),
       3, now()
where exists (select 1 from personalities where display_name = 'Geoffrey Hinton')
on conflict do nothing;

-- ── AI: ai-founders (personalities) ──────────────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'ai-founders'),
       (select personality_id from personalities where display_name = 'Sam Altman'),
       0, now()
where exists (select 1 from personalities where display_name = 'Sam Altman')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'ai-founders'),
       (select personality_id from personalities where display_name = 'Fei-Fei Li'),
       1, now()
where exists (select 1 from personalities where display_name = 'Fei-Fei Li')
on conflict do nothing;

-- ── GEOPOLITICS: geo-world-desks (x_account) ─────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'geo-world-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'Reuters'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'Reuters')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'geo-world-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'AP'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'AP')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'geo-world-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'BBCWorld'),
       2, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'BBCWorld')
on conflict do nothing;

-- ── GEOPOLITICS: geo-world-leaders (personalities) ───────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'geo-world-leaders'),
       (select personality_id from personalities where display_name = 'Emmanuel Macron'),
       0, now()
where exists (select 1 from personalities where display_name = 'Emmanuel Macron')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'geo-world-leaders'),
       (select personality_id from personalities where display_name = 'Olaf Scholz'),
       1, now()
where exists (select 1 from personalities where display_name = 'Olaf Scholz')
on conflict do nothing;

-- ── BUSINESS: biz-markets-desks (x_account) ──────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'biz-markets-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'Bloomberg'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'Bloomberg')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'biz-markets-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'WSJ'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'WSJ')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'biz-markets-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'CNBC'),
       2, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'CNBC')
on conflict do nothing;

-- ── BUSINESS: biz-investors (personalities) ──────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'biz-investors'),
       (select personality_id from personalities where display_name = 'Warren Buffett'),
       0, now()
where exists (select 1 from personalities where display_name = 'Warren Buffett')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'biz-investors'),
       (select personality_id from personalities where display_name = 'Ray Dalio'),
       1, now()
where exists (select 1 from personalities where display_name = 'Ray Dalio')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'biz-investors'),
       (select personality_id from personalities where display_name = 'Jerome Powell'),
       2, now()
where exists (select 1 from personalities where display_name = 'Jerome Powell')
on conflict do nothing;

-- ── ENVIRONMENT: env-climate-voices (personalities) ──────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'env-climate-voices'),
       (select personality_id from personalities where display_name = 'Greta Thunberg'),
       0, now()
where exists (select 1 from personalities where display_name = 'Greta Thunberg')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'env-climate-voices'),
       (select personality_id from personalities where display_name = 'Katharine Hayhoe'),
       1, now()
where exists (select 1 from personalities where display_name = 'Katharine Hayhoe')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'env-climate-voices'),
       (select personality_id from personalities where display_name = 'Michael E. Mann'),
       2, now()
where exists (select 1 from personalities where display_name = 'Michael E. Mann')
on conflict do nothing;

-- ── ENVIRONMENT: env-climate-orgs (x_account) ────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'env-climate-orgs'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'IEA'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'IEA')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'env-climate-orgs'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'UNFCCC'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'UNFCCC')
on conflict do nothing;

-- ── POLITICS: pol-us-desks (x_account) ───────────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'pol-us-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'politico'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'politico')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'pol-us-desks'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'nytimes'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'nytimes')
on conflict do nothing;

-- ── POLITICS: pol-us-anchors (personalities) ─────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'pol-us-anchors'),
       (select personality_id from personalities where display_name = 'Jake Tapper'),
       0, now()
where exists (select 1 from personalities where display_name = 'Jake Tapper')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'pol-us-anchors'),
       (select personality_id from personalities where display_name = 'Anderson Cooper'),
       1, now()
where exists (select 1 from personalities where display_name = 'Anderson Cooper')
on conflict do nothing;

-- ── TECH: tech-leaders (personalities) ───────────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'tech-leaders'),
       (select personality_id from personalities where display_name = 'Tim Cook'),
       0, now()
where exists (select 1 from personalities where display_name = 'Tim Cook')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'tech-leaders'),
       (select personality_id from personalities where display_name = 'Satya Nadella'),
       1, now()
where exists (select 1 from personalities where display_name = 'Satya Nadella')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'tech-leaders'),
       (select personality_id from personalities where display_name = 'Sundar Pichai'),
       2, now()
where exists (select 1 from personalities where display_name = 'Sundar Pichai')
on conflict do nothing;

-- ── TECH: tech-reviewers (x_account) ─────────────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'tech-reviewers'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'MKBHD'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'MKBHD')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'tech-reviewers'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'verge'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'verge')
on conflict do nothing;

-- ── SPORT: sport-footballers (personalities) ─────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'sport-footballers'),
       (select personality_id from personalities where display_name = 'Lionel Messi'),
       0, now()
where exists (select 1 from personalities where display_name = 'Lionel Messi')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'sport-footballers'),
       (select personality_id from personalities where display_name = 'Cristiano Ronaldo'),
       1, now()
where exists (select 1 from personalities where display_name = 'Cristiano Ronaldo')
on conflict do nothing;

-- ── SPORT: sport-leagues (x_account) ─────────────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'sport-leagues'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'NBA'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'NBA')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'sport-leagues'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'premierleague'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'premierleague')
on conflict do nothing;

-- ── ARTS: arts-figures (personalities) ───────────────────────────────────────
insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'arts-figures'),
       (select personality_id from personalities where display_name = 'Steven Spielberg'),
       0, now()
where exists (select 1 from personalities where display_name = 'Steven Spielberg')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'arts-figures'),
       (select personality_id from personalities where display_name = 'Taylor Swift'),
       1, now()
where exists (select 1 from personalities where display_name = 'Taylor Swift')
on conflict do nothing;

insert into source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'arts-figures'),
       (select personality_id from personalities where display_name = 'Meryl Streep'),
       2, now()
where exists (select 1 from personalities where display_name = 'Meryl Streep')
on conflict do nothing;

-- ── ARTS: arts-institutions (x_account) ──────────────────────────────────────
insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'arts-institutions'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'TheAcademy'),
       0, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'TheAcademy')
on conflict do nothing;

insert into source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
select (select cluster_id from source_clusters where cluster_slug = 'arts-institutions'),
       (select source_id from content_sources where content_source_type = 'x_account' and external_id = 'Variety'),
       1, now()
where exists (select 1 from content_sources where content_source_type = 'x_account' and external_id = 'Variety')
on conflict do nothing;

-- ════════════════════════════════════════════════════════════════════════════
-- Apply-time coverage report (SOFT — mirrors archetypes.sql's apply-time block but
-- raises NOTICE, never EXCEPTION: real per-category coverage is the LIVE-E2E
-- residual, so a thin live catalog must NOT abort the seed). Reports clusters per
-- category so a gap is visible in the apply log.
-- ════════════════════════════════════════════════════════════════════════════
do $$
declare
  v_cat   text;
  v_count int;
  v_roots text[] := array['ai','geopolitics','business','environment','politics','tech','sport','arts'];
begin
  foreach v_cat in array v_roots loop
    select count(*) into v_count from source_clusters where cluster_category = v_cat;
    raise notice 'source_clusters coverage: % → % cluster(s)', v_cat, v_count;
    if v_count = 0 then
      raise notice 'source_clusters WARNING: category % has ZERO clusters at apply time', v_cat;
    end if;
  end loop;
end;
$$;
