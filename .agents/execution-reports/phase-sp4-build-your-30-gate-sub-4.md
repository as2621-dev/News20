# Phase SP4 · Sub-phase 4 — End-to-end selected-only + order smoke

**Status:** SUCCESS

## The smoke I built (scripted, not live — and why)

A **deterministic scripted** integration smoke that drives the REAL units of the phase
end-to-end, offline, with no browser / dev-server / Supabase / network. The phase DoD
explicitly allows "manual or scripted"; a live dev-server + Chrome/CDP + seeded-auth run is
heavy, non-deterministic, and would hit real services — so I chained the same real code
deterministically instead. It is re-runnable and asserts the invariants (not just prints).

**New file:** `tests/lib/onboarding/buildYour30SelectedOrderSmoke.test.tsx` (2 tests).

### What it asserts (the full chain in one place)

`pick a subset → selected-only blocks → arranged order → persisted allocation_sort_order →
(SP3-locked) reel-feed category order`

Test 1 — `shows ONLY the picked buckets, in arranged order, and persists that order with no
unselected leak`:
1. **Selected-only (render):** render `BuildYour30` with `selectedCategoryBuckets=["tech","sport"]`
   and no sources → the REAL `buildSegmentsForSelections` seed renders EXACTLY `["Tech","Sport"]`
   (default-seed order). Asserts NONE of the 8 unselected labels (AI, Geopolitics, Business,
   Environment, Politics, Arts, YouTube, X) leak in.
2. **Order (real reorder handler):** click the REAL ▲ control (`aria-label="Move Sport up"`) →
   on-screen order becomes `["Sport","Tech"]` (the user's arranged order).
3. **Persist (real mapping, fake client):** run the REAL `saveUserFeedAllocation` (pulled via
   `vi.importActual` since the component's import is mocked) over the arranged segments with a
   fake Supabase client → asserts the upsert rows carry ONLY `sport`+`tech` (no unselected enum)
   and `allocation_sort_order` puts Sport at 0, Tech at 1 (index → sort_order mapping exercised
   for real).
4. **Tie to SP3:** asserts the persisted `(category, sort_order)` sequence == `["sport","tech"]`
   == `SP3_LOCKED_FEED_CATEGORY_ORDER`, the EXACT allocation input
   `tests/agents/pipeline/test_feed_assembly_order.py` proves the assembled reel feed emits in
   (Sport reels lead, then Tech reels, no unselected category).

Test 2 — `renders NO category blocks at all when the user picked NOTHING (no phantom seed)`:
render with `[]`/`[]` → zero `.seg` blocks, `#noSignalEmpty` present, Add sheet offers nothing
(`.sheet2 .bk` length 0).

## Back-end order tie-in: referenced SP3's lock (did not duplicate the assembly run)

I **referenced** SP3's `test_feed_assembly_order.py` rather than re-running `assemble_user_feed`
in TS (impossible — different layer) or re-implementing it. The hand-off contract between the
two halves of the chain is the `[(allocation_category, allocation_sort_order)]` rows: this smoke
asserts the exact rows the SP3 pytest consumes as its `CategoryAllocation` input (`sport@0,
tech@1`). The front half (pick → selected-only → arranged → persisted sort_order) is asserted
here with the real component + real persistence mapping; the back half (persisted sort_order →
assembled feed category order) is locked by the SP3 pytest. I also ran the SP3 pytest as part of
validation to confirm the consumed contract still holds.

## Files created / modified

- **Created (owned):** `tests/lib/onboarding/buildYour30SelectedOrderSmoke.test.tsx`
- **No production change.** No gap surfaced — the gate (SP1/SP2) and order lock (SP3) already
  satisfy the smoke. (The `BuildYour30.tsx` / `OnboardingFlow.tsx` / `buildYour30FirstRun.test.tsx`
  diffs in the tree are SP1/SP2's uncommitted work, not mine — orchestrator commits at phase end.)

## Rule-9 mutation proof (intent-encoding, not behavior-mirroring)

- **Order:** temporarily no-op'd `moveSegmentUp`'s swap in `BuildYour30.tsx` → the smoke FAILED
  (`expected ['Tech','Sport'] to deeply equal ['Sport','Tech']`). Restored; clean (no residue).
- **Selected-only / nothing-picked:** the same seed-gate line (`hasSelectionSignal ? ... : []`)
  whose revert-to-`buildDefaultSegments()` failure SP1 already proved is what my selected-only
  leak check + `#noSignalEmpty` check guard — a stricter superset of SP1's assertion. Not
  re-mutated (same line, same failure family).

## Validation results

- `npx vitest run tests/lib/onboarding/` → **29 passed** (27 prior + 2 new). All green.
- `npx vitest run tests/lib/onboarding/buildYour30SelectedOrderSmoke.test.tsx` → **2 passed**.
- `.venv/bin/pytest tests/agents/pipeline/test_feed_assembly_order.py -q` → **2 passed** (the
  back-end order contract this smoke ties to still holds).
- `npx biome check tests/lib/onboarding/buildYour30SelectedOrderSmoke.test.tsx` → clean.
- `git diff` confirms BuildYour30.tsx mutation-check edits fully restored (no residue).

## Definition of done: PASS

- The smoke passes (scripted). ✓
- A profile that picked NOTHING produces no phantom category blocks (Test 2: zero `.seg`,
  `#noSignalEmpty` present, no Add chips). ✓
- Ordering matches the picked/arranged sequence — arranged Sport-before-Tech persists as
  `allocation_sort_order` `sport@0, tech@1`, == the SP3-locked feed category order. ✓

## Concerns

1. **Order semantics clarification (not a gap):** "the order they arrange blocks in" is a manual
   ▲/▼ action in the screen, NOT derived from pick order — `buildSegmentsForSelections` seeds in
   default-seed order, then the user arranges. The smoke drives the REAL ▲ handler to produce the
   Sport-before-Tech arrangement, matching the phase intent exactly. Flagging so no one expects
   pick-order to auto-become feed-order.
2. **Two-half chain by design:** the TS↔Python boundary can't run in one process, so the chain is
   covered as front-half (this smoke) + back-half (SP3 pytest) meeting at the asserted
   `(category, sort_order)` rows. Both were run green in this validation. If the SP3 input
   contract changes, update `SP3_LOCKED_FEED_CATEGORY_ORDER` in lockstep.
3. SP2's report suggested also exercising a REAL returning-profile saved-30→"Fill N more" path
   end-to-end; that drop path is already locked by `buildYour30SavedGate.test.tsx` (component +
   mount effect). This smoke focuses on the fresh-pick selected-only + order chain per the
   sub-phase mission; I did not duplicate the saved-drop assertion.
