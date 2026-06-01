-- Migration 0005 — Follows (Phase 3d SP3)
--
-- Source of truth: plans/phase-3d-m3-personalization-follow.md SP3 + the
-- 2026-05-31 re-scope (follow is a persistent RANKING signal, not a reading
-- surface). A `follows` row says "more of this story's subniche tomorrow"; the
-- daily profile-update job (SP2, agents/memory/session_processor.py) reads this
-- set and boosts the followed story's matched interest node(s).
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0001 (stories) and 0003
-- (users + auth mapping). NO DROPs, no destructive ALTERs. Reversible by drop.
--
-- Adds:
--   1 table — follows (owner-scoped, idempotent toggle via unique constraint)
--   RLS     — owner-all on auth.uid() = follow_user_id (mirrors player_signals)
--
-- Column naming mirrors 0003's verbose, entity-prefixed convention.
-- The follow_story_id FK type MUST match stories.story_id, which is `text`
-- (0001_content_schema.sql line 48) — NOT uuid.

-- ── follows (per-user persistent follow set) ─────────────────────────────────
create table follows (
  follow_id          uuid primary key default gen_random_uuid(),
  follow_user_id     uuid not null references auth.users (id) on delete cascade,
  follow_story_id    text not null references stories (story_id) on delete cascade,
  follow_created_at  timestamptz not null default now(),
  -- Idempotent toggle: one follow row per (user, story); a re-insert is a no-op
  -- against this constraint, so toggleFollow can rely on insert/delete semantics.
  constraint uq_follow_user_story unique (follow_user_id, follow_story_id)
);
-- Access patterns: hydrate-by-user (the reel's batched read) and toggle-by
-- (user, story). The unique constraint already indexes (user, story); add the
-- by-user index for the batched hydrate read. Mirrors 0003's index style.
create index idx_follows_user on follows (follow_user_id);

-- ── RLS (mirror player_signals owner-all, 0003 lines 179–181) ────────────────
-- A user may read/write ONLY their own follows. No cross-user access: both the
-- USING (read/delete) and WITH CHECK (insert/update) predicates pin the row to
-- the caller's auth.uid().
alter table follows enable row level security;
create policy follows_owner_all on follows
  for all using (follow_user_id = auth.uid()) with check (follow_user_id = auth.uid());
