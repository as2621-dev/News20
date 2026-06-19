# Phase SP4 · Sub-phase 1 — Close the no-signal gate

**Status:** SUCCESS

## What changed

`src/components/onboarding/BuildYour30.tsx`:

1. **No-signal seed is now empty.** Initial `segments` seed: when `hasSelectionSignal`
   is false, `useState` seeds `[]` (was `buildDefaultSegments()` = all 10 buckets, the
   phantom Sport/Culture leak). With a signal it is unchanged (filtered
   `buildSegmentsForSelections`).
2. **Add-sheet is always gated.** `addableBuckets = allowedBucketsForSelections(...)`
   unconditionally. Removed the legacy `: new Set<DesignBucketId>(DESIGN_BUCKET_IDS)`
   fallback. With no signal `allowedBucketsForSelections([], [])` is empty → the Add
   sheet offers nothing.
3. **Empty-state CTA.** When `!hasSelectionSignal && segments.length === 0` the component
   returns a "Pick a few interests first." state (reusing the in-file `.a-scroll`/`.a-top`
   chrome and the `.addseg` dashed-button token — no new visual language). The CTA button
   (`#pickInterestsCta`) is wired to the existing **`onSkip`** prop; when `onSkip` is
   undefined (the embedded "Thirty" tab) only the message renders (no dead button).
4. **Structured log** `build_your_30_no_signal_empty_state` fires (once) via effect when
   the empty state renders, with `selected_category_count` / `followed_source_count` +
   `fix_suggestion`.
5. Removed the now-unused `buildDefaultSegments` import (kept `DESIGN_BUCKET_IDS` — still
   used by the Add-sheet grid filters). Updated the two prop JSDoc blocks that described
   the old "full default seed" fallback.
6. The WITH-signal path (filtered seed + gated Add sheet + footer/save chrome) is
   untouched.

## Where the CTA routes (divergence — read this)

The owner decision was "route back to the picker." `BuildYour30` has **no prop or hook
that reaches the picker** — the onboarding picker is an internal `step === "picker"` state
inside `OnboardingFlow` (not a URL route), and `OnboardingFlow` owns step navigation. The
only navigation affordance this component already has is `onSkip`. Per the file-scope
constraint (touch ONLY `BuildYour30.tsx` + a test), I wired the empty-state CTA to
`onSkip`. In the live onboarding flow `onSkip` (= `handleBuildSkip`) routes to `/`
(`router.push("/")`), i.e. onward to the reel — **not** literally back to the picker.

To literally route to the picker, a parent-side change is needed (out of this sub-phase's
file scope): e.g. add an `onPickInterests?: () => void` prop and have `OnboardingFlow` pass
`() => setStep("picker")` (and `AppShell` likewise route to a re-onboarding entry). I did
**not** make that change. Flagging for the orchestrator / a follow-up: the CTA is not a
dead button (it calls a real handler), but its destination is "onward," not "picker."

## Files modified

- `src/components/onboarding/BuildYour30.tsx` (production)
- `tests/lib/onboarding/buildYour30NoSignalGate.test.tsx` (new — DoD test)
- `tests/lib/onboarding/buildYour30FirstRun.test.tsx` (adapted — see below)

### Why the existing test changed (Rule 12 — not silent)

`buildYour30FirstRun.test.tsx` rendered `<BuildYour30 onDone={...} />` with **no** backing
props → previously fell through to the full default 30 (savable, `#cta` present). With the
gate closed, no backing → empty state → no `#cta` → its `handleSave` tests broke. That
suite's intent is to exercise `handleSave` on a savable 30, so I gave it a full selection
signal (`FULL_CATEGORY_BACKING` 8 roots + `FULL_SOURCE_BACKING` youtube/x) → seeds the
complete default 30. Intent preserved; only the precondition (now needs a signal) changed.

## Code review findings + fixes

- **[High] Existing first-run suite broke** — fixed by passing a full signal (above). Not
  a silent skip.
- **[Medium] CTA destination semantics** — `onSkip` routes onward, not to the picker.
  Flagged as a divergence (above); accepted within file scope.
- **[Low] Embedded "Thirty" tab has no `onSkip`** → empty state shows the message with no
  button. Acceptable: a no-signal user in that tab has nothing to allocate; the message is
  still correct. The Thirty tab only mounts once `thirtyBackedBuckets` loads, and a truly
  no-backing user there is an edge case. Noted, not fixed.
- **[Low] `DESIGN_BUCKET_IDS` reference** — still imported/used by the Add-sheet grid
  filters; removing it would break the sheet. Kept. Only `buildDefaultSegments` was
  dropped.

## Validation

- `npx tsc --noEmit` — green.
- `npm run build` — green (Next static export, 6/6 pages).
- `npx biome check` on the two touched files — clean.
- `npx vitest run tests/lib/onboarding/` — **25/25 pass** (3 new + 22 existing).
- **Rule 9 mutation check:** reverting the no-signal seed back to the default allocation
  makes `buildYour30NoSignalGate.test.tsx` FAIL (2/3 assertions) — the test encodes the
  gate's intent, not just current behavior.

## Definition of done: PASS

- Zero picker follows + zero sources → **no** category blocks render (`.seg .nm` = `[]`)
  AND the Add sheet offers nothing (`.sheet2 .bk` = `[]`). ✓
- `["sport"]` follows → only the Sport block renders (`["Sport"]`). ✓

## Handoff to SP2 (saved-allocation mount effect — ~200–232)

**The saved effect is NOT gated and CAN resurrect the empty state into a phantom seed.**
Precise interaction:

- For a no-signal user, initial `segments = []` and `showNoSignalEmptyState = true`.
- The mount effect (`useEffect`, runs once) `await getUserFeedAllocation()`. If a returning
  user has a saved row set totalling 30, it calls `setSegments(saved)` **unconditionally**
  — `saved` is NOT filtered through `allowedBucketsForSelections(currentBacking)`.
- The moment `segments` becomes non-empty, `showNoSignalEmptyState` flips to `false` and the
  full allocation chrome renders with **whatever categories the saved row had** — including a
  stale `sport` the user has since dropped. So my empty initial state is correct for a *live*
  no-signal session but is **overwritten by a stale saved allocation** on mount.
- **SP2 must:** filter `saved` through `allowedBucketsForSelections(selectedCategoryBuckets,
  followedSourceBuckets)` before `setSegments`, and decide the freed-slot behavior (the plan's
  open Q2 recommends "Fill N more"). If, after filtering, the saved set is empty AND there is
  no signal, SP2 should leave `segments = []` so my empty state persists (do not seed the
  default). I deliberately did **not** touch the effect (your territory).
- Note: in the current no-signal *onboarding* path the saved read resolves to `[]` (new
  user), so the empty state holds today. The resurrection risk is specifically the
  **returning-user / library "Thirty" tab** path where a saved row exists.

## Concerns

1. **CTA destination** (Medium) — see divergence. If "route to the picker" is a hard
   requirement, it needs a parent-side prop; cannot be done within this file alone.
2. **SP2 dependency** (High for the phase) — without SP2's saved-path gate, a stale saved
   allocation still resurrects unbacked categories for returning users. SP1 closed only the
   live no-signal path, as scoped.
