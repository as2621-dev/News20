# Phase 7b: Onboarding first-run feed + partial UX

**Milestone:** M7 — Production feed automation & first-run onboarding feed
**Status:** Not started
**Estimated effort:** M

## Goal
A user who finishes onboarding immediately gets a feed built from the **existing** catalog (partial allowed — e.g. 24/30 when their niches aren't all covered yet), sees a "showing the past 24 hours — n/30, full 30 tomorrow" banner on day one, and the finite loop ends with the correct "come back tomorrow" copy.

## Context
- Onboarding ends in `BuildYour30.handleSave` → `saveUserFeedAllocation(segments)` → `onDone()` → `OnboardingFlow.handleBuildDone` → `router.push("/")`. The loading copy is already **"SETTING UP YOUR BRIEFINGS…"** (keep it — no "2 hours" message, per owner call).
- Feed resolution: `getReelFeed(feedDate?)` in `src/lib/feed/index.ts` → per-user `daily_feeds` row, else global seeded feed. `FEED_TOTAL = 30` (`src/lib/reel/feedBriefing.ts`); reads already `.slice(0, FEED_TOTAL)` and gracefully serve fewer rows.
- `AllCaughtUp.tsx` already exists ("You're all caught up." + `30 / 30 · DONE`).
- Depends on Phase 7's `POST /feed/assemble-for-user`.

## Revision 2026-06-16 (security-driven)
Phase 7's CSO pass (`.agents/cso-findings/phase-7-pipeline-http-seam.md`) flagged that the client must NOT hold the shared `PIPELINE_TRIGGER_SECRET` that guards the service-role `/feed/assemble-for-user`. Owner chose the **JWT-scoped worker endpoint** option. So Sub-phase 1 now adds a new endpoint authenticated by the **user's own Supabase access token** (user_id derived from the verified token, never the body), and the client calls *that* with its session — no shared secret in the SPA/Capacitor bundle. Old SP2 (metadata) + SP3 (banner) are merged into one sub-phase to keep the count at 4.

## Sub-phases

### Sub-phase 1: JWT-scoped `/feed/assemble-mine` worker endpoint
- **Files touched:** `agents/worker/pipeline_routes.py`, `tests/agents/worker/test_pipeline_routes.py`
- **What ships:** `POST /feed/assemble-mine` — authenticated by the **caller's Supabase access token** (`Authorization: Bearer <supabase_jwt>`), verified via the Supabase client (`auth.get_user(token)`; 401 on missing/invalid/expired). It derives `user_id` from the verified token (**never** from the body — body carries only optional `feed_date`, default today UTC) and runs the existing `_assemble_for_user(user_id, feed_date)`. This endpoint does **NOT** use the shared `PIPELINE_TRIGGER_SECRET` guard (it has its own JWT dependency), so it is safe to call from the client. Returns the same `AssembleFeedResponse`.
- **Definition of done:** `pytest` — a request with no/invalid JWT → 401; a request with a valid JWT (Supabase `auth.get_user` mocked to return a user) assembles **that** user's feed (asserts `_assemble_for_user` called with the token's user_id, not any body value) and returns 200 with the count; a body attempting to pass a different `user_id` is ignored (no such field is honoured). Ruff clean; existing 21 worker tests stay green.
- **Dependencies:** none _(extends Phase 7's committed `pipeline_routes.py`; reuses `_assemble_for_user`)_

### Sub-phase 2: Client first-run call at onboarding completion
- **Files touched:** new `src/lib/feed/assembleFirstRunFeed.ts`, `src/components/onboarding/BuildYour30.tsx`, `src/components/onboarding/OnboardingFlow.tsx`
- **What ships:** `assembleFirstRunFeed(feedDate)` POSTs to `/feed/assemble-mine` with the **current Supabase session access token** (read from the existing supabase client — no shared secret) and returns `{ allocated_count }`. `handleSave` calls it **after** `saveUserFeedAllocation` succeeds, while "SETTING UP YOUR BRIEFINGS…" shows; on success a first-run flag keyed to the feed date is persisted for the reel. A worker failure is non-fatal — onboarding still routes to `/` (global-feed fallback).
- **Definition of done:** `vitest` — `handleSave` invokes `assembleFirstRunFeed` with today's feed date and the session token (fetch + supabase session mocked); a rejected call still calls `onDone()`/routes to `/`; the first-run flag is persisted only on success.
- **Dependencies:** Sub-phase 1 _(endpoint must exist)_

### Sub-phase 3: Partial-feed metadata + "past 24 hours" banner
- **Files touched:** `src/lib/feed/index.ts`, `src/lib/feed/supabaseFeed.ts`, `src/types/feed.ts`, new `src/components/blip/reel/FirstRunBanner.tsx`, `src/components/blip/reel/BlipReel.tsx`
- **What ships:** `getReelFeed` exposes feed meta `{ allocated_count, feed_total: 30, is_partial: count < 30, is_first_run }` (first-run derived from SP2's per-date flag); when `is_first_run && is_partial` the reel shows a dismissible banner **"Showing you the past 24 hours — {n}/30. Your full 30 land tomorrow."** (shown once; dismiss persists; never on a full or non-first-run feed).
- **Definition of done:** `vitest`/RTL — a 24-row first-run feed → `meta.is_partial === true` and the banner renders with `24/30`; a 30-row feed → `is_partial === false` and no banner; dismiss hides + persists. Type-check passes with the extended `feed.ts`.
- **Dependencies:** Sub-phase 2 _(reads the first-run flag)_

### Sub-phase 4: End-screen copy
- **Files touched:** `src/components/reel/AllCaughtUp.tsx`, the existing AllCaughtUp test
- **What ships:** body copy → **"You're all caught up. We'll see you tomorrow with your 30 stories, 30 reels."** Counter still renders `{n} / {n} · DONE`.
- **Definition of done:** component test asserts the new copy string is present and the old "That's the whole world today…" string is gone; counter assertion still passes.
- **Dependencies:** none

## Phase-level definition of done
A newly-onboarded user (worker reachable) lands on a feed assembled from the existing catalog via the **JWT-scoped** endpoint (their session token only — no shared secret in the client): if fewer than 30 stories match, the reel plays the available n and shows the "past 24 hours — n/30" banner; finishing shows "We'll see you tomorrow with your 30 stories, 30 reels." With the worker unreachable, onboarding still completes and falls back to the global feed (no crash). The shared `PIPELINE_TRIGGER_SECRET` never appears in client code.

## Out of scope
- Building or changing the assemble endpoint (Phase 7).
- The daily/midnight regeneration that makes tomorrow's feed full 30/30 (Phase 7c).
- Any change to dislikes, company-follows, or the "SETTING UP YOUR BRIEFINGS…" copy (all unchanged per owner call).

## Open questions
- Where should the first-run flag + allocated count live so the reel can read it on the next route — `localStorage` keyed by `feed_date`, or re-derived purely from the row count returned by `getReelFeed` (count < 30 ⇒ partial, plus a "first session today" check)? _(Recommendation: prefer re-deriving `is_partial` from the row count and gate `is_first_run` on a lightweight per-date local flag set in SP1, to avoid trusting a stale stored count.)_

## Self-critique

**Product lens:** PASS. Directly delivers the M1 true-when ("a new user can sign in, pick interests, and passively listen to ~20–30 fresh daily stories chosen for them") and the brief's finite "30 = caught up" loop. The partial-feed handling is the honest fix for the day-one cold-start (a brand-new user's niche interests may have <30 ready stories) — no scope creep beyond the owner's stated asks.

**Engineering lens:** PASS with notes. Stack-aligned (Next/React/TS, vitest). DoDs are checkable (rendered strings, row→meta mapping, mock call assertions). Sub-phase 4 (end copy) is intentionally last and independent so it locks in nothing for 1–3. SP1 (onboarding) and SP2 (feed lib/types) touch disjoint files and can run in parallel; SP3 depends on SP2's meta; only the cross-phase dependency on Phase 7 SP3 must be respected.

**Risk lens:** PASS. File boundaries are disjoint across sub-phases (onboarding vs feed-lib vs reel vs end-screen). Each sub-phase has a test DoD that fails on wrong business behavior (partial vs full banner logic, non-fatal failure path), not just compile (Rule 9). Reversibility: pure frontend + a network call — fully reversible, no migrations. Painting-into-corner check: 1→2→3→4 leaves a consistent state; SP4 works regardless of the others.

**Irreversible sub-phases:** none.
