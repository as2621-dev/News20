# Phase SP4 · Sub-phase 2 — Gate the saved allocation against current backing

**Status:** SUCCESS

## What changed

### Task 1 — Saved-allocation backing filter (the resurrection gap)

`src/components/onboarding/BuildYour30.tsx`, the mount seed effect (~200–270):

- After reading `getUserFeedAllocation()`, the saved segments are now **filtered through
  `allowedBucketsForSelections(selectedCategoryBuckets, followedSourceBuckets)`** (the user's
  CURRENT backing) before `setSegments`. A saved category with no live interest/source backing
  (e.g. a stale `sport`) is **dropped** — it can no longer resurrect the phantom block SP1 closed.
- **Where + why:** the filter lives in the **component effect**, NOT `feedAllocation.ts`.
  The gate needs the user's in-memory props (`selectedCategoryBuckets`/`followedSourceBuckets`),
  which `getUserFeedAllocation` has no access to — it's a pure, prop-agnostic Supabase loader.
  Putting the filter there would force threading UI backing into the data layer, breaking its
  single responsibility. So `feedAllocation.ts` was **left untouched** (the plan's "load path"
  note is satisfied by gating the load's RESULT at the seed site).
- **Empty-after-filter → empty state persists:** if filtering leaves zero blocks AND there's no
  live signal, `segments` stays `[]`, so SP1's "pick interests first" empty state holds (no
  fallback to the full default seed). Logged `build_your_30_saved_dropped_to_empty`.
- **Legacy non-30 guard preserved:** a RAW saved set whose total ≠ 30 (a partial/pre-0010 row
  set) is still ignored entirely (early `return`), unchanged from before. The filter's
  under-30 result is the INTENDED "Fill N more" state, distinct from a corrupt legacy save.
- **Structured log on drop:** `build_your_30_saved_category_dropped` fires with
  `dropped_bucket_ids` + `dropped_count` + `fix_suggestion`. The adopt log
  (`build_your_30_seeded_from_saved`) now also carries `dropped_count` + `total_slots`.
- The run-once effect now reads the backing props, so a documented
  `biome-ignore lint/correctness/useExhaustiveDependencies` was added (the `hasSeededRef`
  guard makes it run-once; it intentionally reads mount-time backing — both callers fix those
  props before mount).

### Task 2 — Empty-state CTA routes to the picker

- Added optional prop **`onPickInterests?: () => void`** to `BuildYour30Props`.
- The empty-state CTA (`#pickInterestsCta`) now uses `pickInterestsHandler = onPickInterests ?? onSkip`
  — prefers routing BACK to the picker, falls back to `onSkip` (onward), then to no button when
  neither is set (the embedded "Thirty" tab, which has no picker step).
- **Parent wiring** — `src/components/onboarding/OnboardingFlow.tsx`: the `build`-step
  `<BuildYour30>` now passes `onPickInterests={() => setStep("picker")}`. The picker is a real
  step in the SAME state machine (`OnboardingStep` includes `"picker"`), so this literally
  returns the user to the interest picker — closing SP1's "CTA goes onward, not to picker"
  divergence.

## Files modified

- `src/components/onboarding/BuildYour30.tsx` (production — saved filter + `onPickInterests`)
- `src/components/onboarding/OnboardingFlow.tsx` (production — `onPickInterests` wiring)
- `tests/lib/onboarding/buildYour30SavedGate.test.tsx` (new — DoD test)

`src/lib/feedAllocation.ts` was **NOT** modified (decision above). `feedBuckets.ts` and
`feed_assembly.py` untouched (out of scope).

## "Fill N more" / no-rescale behavior for dropped categories

Dropped categories' slots are **not** redistributed (owner decision 2026-06-18, open Q2 LOCKED).
After the filter, the kept blocks total UNDER 30, so `slotsLeft > 0` and the existing budget CTA
renders `Fill ${slotsLeft} more` (disabled until refilled to 30) — the same no-auto-rescale path
`buildSegmentsForSelections` already produces for first-run users. The screen lands in a savable
"fill the rest" state; nothing crashes and the user reconstitutes the 30 by hand. Verified by the
drop test (4 backed blocks × counts summing to 24 → "Fill 6 more").

## Divergences

None material. SP1's CTA-destination divergence is now RESOLVED (parent wires
`onPickInterests={() => setStep("picker")}`). The only design choice worth flagging: the filter
was placed in the component, not `feedAllocation.ts` (justified above — keeps the data loader pure).

## Code review findings + fixes

- **[High] Saved seed was unfiltered** — the core gap; fixed by the backing filter. Mutation-checked:
  reverting to `setSegments(saved)` makes the drop test FAIL (Rule 9).
- **[Medium] Legacy non-30 guard could be lost** — re-adding the filter risked dropping the old
  "ignore a partial/legacy save" protection. Fixed: the non-30 early-return now guards the RAW
  saved set BEFORE filtering, so a corrupt legacy save is still ignored while a deliberately
  under-30 filtered result is kept ("Fill N more").
- **[Low] exhaustive-deps lint** on the now-prop-reading run-once effect — documented
  `biome-ignore` (run-once mount seed; reads mount-time backing intentionally).
- **[Low] CTA backward-compat** — `onPickInterests ?? onSkip ?? (no button)` keeps the embedded
  "Thirty" tab (passes neither) and SP1's "CTA calls onSkip" test both green.

## Validation

- `npx tsc --noEmit` — green.
- `npm run build` — green (Next static export, 4 routes incl. `/onboarding`).
- `npx biome check` on both touched production files — clean.
- `npx vitest run tests/lib/onboarding/` — **27/27 pass** (SP1's 25 + 2 new).
- **Rule 9 mutation check:** replacing `setSegments(filteredSaved)` with `setSegments(saved)`
  makes `buildYour30SavedGate.test.tsx`'s drop test FAIL (the Sport block resurrects) — the test
  encodes the gate's intent, not just current behavior. File restored after the check.

## Definition of done: PASS

- Saved allocation contains `sport` but the user has no `sport.*` interest and no sport source →
  the hydrated screen has **no Sport block** (`["AI","Tech","Geopolitics","Business"]`). ✓
- A saved allocation FULLY backed → **unchanged** (all 5 blocks, saved order). ✓

## Concerns for SP4 (the e2e smoke)

1. The drop test uses synthetic counts (5×6=30) for clarity, not the real default seed — the
   e2e smoke should exercise a REAL returning profile (saved 30 with a since-dropped category) to
   confirm the live `getUserFeedAllocation` → filter → "Fill N more" path end-to-end.
2. The returning-user resurrection path is now gated in BOTH the onboarding flow AND the AppShell
   "Thirty" tab (same component, same effect). The Thirty tab passes neither `onSkip` nor
   `onPickInterests`, so a no-backing user there sees the message with no button — acceptable
   (a backed user is the only one who reaches that tab in practice), but the smoke could note it.
3. `feedAllocation.ts` stays a pure loader — if SP4 expected the gate IN that file, note the
   deliberate placement (component effect) so the e2e asserts at the screen, not the loader.
