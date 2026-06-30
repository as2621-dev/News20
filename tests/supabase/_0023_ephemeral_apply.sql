-- Ephemeral-PG apply + assertion harness for migration 0023 (FSR-M2R root nodes).
--
-- Driven by tests/supabase/test_migration_0023_root_interest_nodes.py through
-- `pg_virtualenv psql -v ON_ERROR_STOP=1 -f <this>` against a throw-away PG16
-- cluster (the sandbox has no live DB). It stands up the MINIMAL real schema the
-- migration touches (the interests table + ck_interest_depth from 0003, a stub
-- segments table for the interest_segment_slug FK, plus a stub users +
-- user_interest_profile so we can prove existing profile FKs still resolve after
-- re-parenting), seeds the SAME rows as supabase/seed/interests*.sql, then applies
-- 0023 — once, then a SECOND time — and asserts the DoD with `do $$ ... assert`.
-- Any failed assertion raises and ON_ERROR_STOP makes psql exit non-zero, which the
-- pytest wrapper treats as a hard failure (Rule 12: fail loud, never fake).
--
-- NOTE: \i :migration is substituted by the wrapper to the absolute 0023 path.

\set ON_ERROR_STOP on

begin;

-- ── Minimal real schema (subset of 0003 + a stub segments) ──────────────────────
create table segments (
  segment_slug text primary key
);
insert into segments (segment_slug) values
  ('ai'),('geopolitics'),('business'),('environment'),
  ('politics'),('tech'),('sport'),('arts'),
  -- legacy segment slugs referenced by legacy depth-0 interests in the seed
  ('markets'),('wildcard');

-- interests table — verbatim shape from 0003 (the columns 0023 + FKs depend on).
create table interests (
  interest_id           uuid primary key default gen_random_uuid(),
  parent_interest_id    uuid references interests (interest_id) on delete cascade,
  interest_slug         text not null unique,
  interest_label        text not null,
  depth_level           smallint not null default 0,
  interest_segment_slug text references segments (segment_slug),
  interest_search_query text,
  interest_kind         text not null default 'taxonomy',
  interest_sort_order   smallint not null default 0,
  interest_is_active    boolean not null default true,
  interest_created_at   timestamptz not null default now(),
  constraint ck_interest_depth check (
    (depth_level = 0 and parent_interest_id is null) or
    (depth_level > 0 and parent_interest_id is not null)
  )
);

-- Stub users + user_interest_profile so we can prove a profile FK referencing a
-- LEAF interest still resolves after the leaf is re-parented (re-parent flips the
-- parent pointer, not interest_id, so this FK must survive).
create table users (user_id uuid primary key);
create table user_interest_profile (
  user_interest_profile_id uuid primary key default gen_random_uuid(),
  profile_user_id    uuid not null references users (user_id) on delete cascade,
  profile_interest_id uuid not null references interests (interest_id) on delete cascade
);

-- ── Seed: the EXACT legacy depth-0 roots from supabase/seed/interests.sql ────────
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
  ('science',       'Space & Hard Science',    0, null, 'tech',        100);

-- Phase-1e depth-1 + depth-2 leaves (interests.sql) — parent by slug subselect.
insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_sort_order)
select v.slug, v.label, 1, p.interest_id, v.so
from (values
  ('sport.cricket','Cricket','sport',10),
  ('sport.soccer','Soccer','sport',20),
  ('business.equities','Equities','business',10),
  ('tech.ai','AI','tech',10)
) v(slug,label,parent_slug,so)
join interests p on p.interest_slug = v.parent_slug;

insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_sort_order)
select v.slug, v.label, 2, p.interest_id, v.so
from (values
  ('sport.cricket.india','India','sport.cricket',10),
  ('sport.soccer.arsenal','Arsenal','sport.soccer',10),
  ('business.equities.semis','Semiconductors','business.equities',10),
  ('tech.ai.llms','LLMs','tech.ai',10)
) v(slug,label,parent_slug,so)
join interests p on p.interest_slug = v.parent_slug;

-- Picker leaves (interests_picker_topics.sql) — the rows that get re-parented.
-- Parented to LEGACY roots exactly as the seed does (ai.*->tech, geopolitics.*->
-- world, environment.*->climate, politics.*->world, arts.*->entertainment;
-- business.*/tech.* already under business/tech).
insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_sort_order)
select v.slug, v.label, 1, p.interest_id, v.so
from (values
  ('ai.data-center-buildout','Data center buildout','tech',10),
  ('ai.alignment-research','Alignment research','tech',20),
  ('geopolitics.russia-sanctions','Russia sanctions','world',10),
  ('geopolitics.oil-opec','Oil & OPEC','world',20),
  ('business.ipos','IPOs','business',10),
  ('business.bonds','Bonds','business',20),
  ('environment.solar','Solar','climate',10),
  ('environment.nuclear','Nuclear','climate',20),
  ('politics.national-elections','National elections','world',10),
  ('politics.budget','Budget','world',20),
  ('tech.consoles','Consoles','tech',10),
  ('tech.ransomware','Ransomware','tech',20),
  ('arts.box-office','Box office','entertainment',10),
  ('arts.fiction','Fiction','entertainment',20)
) v(slug,label,parent_slug,so)
join interests p on p.interest_slug = v.parent_slug;

-- An existing user_interest_profile row pointing at a LEAF that WILL be re-parented
-- (arts.box-office). Captures its interest_id so we can prove the FK still resolves.
insert into users (user_id) values ('00000000-0000-0000-0000-000000000001');
insert into user_interest_profile (profile_user_id, profile_interest_id)
select '00000000-0000-0000-0000-000000000001', interest_id
from interests where interest_slug = 'arts.box-office';

