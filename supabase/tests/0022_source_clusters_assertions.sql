-- DoD assertions for migration 0022 (source clusters + cluster members) — Phase FSR-M1 SP1
--
-- WHY THIS EXISTS: the build environment has NO database (no Docker/psql/local
-- Postgres), so SP1 cannot apply 0022 at runtime. This script is the artifact the
-- OWNER runs at apply-time (after `supabase db push` of 0022, against a DB that
-- already holds 0009's content_sources/personalities with at least one curated row
-- each) to prove the Definition-of-Done checks. It FAILS LOUD (Rule 12): every
-- check `raise exception` on violation, so a non-zero exit means a real DoD miss.
--
-- HOW TO RUN (owner, against the DB holding 0001–0022 + a populated catalog):
--   psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f supabase/tests/0022_source_clusters_assertions.sql
--
-- The DoD checks (phase-fsr-m1 SP1):
--   1. source_clusters + source_cluster_members exist; the cluster_category 8-value
--      CHECK exists.
--   2. A trial insert of a cluster + one source_id member (borrowing a real
--      content_sources row) + one personality_id member (borrowing a real
--      personalities row) resolves all FKs, then ROLLBACK.
--   3. The exactly-one-of XOR CHECK REJECTS a member with both refs null AND a
--      member with both refs set.
--   4. anon SELECT on source_clusters succeeds (public-read).
--
-- All mutations run inside transactions that ROLL BACK / clean up — no residue.

\set ON_ERROR_STOP on

-- ── Check 1 — tables + cluster_category CHECK exist ──────────────────────────
do $$
begin
  if to_regclass('public.source_clusters') is null then
    raise exception 'DoD-1 FAILED: table source_clusters does not exist.';
  end if;
  if to_regclass('public.source_cluster_members') is null then
    raise exception 'DoD-1 FAILED: table source_cluster_members does not exist.';
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.source_clusters'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) ilike '%cluster_category%'
  ) then
    raise exception 'DoD-1 FAILED: cluster_category CHECK constraint not found on source_clusters.';
  end if;
  raise notice 'DoD-1 PASSED: both cluster tables + cluster_category CHECK present.';
end $$;

-- ── Check 2 — FK resolution: cluster + source member + personality member ────
-- Borrows a real content_sources row and a real personalities row so all FKs
-- resolve against actual catalog data, then ROLLBACK so nothing persists.
do $$
declare
  v_cluster_id   uuid;
  v_source_id    uuid;
  v_personality  uuid;
  v_count        integer;
begin
  select source_id into v_source_id from public.content_sources limit 1;
  if v_source_id is null then
    raise exception 'DoD-2 FAILED: no content_sources row to borrow for the source member FK.';
  end if;
  select personality_id into v_personality from public.personalities limit 1;
  if v_personality is null then
    raise exception 'DoD-2 FAILED: no personalities row to borrow for the personality member FK.';
  end if;

  insert into public.source_clusters (cluster_slug, cluster_label, cluster_category)
  values ('dod-trial-cluster-0022', 'DoD trial cluster', 'ai')
  returning cluster_id into v_cluster_id;

  insert into public.source_cluster_members (cluster_id, source_id, member_sort_order, member_created_at)
  values (v_cluster_id, v_source_id, 0, now());
  insert into public.source_cluster_members (cluster_id, personality_id, member_sort_order, member_created_at)
  values (v_cluster_id, v_personality, 1, now());

  select count(*) into v_count
  from public.source_cluster_members
  where cluster_id = v_cluster_id;
  if v_count <> 2 then
    raise exception 'DoD-2 FAILED: expected 2 trial members, got %.', v_count;
  end if;

  -- Roll back the trial rows so the assertion leaves no residue.
  delete from public.source_cluster_members where cluster_id = v_cluster_id;
  delete from public.source_clusters where cluster_id = v_cluster_id;

  raise notice 'DoD-2 PASSED: cluster + source member + personality member FKs resolve.';
end $$;

-- ── Check 3 — exactly-one-of XOR CHECK rejects both-null and both-set ────────
-- Each illegal insert is wrapped in its own sub-block that EXPECTS the check
-- violation; if the insert unexpectedly succeeds we raise loud.
do $$
declare
  v_cluster_id   uuid;
  v_source_id    uuid;
  v_personality  uuid;
begin
  select source_id into v_source_id from public.content_sources limit 1;
  select personality_id into v_personality from public.personalities limit 1;
  if v_source_id is null or v_personality is null then
    raise exception 'DoD-3 FAILED: need one content_sources row and one personalities row to test the XOR check.';
  end if;

  insert into public.source_clusters (cluster_slug, cluster_label, cluster_category)
  values ('dod-trial-cluster-0022-xor', 'DoD XOR trial cluster', 'ai')
  returning cluster_id into v_cluster_id;

  -- 3a — both refs NULL must be rejected.
  begin
    insert into public.source_cluster_members (cluster_id, source_id, personality_id, member_sort_order)
    values (v_cluster_id, null, null, 0);
    raise exception 'DoD-3a FAILED: a member with BOTH refs null was accepted (XOR check broken).';
  exception
    when check_violation then null;  -- expected
  end;

  -- 3b — both refs SET must be rejected.
  begin
    insert into public.source_cluster_members (cluster_id, source_id, personality_id, member_sort_order)
    values (v_cluster_id, v_source_id, v_personality, 0);
    raise exception 'DoD-3b FAILED: a member with BOTH refs set was accepted (XOR check broken).';
  exception
    when check_violation then null;  -- expected
  end;

  delete from public.source_clusters where cluster_id = v_cluster_id;
  raise notice 'DoD-3 PASSED: XOR check rejects both-null and both-set members.';
end $$;

-- ── Check 4 — anon SELECT on source_clusters succeeds (public-read) ──────────
-- Insert a curated cluster as table owner, switch to anon, assert it is visible,
-- reset role, clean up. Role is reset even on failure.
do $$
declare
  v_cluster_id  uuid;
  v_visible     integer;
begin
  insert into public.source_clusters (cluster_slug, cluster_label, cluster_category)
  values ('dod-trial-cluster-0022-anon', 'DoD anon-read trial cluster', 'ai')
  returning cluster_id into v_cluster_id;

  set local role anon;
  select count(*) into v_visible
  from public.source_clusters
  where cluster_id = v_cluster_id;
  reset role;

  if v_visible <> 1 then
    raise exception 'DoD-4 FAILED: anon SELECT saw % of the curated cluster (expected 1) — public-read broken.', v_visible;
  end if;

  delete from public.source_clusters where cluster_id = v_cluster_id;
  raise notice 'DoD-4 PASSED: anon SELECT on source_clusters succeeds (public-read).';
exception when others then
  reset role;
  raise;
end $$;

-- If we reached here, all DoD checks passed.
do $$ begin raise notice 'ALL DoD CHECKS PASSED for migration 0022 (source clusters + members).'; end $$;
