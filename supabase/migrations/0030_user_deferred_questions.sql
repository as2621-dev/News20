-- Migration 0030 — user_deferred_questions: per-user record of SKIPPED interview questions
--
-- Phase FSR (interview onboarding revamp), data core for slice #19 (chat closing arc).
-- Source of truth: reference/interview-onboarding-spec.md §5 (skip semantics) + §6
-- (terminal schema). The engine already emits these records
-- (agents/interview/models.py::DeferredQuestion, built in agents/interview/phases.py and
-- carried by agents/interview/responses.py::terminal_response); only the client dropped
-- them until now. PRD closing-arc stories.
--
-- ── WHAT THIS ADDS ──────────────────────────────────────────────────────────────
--  user_deferred_questions — one row per question the user SKIPPED during the interview,
--  recorded at terminal for later in-app resurfacing (spec §5). The chat never blocks on a
--  skip — it fast-forwards — but every skipped question is persisted as *deferred* so a
--  fast-follow surface can re-ask it. The record is engine-owned and DETERMINISTIC (never
--  model judgment): the phase state machine emits exactly one per skip.
--
--  Written by the browser interview persister (src/lib/interviewProfile.ts
--  ::persistDeferredQuestions) under the authenticated anon-key JWT — like a mute term
--  (0029), a deferred record is per-user private data, so a plain owner-scoped RLS write
--  policy is the whole seam (no definer RPC — unlike the interests taxonomy, which mints
--  global nodes). Nothing reads it yet in this slice; the resurfacing surface is the
--  fast-follow (#19 and beyond).
--
-- ── ADDITIVE / expand-only · forward-safe deploy window (migration lens) ─────────
--  * Brand-new table: no existing code selects or writes it, so creating it cannot break
--    old code. Expand/contract safe either direction during a rolling deploy.
--  * No DROP, no destructive ALTER, no data rewrite. Reversible by dropping the table
--    (see ROLLBACK below).
--
-- ── IDEMPOTENT ──────────────────────────────────────────────────────────────────
--  * `create table if not exists` + `create index if not exists`; the policy is guarded
--    by a drop-if-exists, so re-applying the whole file is a no-op.
--  * The persister is delete-all-then-insert (deferred records are fully re-derived each
--    interview and carry NO natural conflict key — two category skips differ only by a
--    nullable root), so the PK is a SURROGATE uuid; re-persisting never duplicates because
--    the whole set is cleared first on a rebuild.
--
-- DEPENDS ON: auth.users (Supabase managed) + gen_random_uuid() (Postgres core, PG13+).
-- Mirrors the RLS + by-user-index convention of 0029 (user_mute_terms) and 0008
-- (user_feed_allocation).

-- ── Table ────────────────────────────────────────────────────────────────────────
create table if not exists user_deferred_questions (
  deferred_id             uuid not null default gen_random_uuid(),
  deferred_user_id        uuid not null references auth.users (id) on delete cascade,
  deferred_kind           text not null
    check (deferred_kind in ('subniche_skip', 'category_skip', 'who_drill_skip', 'roots_skip')),
  deferred_question_text  text not null default ''
    check (char_length(deferred_question_text) <= 1000),
  deferred_root_slug      text check (deferred_root_slug is null or char_length(deferred_root_slug) between 1 and 200),
  deferred_subniche_label text check (deferred_subniche_label is null or char_length(deferred_subniche_label) between 1 and 200),
  deferred_created_at     timestamptz not null default now(),
  constraint pk_user_deferred_questions primary key (deferred_id)
);

comment on table user_deferred_questions is
  'Per-user record of questions SKIPPED during the conversational interview (FSR, data core '
  'for #19). Engine-emitted + deterministic (never model judgment). Written by the interview '
  'persister under the owner JWT as delete-all-then-insert (subtractive, fully re-derived each '
  'interview); read by a fast-follow in-app resurfacing surface. Not consumed by assembly.';
comment on column user_deferred_questions.deferred_kind is
  'Which skippable turn produced this record: subniche_skip | category_skip | who_drill_skip | '
  'roots_skip (TS twin of agents/interview/models.py::DeferralKind).';
comment on column user_deferred_questions.deferred_root_slug is
  'The category root the skip belongs to, when applicable (NULL for a roots-level skip).';
comment on column user_deferred_questions.deferred_subniche_label is
  'The sub-niche label the WHO drill was about, when applicable (NULL otherwise).';

-- Access pattern: hydrate-by-user (a resurfacing surface loads a user''s whole deferred set).
-- Add the explicit by-user index to mirror the 0008 / 0029 by-user index convention.
create index if not exists idx_user_deferred_questions_user on user_deferred_questions (deferred_user_id);

-- ── RLS ──────────────────────────────────────────────────────────────────────────
-- user_deferred_questions: OWNER-ALL scoped to auth.uid() (mirrors 0029 user_mute_terms
-- and 0008 user_feed_allocation EXACTLY). Both USING (read/delete) and WITH CHECK
-- (insert/update) pin every row to the caller — another user's deferred records return zero.
alter table user_deferred_questions enable row level security;
drop policy if exists user_deferred_questions_owner_all on user_deferred_questions;
create policy user_deferred_questions_owner_all on user_deferred_questions
  for all using (deferred_user_id = auth.uid()) with check (deferred_user_id = auth.uid());

-- ── ROLLBACK / DOWN (manual; forward-only repo convention) ──────────────────────
--   drop policy if exists user_deferred_questions_owner_all on user_deferred_questions;
--   drop table if exists user_deferred_questions;
