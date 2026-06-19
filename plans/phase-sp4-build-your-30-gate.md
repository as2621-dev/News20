# Phase SP4: Build-your-30 selected-only gate + order-consistency lock

**Milestone:** M1 — Kill breaking + taxonomy cleanup (`plans/shared-pool-rework-master-plan.md`)
**Status:** Not started
**Estimated effort:** M

## Goal
"Build your 30" shows **only** the categories a user actually backs (a category appears iff they picked it in onboarding OR follow a source for it — Sport appears iff they picked Sport), and the **order** they arrange blocks in is the order categories appear in their reel feed.

## Context for the executor
The gate already mostly exists: `src/lib/feedBuckets.ts` `buildSegmentsForSelections` / `allowedBucketsForSelections` filter to backed buckets. The leak is in `src/components/onboarding/BuildYour30.tsx` (~180–190): when `hasSelectionSignal` is **false** (picker skipped AND no sources followed) it falls back to `buildDefaultSegments()` (ALL buckets) and `addableBuckets = all` → phantom Sport/Culture. A returning user's **saved** allocation (~200–232) also takes precedence and can resurrect an unbacked category. Order is already honored at the category level: `agents/pipeline/feed_assembly.py` `_ordered_categories_from_allocation` (~172) sorts by `allocation_sort_order`, and the fill loop (~552–575) emits each category's slots in that order; within a category, order is by Score (correct — rank, then top-N). This phase **closes the gate** and **locks the order with a test** — it does not rebuild the ordering machinery.

## Sub-phases

### Sub-phase 1: Close the no-signal gate
- **Files touched:** `src/components/onboarding/BuildYour30.tsx` (~175–190).
- **What ships:** when `hasSelectionSignal` is false, the screen does **not** seed all buckets — it renders an empty/"pick interests first" CTA state (and/or only source-backed blocks if any sources are followed); the Add-block sheet offers only `allowedBucketsForSelections` (never all `DESIGN_BUCKET_IDS`).
- **Definition of done:** a component test: render with zero picker follows + zero sources → **no** category blocks render and the Add-sheet offers nothing; render with `["sport"]` follows → only the Sport block (+ any source blocks) renders.
- **Dependencies:** none

### Sub-phase 2: Gate the saved allocation against current backing
- **Files touched:** `src/components/onboarding/BuildYour30.tsx` (~200–232), `src/lib/feedAllocation.ts` (load path).
- **What ships:** on hydrating a returning user's saved `user_feed_allocation`, segments are filtered through `allowedBucketsForSelections` against their **current** interest+source backing — a saved category with no live backing is dropped (and its slots redistributed to remaining blocks or surfaced as "Fill N more"), so a stale Sport allocation can't resurrect after the user dropped Sport.
- **Definition of done:** a test: saved allocation contains `sport` but the user has no `sport.*` interest and no sport source → the hydrated screen has no Sport block; a saved allocation fully backed → unchanged.
- **Dependencies:** Sub-phase 1 (shares the gating helper + file region)

### Sub-phase 3: Lock category-order persistence with a backend test
- **Files touched:** new test `tests/agents/pipeline/test_feed_assembly_order.py` (no production change unless the test reveals a gap); read `agents/pipeline/feed_assembly.py` `_ordered_categories_from_allocation`.
- **What ships:** a test asserting that for a user whose `user_feed_allocation` has categories in order `[B, A, C]` (by `allocation_sort_order`), the assembled `daily_feeds` emits B's slots, then A's, then C's — i.e. the reel feed category order == the Build-your-30 order. If the test reveals the order is NOT honored, fix `feed_assembly.py` minimally.
- **Definition of done:** `pytest tests/agents/pipeline/test_feed_assembly_order.py` green; the test FAILS if `_ordered_categories_from_allocation` is changed to ignore `allocation_sort_order` (encodes intent — Rule 9).
- **Dependencies:** none (different layer — parallel-safe with SP1/SP2)

### Sub-phase 4: End-to-end selected-only + order smoke
- **Files touched:** test/e2e only — extend `scripts/e2e/` or a `go-live-check` profile; no production change unless a gap surfaces.
- **What ships:** a smoke run: a seeded profile picks a known subset (e.g. Tech + Sport, Sport before Tech) → Build-your-30 shows only Tech + Sport with Sport first → the produced/assembled reel feed leads with Sport then Tech and contains no unselected category.
- **Definition of done:** the smoke passes (manual or scripted); a profile that picked NOTHING produces no phantom category blocks; ordering matches the picked sequence.
- **Dependencies:** Sub-phases 1, 2, 3

## Phase-level definition of done
Onboarding with no Sport pick → Build-your-30 has no Sport block → reel feed has no Sport reels; a skipped picker + no sources → no category blocks at all (no phantom default seed); reordering Build-your-30 blocks reorders the reel feed's category sequence (proven by the SP3 test + SP4 smoke). `npm test` + `pytest` green. Single commit at phase end.

## Out of scope
- Within-category story ordering (stays rank/Score-based — correct per the per-story model).
- The shared-pool ranking that fills the slots (M3/M4) — this phase only governs which categories appear and in what order.
- Re-seeding or migrating existing users' allocations (the SP3 taxonomy migration handles category renames).

## Open questions
1. **No-signal empty state copy/behavior:** show a "pick interests first" CTA that routes back to the picker (recommended) vs. show source-only blocks vs. allow a minimal default. Confirm UX at SP1.
2. **Dropped-category slot redistribution** (SP2): redistribute freed slots to remaining blocks vs. open on "Fill N more" (recommended — matches the existing no-auto-rescale owner decision).

## Self-critique

**Product lens:** PASS. "Show only what I selected" + "my order is my feed's order" is exactly the owner ask and tightens the MVP's simple-personalization promise (onboarding intent → feed). No out-of-brief feature. Not the rework's riskiest assumption (M3) — deterministic UI/allocation correctness, correctly in M1.

**Engineering lens:** PASS. DoDs are checkable in fresh context (component renders N blocks, a saved-allocation hydration drops an unbacked block, a pytest asserts emit order, a smoke shows label order). SP1 and SP2 are both gating but distinct conditions (live no-signal session vs. persisted stale allocation) — they share the file so SP2 depends on SP1. SP3/SP4 are verification, locking nothing premature.

**Risk lens:** PASS. **File-boundary:** SP1+SP2 share `BuildYour30.tsx` → SP2 depends on SP1 (marked); SP3 (Python test) + SP4 (e2e) are separate. **Reversibility:** no DB migration, no destructive change (pure gating + tests). **Test coverage:** SP1/SP2/SP3 each carry an automated test (Rule 9); SP4 is the integration smoke. **Painting-into-a-corner:** 1→2→3→4 simulated — gate the live path (1), gate the saved path (2), lock the order (3), prove end-to-end (4); SP4 works given the state from 1–3. No corner.

**Irreversible sub-phases:** none.