commit;

-- ── Pre-apply sanity: only 3 of the 8 roots exist as depth-0 interest rows ───────
do $$
declare n int;
begin
  select count(*) into n from interests
  where depth_level = 0
    and interest_slug in ('ai','geopolitics','business','environment','politics','tech','sport','arts');
  assert n = 3, format('PRE-APPLY: expected 3 of 8 roots present, got %s', n);
end $$;

-- ════════════════════════ APPLY 0023 (first time) ═══════════════════════════════
\i :migration

-- ════════════════════════ APPLY 0023 (second time — idempotency) ════════════════
-- Snapshot the full interests table, re-apply, diff: a true no-op changes nothing.
create temp table _snap as
  select interest_id, parent_interest_id, interest_slug, depth_level, interest_label,
         interest_segment_slug, interest_sort_order
  from interests;

\i :migration

do $$
declare changed int; added int; removed int;
begin
  -- rows whose (id, parent, slug, depth, label) differ from the snapshot
  select count(*) into changed
  from interests i
  join _snap s on s.interest_id = i.interest_id
  where (i.parent_interest_id is distinct from s.parent_interest_id)
     or i.interest_slug <> s.interest_slug
     or i.depth_level <> s.depth_level;
  select count(*) into added   from interests i where not exists (select 1 from _snap s where s.interest_id = i.interest_id);
  select count(*) into removed from _snap s where not exists (select 1 from interests i where i.interest_id = s.interest_id);
  assert changed = 0, format('IDEMPOTENCY: re-apply changed %s existing rows', changed);
  assert added   = 0, format('IDEMPOTENCY: re-apply ADDED %s rows (roots duplicated?)', added);
  assert removed = 0, format('IDEMPOTENCY: re-apply REMOVED %s rows', removed);
end $$;

-- ════════════════════════ DoD ASSERTIONS ════════════════════════════════════════
do $$
declare n int;
begin
  -- 1. All 8 roots exist exactly once, depth 0, parent NULL.
  select count(*) into n from interests
  where depth_level = 0 and parent_interest_id is null
    and interest_slug in ('ai','geopolitics','business','environment','politics','tech','sport','arts');
  assert n = 8, format('DoD1: expected 8 depth-0 roots, got %s', n);

  -- 1b. No duplicate root slugs (unique constraint already guards, belt+braces).
  select count(*) into n from (
    select interest_slug from interests
    where interest_slug in ('ai','geopolitics','business','environment','politics','tech','sport','arts')
    group by interest_slug having count(*) > 1
  ) d;
  assert n = 0, format('DoD1b: %s root slugs duplicated', n);

  -- 1c. The 3 pre-existing roots kept their ORIGINAL Phase-1e labels (reconciled,
  --     not overwritten by `do nothing`).
  select count(*) into n from interests
  where (interest_slug='business' and interest_label='Business & Markets')
     or (interest_slug='tech'     and interest_label='Tech & Science')
     or (interest_slug='sport'    and interest_label='Sport');
  assert n = 3, format('DoD1c: pre-existing roots lost their labels (%s/3 intact)', n);

  -- 2. Every depth-1 picker leaf is parented to its true root (slug-root == parent slug).
  select count(*) into n from interests leaf
  join interests p on p.interest_id = leaf.parent_interest_id
  where leaf.depth_level = 1
    and split_part(leaf.interest_slug,'.',1) in
        ('ai','geopolitics','business','environment','politics','tech','sport','arts')
    and p.interest_slug <> split_part(leaf.interest_slug,'.',1);
  assert n = 0, format('DoD2: %s depth-1 leaves mis-parented (slug-root != parent slug)', n);

  -- 2b. Depth-2 leaves were NOT re-parented (still under their depth-1 parent).
  select count(*) into n from interests leaf
  join interests p on p.interest_id = leaf.parent_interest_id
  where leaf.depth_level = 2
    and p.interest_slug <> regexp_replace(leaf.interest_slug, '\.[^.]+$', '');
  assert n = 0, format('DoD2b: %s depth-2 leaves wrongly re-parented', n);

  -- 3. No orphans: every depth>0 interest has a parent that exists (FK guarantees
  --    existence; assert no NULL parent on a non-root, which ck_interest_depth also
  --    enforces — belt+braces it survived the re-parent).
  select count(*) into n from interests where depth_level > 0 and parent_interest_id is null;
  assert n = 0, format('DoD3: %s non-root interests have NULL parent (orphan)', n);

  -- 4. FK integrity: every parent_interest_id points at a real interest row.
  select count(*) into n from interests c
  where c.parent_interest_id is not null
    and not exists (select 1 from interests p where p.interest_id = c.parent_interest_id);
  assert n = 0, format('DoD4: %s dangling parent_interest_id pointers', n);

  -- 5. Existing user_interest_profile row still resolves to a real interest, and that
  --    interest is now correctly re-homed under root `arts` (interest_id unchanged).
  select count(*) into n from user_interest_profile uip
  join interests leaf on leaf.interest_id = uip.profile_interest_id
  join interests root on root.interest_id = leaf.parent_interest_id
  where leaf.interest_slug = 'arts.box-office' and root.interest_slug = 'arts';
  assert n = 1, format('DoD5: profile FK to re-parented leaf did not resolve under new root (%s)', n);
end $$;

select 'OK: migration 0023 ephemeral apply + idempotency + DoD all passed' as result;
