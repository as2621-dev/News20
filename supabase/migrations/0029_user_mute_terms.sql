-- Migration 0029 — user_mute_terms: per-user SKIP-TUNE mute list (hard feed filters)
--
-- Phase FSR (interview onboarding revamp), slice #17 (TUNE turns + mutes as hard filters).
-- Source of truth: reference/interview-onboarding-spec.md §6 (terminal schema) + §7
-- (persistence mapping) + §8 (assembly consumption). PRD stories #10/#11.
--
-- ── WHAT THIS ADDS ──────────────────────────────────────────────────────────────
--  user_mute_terms — one row per (user, category, muted term) captured from the SKIP
--  TUNE turn. A mute is a HARD FILTER applied at FEED ASSEMBLY ONLY
--  (agents/pipeline/feed_assembly.py::_filter_muted_stories): a story whose title/body
--  matches a mute term never enters that user's assembled feed. Mutes are NEVER applied
--  at ingestion (the story pool is shared across all users) and NEVER as downranking —
--  "mute" means gone, not demoted (PRD story #11).
--
--  Written by the browser interview persister (src/lib/interviewProfile.ts
--  ::persistMuteTerms) under the authenticated anon-key JWT — unlike the interests
--  taxonomy (which needs a definer RPC to mint global nodes), a mute term is per-user
--  private data, so a plain owner-scoped RLS write policy is the whole seam (mirrors
--  user_feed_allocation in 0008 and follows in 0005). The service-role batch loader
--  (agents/pipeline/daily_batch.py::_load_mute_terms) reads every user's mutes (RLS is
--  bypassed by the service role) to feed the assembler.
--
-- ── ADDITIVE / expand-only · forward-safe deploy window (migration lens) ─────────
--  * Brand-new table: no existing code selects or writes it, so creating it cannot break
--    old code (old assembly ran with no mutes → identical to a user with no mute rows).
--    New code reads/writes it. Expand/contract safe either direction during a rolling deploy.
--  * No DROP, no destructive ALTER, no data rewrite. Reversible by dropping the table
--    (see ROLLBACK below).
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * `create table if not exists` + `create index if not exists`; the policy is guarded
--    by a drop-if-exists so re-applying the whole file is a no-op.
--  * The composite PK makes an INSERT of the same (user, category, term) a natural
--    conflict target — the persister upserts, so re-persisting a mute never duplicates.
--
-- DEPENDS ON: auth.users (Supabase managed). Mirrors the RLS + by-user-index convention
-- of 0005 (follows) and 0008 (user_feed_allocation).

-- ── Table ────────────────────────────────────────────────────────────────────────
create table if not exists user_mute_terms (
  mute_user_id    uuid not null references auth.users (id) on delete cascade,
  mute_category   text not null check (char_length(mute_category) between 1 and 64),
  mute_term       text not null check (char_length(mute_term) between 1 and 200),
  mute_created_at timestamptz not null default now(),
  constraint pk_user_mute_terms primary key (mute_user_id, mute_category, mute_term)
);

comment on table user_mute_terms is
  'Per-user SKIP-TUNE mute list (FSR #17). Each term is a HARD FILTER applied at feed '
  'assembly only (never ingestion — the pool is shared; never downranking): a story '
  'matching the term never appears in this user''s feed. Written by the interview '
  'persister under the owner JWT; read by the service-role daily batch.';
comment on column user_mute_terms.mute_category is
  'The root category slug the mute belongs to (e.g. "sport"); text, not the feed_category '
  'enum, so a root-parked typed interest can carry a mute without enum coupling.';
comment on column user_mute_terms.mute_term is
  'The user-vocabulary term to mute — a tapped SKIP option or typed text, traceable to a '
  'real answer (never model-invented).';

-- Access pattern: hydrate-by-user (the batch loads a user''s whole mute set). The PK
-- already leads with mute_user_id, but add the explicit by-user index to mirror the
-- 0005 idx_follows_user / 0008 idx_user_feed_allocation_user convention.
create index if not exists idx_user_mute_terms_user on user_mute_terms (mute_user_id);

-- ── RLS ──────────────────────────────────────────────────────────────────────────
-- user_mute_terms: OWNER-ALL scoped to auth.uid() (mirrors 0005 follows_owner_all and
-- 0008 user_feed_allocation_owner_all EXACTLY). Both USING (read/delete) and WITH CHECK
-- (insert/update) pin the row to the caller — another user's mutes return zero. The
-- service-role pipeline (which bypasses RLS) reads every user's mutes when assembling.
alter table user_mute_terms enable row level security;
drop policy if exists user_mute_terms_owner_all on user_mute_terms;
create policy user_mute_terms_owner_all on user_mute_terms
  for all using (mute_user_id = auth.uid()) with check (mute_user_id = auth.uid());

-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ──────────────────────
--   drop policy if exists user_mute_terms_owner_all on user_mute_terms;
--   drop table if exists user_mute_terms;
