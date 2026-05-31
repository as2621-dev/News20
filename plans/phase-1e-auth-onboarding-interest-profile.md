# Phase 1e: Auth + chip onboarding + interest profile (+ migration 0003)

**Milestone:** M1 — Personalized audio-first karaoke reel MVP
**Status:** Not started
**Estimated effort:** L

## Goal
A new user signs in by **email magic-link**, taps through a **3-level interest chip tree** (no voice — chips only), with a free-text custom-interest chip and a per-interest **strict toggle** ("just give me cricket, nothing broader"), and lands with a persisted, RLS-scoped `user_interest_profile`. Ships the additive **migration 0003** (the user/personalization half of the schema + the two pipeline tables) so Phase 1d has interests/profiles to key on and Phase 1c has auth to gate on.

## Why this phase exists (re-scope context)
M1 was defined as an anonymous passive reel (master-plan §107–109); auth + onboarding + the user-side schema lived in M3. The owner re-scoped M1 to be **personalized end-to-end** (no anonymous reel) with **email magic-link auth now**. This phase pulls the minimum slice forward: a chip-based (voice-free) onboarding that writes the `user_interest_profile` the ranker reads. See `reference/ranking-spec.md` for what consumes this profile. *(Voice-agent onboarding was dropped 2026-05-30 — chips are the only onboarding, not a v1 stand-in.)*

## Context the sub-agents need
- **Schema is already designed** in `reference/supabase-schema.md` §3 + §6 (the `interests` tree, `users`, `user_interest_profile`, `user_interest_traits`, `player_signals`, RLS). This phase **applies** the M1 subset as migration `0003` and adds the two NEW pipeline tables (`story_interests`, `daily_feeds`) + three new columns. Content tables (0001/0002) are untouched — additive only.
- **Onboarding is chips, not voice.** `InterestChips` + lazy child expansion render the `interests` tree; there is no Gemini Live orb. *(Voice onboarding was dropped 2026-05-30 — Phase 3c is cancelled; chips are the final onboarding.)*
- **Auth = Supabase email OTP** (`signInWithOtp`), pulled forward from M3 Phase 3 SP2. The browser client (`src/lib/supabase/client.ts`) currently sets `persistSession:false, autoRefreshToken:false` — these must flip to `true` for an authed session to survive.
- **Static export reality:** no Next.js server runtime on device — auth + profile writes run **client-side directly against Supabase under RLS**.
- **Taxonomy seed:** ~7–10 depth-0 categories mapping to a `segment_slug` accent, each interest carrying an `interest_search_query` (the news query Phase 1d ingests on). Draft list + example chains below.

## Sub-phases

### Sub-phase 1: Migration 0003 — user + taxonomy schema (additive)
- **Files touched:** `supabase/migrations/0003_personalization_schema.sql`, `supabase/seed/interests.sql`.
- **What ships:** a forward-only additive migration adding — 2 enums (`interest_profile_source`, `player_signal_event`); 5 user-side tables (`users` + a `handle_new_user()` trigger mirroring `auth.users`, `interests`, `user_interest_profile`, `user_interest_traits`, `player_signals`); 2 NEW pipeline tables (`story_interests` M:N story↔interest with `story_interest_match_depth` + optional relevance; `daily_feeds` per-user `feed_date`/`feed_position`/`feed_score`/`feed_matched_interest_id`/`feed_slot_kind`); 2 ALTERs on `interests` (`interest_search_query text`, `interest_kind text default 'taxonomy'`) and 1 on `user_interest_profile` (`profile_is_strict boolean default false`); the §6 RLS policies (public-read `interests`/`story_interests`; owner-all `user_interest_profile`/`user_interest_traits`/`player_signals`; self `users`; **select-self-only** `daily_feeds`). Plus `interests.sql` seeding the ~7–10 depth-0 categories + example 3-level chains with search queries.
- **Definition of done:** the migration applies cleanly on a DB already holding 0001/0002 (all FKs resolve: `interests.interest_segment_slug→segments`, `story_interests.story_interest_story_id→stories`, `daily_feeds.feed_story_id→stories`/`feed_user_id→users`); a recursive CTE over seeded `interests` returns the 3-level chains; inserting an `auth.users` row creates the matching `users` row via the trigger; an **anon** `SELECT` on `interests`/`story_interests` succeeds while an anon `SELECT` on another user's `user_interest_profile`/`daily_feeds` returns **zero rows** (RLS allow/deny assertion). ⚠ irreversible (forward-only migration; additive, no drops).
- **Dependencies:** Phase 1b (0001/0002 applied).

