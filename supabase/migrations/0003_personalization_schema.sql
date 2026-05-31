-- Migration 0003 — Personalization schema (Phase 1e SP1, M1 re-scope 2026-05-30)
--
-- Source of truth: reference/supabase-schema.md §1 (enums), §3 (taxonomy & user
-- tables), §2 (story_interests / daily_feeds), §6 (RLS) + reference/ranking-spec.md
-- (how interest_search_query + profile_is_strict are consumed).
--
-- ADDITIVE / forward-only. Applies cleanly ON TOP OF 0001 (content tables +
-- segment_slug enum) and 0002 (content RLS + storage). NO DROPs, no destructive
-- ALTERs. This is the FIRST migration to create interests / users /
-- user_interest_profile, so the M1 columns (interest_search_query, interest_kind,
-- profile_is_strict) are inline in CREATE — not a later ALTER (per phase file note).
--
-- Adds:
--   2 enums   — interest_profile_source, player_signal_event
--   5 user    — users (+ handle_new_user trigger), interests, user_interest_profile,
--               user_interest_traits, player_signals
--   2 pipeline— story_interests (M:N story↔interest), daily_feeds (per-user feed)
--   RLS       — public-read interests/story_interests; owner-all profile/traits/
--               signals; self users; select-self-only daily_feeds
--
-- Column names transcribed VERBATIM from supabase-schema.md.

-- ── Enums ────────────────────────────────────────────────────────────────────
-- Where an interest weight came from (M1 onboarding is chip-based → 'typed';
-- voice path added in M3; 'signal' = implicit nudge from player_signals).
create type interest_profile_source as enum ('voice', 'typed', 'signal');

-- Implicit engagement events feeding category prioritization (reuse: TLDW player_signals).
create type player_signal_event as enum (
  'play', 'complete', 'open_detail', 'ask', 'voice', 'save', 'follow', 'skip'
);

-- ── users (1:1 with Supabase auth.users, email-only magic-link) ──────────────
create table users (
  user_id                uuid primary key references auth.users (id) on delete cascade,
  user_email             text not null unique,
  user_display_label     text not null default 'Commuter',
  user_onboarded_at      timestamptz,
  user_streak_day_count  integer not null default 0,
  user_created_at        timestamptz not null default now(),
  user_last_active_at    timestamptz not null default now()
);

