# Phase 1e — Sub-phase 4 execution report

**Sub-phase:** Persist interest profile + onboarding flow order
**Status:** COMPLETE (with one flagged path divergence + one documented DoD caveat)
**Date:** 2026-05-30

## What was implemented

The integration layer that turns SP2 (email auth) + SP3 (interest chips) into a complete onboarding flow that persists an RLS-scoped `user_interest_profile`.

1. **`src/lib/onboardingProfile.ts`** — the SHARED upsert path (M3 voice reuses it; `profile_source` parameterized, default `"typed"`).
   - `persistInterestProfile(userId, selection, opts?, client?)` → `{ persisted_count, unpersisted_customs }`.
   - Taxonomy picks → upsert `user_interest_profile` on `onConflict: "profile_user_id,profile_interest_id"`, with `profile_is_strict` **preserved** and `profile_weight` from the exported const **`PROFILE_WEIGHT_BY_DEPTH = {0:1.0, 1:1.5, 2:2.0}`** (Open Q1; JSDoc "tunable, see ranking-spec §1").
   - Customs → canonicalize via case-insensitive `interest_label`/`interest_slug` `ilike` lookup. MATCH → upsert profile row to that node. NO MATCH → collected into `unpersisted_customs`, **never** written as an orphan row (RLS forbids client-side `interests` inserts).
   - Upserts a default `user_interest_traits` row (`onConflict: "traits_user_id"`) and stamps `users.user_onboarded_at = now()`.
   - Structured logging, no PII/token logging, typed errors thrown (never swallowed — Rule 12).
2. **`src/components/onboarding/OnboardingSplash.tsx`** — `"use client"` brand splash (BlipLogo glow, product promise, "Get started"), reel visual register.
3. **`src/components/onboarding/OnboardingFlow.tsx`** — `"use client"` state machine: `splash → email → wait_session → chips → loading → reel`. Advances on `EmailSignIn.onSent`; waits for a session via `getCurrentSession` + `onAuthStateChange`; "Continue" CTA gated by **"pick ≥1"** (Open Q3); calls `persistInterestProfile` then `router.push("/")`; surfaces `unpersisted_customs` as an inline note (Rule 12). `window`-guarded, no `useSearchParams`.
4. **`src/app/(onboarding)/onboarding/page.tsx`** — `"use client"` route: on mount checks session + `users.user_onboarded_at`; already-onboarded → `router.replace("/")`; else renders `OnboardingFlow`. Does NOT gate the ROOT. `window`-guarded.
5. **`tests/lib/onboardingProfile.test.ts`** — 6 boundary-mocked tests (see Validation).

## Files touched (ONLY the allowed set — confirmed)

- `src/lib/onboardingProfile.ts` (new)
- `src/components/onboarding/OnboardingSplash.tsx` (new)
- `src/components/onboarding/OnboardingFlow.tsx` (new)
- `src/app/(onboarding)/onboarding/page.tsx` (new — see DIVERGENCE 1 re: path)
- `tests/lib/onboardingProfile.test.ts` (new)

Verified by mtime: SP1/SP2/SP3 files (`client.ts`, `auth.ts`, `interests.ts`, `EmailSignIn.tsx`, `InterestChips.tsx`, `CustomInterestChip.tsx`, `callback/page.tsx`) retain their pre-session timestamps (20:13–20:24); only the 5 files above carry this session's timestamps (20:30–20:33). `src/app/page.tsx` (reel home) untouched. Nothing under `plans/`, `reference/`, `supabase/` touched.

## Divergences

**DIVERGENCE 1 (path — REQUIRES ORCHESTRATOR AWARENESS).** The scope-lock + phase file both name the route file `src/app/(onboarding)/page.tsx`. That path is **broken**: a Next.js route group `(onboarding)` adds NO URL segment, so `src/app/(onboarding)/page.tsx` resolves to `/` — colliding with the existing reel home `src/app/page.tsx`. Confirmed empirically: `next build` fails with a duplicate-`/` prerender error when the file is at the named path, and builds cleanly once relocated. Since the phase mission text explicitly calls it "the `/onboarding` route", I placed it at **`src/app/(onboarding)/onboarding/page.tsx`** so it resolves to `/onboarding` while staying inside the whitelisted `(onboarding)` route group. This is the minimal change that honors the stated intent; I did NOT edit `src/app/page.tsx`. Flagging per Rule 7/12 — orchestrator should confirm `/onboarding` is the intended URL (it is, per the phase file).

**DIVERGENCE 2 (toast → inline note).** `sonner` is a dependency but its `<Toaster>` must be mounted in `layout.tsx` (out of scope). I surfaced `unpersisted_customs` as an inline note on the `loading` step instead of a toast — same Rule-12 guarantee (not silently dropped), zero out-of-scope edits.

