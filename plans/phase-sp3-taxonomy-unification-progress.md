# Progress: phase-sp3-taxonomy-unification

**Phase file:** plans/phase-sp3-taxonomy-unification.md
**Started:** 2026-06-19

## Owner decision — default 30-slot split (locked 2026-06-19)
ai 4, tech 4, geopolitics 4, business 4, politics 2, environment 2, sport 3, arts 3, youtube 2, x 2 = 30

## Sub-phase progress
- [x] 1: Canonical taxonomy in the Python pipeline — COMPLETED
- [x] R: Fixture remediation (sim/world.py + 5 red test files) — COMPLETED (30→8 red)
- [x] R2: test_ranking.py fixture migration (7 reds, leftover) — COMPLETED (27/27 green)
- [x] 2: Canonical taxonomy in the TS twin — COMPLETED (feedBuckets 32/32; build red from collateral → R3)
- [x] R3: TS collateral (feedAllocation.ts dead podcasts path + feedAllocation.test.ts fixtures) — COMPLETED (build green, 10/10)
- [x] 3: DB migration (0020+0021) + reel-chip parity (⚠ irreversible) — COMPLETED (applied to remote, backfill 0 invalid, live e2e green)
- [ ] 4: Finish breaking removal in 3 scripts + cross-surface parity — IN PROGRESS
- [ ] R4: collateral drift (userInterests.test.ts red + detailTemplates twin + sourceSwipeData accent) — IN PROGRESS

## Python suite state
308 passed, 1 failed = test_phase5a_live_e2e (LIVE DB, needs migration 0020 applied to remote). Clears after SP3 applies.
## Drift flagged for SP4 sweep (from R3): detailTemplates.ts DetailCategory, interestVector.ts JSDoc, sourceSwipeData.ts geopolitics accent.
## Accent flagged for owner review: ai = #3B82F6 (inferred); geopolitics=red, politics=purple.

## Phase-gated reds
- test_phase5a_live_e2e.py: 1 red — live-DB enum insert needs migration 0020 (SP3) APPLIED TO REMOTE. Clears after SP3.
- [ ] 3: DB migration (0020) + reel-chip label/color parity (⚠ irreversible) — PENDING
- [ ] 4: Finish breaking removal in 3 scripts + cross-surface parity — PENDING

## Execution
Mode: SEQUENTIAL (SP3-sub-3 is ⚠ irreversible — DB enum + backfill). Chain: 1→2→3→4.
Migration number: 0020 (0019 taken by entity_reference_images).
Note: tests/agents/pipeline/test_categories.py does NOT exist yet → SP1 creates it (divergence from listed Files touched, required by DoD).