### Sub-phase 2: Email magic-link sign-in
- **Files touched:** `src/components/onboarding/EmailSignIn.tsx`, `src/lib/supabase/auth.ts`, `src/lib/supabase/client.ts` (auth config: `persistSession:true`, `autoRefreshToken:true`, `detectSessionInUrl:true`), `src/app/(auth)/callback/page.tsx`.
- **What ships:** an email field → `supabase.auth.signInWithOtp({ email })` → "check your inbox" state (states: empty / invalid / sending / sent / error), and the magic-link callback that establishes the session.
- **Definition of done:** a valid email calls `signInWithOtp` (asserted against a mocked client) and renders the "sent" state; an invalid email renders an inline error and never calls the API (Rule 12); completing the callback yields a session whose `auth.uid()` matches a `users` row (the SP1 trigger). No secrets logged.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: 3-level interest chips + custom interest + strict toggle
- **Files touched:** `src/components/onboarding/InterestChips.tsx`, `src/components/onboarding/CustomInterestChip.tsx`, `src/lib/interests.ts`.
- **What ships:** `InterestChips` rendering the `interests` tree with **lazy child expansion** (tap a depth-0 chip → fetch depth-1 → depth-2 via the recursive read in `src/lib/interests.ts`); a **free-text "custom interest" chip** producing a pending `interest_kind='custom'` selection; a per-interest **strict toggle** ("just give me cricket, nothing broader") setting a pending `profile_is_strict`. No voice/orb (dropped 2026-05-30).
- **Definition of done:** chips render the seeded depth-0 categories; tapping reveals depth-1 then depth-2 (asserted against mocked `interests` data); toggling strict on a chip marks that selection strict; a custom free-text entry yields a pending custom selection. No write happens yet (SP4 persists).
- **Dependencies:** Sub-phase 1.

### Sub-phase 4: Persist interest profile + onboarding flow order
- **Files touched:** `src/components/onboarding/OnboardingFlow.tsx`, `src/components/onboarding/OnboardingSplash.tsx`, `src/lib/onboardingProfile.ts`, `src/app/(onboarding)/page.tsx`.
- **What ships:** upsert handlers writing selected leaves (with `profile_is_strict`) to `user_interest_profile` (`profile_source='typed'`, default `profile_weight` by selection) scoped to `auth.uid()`, plus `user_interest_traits` defaults (**now deprecated** — voice onboarding dropped; the table is retained in the DB but unused); custom free-text is **canonicalized** into the tree (matched to an existing node or attached as a typed custom entry, never a dangling row — Rule 12); the flow order **splash → email sign-in → interest chips → loading → reel**; `users.user_onboarded_at` set on completion; a returning onboarded user skips straight to the reel.
- **Definition of done:** completing onboarding writes ≥1 `user_interest_profile` row scoped to `auth.uid()` with strict flags preserved (asserted against mocked Supabase); `user_onboarded_at` is set; an onboarded user routes to the reel and an authed-but-un-onboarded user routes to chips; a custom interest with no taxonomy match is stored as a typed custom entry, never orphaned.
- **Dependencies:** Sub-phases 1–3.

## Phase-level definition of done
A signed-out user requests a magic link, lands authenticated with a `users` row, taps a 3-level chip interest profile (with custom interest + per-interest strict toggles), and that `user_interest_profile` + traits persist RLS-scoped to their `auth.uid()`; migration 0003 (incl. `story_interests`, `daily_feeds`, the three new columns, RLS) is live and the taxonomy is seeded with per-interest search queries. **Validated by:** migration-applies + recursive-CTE assertion; RLS allow/deny; `signInWithOtp` mock assertion; chip lazy-expansion test; the profile-write test (strict preserved, no orphan custom rows).

