# Execution Report — phase-m2-demand-pool-sizing — Sub-phase 3

**STATUS:** COMPLETE

## Mission
Add the pure cross-user aggregation `compute_pool_target(...)` to `agents/pipeline/demand.py`
(max-over-users × buffer, ceil, then category-level floor via `(C, "_all")` cells), and extend
`tests/agents/pipeline/test_demand.py` with SP3 cases. Do not commit. Touch only the two files.

## Files touched
- `agents/pipeline/demand.py` (extended — added `import math`, `compute_pool_target`, helper `_apply_category_floor`). SP2's `derive_user_subcategory_demand` + helpers untouched. 375 lines total.
- `tests/agents/pipeline/test_demand.py` (extended — added 4 SP3 tests; SP2's 4 tests intact).

## Implementation notes
- `compute_pool_target` signature matches the mission spec exactly, incl. keyword-only `buffer` / `category_floor`. Pure: no DB/clock/network/Settings access — `buffer` and `category_floor` are explicit params (SP4's caller passes `Settings().pool_buffer` + `CATEGORY_FLOOR`).
- Aggregation is **MAX over users** per cell (`agg[cell] = max(agg.get(cell,0), user[cell])`), then `math.ceil(agg × buffer)`.
- Floor applied at the **category** level (Open Question #1 recommendation): if a category's summed cell targets < `category_floor[C]`, add/raise a single `(C, "_all")` cell by the shortfall. A category with zero cells gets a fresh `(C, "_all") = floor`. No existing cell is ever reduced. Non-positive floors are no-ops.
- Structured `pool_target_computed` log emits `cells`, `total_target`, `active_users`, `buffer` via the module's existing `get_logger("pipeline.demand")`.
- Full Google-style docstrings + doctically-runnable Examples + type hints on both new functions.

### Gotcha handled
`CategoryAllocation.allocation_category` is the `FeedCategory` Literal — `"geopolitics"` is NOT a
valid value (it's a *slug*, not a screen category). The geopolitics tests therefore set
`allocation_category="world_politics"` and supply the subcategory via `geopolitics.*` **follows**
(the slug map resolves `geopolitics` root → `world_politics`). This mirrors real data flow.

## Validation — PASS
- `python -m pytest tests/agents/pipeline/test_demand.py -q` → **8 passed in 0.03s** (4 SP2 + 4 SP3).
- `ruff check agents/pipeline/demand.py tests/agents/pipeline/test_demand.py` → **All checks passed!**

### SP3 test coverage (Rule 9 — encodes WHY)
- (a) `test_pool_target_aggregates_max_not_sum_across_users` — two users, `world_politics=10`, SAME `geopolitics.elections` cell → cell == ceil(10×1.5)=15, total==15 (SUM would be 30). Load-bearing max-not-sum.
- (a') `test_pool_target_distinct_subcats_size_independently` — two users on DIFFERENT subcats → each cell ceil(10×1.5)=15; every cell is 10-based, none 20-based.
- (b) `test_pool_target_floors_unallocated_topic_category` — nobody allocates `culture`; it still surfaces at `(culture,'_all') == CATEGORY_FLOOR['culture'] == 3`; source cats (floor 0) get no phantom cell.
- (c) `test_pool_target_buffer_one_no_floor_is_plain_max_demand` — buffer=1.0 + `category_floor={}` reduces to exact per-cell max demand (crypto max(3,6)=6, stocks max(3,0)=3).

## DoD — PASS
All three phase-file SP3 DoD assertions present and green; `pytest tests/agents/pipeline/test_demand.py -q` green; ruff clean; demand.py 375 lines (< 500).

## Concerns
- None blocking. Minor: param is named `interest_nodes_by_user` (per the explicit mission signature); the phase file body calls it `interests_by_user`. SP4 wires the real loader output (`daily_batch.load_active_user_inputs` returns `interests_by_user`) — the SP4 caller should map that to this positional arg. Flagged so SP4 doesn't trip on the name.
- Did NOT commit (per instructions). SP4 will own the single phase-end commit.
