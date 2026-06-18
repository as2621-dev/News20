# Phase M2: Demand computation + pool sizing

**Milestone:** M2 — Demand computation + pool sizing (`plans/shared-pool-rework-master-plan.md`)
**Status:** Not started
**Estimated effort:** M

## Goal
A daily run computes a **subcategory-granular shopping list** — `pool_target[(category, subcategory)] = ceil(max_over_active_users(demand) × BUFFER)`, floored by `CATEGORY_FLOOR` — and emits it (structured log + return value) for the active user set, **without changing any current production behavior** (it's additive: compute + log, not yet consumed by ingestion — that's M3/B).

## Context for the executor
This implements Stage (A) of `reference/shared-pool-pipeline.md` §2. Key facts verified in the codebase:
- **Demand source already exists.** `agents/pipeline/produce_caps.compute_category_produce_caps` already folds per-user `user_feed_allocation` rows into `{category: max slot_count}`. M2 extends that idea to **(category, subcategory)** granularity and adds the **× BUFFER** and **CATEGORY_FLOOR** terms. Do NOT modify `compute_category_produce_caps` (the produce-cap path still uses it) — build the pool-target path alongside it in a new `demand.py`.
- **Subcategory comes from the interest slug, not a tree walk.** `InterestNode.interest_slug` is dotted: `sport.soccer.arsenal` → category root `sport`, subcategory `sport.soccer`. A depth-0 follow (`sport`) has no subcategory → use a sentinel `None`/`"_all"` cell for that category. Derive category+subcategory by splitting the slug (`agents/ingestion/models.py:69`); the category root must map through the existing `assign_category`/`SLUG_TO_CATEGORY` path so it lands on one of the 7 `FeedCategory` values (mirror, don't reinvent the mapping).
- **Inputs are already loaded.** `daily_batch.load_active_user_inputs` returns per-user interests (`interests_by_user`) and `allocation_by_user`; `_load_active_user_ids` gives the active set. `DEFAULT_FEED_ALLOCATION` (`agents/pipeline/categories.py`) is the no-row user's budget (now 7 categories totalling 30 after SP1).
- **`max`, not `sum`** — two users wanting 10 geopolitics want *different* 10s (spec §2(A) rationale). The aggregate is `max_over_users`, then `× BUFFER`, then `max(..., CATEGORY_FLOOR[cat])`.
- **No DB migration in M2.** Pure compute + config + one wiring/log site. (The cluster-table migration is M3; note it will be `0018`, since `0017` was used by SP1.)

## Sub-phases

### Sub-phase 1: Add pool-sizing config (BUFFER + CATEGORY_FLOOR)
- **Files touched:** `agents/shared/settings.py`, `agents/pipeline/categories.py`.
- **What ships:** a `POOL_BUFFER` setting (float, default `1.5`, env-overridable, validated to `[1.0, 3.0]`) on the existing pydantic `Settings`; a `CATEGORY_FLOOR: dict[FeedCategory, int]` constant in `categories.py` (a per-category minimum unique-story floor so a live category is never starved — default a small uniform value, e.g. `3`, for the 5 topic categories; `0` for the 2 source categories since those are follow-gated). Both documented (docstring/`# Reason:`).
- **Definition of done:** `python -c "from agents.shared.settings import get_settings; assert 1.0 <= get_settings().pool_buffer <= 3.0"` succeeds; `python -c "from agents.pipeline.categories import CATEGORY_FLOOR, FeedCategory; assert set(CATEGORY_FLOOR).issubset(set(FeedCategory)); assert all(v>=0 for v in CATEGORY_FLOOR.values())"` succeeds; `ruff check` clean on both files.
- **Dependencies:** none

### Sub-phase 2: Per-user (category, subcategory) demand
- **Files touched:** `agents/pipeline/demand.py` (new).
- **What ships:** a pure function `derive_user_subcategory_demand(category_allocation, followed_interest_nodes, default_allocation) -> dict[tuple[FeedCategory, str], int]` that splits each user's per-category slot budget across the subcategories they actually follow in that category (subcategory = first two slug segments; a depth-0 follow or a category with budget but no followed subcategory → the `"_all"` sentinel cell holding the full category budget). A no-row user inherits `default_allocation` mapped to `"_all"` cells. The split is deterministic (even split with remainder to the lexicographically-first subcategory) — code answers, no model (Rule 5).
- **Definition of done:** new `tests/agents/pipeline/test_demand.py` asserts: (a) a user with budget `markets=6` who follows `markets.crypto` + `markets.stocks` yields `{(markets,'markets.crypto'):3, (markets,'markets.stocks'):3}`; (b) a user with `sport=4` who follows only the root `sport` yields `{(sport,'_all'):4}`; (c) a no-row user yields `default_allocation` as `"_all"` cells summing to 30. `pytest tests/agents/pipeline/test_demand.py -q` green.
- **Dependencies:** none (independent of SP1; uses categories/models only)

### Sub-phase 3: Aggregate to pool_target (max × buffer, floored)
- **Files touched:** `agents/pipeline/demand.py` (extend).
- **What ships:** `compute_pool_target(allocation_by_user, interests_by_user, active_user_ids, default_allocation, *, buffer, category_floor) -> dict[tuple[FeedCategory, str], int]` that calls `derive_user_subcategory_demand` per user, takes `max_over_users` per cell, multiplies by `buffer` and `ceil`s, then lifts each cell to at least `category_floor[cat]` (floor applied at the category level, distributed to its cells so the category's total unique target ≥ floor). Emits a structured `pool_target_computed` log (cells, active_users, buffer). Returns the shopping list.
- **Definition of done:** `test_demand.py` asserts: (a) two users with `geopolitics=10` (different subcategories) produce a per-cell target of `ceil(10×buffer)` using **max not sum** (NOT 20-based); (b) a category nobody allocates but with a non-zero `CATEGORY_FLOOR` still appears at its floor; (c) buffer=1.0 + floor=0 reduces to plain max demand. `pytest tests/agents/pipeline/test_demand.py -q` green.
- **Dependencies:** Sub-phase 1 (buffer/floor config), Sub-phase 2 (per-user demand)

### Sub-phase 4: Emit the shopping list from the daily batch + verify end-to-end
- **Files touched:** `agents/pipeline/daily_batch.py` (call `compute_pool_target` in `run_daily_pipeline` near the existing `compute_category_produce_caps` call ~`:659-662`, log it, thread it into the returned/observable batch state — additive only, do NOT remove or alter the produce-caps path), `tests/agents/pipeline/test_demand.py` (integration-style assert), optionally `agents/pipeline/sim/world.py` if the sim needs the new field.
- **What ships:** `run_daily_pipeline` computes `pool_target` from real loaded inputs and logs a `pool_target_computed` shopping list for the active user set; the value is available on the batch result/state for M3 to consume. No change to which reels are produced (M2 is observe-only).
- **Definition of done:** a test drives `compute_pool_target` with a 2-user fixture (one customized, one default) and asserts the emitted shopping list is correct (max×buffer, floored, subcategory-split) and totals are sane (≥ the heaviest single user's 30 after buffer); `pytest tests/agents/pipeline/ -q` fully green; `grep -n "compute_category_produce_caps" agents/pipeline/daily_batch.py` still present (produce-cap path untouched).
- **Dependencies:** Sub-phases 1, 2, 3

## Phase-level definition of done
`pytest tests/agents/pipeline/ -q` is green; a daily run (or its unit-driven equivalent) emits a correct subcategory-granular `pool_target` shopping list for the active user set using **max-over-users × BUFFER, floored** — and the existing produce-caps / feed-assembly behavior is **unchanged** (M2 is additive). No DB migration. The shopping list is the artifact M3 (ingest-to-target) will consume.

## Out of scope
- Actually **ingesting to** the target (Stage B targeted ingest) — M3.
- Clustering, classification, the `story_clusters` table — M3.
- Tuning BUFFER / CATEGORY_FLOOR values — M6 (today ash dominates; defaults are placeholders, flagged in the master plan open questions).
- Removing or refactoring `compute_category_produce_caps` — it stays until ingest consumes `pool_target`.

## Open questions
1. **Floor distribution:** when `CATEGORY_FLOOR[cat]` exceeds the summed cell demand for a category, spread the floor across existing cells vs add a single `"_all"` floor cell? → recommend a single `"_all"` floor cell (simplest, and ingest treats `"_all"` as "any subcategory in this category"). Confirm at SP3.
2. **Default BUFFER:** 1.5 vs 2.0 — recommend **1.5** to start (less over-fetch while ash dominates); revisit in M6.

## Self-critique

**Product lens:** PASS. M2 directly implements decision #2 (invert to a demand-sized shared pool) from the rework master plan — the per-user→aggregate demand computation. No out-of-brief feature. The rework's riskiest assumption (MiniLM clustering thresholds) is correctly the M3 spike, not M2; M2 is the necessary prerequisite (you can't ingest-to-target without a target). Additive-only scope means zero regression risk to the live feed.

**Engineering lens:** PASS. Every DoD is fresh-context checkable (`python -c` asserts on real symbols, `pytest ... -q` green, `grep` confirms the produce-cap path is untouched) — not "works end-to-end". SP4 is additive wiring + verification, not a premature API lock-in; `pool_target`'s shape (a `{(cat,sub): int}` dict) is the natural Stage-A→B contract and M3 consumes it as-is. SP2 (per-user demand) and SP3 (cross-user aggregate) are genuinely distinct — different inputs, different math (split vs max×buffer×floor) — not the same thing split. Stack-conformant: pure Python helpers + pydantic settings, mirrors the existing `produce_caps.py` pattern.

**Risk lens:** PASS with flags. **File boundary:** SP2 and SP3 both touch `agents/pipeline/demand.py` — marked as an explicit dependency (SP3 depends on SP2); `/run-phase` runs sequentially so no write conflict. SP4 touches `daily_batch.py` + the test file; no overlap with 1-3. **Reversibility:** no irreversible sub-phases — no DB migration, no data deletion, no public API; the only production touch (SP4) is an additive compute+log. **Test coverage:** every sub-phase DoD includes a `test_demand.py` assertion encoding *why* (max-not-sum, buffer applied, floor enforced, subcategory split correct) per Rule 9. **Painting-into-a-corner:** 1(config)→2(per-user demand)→3(aggregate)→4(wire+verify) simulated — each builds on the prior's state, SP4 works given 1-3. No corner.

**Irreversible sub-phases:** none.