## Proposed depth-0 taxonomy (seed)
~7–10 chips; the `segment_slug` enum is **not** expanded (it drives reel accents) — multiple interests map to one accent, `wildcard` is the long-tail catch-all.

| `interest_slug` (label) | `interest_segment_slug` (accent) |
|---|---|
| `world` (World & Politics) | geopolitics |
| `business` (Business & Markets) | markets |
| `tech` (Tech & Science) | tech |
| `sport` (Sport) | sport |
| `health` (Health & Wellbeing) | wildcard |
| `entertainment` (Entertainment & Culture) | wildcard |
| `climate` (Climate & Environment) | geopolitics |
| `lifestyle` (Lifestyle & Travel) | wildcard |
| `crypto` (Crypto & Web3) | markets |
| `science` (Space & Hard Science) | tech |

Example 3-level chains (each with an `interest_search_query`): Sport→Cricket→India (`"India cricket team BCCI news"`); Sport→Soccer→Arsenal (`"Arsenal FC news Premier League"` — the ancestor-tag demo); Business→Equities→Semiconductors (`"semiconductor stocks NVIDIA TSMC news"`); Tech→AI→LLMs (`"large language models OpenAI Anthropic news"`).

## Out of scope
- Voice onboarding / Gemini Live orb — **dropped 2026-05-30** (Phase 3c cancelled). Onboarding is chip-only; the surviving in-news Voice mode lives in Phase 3b.
- `onboarding_conversations` table — **dropped** (voice onboarding cut; never created). `user_interest_traits` + the `'voice'` `interest_profile_source` value shipped in migration 0003 (applied) but are now deprecated/unused; retained in the DB (not un-migrated), per the keep-DB-as-is decision.
- `follows` / `saves` / `play_sessions` tables (Phase 3d / M3 / M4).
- The daily pipeline that fills `daily_feeds` (Phase 1d) and the reel read of it (Phase 1c SP4).
- Applying/seeding migration 0003 against a live DB (needs creds; done at `/run-phase` time — same blocker noted in `phase-1b-supabase-backend-seed-progress.md`).

## Open questions
1. **Default `profile_weight` by selection depth:** does a depth-2 leaf pick start heavier than a depth-0 category pick? Recommend a simple constant per depth, tuned later via `reference/ranking-spec.md`.
2. **Custom-interest canonicalization:** match free-text to the nearest taxonomy node (embedding/LLM) vs store as a flat custom node with its own search query. Recommend flat custom node for v1 (simplest, still ingestable); revisit if customs proliferate.
3. **Min viable profile size** for Phase 1d to produce a non-empty feed — enforce a "pick at least 1" gate in SP4 (sparse-profile fallback in ranking-spec §3 covers the rest).

## Self-critique

**Product lens:** PASS — delivers the owner's "personalized from day one" directive with the lowest-friction onboarding that still feeds the ranker (chips, not voice). Strict toggle + custom interest are exactly the controls the owner asked for. No M2/M3 surface creep (no follow, no voice, no detail).

**Engineering lens:** PASS — the chip-tap onboarding writes `user_interest_profile` through one upsert path (`onboardingProfile.ts`). Migration 0003 is additive-only and applies the already-designed DDL (conforms to `supabase-schema.md`, doesn't reinvent). DoDs are mock-verifiable in fresh context. The `client.ts` auth-config flip is called out explicitly (a silent `persistSession:false` would break every authed read downstream).

**Risk lens:** PASS with flags. ⚠ SP1 is a forward-only migration (additive, no drops — reversible only by a new down-migration; flagged for a disposable DB first). Within-phase file overlap: `OnboardingFlow.tsx` is touched by SP4 only; `client.ts` by SP2 only — no parallel-edit conflict. RLS allow/deny is a first-class DoD (a leaked `daily_feeds`/profile is the worst failure here). Painting-into-a-corner check: SP1 schema → SP2 auth → SP3 chips → SP4 persist leaves a profile Phase 1d reads directly.

**Irreversible sub-phases:** SP1 (forward-only migration + seed).
