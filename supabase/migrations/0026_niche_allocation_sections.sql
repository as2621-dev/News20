-- Migration 0026 — Niche allocation sections (user-vocab label + interest node ref)
--
-- Phase FSR (interview onboarding revamp), slice #6 (allocation schema + niche-section
-- allocator). Source of truth: plans/prd.md Technical Foundation Decisions #6/#7 +
-- reference/interview-onboarding-spec.md §5 (persistence mapping). Consumed downstream
-- by slice #7 (fallback-ladder assembly) and #8 (section rendering).
--
-- ── WHAT THIS ADDS ──────────────────────────────────────────────────────────────
--  1. user_feed_allocation.allocation_interest_id — nullable FK to interests(interest_id).
--     A NICHE section row references the interest node it is named for; backbone/
--     beyond-bubble/source rows leave it NULL (they allocate by enum category, not node).
--     `on delete cascade` keeps referential integrity: dropping a taxonomy node removes
--     its allocation rows — never an orphaned section pointing at a dead node.
--  2. user_feed_allocation.allocation_section_label — nullable user-vocabulary label
--     (spec §5: the per-user display label drives the feed section header). NULL for
--     legacy/roots-only coarse rows and source rows; set to the user's words on a niche
--     row and to the reserved "Beyond your bubble" label on a beyond-bubble row.
--
-- ── WHY THE PRIMARY KEY CHANGES (multiple sections per category) ─────────────────
-- Migration 0008's PK was (follow_user_id, allocation_category) — exactly ONE row per
-- (user, category). Niche sections break that assumption: a cricket obsessive has THREE
-- sport niches (IPL, Team India, World Cup), all under the `sport` root, so the user must
-- hold three `sport` rows distinguished by allocation_interest_id. The PK therefore moves
-- to a surrogate `allocation_id`, and a NEW unique constraint on
-- (follow_user_id, allocation_category, allocation_interest_id) — declared NULLS NOT
-- DISTINCT so two NULL-interest rows in the same (user, category) still collide — PRESERVES
-- the old invariant for coarse/backbone/source rows (one per user+category) WHILE allowing
-- one niche row per (user, category, node). This 3-column constraint is the new upsert
-- arbiter the frontend "Build your 30" writer targets (src/lib/feedAllocation.ts, updated
-- in the same commit; its coarse rows carry a NULL allocation_interest_id).
--
-- ── EXPAND/CONTRACT — mid-deploy safety (migration lens) ─────────────────────────
--  * The two new columns are NULLABLE with no default: OLD code that never selects or
--    writes them keeps working (an old INSERT that omits them leaves them NULL). The
--    Python allocator loader (agents/pipeline/daily_batch._load_category_allocation) reads
--    only (follow_user_id, allocation_category, allocation_slot_count, allocation_sort_order)
--    — untouched columns — so it keeps working on the new schema: it simply treats extra
--    niche rows as additional per-category budget until slice #7 makes it node-aware.
--  * The feed_category ENUM is NOT touched (no new value, no rename) — Decision #6's
--    "no enum explosion". youtube/x/backbone/beyond-bubble all keep enum semantics.
--  * The PK/constraint swap is the one NON-additive change. It is done in a single
--    transaction (drop old PK → add surrogate PK → add the 3-col unique) so the table is
--    never left without a uniqueness guarantee. The frontend upsert's onConflict target
--    changes in the SAME commit; a rolling deploy where old frontend code briefly runs
--    against the new schema would fail its 2-column upsert loudly (not silently corrupt) —
--    acceptable because the onboarding "build" write is user-initiated, not a batch path.
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * Column adds are `if not exists`. The PK/constraint swap is guarded with
--    `if exists` / `if not exists` on the drop/add, and the surrogate column add is
--    `if not exists`, so re-applying the whole file is a no-op.
--
-- DEPENDS ON: 0008 (user_feed_allocation + pk_user_feed_allocation), 0003 (interests).
-- Apply order: … → 0025 → 0026. (0022/0024 are deliberately unapplied to prod; this
-- migration does NOT depend on them — it only touches 0008's table + 0003's interests.)
--
-- ⚠ forward-only (repo convention). ROLLBACK is documented at the bottom (manual).