**DIVERGENCE 3 (extra `wait_session` step + canonicalization-error test).** Added an internal `wait_session` step (the magic-link round-trip means a session won't exist the instant `onSent` fires) and a 6th test asserting a canonicalization read-error throws rather than silently dropping a custom. Both strengthen Rule 12; neither expands scope.

## Self-review findings + fixes

- **Route collision** (critical) — found via build; fixed by relocating to `/onboarding` segment (Divergence 1).
- **`or`-filter injection** (medium) — a custom label containing `,()*` could break the PostgREST `or` filter; fixed by stripping those metacharacters into spaces before building the filter (`findCanonicalInterest`).
- **Unused `vi` import** (lint) — removed after the fake client ended up not needing `vi.fn()`.
- No `any` without justification; the test's `as never` mirrors the established `supabaseFeed.test.ts` boundary pattern. Double quotes throughout (Biome clean).

## Validation output

- `npm run lint` (biome check .) → **0 errors** (48 files).
- `npx tsc --noEmit` → **0 errors**.
- `npx vitest run` → **95 passed (11 files)**, including the 6 new tests:
  - (a) ≥1 `user_interest_profile` row scoped to the user, `profile_source="typed"`, **strict preserved**;
  - (b) `profile_weight` follows `PROFILE_WEIGHT_BY_DEPTH` (asserts depth-0 ≠ depth-2 → fails if flattened);
  - (c) a custom MATCHING an existing node persists to that `interest_id`;
  - (d) a NO-MATCH custom is returned in `unpersisted_customs` and **no `user_interest_profile` upsert is issued** (orphan-prevention);
  - (e) `users.user_onboarded_at` update issued, scoped `user_id = userId`;
  - (f) a canonicalization read-error throws (no silent drop).
- `npx next build` → **static export compiles**; `/onboarding` generates as a static page (19 kB). Routes: `/`, `/_not-found`, `/callback`, `/onboarding`.

## Definition of done — PASS (with one documented caveat)

| DoD clause | Result |
|---|---|
| Completing onboarding writes ≥1 `user_interest_profile` row scoped to `auth.uid()` with strict preserved (mocked) | **PASS** (test a) |
| `user_onboarded_at` set | **PASS** (test e) |
| An onboarded user routes to the reel | **PASS** — `(onboarding)` page `router.replace("/")` on `user_onboarded_at` present |
| Flow gates un-onboarded users into chips | **PASS** — flow renders for no-session / not-onboarded; "pick ≥1" gate before persist |
| Custom canonicalized to an existing node | **PASS** (test c) |
| NO-MATCH custom surfaced as unpersisted, NOT orphaned | **PASS** (test d) |
| Creating brand-new custom taxonomy nodes | **DEFERRED CAVEAT** (see below) |

**Documented custom-node caveat (cannot satisfy client-side):** Migration 0003 makes `interests` public-read with **no insert policy**, so the authed browser client physically cannot create a new taxonomy node for a novel custom interest (a `401`). v1 therefore canonicalizes customs to existing nodes and surfaces unmatched ones as `unpersisted_customs` rather than faking a write or orphaning a row. Seeding genuinely new custom nodes (with an `interest_search_query`) needs a **service-role pipeline / migration follow-up**. Reported plainly, not hidden — this is the one DoD nuance not fully satisfiable from the client.

## Concerns for the orchestrator + Phase 1c

1. **CONFIRM Divergence 1** — the route lives at `src/app/(onboarding)/onboarding/page.tsx` (URL `/onboarding`), not the literally-named `src/app/(onboarding)/page.tsx` (which collides with `/`). The orchestrator's scope diff will see this path; it is intentional and the only buildable option. If a bare `/onboarding`-group with no nested segment is ever wanted, the reel home would have to move out of the root — out of SP4 scope.
2. **Phase 1c owes the ROOT auth-gate.** SP4 only implements the inverse (onboarded users on `/onboarding` skip to `/`). The app-wide gate — an un-onboarded/signed-out user landing on `/` (the reel) being redirected INTO `/onboarding` — is explicitly Phase 1c's job and was NOT implemented here. Until 1c adds it, `/` renders the reel for everyone regardless of onboarding state.
3. **Service-role custom-node seeding** — the deferred caveat above is a real product gap (a user typing a genuinely novel interest gets a "we'll add it soon" note, not a personalized slot). Needs a pipeline follow-up; surfaced for backlog.
4. **No DB integration run** — all persistence is mock-verified (consistent with the phase's "applying 0003 needs creds at run-phase time" note). The upsert column names / `onConflict` targets are transcribed from migration 0003 but have not been exercised against a live RLS'd DB.
```
