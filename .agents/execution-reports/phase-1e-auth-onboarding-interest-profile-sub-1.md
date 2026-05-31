# Phase 1e SP1 — Migration 0003 (user + taxonomy schema) — execution report

**Sub-phase:** 1 of 4 — Migration 0003 + interests seed (SQL files only; orchestrator applies).
**Status:** SUCCESS (by inspection — offline, DB not touched per decision Q2).

## What I implemented
A forward-only, additive migration that applies on top of 0001/0002, plus an idempotent taxonomy seed. No existing file touched; no DB command run.

### Files created
- `supabase/migrations/0003_personalization_schema.sql`
- `supabase/seed/interests.sql`

### Migration 0003 contents (dependency-safe order)
1. **2 enums** — `interest_profile_source AS ENUM ('voice','typed','signal')`; `player_signal_event AS ENUM ('play','complete','open_detail','ask','voice','save','follow','skip')`. Labels transcribed verbatim from schema §1.
2. **5 user-side tables** — `users` (PK `user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE`), `interests` (self-FK tree, `ck_interest_depth` CHECK, M1 columns `interest_search_query` + `interest_kind DEFAULT 'taxonomy'` **inline**), `user_interest_profile` (`profile_is_strict DEFAULT false` **inline**, `uq_user_interest`, `profile_source` NOT NULL), `user_interest_traits`, `player_signals`. All §3 indexes recreated.
3. **2 pipeline tables** — `story_interests` (`uq_story_interest`, both indexes) and `daily_feeds` (both unique constraints + index).
4. **`handle_new_user()`** — `SECURITY DEFINER`, `set search_path = ''`, fully-qualified `public.users`/`public.handle_new_user`, inserts `(new.id, new.email)`; `AFTER INSERT ON auth.users FOR EACH ROW` trigger `on_auth_user_created`.
5. **RLS on all 7 new tables** — public-read SELECT on `interests`/`story_interests`; owner-all on `user_interest_profile`/`user_interest_traits`/`player_signals`; `users` select-self + update-self; `daily_feeds` **select-self only, NO write policy**. Policy names match §6.

### Seed `interests.sql`
- 10 depth-0 categories with the exact `interest_segment_slug` accents from the phase table (world→geopolitics, business→markets, tech→tech, sport→sport, health→wildcard, entertainment→wildcard, climate→geopolitics, lifestyle→wildcard, crypto→markets, science→tech).
- Four 3-level chains with realistic leaf `interest_search_query`: Sport→Cricket→India, Sport→Soccer→Arsenal, Business→Equities→Semiconductors, Tech→AI→LLMs.
- Idempotent (`on conflict (interest_slug) do nothing`); parents resolved by slug subselect (no hardcoded UUIDs); depth-0 → depth-1 → depth-2 insert order so parents resolve within one run; depths 0/1/2 set correctly and respect `ck_interest_depth`.

## Divergences from plan (+why)
- **Phase file said "2 ALTERs on `interests` + 1 ALTER on `user_interest_profile`."** I verified `interests`/`user_interest_profile` are NOT in 0001/0002 (0001 = content tables only; 0002 = RLS/storage only). Since 0003 is the first migration to CREATE them, the three M1 columns are inline in CREATE TABLE (no CREATE-then-ALTER dance). This is exactly the note in the SP1 brief and avoids a pointless ALTER on a table created in the same file. No behavioral difference for the orchestrator.
- **Lowercase SQL keywords.** Matched 0001/0002 house style (Rule 11 conformance) even though the schema doc shows uppercase DDL.

## Self-review (Step B) + fixes (Step C)
Grepped the diff for `DROP`/`TRUNCATE`/`DELETE`/destructive `ALTER` — **none** (additive-only confirmed). Verified: enum labels exact; every FK target exists in 0001 or earlier in 0003; `ck_interest_depth` present; `handle_new_user` is SECURITY DEFINER + empty search_path + fully-qualified; 7 ENABLE RLS for 7 new tables; 8 policies (5 owner/public + users select + users update + daily_feeds select) — `daily_feeds` has exactly one SELECT policy and **no write policy**; seed idempotent and CHECK-respecting; each depth-2 parent slug is seeded at depth-1 in the prior statement. **No critical/high issues → no fixes needed.**

## Step D — Validation (offline only)
No `psql`/`pg_format`/`sqlparse` available locally, and I was instructed not to install or connect. Validation was manual inspection against 0001/0002 + schema §1/§2/§3/§6: dollar-quoted function body balanced, statement ordering dependency-safe, syntax is plausible Postgres. **Offline inspection: PASS.**

## Step E — Definition of done (by inspection)
- **Migration applies cleanly on 0001/0002; all FKs resolve** — HOLDS by construction. `interests.interest_segment_slug→segments(segment_slug)`, `story_interests.story_interest_story_id→stories(story_id text)`, `daily_feeds.feed_story_id→stories`/`feed_user_id→users`, `users.user_id→auth.users(id)` all reference tables that exist in 0001 / earlier in 0003 / Supabase-managed. *Orchestrator must live-verify at `db push`.*
- **Recursive CTE returns the 3-level chains** — HOLDS. Four full depth-0→1→2 chains seeded with correct `parent_interest_id` + `depth_level`.
- **auth.users insert → users row via trigger** — HOLDS by construction (standard Supabase SECURITY DEFINER pattern). *Orchestrator: verify the trigger fires and `user_email` copies on a real magic-link signup.*
- **anon SELECT allowed on interests/story_interests; denied (0 rows) on other users' user_interest_profile/daily_feeds** — HOLDS. Public-read policies vs owner/self policies keyed on `auth.uid()`; `daily_feeds` is select-self with no broad read. *Orchestrator: confirm RLS allow/deny with an anon key + a second user.*

## Concerns for the orchestrator (apply-time risks)
1. **`auth.users` trigger needs elevated privileges.** Creating a trigger on `auth.users` and a SECURITY DEFINER function works under the service-role/superuser used by `supabase db push`. If applied under a restricted role it could fail on the `create trigger ... on auth.users` line.
2. **First-apply only (no `if not exists`).** Matches 0001/0002 convention. Re-running 0003 on a DB that already has it will error on `create type`/`create table` — expected for a one-shot forward migration; do not re-apply.
3. **Trigger duplicate-key edge case.** If a `public.users` row already exists for an `auth.users` id (e.g. manual backfill) the trigger insert will hit `user_id`/`user_email` unique violation. Not a concern for greenfield apply; flag only if backfilling.
4. **Seed depends on segments being populated** — `interest_segment_slug` FKs `segments(segment_slug)`. The 5 segment rows must be seeded (0001 creates the table; segment rows come from the M0/1b seed). All 5 enum values used (geopolitics/markets/tech/sport/wildcard) must exist as `segments` rows before `interests.sql` runs, or depth-0 inserts fail the FK.

## Return to orchestrator
1. **STATUS:** SUCCESS
2. **Files touched:** `supabase/migrations/0003_personalization_schema.sql`, `supabase/seed/interests.sql`
3. **Validation (offline inspection):** PASS — additive-only (no DROP/destructive ALTER), enum labels exact, FK targets resolve, ordering dependency-safe, dollar-quoting balanced. No local SQL linter available to run.
4. **Definition of done (by inspection):** PASS — all four SP1 DoD criteria hold by construction; flagged items for live verification at apply time.
5. **Concerns:** auth.users trigger privilege at `db push`; first-apply-only (no re-run); ensure `segments` rows seeded before `interests.sql` (FK on `interest_segment_slug`).