-- ── 1. Nullable interest-node reference + user-vocabulary section label ───────────
alter table user_feed_allocation
  add column if not exists allocation_interest_id uuid
    references interests (interest_id) on delete cascade;

alter table user_feed_allocation
  add column if not exists allocation_section_label text;

comment on column user_feed_allocation.allocation_interest_id is
  'The interest node a NICHE section is named for (spec §5). NULL for coarse/roots-only, '
  'beyond-bubble, and youtube/x source rows (those allocate by enum category, not node).';
comment on column user_feed_allocation.allocation_section_label is
  'The user''s own vocabulary for a niche section header (e.g. "IPL"), the reserved '
  '"Beyond your bubble" label on a beyond-bubble row, or NULL for coarse/source rows.';

-- ── 2. Surrogate primary key (multiple sections per category) ────────────────────
-- Add the surrogate id column first (nullable-by-default add), backfill any existing
-- rows, then swap the PK. gen_random_uuid() is a Postgres 13+ builtin (Supabase = PG15).
alter table user_feed_allocation
  add column if not exists allocation_id uuid not null default gen_random_uuid();

-- Swap the PK: drop the old (user, category) PK, promote allocation_id.
alter table user_feed_allocation drop constraint if exists pk_user_feed_allocation;
alter table user_feed_allocation
  add constraint pk_user_feed_allocation primary key (allocation_id);

-- ── 3. New upsert arbiter — one row per (user, category, node) ────────────────────
-- NULLS NOT DISTINCT (PG15) so two NULL-interest rows in one (user, category) still
-- collide — this preserves migration 0008's "one coarse/backbone/source row per
-- (user, category)" invariant, while a niche row (non-null interest) is unique per node.
-- This is the constraint the frontend "Build your 30" upsert and the backend niche
-- allocator both target.
alter table user_feed_allocation
  drop constraint if exists uq_user_feed_allocation_user_category_interest;
alter table user_feed_allocation
  add constraint uq_user_feed_allocation_user_category_interest
    unique nulls not distinct (follow_user_id, allocation_category, allocation_interest_id);

-- Access pattern: assembly resolves a niche row's node → keep an index on it so the
-- join to interests (and the on-delete cascade) is not a seq scan on a large table.
create index if not exists idx_user_feed_allocation_interest
  on user_feed_allocation (allocation_interest_id);

-- RLS: unchanged. The 0008 user_feed_allocation_owner_all policy is row-level and pins
-- every row (including the new columns) to auth.uid() for the browser writer; the
-- service-role pipeline (niche allocator) bypasses RLS as before. No new policy needed.

-- Verification (run after apply — see the acceptance-criteria check):
--   -- the two new columns exist and are nullable:
--   select column_name, is_nullable, data_type from information_schema.columns
--   where table_name = 'user_feed_allocation'
--     and column_name in ('allocation_interest_id','allocation_section_label');
--   -- the surrogate PK + 3-col unique are present:
--   select conname, contype from pg_constraint
--   where conrelid = 'user_feed_allocation'::regclass and contype in ('p','u');
--
-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ───────────────────────
--   alter table user_feed_allocation drop constraint if exists uq_user_feed_allocation_user_category_interest;
--   alter table user_feed_allocation drop constraint if exists pk_user_feed_allocation;
--   -- (restore the 0008 PK — only valid once all niche rows are deleted:)
--   -- delete from user_feed_allocation where allocation_interest_id is not null;
--   alter table user_feed_allocation add constraint pk_user_feed_allocation primary key (follow_user_id, allocation_category);
--   alter table user_feed_allocation drop column if exists allocation_id;
--   drop index if exists idx_user_feed_allocation_interest;
--   alter table user_feed_allocation drop column if exists allocation_section_label;
--   alter table user_feed_allocation drop column if exists allocation_interest_id;
