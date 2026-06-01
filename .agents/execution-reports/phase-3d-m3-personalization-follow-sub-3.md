# Execution report — phase-3d-m3-personalization-follow · Sub-phase 3

**Sub-phase:** SP3 — Follow toggle + persistent `follows` table as a ranking signal
**Date:** 2026-05-31
**Status:** SUCCESS

## What this implemented
Made "follow a story" PERSISTENT (was in-memory only) so the daily profile-update
job (SP2) can read it as a ranking signal. Three changes: a minimal `follows`
table (migration 0005), a typed client lib (`src/lib/follows.ts`), and wiring the
already-prop-driven reel follow toggle in `Reel.tsx` to write/read it. No Following
view, no what's-new, no `follow_last_seen_at` (per the re-scope).

## Files created / modified
- **NEW** `supabase/migrations/0005_m3_follows.sql` — `follows` table + RLS + index.
- **NEW** `src/lib/follows.ts` — `toggleFollow`, `isFollowing`, `getFollowedStoryIds`.
- **MODIFIED** `src/components/reel/Reel.tsx` — replaced the in-memory follow seed
  with a batched hydrate on feed load + persist-through-toggle (optimistic +
  reconcile).
- **NEW** `tests/lib/follows.test.ts` — 7 tests (happy/toggle-off/unauth/hydrate).

`ReelChrome.tsx` was NOT touched — it is already fully prop-driven via
`isFollowed` + `onToggleFollow` with the `follow-on` accent wired. All persistence
lives in the lifted state owner (`Reel.tsx`), exactly as the priming notes
predicted.

## Divergences from the plan (+ why)
1. **`ActionRow.tsx` does not exist** — the plan's "Files touched" named it. The
   follow button lives in `ReelChrome.tsx` (prop-driven) and its state is lifted
   into `Reel.tsx`. Per the progress file's "Repo divergences" section, I wired
   persistence in `Reel.tsx` instead. No `ReelChrome.tsx` change needed.
2. **Migration number `0005`** (existing 0001–0004), named `0005_m3_follows.sql`.
3. **stories FK type is `text`, not uuid.** `stories.story_id` is
   `text primary key` (`0001_content_schema.sql:48`). So `follow_story_id` is
   `text not null references stories (story_id) on delete cascade`. This is
   load-bearing for SP2's Python join — see Concerns.
4. **App-side auth guard in addition to RLS.** `follows.ts` resolves the user via
   `auth.getUser()` and no-ops when signed out, so an anon tap degrades gracefully
   (no throw, no rejected-write noise) rather than relying on RLS alone. Matches
   how `auth.ts` reads the session.
5. **`Reel.tsx` still imports `getFeed` from `fixtureFeed`** (unchanged — out of
   scope). The follow id passed to `toggleFollow` is `Story.digest_id`, which the
   production `supabaseFeed.mapStoryRow` sets to `story_id`; under fixtures it is
   the fixture slug. Persistence is keyed on whatever id the feed carries — correct
   once the feed provider is swapped; no change required here.

## Self-review findings + fixes
- **RLS cross-user access (CRITICAL → verified safe):** `follows_owner_all` pins
  `follow_user_id = auth.uid()` on BOTH `using` and `with check`, mirroring
  `player_signals_owner_all` in 0003 exactly. A user cannot read, insert, update,
  or delete another user's follows. Client reads/writes are additionally filtered
  on the resolved authed id. No finding to fix.
- **Swallowed errors (MEDIUM → intentional, logged):** all read/write failures log
  `error_message` + `fix_suggestion` and return the unchanged persisted state, so
  the optimistic UI reconciles instead of crashing the home surface. Not silent.
- **No `any`:** all caught errors typed `unknown`; query results typed via
  `.returns<...>()`. PASS.
- **Hydrate effect dep `[stories]` (LOW):** `loadFeed` only sets a new array on
  success, so the effect won't loop. Accepted.
- **Idempotency:** the `uq_follow_user_story` unique constraint backs idempotent
  toggling; `toggleFollow` reads current state first then applies the inverse.

## Validation (commands + outcomes)
- `npx biome check --write src/lib/follows.ts src/components/reel/Reel.tsx tests/lib/follows.test.ts`
  → **PASS** (2 files auto-formatted to double quotes / 120-col, then clean).
- `npx tsc --noEmit` → **PASS** (exit 0, no errors).
- `npx vitest run tests/lib/follows.test.ts` → **PASS** (7/7).
- `npx vitest run tests/lib/reel tests/lib/feed` → **PASS** (26/26 — no regressions
  in the reel state machine / feed mapping).
- Migration `0005` cannot be unit-tested here; verified by inspection — parses
  sanely, FK type matches `stories.story_id` (`text`), RLS mirrors 0003's
  `player_signals` shape verbatim, additive/forward-only (reversible by drop).

## Definition of done — **PASS**
- Tapping follow inserts a `follows` row and flips to `follow-on`: test
  `toggleFollow … toggle ON` asserts one owner-scoped insert + returns `true`;
  `Reel.tsx` adds the id to the lifted set → `ReelChrome` renders `follow-on`. ✓
- Tapping again deletes it and reverts the accent: test `… toggle OFF` asserts
  `delete` called, `insert` not, returns `false` → set reconciles to off. ✓
- `isFollowing(story_id)` reflects the persisted row: test asserts
  true-when-present / false-when-absent. ✓

## Concerns for SP2 (read this — exact contract)
SP2's `agents/memory/session_processor.py` must join the new `follows` table to
`story_interests` → `user_interest_profile`. **Exact column names (verbatim):**

`follows` table:
- `follow_id` — uuid PK
- `follow_user_id` — uuid, FK → `auth.users(id)` (= `auth.uid()`, equals
  `users.user_id` / `user_interest_profile.profile_user_id`)
- `follow_story_id` — **`text`**, FK → `stories.story_id`
- `follow_created_at` — timestamptz
- unique `(follow_user_id, follow_story_id)`; index `idx_follows_user (follow_user_id)`

Join path for the boost:
`follows.follow_story_id` (text) = `story_interests.story_interest_story_id` (text)
→ `story_interests.story_interest_interest_id` (uuid) is the matched interest node
to boost on `user_interest_profile (profile_user_id = follows.follow_user_id,
profile_interest_id = story_interest_interest_id)`.

**Type gotcha for SP2:** both `follow_story_id` and `story_interest_story_id` are
`text` — no uuid cast needed on the story side. The user side is uuid throughout.
SP2 reads with the **service-role** key (bypasses RLS), so it sees all users'
follows — RLS only constrains the browser anon client.
