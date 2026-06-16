# Phase 7b — Sub-phase 2 execution report: Client first-run call at onboarding completion

**Status:** SUCCESS

## What was implemented
A new client module `assembleFirstRunFeed.ts` that, right after onboarding's "Build
your 30" allocation persists, POSTs to the JWT-scoped worker endpoint
`POST /feed/assemble-mine` carrying the user's OWN Supabase session access token
(`Authorization: Bearer <jwt>`). It returns `{ allocated_count }`. The shared
`PIPELINE_TRIGGER_SECRET` is never referenced in client code — only the session token.

`BuildYour30.handleSave` now calls it AFTER `saveUserFeedAllocation` succeeds (while
"SETTING UP YOUR BRIEFINGS…" / the `loading` step shows), persists a per-date first-run
flag ONLY on success, and treats any worker failure as NON-FATAL — onboarding still
calls `onDone()` (→ `OnboardingFlow.handleBuildDone` → `router.push("/")`, the
global-feed fallback).

## Files created / modified
- **NEW** `src/lib/feed/assembleFirstRunFeed.ts` — `assembleFirstRunFeed(feedDate?, fetchImpl?)`, `todayUtcFeedDate()`, `firstRunFlagKey(feedDate)`, `markFirstRunFeed(feedDate)`.
- **MODIFIED** `src/components/onboarding/BuildYour30.tsx` — import + 17-line non-fatal first-run block inside `handleSave`'s success branch.
- **NEW** `tests/lib/feed/assembleFirstRunFeed.test.ts` — 8 module unit tests.
- **NEW** `tests/lib/onboarding/buildYour30FirstRun.test.tsx` — 3 `handleSave` component tests (the DoD).

`OnboardingFlow.tsx` was listed as a touched file but **left UNCHANGED** (divergence below).

## Exact contract chosen (SP3 MUST match)
- **Endpoint:** `POST {NEXT_PUBLIC_QA_API_BASE_URL}/feed/assemble-mine` (same env var every other client→worker call uses; empty → same-origin relative path).
- **Auth:** `Authorization: Bearer <supabase session access_token>` (read via `getCurrentSession()` from `@/lib/supabase/auth`). No `user_id` in the body, no shared secret.
- **Request body:** `{ "feed_date": "YYYY-MM-DD" }` (default = today UTC via `todayUtcFeedDate()`).
- **Response field consumed:** `allocated_count` (worker also returns `feed_total`; SP2 only needs `allocated_count`).
- **First-run flag key:** `blip:first-run:<feed_date>` → value `"1"` in `localStorage`. Exported as `firstRunFlagKey(feedDate)` from `assembleFirstRunFeed.ts`. Set ONLY on a successful assembly.

## Divergences (+ why)
- **`OnboardingFlow.tsx` not modified.** Its `handleBuildDone` already does
  `router.push("/")` and the first-run logic belongs entirely in `BuildYour30.handleSave`
  (where the persist + assemble + flag sequence lives). Touching it would be a no-op
  edit (Rule 3 — surgical). The non-fatal route-to-`/` is already satisfied by the
  existing `onDone` → `handleBuildDone` wiring.
- **Module throws on failure (rather than returning a sentinel).** Mirrors the phase's
  "non-fatal" intent by pushing the decision to the caller's try/catch: `handleSave`
  swallows the throw and still calls `onDone()`. A no-session is also a throw (Rule 12 —
  fail loud, not a silent skip), which the caller treats identically to any worker error.
- **Test localStorage stub.** jsdom's native `localStorage` is non-functional in this
  repo's vitest setup; reused the in-memory stub pattern already present in
  `tests/lib/onboardingProfile.test.ts`.

## Review findings + fixes (Step B/C)
Self-review against the checklist — no critical/high issues:
- **onDone always called** on assemble failure — PASS (inner try/catch; outer catch is only for the `saveUserFeedAllocation` failure, which correctly surfaces the user's lost work). Verified by the rejecting test.
- **Token from session, not body** — PASS (`session.access_token` → Bearer header; body is `{ feed_date }` only). Test asserts request never contains `user_id`.
- **No shared secret** — PASS (only mention of `PIPELINE_TRIGGER_SECRET` is the JSDoc stating it is never present).
- **No `any`** — PASS (module uses `unknown` + narrowing; the only casts are `as unknown as typeof fetch` in tests).
- **Flag persisted only on success** — PASS (`markFirstRunFeed` is inside the inner `try` after a resolved assemble; not reached on reject). Asserted by the "ONLY on success" test.
- **Fixes applied:** one biome line-length wrap auto-fixed (`--write`). No logic changes.

## Validation results (Step D)
```
$ npx vitest run tests/lib/feed/assembleFirstRunFeed.test.ts tests/lib/onboarding/buildYour30FirstRun.test.tsx
 Test Files  2 passed (2)
      Tests  11 passed (11)
```
```
$ npx tsc --noEmit   (count of "error TS" lines)
0
```
(0 total TS errors — well within scope; no pre-existing remotion errors surfaced in this run either.)
Biome: `Checked 4 files … Fixed 1 file.` then clean.

## Definition of done (Step E) — PASS
- `handleSave` invokes `assembleFirstRunFeed("2026-06-16")` AFTER `saveUserFeedAllocation` (call-order asserted) and the session token is the auth (module-level test asserts `Authorization: Bearer <access_token>`) — PASS.
- A rejected `assembleFirstRunFeed` still calls `onDone()` (route to `/`) — PASS.
- The first-run flag is persisted ONLY on success (`markFirstRunFeed` NOT called when the assemble rejects) — PASS.

## Concerns / handoff
**For SP3 (must match exactly):**
- Read the first-run flag with `firstRunFlagKey(feedDate)` (exported from `src/lib/feed/assembleFirstRunFeed.ts`) → key `blip:first-run:<feed_date>`, value `"1"`.
- Derive `is_first_run = localStorage.getItem(firstRunFlagKey(feedDate)) === "1"` for the **feed_date currently being shown**. Per the phase's Open-questions recommendation, derive `is_partial` from the row count (`allocated_count < 30`), NOT from a stored count — the flag is a boolean presence marker only, it does NOT store the count.
- The flag is keyed by feed date, so it naturally stops gating the banner on subsequent days. SP3's dismiss persistence should use its own key (don't clear `blip:first-run:*`, or the banner state is lost across a reload within the same day).

**For the orchestrator:**
- `OnboardingFlow.tsx` is intentionally unchanged — if the phase DoD literally requires a diff there, flag it; functionally the non-fatal route-to-`/` already holds via the existing `onDone`.
- The module sends `feed_date` always (never omitted); SP1's route defaults it server-side too, so either is safe.

---
**Return to orchestrator:**
1. STATUS: SUCCESS
2. Files: `src/lib/feed/assembleFirstRunFeed.ts`, `src/components/onboarding/BuildYour30.tsx`, `tests/lib/feed/assembleFirstRunFeed.test.ts`, `tests/lib/onboarding/buildYour30FirstRun.test.tsx`
3. Validation: PASS — `Test Files 2 passed (2) / Tests 11 passed (11)`; `tsc --noEmit` → 0 errors
4. Definition of done: PASS (all 3 DoD assertions green)
5. Flag key: `blip:first-run:<feed_date>` (value `"1"`, success-only); endpoint `POST /feed/assemble-mine`, Bearer session token, body `{ feed_date: "YYYY-MM-DD" }`, response field consumed `allocated_count`.
