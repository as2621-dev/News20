-- Migration 0012 — user_profiles (Settings: editable display name)
--
-- Source of truth: the "App Surfaces — Settings" design (SettingsLayer.tsx).
-- The app's email-OTP auth carries no profile-name field, so the Settings
-- header/name row previously derived a name from the email local-part. This
-- table stores the user-chosen display name; `src/lib/profile.ts` is the ONLY
-- client-side reader/writer.
--
-- ADDITIVE / forward-only. Applies cleanly on top of 0003 (auth mapping).
-- NO DROPs, no destructive ALTERs. Reversible by drop.
--
-- Adds:
--   1 table — user_profiles (one row per auth user, PK = user id)
--   RLS     — owner-all on auth.uid() = profile_user_id (mirrors follows, 0005)
--
-- Column naming mirrors 0003/0005's verbose, entity-prefixed convention.

-- ── user_profiles (per-user editable profile) ────────────────────────────────
create table user_profiles (
  profile_user_id      uuid primary key references auth.users (id) on delete cascade,
  profile_display_name text not null,
  profile_created_at   timestamptz not null default now(),
  profile_updated_at   timestamptz not null default now(),
  -- Keep names render-safe for the Settings header / avatar initial.
  constraint ck_profile_display_name_length
    check (char_length(profile_display_name) between 1 and 80)
);

-- ── RLS (mirror follows owner-all, 0005 lines 39–41) ─────────────────────────
-- A user may read/write ONLY their own profile row. Both USING (read/delete)
-- and WITH CHECK (insert/update) pin the row to the caller's auth.uid().
alter table user_profiles enable row level security;
create policy user_profiles_owner_all on user_profiles
  for all using (profile_user_id = auth.uid()) with check (profile_user_id = auth.uid());
