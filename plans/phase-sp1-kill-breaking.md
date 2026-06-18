# Phase SP1: Kill the "breaking" category

**Milestone:** M1 — Kill breaking + taxonomy cleanup (`plans/shared-pool-rework-master-plan.md`)
**Status:** Not started
**Estimated effort:** M

## Goal
The "breaking" *category/tier* is gone from the pipeline, the app, the data, and the sims — the feed runs on **7 categories** (5 topic + 2 source), still totals 30, and the `CoverageMomentum` velocity signal is **preserved** for later use in `story_importance` (M4).

## Context for the executor
"Breaking" is a tier/enum/template, NOT a seeded interest (`supabase/seed/interests.sql` has no breaking row). Exact sites are enumerated in `reference/shared-pool-pipeline.md` §1. **Do NOT touch** the two KEEP sites: `agents/ingestion/adapters/gdelt_bigquery.py:115` (a recall keyword) and `CoverageMomentum = breaking|developing|settled` in `agents/pipeline/models.py:95` + `coverage_gdelt.py` (the velocity signal — this is the thing we're preserving). The default allocation currently is `breaking 4 + even split of 26`; after removal it must become an even split of **30** across topic categories so feeds still total 30.

## Sub-phases

### Sub-phase 1: Remove breaking from the Python pipeline
- **Files touched:** `agents/pipeline/categories.py` (`:40,51,112,163`), `agents/pipeline/feed_assembly.py` (`:92,100,269,466,568-580,673-682`), `agents/pipeline/produce_caps.py` (`:45,48,106-107,197-208`), `agents/pipeline/daily_batch.py` (`:661,670`), `agents/pipeline/detail_templates.py` (`:54,111,162,192,221`).
- **What ships:** `FeedCategory` has 7 values (no `breaking`); `_select_breaking`, `DEFAULT_BREAKING_SLOTS`, `SLOT_KIND_BREAKING`, the Pass-1 breaking fill, and `breaking_headroom` are removed; `_default_allocation` evenly splits **30** across topic categories; `produce_caps` returns caps only; `daily_batch` call sites updated to the new `compute_category_produce_caps` signature; `DETAIL_TEMPLATES`/`DetailCategory` drop the breaking template.
- **Definition of done:** `grep -ri "breaking" agents/pipeline/` returns ONLY the kept `CoverageMomentum`/`coverage_*` sites; `pytest tests/agents/pipeline/test_feed_assembly.py` passes; a unit assertion shows `_default_allocation` for a multi-category user sums to 30 with no `breaking` key and no slot has `feed_slot_kind == "breaking"`.
- **Dependencies:** none

### Sub-phase 2: Remove breaking from the frontend (TS)
- **Files touched:** `src/lib/feedBuckets.ts` (`:70,93,127,172`), `src/lib/detailTemplates.ts`, `src/types/feed.ts` (`:181`).
- **What ships:** `DESIGN_BUCKETS.breaking`, `DESIGN_BUCKET_TO_ENUM.breaking`, the `breaking` default-allocation segment, and `ALWAYS_INCLUDED_CATEGORY_BUCKET` (the force-inject) are removed; `feed_slot_kind` union drops `"breaking"`; the TS `DETAIL_TEMPLATES` twin drops breaking. Default allocation segments cover the 7 categories and total 30.
- **Definition of done:** `grep -rn "breaking" src/lib src/types` returns nothing functional (only unrelated/comment hits, if any); `npm run build` is green; `npm test` (feedBuckets + detailTemplates suites) passes; no "Breaking News" bucket renders.
- **Dependencies:** none (independent of SP1; parallel-safe — different files)

### Sub-phase 3: Data migration + enum decision (`⚠ irreversible` — DB)
- **Files touched:** new `supabase/migrations/0017_drop_breaking_allocation.sql`.
- **What ships:** a migration that **deletes** `user_feed_allocation` rows where `allocation_category = 'breaking'` (their budget is absorbed by the even-split default / remaining category budgets), documents that the `feed_category` enum **retains** the now-unused `'breaking'` value (Postgres can't cheaply DROP an enum value; leaving it unused is safe + reversible — full enum swap deferred), and **keeps** `stories.story_is_breaking` (0015) as the future velocity flag (do not drop).
- **Definition of done:** migration applies cleanly via the session pooler (`db push --db-url`, IPv4 pooler per `news20-supabase-ddl-connection`); `select count(*) from user_feed_allocation where allocation_category='breaking'` returns 0; `story_is_breaking` column still exists; a re-run is idempotent (guarded delete).
- **Dependencies:** none (but coordinate ordering: run after SP1/SP2 land so code no longer writes breaking rows)

### Sub-phase 4: Update sims + tests, verify end-to-end
- **Files touched:** `agents/pipeline/sim/world.py` (`:347`), `agents/pipeline/sim/ranking_sim.py` (breaking-tier assertions), any `tests/` asserting the breaking tier (e.g. `tests/agents/pipeline/test_feed_assembly.py`, `tests/lib/detailTemplates.test.ts`).
- **What ships:** sim fixtures + assertions drop the breaking tier; tests assert the 7-category feed still totals 30 and emits only `{interest, source}` slot kinds.
- **Definition of done:** `pytest` (full suite incl. `agents/pipeline/sim`) green; `npm test` green; a `daily_batch` dry-run / sim produces a 30-slot feed across 7 categories with zero breaking slots and the `CoverageMomentum` signal still computed on stories.
- **Dependencies:** Sub-phases 1, 2, 3

## Phase-level definition of done
The daily batch and the app build run with **zero functional `breaking` category**; `pytest` + `npm test` are green; a produced (or simulated) feed has **no breaking slots, 7 categories, and totals 30**; the `CoverageMomentum`/`story_is_breaking` velocity signal is still present (proven by a test asserting it's computed). Single commit at phase end.

## Out of scope
- Enriching the velocity signal **into** `story_importance` — that's M4 (Phase SP4).
- The shared pool, demand sizing, clustering — M2/M3.
- A full Postgres enum swap to physically remove the `'breaking'` value (deferred; retained-unused is sufficient).

## Open questions
1. **Slot redistribution:** absorb breaking's 4 default slots by an **even split across topic categories** (recommended — matches the existing `_default_allocation` pattern) vs proportional? → recommend even split; confirm at SP1.
2. **Existing users' breaking budgets:** delete the rows (recommended, simplest) vs migrate each user's breaking slot_count into their top category? → recommend delete + let the even-split default cover it.

## Self-critique

**Product lens:** PASS with note. The original `documents/product-brief.md` predates this rework, so M1 isn't traceable to it — the governing brief is `plans/shared-pool-rework-master-plan.md` (decision #1). M1 is pure cleanup that unblocks the rework; it adds no out-of-brief feature (no scope creep). The rework's riskiest assumption (clustering thresholds) is correctly deferred to M3, not M1 — M1 is intentionally the safe unblock, so "test riskiest assumption first" applies to the rework as a whole (M3 spike), not to this cleanup phase.

**Engineering lens:** PASS. Every sub-phase DoD is a concrete, fresh-context-checkable command (`grep` returns N, `pytest`/`npm test` green, a `select` returns 0, build green) — not "works end-to-end." SP4 is sequencing/verification, not a premature lock-in of an API shape. SP1 and SP2 are genuinely distinct (Python vs TS, different files) — not the same thing split. The new `compute_category_produce_caps` signature change is contained to SP1 + its `daily_batch` call sites.

**Risk lens:** PASS with flags. **File-boundary:** SP1/SP2/SP3 touch disjoint files; SP4 touches test files only — no in-phase write conflict, but SP4 depends on 1/2/3 (marked). **Reversibility:** SP3 is `⚠ irreversible` (DB delete) — mitigated by retaining the enum value (no destructive enum swap), keeping `story_is_breaking`, and an idempotent guarded delete; the deleted allocation rows are re-creatable. **Test coverage:** each sub-phase DoD includes a test/automated check (Rule 9); the velocity-signal-still-computed assertion guards against accidentally deleting the KEEP sites. **Painting-into-a-corner:** 1→2→3→4 simulated — after 1+2 code stops emitting breaking, 3 cleans data, 4 verifies; SP4 still works given that state. No corner.

**Irreversible sub-phases:** Sub-phase 3 (`⚠ irreversible` — deletes `user_feed_allocation` breaking rows; mitigated as above).
