# Progress: phase-m2-demand-pool-sizing

**Phase file:** plans/phase-m2-demand-pool-sizing.md
**Started:** 2026-06-18

## Sub-phase progress
- [x] 1: Add pool-sizing config (BUFFER + CATEGORY_FLOOR) — COMPLETED (pool_buffer=1.5 [1.0,3.0]; CATEGORY_FLOOR topic=3/source=0; NB use Settings() not get_settings())
- [x] 2: Per-user (category, subcategory) demand — COMPLETED (demand.py + test_demand.py 4 green; reused category_for_slug; sentinel ALL_SUBCATEGORY_SENTINEL; SP4 must resolve profile_interest_id→InterestNode)
- [x] 3: Aggregate to pool_target (max × buffer, floored) — COMPLETED (compute_pool_target + _apply_category_floor; test_demand.py 8 green; param interest_nodes_by_user)
- [x] 4: Emit shopping list from daily batch + verify — COMPLETED (DailyPipelineResult.pool_target + _load_interest_nodes_by_user; pytest 271 green; touched test_daily_batch.py to fix mock)

## Status: COMPLETE — committed b176dc3

## Notes
- Execution mode: SEQUENTIAL (SP2/SP3 share demand.py; SP3/SP4 have deps).
- No irreversible sub-phases (no migration; additive only).
- pool_target surfaced as DailyPipelineResult.pool_target (list[PoolTargetCell]) for M3 to consume.
