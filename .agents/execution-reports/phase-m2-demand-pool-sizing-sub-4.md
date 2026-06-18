# Execution Report — phase-m2-demand-pool-sizing — Sub-phase 4 (final)

**STATUS:** COMPLETE
**Date:** 2026-06-18

## Mission
Wire `compute_pool_target` into `run_daily_pipeline`, log it, make it observable for
M3, add an integration-style 2-user test — additive only, produce-cap path untouched.

## Files touched (3)
- `agents/pipeline/daily_batch.py` — imports (`CATEGORY_FLOOR`, `FeedCategory`,
  `compute_pool_target`, `Settings`); new `PoolTargetCell` model; new
  `pool_target` field on `DailyPipelineResult`; new private loader
  `_load_interest_nodes_by_user`; additive M2 block in `run_daily_pipeline` + return
  threading.
- `tests/agents/pipeline/test_demand.py` — added the SP4 integration-style 2-user
  test (SP2's 4 + SP3's 4 tests left intact → 9 total).
- `tests/agents/pipeline/test_daily_batch.py` — **necessary minimal test fix**: the
  existing ordering/gating test stubs the pre-gate DB seams (`_load_active_user_ids`,
  `_load_category_allocation`) with a bare `object()` client; my additive M2 step
  adds one more pre-gate seam (`_load_interest_nodes_by_user`), so I stubbed it the
  same way + added one assertion that `result.pool_target` is surfaced. Without this
  the test hit `AttributeError: 'object' has no attribute 'table'` (Rule 12 — fix it,
  don't leave it red). `sim/world.py` was NOT touched (it never builds a
  `DailyPipelineResult`; the suite is green without it).

## How `interest_nodes_by_user` was built (REAL resolution, no `{}` fallback)
At the produce-caps call site, `active_user_ids` + `allocation_by_user` are already
in scope, and `run_daily_pipeline` already receives `interest_nodes:
{interest_id: InterestNode}` as a param (used for scoring). But the produce-cap site
does NOT have each user's followed interests — those live in `user_interest_profile`
(`profile_interest_id` only; **no slug** — confirmed by SP2's handoff note).

I added `_load_interest_nodes_by_user(supabase_client, active_user_ids,
interest_nodes)` which does **ONE batched `.in_()` query** on `user_interest_profile`
(selecting `profile_user_id, profile_interest_id`) and resolves each
`profile_interest_id` through the **already-in-scope `interest_nodes` lookup** —
**no new taxonomy DB query** (the constraint in the brief). This is the cleanest real
resolution and mirrors the sibling loaders' style (`_load_category_allocation`,
`_load_prior_feed_story_ids`). A dangling `profile_interest_id` with no node is
skipped with a structured `interest_nodes_by_user_unresolved` warning + `fix_suggestion`
(fail-loud, Rule 12). Users with no resolvable follow are simply absent →
`compute_pool_target` treats them as no-follows (their allocation rows route to
`"_all"` cells), exactly as documented.

## How `pool_target` is made observable for M3
1. **Return value:** added a `pool_target: list[PoolTargetCell]` field to
   `DailyPipelineResult`. The native target is a `{(FeedCategory, str): int}` dict —
   tuple keys are not JSON/pydantic-serializable — so each cell is mapped to a
   `PoolTargetCell(cell_category, cell_subcategory, cell_target_count)` (sorted for
   determinism). M3 consumes this list straight off the batch result.
2. **Log:** the function itself already emits `pool_target_computed` (SP3); I added a
   batch-level `daily_batch_pool_target_emitted` line referencing the **active user
   set** (`active_user_count`, `cell_count`, `total_target`).
3. **Additive:** the block sits AFTER the produce-cap path, reuses its already-loaded
   inputs, and changes **nothing** about which reels are produced (M2 is observe-only).

## Validation — PASS (phase gate)
- `python -m pytest tests/agents/pipeline/ -q` → **271 passed, 3 warnings** (fully green).
- `ruff check agents/pipeline/daily_batch.py agents/pipeline/demand.py
  tests/agents/pipeline/test_demand.py tests/agents/pipeline/test_daily_batch.py`
  → **All checks passed!**
- `python -c "import agents.pipeline.daily_batch"` → **import ok**.
- `grep -n "compute_category_produce_caps" agents/pipeline/daily_batch.py` →
  `49:` (import) + `760:` (call) — produce-cap path **untouched**.

## SP4 test (Rule 9 — encodes WHY)
`test_pool_target_two_user_shopping_list_is_correct_and_sane` drives
`compute_pool_target` with the REAL config (`Settings().pool_buffer` + `CATEGORY_FLOOR`):
user A customized (markets=8, follows crypto+stocks) + user B default (no rows, no
follows). Asserts: (1) subcategory split for A — `markets.crypto`/`markets.stocks`
each `ceil(4×buffer)`; (2) B's default `_all` cells present at `≥ ceil(default×buffer)`;
(3) **max-not-sum** — `markets._all` is purely B's default (`ceil(4×buffer)`), NOT
A's 8 added in; (4) every positive `CATEGORY_FLOOR` topic category total `≥` its
floor; (5) grand total `≥ ceil(30×buffer)` (pool never under one full feed).

## DoD — PASS
2-user fixture asserts max×buffer + floor + subcategory-split + sane totals;
`pytest tests/agents/pipeline/ -q` green; `compute_category_produce_caps` still
present. Phase-level DoD met: a unit-driven daily run emits a correct
subcategory-granular `pool_target` for the active user set
(max-over-users × BUFFER, floored), produce-caps / feed-assembly behavior unchanged,
no DB migration.

## Concerns / flags
- **Test-file scope:** I edited `test_daily_batch.py` (outside the listed in-scope
  files) because my additive production seam broke its bare-`object()` mock — fixing
  it is required to keep the suite green (Rule 12). Mirrors the file's existing
  seam-stub pattern; no behavioral change.
- **`get_settings()` does not exist** (SP1 flag carried forward): used `Settings()`
  directly (codebase convention) for `pool_buffer`.
- Did NOT commit — the orchestrator owns the single phase-end commit.