-- ── interests (hierarchical self-referencing taxonomy) ───────────────────────
-- M1 columns interest_search_query + interest_kind are INLINE (first migration to
-- create this table). ck_interest_depth: depth 0 ⇒ no parent; depth > 0 ⇒ parent.
create table interests (
  interest_id           uuid primary key default gen_random_uuid(),
  parent_interest_id    uuid references interests (interest_id) on delete cascade,
  interest_slug         text not null unique,
  interest_label        text not null,
  depth_level           smallint not null default 0,
  interest_segment_slug segment_slug references segments (segment_slug),
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
create index idx_interests_parent  on interests (parent_interest_id);
create index idx_interests_segment on interests (interest_segment_slug);
create index idx_interests_depth   on interests (depth_level);

-- ── user_interest_profile (per-user weighted interest graph) ─────────────────
-- profile_is_strict is INLINE (first migration to create this table).
create table user_interest_profile (
  user_interest_profile_id  uuid primary key default gen_random_uuid(),
  profile_user_id           uuid not null references users (user_id) on delete cascade,
  profile_interest_id       uuid not null references interests (interest_id) on delete cascade,
  profile_weight            numeric not null default 1.0,
  profile_source            interest_profile_source not null,
  profile_is_strict         boolean not null default false,
  profile_created_at        timestamptz not null default now(),
  profile_updated_at        timestamptz not null default now(),
  constraint uq_user_interest unique (profile_user_id, profile_interest_id)
);
create index idx_user_interest_profile_user on user_interest_profile (profile_user_id);

-- ── user_interest_traits (non-category ordering / depth preferences) ─────────
create table user_interest_traits (
  user_interest_traits_id     uuid primary key default gen_random_uuid(),
  traits_user_id              uuid not null unique references users (user_id) on delete cascade,
  prefers_world_first         boolean not null default true,
  prefers_context_over_facts  boolean not null default false,
  context_vs_facts_ratio      numeric not null default 0.5,
  traits_updated_at           timestamptz not null default now()
);

-- ── player_signals (implicit per-event engagement signals) ───────────────────
create table player_signals (
  player_signal_id  uuid primary key default gen_random_uuid(),
  signal_user_id    uuid not null references users (user_id) on delete cascade,
  signal_story_id   text references stories (story_id) on delete set null,
  event_type        player_signal_event not null,
  dwell_ms          integer,
  completion_pct    numeric,
  occurred_at       timestamptz not null default now()
);
create index idx_player_signals_user_time  on player_signals (signal_user_id, occurred_at desc);
create index idx_player_signals_story      on player_signals (signal_story_id);
create index idx_player_signals_event_type on player_signals (event_type);

-- ── story_interests (M:N story↔interest fan-out join) ────────────────────────
-- story_interest_match_depth: 0 = leaf-matched, 1 = parent, 2 = grandparent
-- (feeds the DepthMatch score term, ranking-spec §1).
create table story_interests (
  story_interest_id          uuid primary key default gen_random_uuid(),
  story_interest_story_id    text not null references stories (story_id) on delete cascade,
  story_interest_interest_id uuid not null references interests (interest_id) on delete cascade,
  story_interest_match_depth smallint not null,
  story_interest_relevance   numeric,
  story_interest_created_at  timestamptz not null default now(),
  constraint uq_story_interest unique (story_interest_story_id, story_interest_interest_id)
);
create index idx_story_interests_interest on story_interests (story_interest_interest_id);
create index idx_story_interests_story    on story_interests (story_interest_story_id);

-- ── daily_feeds (precomputed per-user feed; written by service-role pipeline) ─
create table daily_feeds (
  daily_feed_id            uuid primary key default gen_random_uuid(),
  feed_user_id             uuid not null references users (user_id) on delete cascade,
  feed_story_id            text not null references stories (story_id) on delete cascade,
  feed_date                date not null,
  feed_position            smallint not null,
  feed_score               numeric not null,
  feed_matched_interest_id uuid references interests (interest_id) on delete set null,
  feed_slot_kind           text not null default 'interest',
  feed_created_at          timestamptz not null default now(),
  constraint uq_daily_feed_position unique (feed_user_id, feed_date, feed_position),
  constraint uq_daily_feed_story    unique (feed_user_id, feed_date, feed_story_id)
);
create index idx_daily_feeds_user_date on daily_feeds (feed_user_id, feed_date, feed_position);

-- ── handle_new_user() — create the app users row on auth.users insert ────────
-- Standard Supabase pattern: SECURITY DEFINER + empty search_path + fully-qualified
-- names, so the public.users profile exists immediately after the first magic-link
-- click (supabase-schema.md §6 auth mapping note + SP1 DoD).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.users (user_id, user_email)
  values (new.id, new.email);
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ── RLS (supabase-schema.md §6) ──────────────────────────────────────────────
-- Public-read content: interests, story_interests (SELECT using true; no write
-- policy → only the service-role key, which bypasses RLS, can write).
alter table interests enable row level security;
create policy interests_public_read on interests
  for select using (true);

alter table story_interests enable row level security;
create policy story_interests_public_read on story_interests
  for select using (true);

-- Per-user private tables (owner-all via auth.uid()).
alter table user_interest_profile enable row level security;
create policy user_interest_profile_owner_all on user_interest_profile
  for all using (profile_user_id = auth.uid()) with check (profile_user_id = auth.uid());

alter table user_interest_traits enable row level security;
create policy user_interest_traits_owner_all on user_interest_traits
  for all using (traits_user_id = auth.uid()) with check (traits_user_id = auth.uid());

alter table player_signals enable row level security;
create policy player_signals_owner_all on player_signals
  for all using (signal_user_id = auth.uid()) with check (signal_user_id = auth.uid());

-- users: select-self + update-self (insert is via the SECURITY DEFINER trigger).
alter table users enable row level security;
create policy users_select_self on users
  for select using (user_id = auth.uid());
create policy users_update_self on users
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

-- daily_feeds: SELECT-self only. NO write policy → only the service-role pipeline writes it.
alter table daily_feeds enable row level security;
create policy daily_feeds_select_self on daily_feeds
  for select using (feed_user_id = auth.uid());
