-- Seed — draft archetype catalog (Phase 5b SP2)
--
-- Source of truth: reference/archetypes.md §3 (the draft 12-archetype table with
-- 0–3 per-category weights) and §2 (the 8 pinned categories, axis C1). Seeds the
-- DRAFT 12 set so Phase 5c (the recommendation matcher) is unblocked; the set is
-- re-seedable without a schema change (open question #1 → /cmo locks the final set).
--
-- Depends on migration 0009 (`archetypes` table: archetype_slug unique,
-- archetype_label, archetype_vector jsonb). Run after migrations.
--
-- IDEMPOTENT: `on conflict (archetype_slug) do update` re-applies label + vector,
-- so re-seeding after a vector tweak is safe and converges (matches 0009's note
-- that the draft set is re-seedable without schema change). The seed files this
-- repo already ships (interests.sql / outlets.sql) use `do nothing`; this seed
-- DIVERGES to `do update` ON PURPOSE — the archetype vectors are tuning data that
-- must be overwritable in place, not append-only catalog rows (Rule 7: the more
-- recent requirement — "re-seedable without schema change" — wins).
--
-- archetype_vector is a NORMALIZED weight map over the 8 pinned category keys
-- (ai, geopolitics, business, environment, politics, tech, sport, arts), lowercase.
-- Each map is the §3 row's 0–3 weights divided by the row sum, rounded to 4
-- decimals, so every vector sums to ≈1.0 (residual ≤ 0.0002, well inside the
-- ±0.001 assertion tolerance below). balanced-generalist is flat (all = 0.125)
-- and is the no-match fallback (reference/archetypes.md §3 row 12).

-- ── Draft 12 archetypes — slug + label + normalized 8-key vector ──────────────
insert into archetypes (archetype_slug, archetype_label, archetype_vector)
values
  ('ai-frontier-tech',    'AI & Frontier Tech',
   '{"ai":0.4286,"geopolitics":0,"business":0.1429,"environment":0,"politics":0,"tech":0.4286,"sport":0,"arts":0}'::jsonb),
  ('markets-macro',       'Markets & Macro',
   '{"ai":0,"geopolitics":0.2,"business":0.6,"environment":0,"politics":0.2,"tech":0,"sport":0,"arts":0}'::jsonb),
  ('startup-operator',    'Startup Operator',
   '{"ai":0.1667,"geopolitics":0,"business":0.5,"environment":0,"politics":0,"tech":0.3333,"sport":0,"arts":0}'::jsonb),
  ('crypto-fintech',      'Crypto & Fintech',
   '{"ai":0,"geopolitics":0,"business":0.5,"environment":0,"politics":0,"tech":0.5,"sport":0,"arts":0}'::jsonb),
  ('geopolitics-world',   'Geopolitics & World',
   '{"ai":0.1429,"geopolitics":0.4286,"business":0.1429,"environment":0,"politics":0.2857,"tech":0,"sport":0,"arts":0}'::jsonb),
  ('us-politics-policy',  'US Politics & Policy',
   '{"ai":0,"geopolitics":0.25,"business":0,"environment":0,"politics":0.75,"tech":0,"sport":0,"arts":0}'::jsonb),
  ('climate-energy',      'Climate & Energy',
   '{"ai":0,"geopolitics":0.1429,"business":0.1429,"environment":0.4286,"politics":0.1429,"tech":0.1429,"sport":0,"arts":0}'::jsonb),
  ('sports-fan',          'Sports Fan',
   '{"ai":0,"geopolitics":0,"business":0,"environment":0,"politics":0,"tech":0,"sport":1.0,"arts":0}'::jsonb),
  ('arts-culture',        'Arts & Culture',
   '{"ai":0,"geopolitics":0,"business":0,"environment":0,"politics":0,"tech":0,"sport":0,"arts":1.0}'::jsonb),
  ('creator-media',       'Creator / Media',
   '{"ai":0.1667,"geopolitics":0,"business":0.1667,"environment":0,"politics":0,"tech":0.3333,"sport":0,"arts":0.3333}'::jsonb),
  ('tech-generalist',     'Tech Generalist',
   '{"ai":0.2222,"geopolitics":0.1111,"business":0.1111,"environment":0.1111,"politics":0,"tech":0.3333,"sport":0,"arts":0.1111}'::jsonb),
  ('balanced-generalist', 'Balanced Generalist',
   '{"ai":0.125,"geopolitics":0.125,"business":0.125,"environment":0.125,"politics":0.125,"tech":0.125,"sport":0.125,"arts":0.125}'::jsonb)
on conflict (archetype_slug) do update
  set archetype_label  = excluded.archetype_label,
      archetype_vector = excluded.archetype_vector;

-- ── Apply-time assertions (the DoD's "SQL assertion") ────────────────────────
-- These run only on a manual `psql`/`supabase db` apply — there is NO local DB in
-- this repo (no supabase/config.toml), so they were NOT executed at author time;
-- they are written to fail loud the moment the seed is applied against a real DB.

-- (a) Exactly 12 archetype rows exist.
do $$
declare
  v_row_count int;
begin
  select count(*) into v_row_count from archetypes;
  if v_row_count <> 12 then
    raise exception 'archetype seed assertion failed: expected 12 rows, found %', v_row_count;
  end if;
end;
$$;

-- (b) Every archetype_vector contains all 8 pinned category keys and sums to ≈1.0
--     (±0.001). Iterates each row, checks key presence, then totals the numeric
--     values across the 8 keys.
do $$
declare
  v_archetype record;
  v_key       text;
  v_sum       numeric;
  v_keys      text[] := array['ai','geopolitics','business','environment','politics','tech','sport','arts'];
begin
  for v_archetype in select archetype_slug, archetype_vector from archetypes loop
    -- key presence
    foreach v_key in array v_keys loop
      if not (v_archetype.archetype_vector ? v_key) then
        raise exception 'archetype % is missing the "%" category key', v_archetype.archetype_slug, v_key;
      end if;
    end loop;
    -- normalized sum
    v_sum := 0;
    foreach v_key in array v_keys loop
      v_sum := v_sum + (v_archetype.archetype_vector ->> v_key)::numeric;
    end loop;
    if abs(v_sum - 1.0) > 0.001 then
      raise exception 'archetype % vector sums to % (expected ≈1.0 ±0.001)', v_archetype.archetype_slug, v_sum;
    end if;
  end loop;
end;
$$;

-- (c) Cosine-similarity sanity: a heavy ai+tech probe must resolve to
--     ai-frontier-tech as the single nearest archetype, and a flat probe must
--     resolve to balanced-generalist. Cosine is computed in-SQL over the 8 keys.
do $$
declare
  v_keys           text[] := array['ai','geopolitics','business','environment','politics','tech','sport','arts'];
  v_probe_ai_tech  jsonb  := '{"ai":1,"geopolitics":0,"business":0,"environment":0,"politics":0,"tech":1,"sport":0,"arts":0}'::jsonb;
  v_probe_flat     jsonb  := '{"ai":1,"geopolitics":1,"business":1,"environment":1,"politics":1,"tech":1,"sport":1,"arts":1}'::jsonb;
  v_nearest        text;
begin
  -- nearest to the ai+tech probe
  select archetype_slug into v_nearest
  from archetypes a
  order by (
    -- dot product
    (select sum((v_probe_ai_tech ->> k)::numeric * (a.archetype_vector ->> k)::numeric) from unnest(v_keys) as k)
    / nullif(
        sqrt((select sum(((v_probe_ai_tech ->> k)::numeric)^2) from unnest(v_keys) as k))
        * sqrt((select sum(((a.archetype_vector ->> k)::numeric)^2) from unnest(v_keys) as k)),
      0)
  ) desc nulls last
  limit 1;
  if v_nearest is distinct from 'ai-frontier-tech' then
    raise exception 'cosine assertion failed: heavy ai+tech probe resolved to % (expected ai-frontier-tech)', v_nearest;
  end if;

  -- nearest to the flat probe
  select archetype_slug into v_nearest
  from archetypes a
  order by (
    (select sum((v_probe_flat ->> k)::numeric * (a.archetype_vector ->> k)::numeric) from unnest(v_keys) as k)
    / nullif(
        sqrt((select sum(((v_probe_flat ->> k)::numeric)^2) from unnest(v_keys) as k))
        * sqrt((select sum(((a.archetype_vector ->> k)::numeric)^2) from unnest(v_keys) as k)),
      0)
  ) desc nulls last
  limit 1;
  if v_nearest is distinct from 'balanced-generalist' then
    raise exception 'cosine assertion failed: flat probe resolved to % (expected balanced-generalist)', v_nearest;
  end if;
end;
$$;
