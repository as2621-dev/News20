-- Migration 0008 — Feed allocation ("Build your 30, in order") (Phase 5a SP1)
--
-- Source of truth: plans/phase-5a-build-your-30-and-entity-ranker.md SP1 + the
-- 2026-06-05 owner decision replacing the master-dial / 30-cell-ribbon control
-- surface (phase-5e, now SUPERSEDED) with the "Build your 30" screen: one ordered
-- list of categories, an explicit per-category slot count, and a manual sequence.
-- This migration is the BACKEND CONTRACT that screen writes to; the allocator
-- (Phase 5a SP3, agents/pipeline/feed_assembly.py) reads it to fill the 30 slots.
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0003 (users + auth mapping)
-- and 0005 (the owner-all follows pattern this mirrors). NO DROPs, no destructive
-- ALTERs — only one new enum + one new table + one index + one policy. ⚠ Forward-
-- only (additive) — no down migration; reversible only by manual drop of the new
-- objects on a disposable/backed-up DB.
--
-- Adds:
--   1 enum  — feed_category (the 8 screen categories, keys verbatim from SP1)
--   1 table — user_feed_allocation (owner-all; one row per (user, category))
--   1 index — idx_user_feed_allocation_user (the batched hydrate-by-user read)
--   RLS     — owner-all user_feed_allocation (mirrors 0005 follows_owner_all and
--             0007 user_entity_follows_owner_all EXACTLY)
--
-- KEY DESIGN DECISIONS (surfaced per Rule 7):
--
--  1. feed_category is an ENUM, NOT a free-text/lookup-table column. The 8 keys are
--     a CLOSED set drawn on the "Build your 30" screen (Breaking · World & Politics ·
--     Tech & Science · YouTube · Markets · Sport · X · Culture). A closed editorial
--     set is exactly what §0/§1 of reference/supabase-schema.md models as an enum
--     (cf. segment_slug, coverage_mode). Keys are the snake_case machine ids; the
--     human labels live in the frontend, not the DB. YouTube and X are SOURCE
--     categories (phase-5d, not built) — budgeted-but-empty today; their slots
--     soft-roll into topic categories in the allocator (SP3), so they are members
--     of the enum from day one and need no later ALTER TYPE.
--
--  2. follow_user_id references auth.users(id) — mirroring the MOST RECENT shipped
--     user-scoped tables, 0005 follows and 0007 user_entity_follows (both reference
--     auth.users(id) directly), NOT 0003's public.users(user_id). Both resolve to the
--     same uuid; we pick the newer/tested pattern (Rule 7). The column is named
--     follow_user_id to match 0005/0007 verbatim (the owner-all predicate column).
--
--  3. PK is (follow_user_id, allocation_category) — one allocation row per user per
--     category (idempotent upsert of a slot budget). allocation_sort_order carries the
--     user's MANUAL sequence (the order they arranged the list); it is a plain int,
--     not unique, so a transient reorder mid-edit can't trip a constraint.
--
--  4. allocation_slot_count is CHECK (0 <= count <= 30): a single category can claim
--     at most the whole 30-slot feed, and 0 means "muted but kept in the list/order".
--     The cross-category SUM(count) == 30 invariant is enforced by the WRITER (the
--     screen / service-role seed) + the allocator's roll-over logic, NOT by a table
--     CHECK — a per-row CHECK cannot see sibling rows, and a too-strict SUM trigger
--     would reject the intermediate states of an interactive edit.

-- ── Enum — feed_category (the 8 "Build your 30" screen categories) ────────────
-- Keys VERBATIM from the SP1 spec / owner lock (2026-06-05). 'youtube' and 'x' are
-- the source-axis categories (phase-5d); 'breaking' is the user-budgeted top-
-- Importance tier; the rest are topic categories the slug taxonomy maps up into.
create type feed_category as enum (
  'breaking',
  'world_politics',
  'tech_science',
  'youtube',
  'markets',
  'sport',
  'x',
  'culture'
);

-- ── user_feed_allocation (per-user per-category slot budget + manual sequence) ─
-- One row per (user, category). allocation_slot_count = how many of the 30 slots the
-- user gave this category; allocation_sort_order = where it sits in their manual
-- sequence. The allocator (SP3) reads the whole set for a user, fills each category's
-- slots from the entity-aware Score buckets in sequence order, and soft-rolls empty
-- source-category budgets into topic categories so the feed still totals 30.
create table user_feed_allocation (
  follow_user_id         uuid not null references auth.users (id) on delete cascade,
  allocation_category    feed_category not null,
  allocation_slot_count  int not null check (allocation_slot_count >= 0 and allocation_slot_count <= 30),
  allocation_sort_order  int not null,
  allocation_updated_at  timestamptz not null default now(),
  constraint pk_user_feed_allocation primary key (follow_user_id, allocation_category)
);
-- Access pattern: hydrate-by-user (the allocator reads a user's whole allocation set).
-- The PK already leads with follow_user_id, but add the explicit by-user index to
-- mirror 0005's idx_follows_user / 0007's idx_user_entity_follows_user convention.
create index idx_user_feed_allocation_user on user_feed_allocation (follow_user_id);

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- user_feed_allocation: OWNER-ALL scoped to auth.uid() (mirrors 0005 follows_owner_all
-- and 0007 user_entity_follows_owner_all EXACTLY). Both the USING (read/delete) and
-- WITH CHECK (insert/update) predicates pin the row to the caller — another user's
-- allocation rows return zero. The service-role pipeline (which bypasses RLS) reads
-- every user's allocation when assembling daily_feeds.
alter table user_feed_allocation enable row level security;
create policy user_feed_allocation_owner_all on user_feed_allocation
  for all using (follow_user_id = auth.uid()) with check (follow_user_id = auth.uid());
