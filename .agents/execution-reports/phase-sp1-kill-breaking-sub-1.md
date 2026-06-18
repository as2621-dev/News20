# Phase SP1 — Sub-phase 1: Remove breaking from the Python pipeline

**Status:** SUCCESS (production code complete; stale tests left for SP4 per phase plan)
**Date:** 2026-06-18

## Mission
Remove the "breaking" feed *category/tier/template* from the Python pipeline. Keep
the `CoverageMomentum` velocity signal + `story_is_breaking` data flag untouched.

## Files modified (only the 5 in scope)
- `agents/pipeline/categories.py`
- `agents/pipeline/feed_assembly.py`
- `agents/pipeline/produce_caps.py`
- `agents/pipeline/daily_batch.py`
- `agents/pipeline/detail_templates.py`

## What was implemented

### categories.py
- `FeedCategory` Literal now has **7 values** (dropped `"breaking"`):
  `world_politics, tech_science, youtube, markets, sport, x, culture`.
- `DEFAULT_FEED_ALLOCATION` drops the `breaking: 2` key and **sums to 30** across the
  7 categories. Breaking's 2 default slots absorbed via `world_politics 4→5` and
  `culture 3→4` (documented inline; the TS twin in SP2 must match — Rule 7).
- `empty_category_buckets()` returns 7 keys (no breaking).
- Docstrings/field descriptions updated 8→7.

### feed_assembly.py
- Removed `DEFAULT_BREAKING_SLOTS`, `SLOT_KIND_BREAKING`, and `_select_breaking()`.
- `_default_allocation()` lost the `breaking_slots` param; now **evenly splits the
  full 30** across non-empty topic categories (largest-remainder), so a multi-category
  default feed still totals 30 with no breaking key.
- `assemble_user_feed()` lost the `default_breaking_slots` param; removed Pass-1
  breaking fill and the breaking ordering block; `topic_capacity` no longer subtracts
  `len(breaking)`; `feed_matched_interest_id` None-check now only on `SLOT_KIND_SOURCE`;
  removed `breaking_slots` from the completion log. Passes renumbered (old 1.5→1).
- `SLOT_KIND_INTEREST`, `SLOT_KIND_EXPLORATION`, `SLOT_KIND_SOURCE` retained.

### produce_caps.py
- `compute_category_produce_caps()` now returns **`dict` only** (was
  `tuple[dict, int]`); removed `_BREAKING_CATEGORY`, the `breaking_headroom`
  accumulator + return + log field. No-row users still inherit `DEFAULT_FEED_ALLOCATION`.
- `cap_stories_per_category()` lost the `breaking_headroom` positional param and the
  top-N-by-importance union block. Per-category capping unchanged otherwise.

### daily_batch.py
- Both call sites updated to the new signatures: `caps = compute_category_produce_caps(...)`
  (single return) and `cap_stories_per_category(...)` without the `breaking_headroom` arg.

### detail_templates.py
- `DetailCategory` Literal now **8 values** (dropped `"breaking"`).
- `DETAIL_TEMPLATES` drops the `breaking` template.
- `_FEED_CATEGORY_TO_DETAIL` drops the `breaking → breaking` mapping.
- `detail_category_for_segment()` and `detail_category_for()` keep `is_breaking`
  (defaulted to `False`) **for backward-compat with out-of-scope callers**
  (`persist.py`, `orchestrator.py`, `scripts/regenerate_feed_content.py`) but the
  parameter is now **ignored** — they no longer route to a breaking template. This
  was the surgical choice (Rule 3): changing their signatures would have required
  editing out-of-scope files.

## Self code-review (Step B/C)
- **Call-site coverage:** grepped every caller. `daily_batch.py` (in scope) updated.
  `scripts/run_live_batch.py:448` also unpacks the old 2-tuple — **OUT OF SCOPE**
  (not one of the 5 files) → flagged below.
- **Downstream imports:** `persist`, `orchestrator`, `stages.detail_enrichment`,
  `stages.ranking`, `daily_batch` all import cleanly after the change.
- **`assign_category` never returns breaking:** confirmed — it resolves via
  `category_for_slug` → `SLUG_TO_CATEGORY`/`DEFAULT_CATEGORY`, none of which map to
  breaking; and `FeedCategory` no longer contains it.
- **Leftover "breaking" in the 5 files:** only docstrings/comments + the backward-compat
  `is_breaking` params — no functional category/tier/slot/template usage remains.
- No critical/high issues found. No production code weakened to satisfy a stale test.

## Validation (Step D)

### Production invariant checks (inline, not committed)
- `FeedCategory` = 7 values, no breaking. PASS
- `DEFAULT_FEED_ALLOCATION` sums to 30, no breaking key. PASS
- `empty_category_buckets()` = 7 keys. PASS
- `_default_allocation()` for a multi-category user sums to **30**, no breaking key. PASS
- `feed_assembly` has no `SLOT_KIND_BREAKING` / `_select_breaking` / `DEFAULT_BREAKING_SLOTS`. PASS
- `compute_category_produce_caps()` returns a plain dict; no-row users get the default;
  `cap_stories_per_category` signature has no `breaking_headroom`. PASS

### ruff
`ruff check` on all 5 files → **All checks passed!** PASS

### pytest tests/agents/pipeline/test_feed_assembly.py
**FAILS at collection** with `ImportError: cannot import name 'SLOT_KIND_BREAKING'`.
This is the **expected** stale-test outcome called out in the sub-phase brief — the
test module imports a symbol I correctly removed. **SP4 owns updating this test.**
Production code was NOT reverted to satisfy it.

Running the rest of `tests/agents/pipeline/` (ignoring the two known-stale files):
**225 passed, 12 failed** — every failure is a stale assertion of removed breaking
behavior (verified: they feed `"breaking"` into the now-7-value `CategoryAllocation`
Literal, or assert breaking templates/keys). No unrelated regression.

## Definition of done (Step E)
- `grep -rin "breaking" agents/pipeline/` (the 5 files): only comments/docstrings +
  backward-compat `is_breaking` params remain. **PASS**
- Other `agents/pipeline/` "breaking" hits are all KEEP sites: `CoverageMomentum` /
  `coverage_momentum` / `story_is_breaking` / GDELT-census `is_breaking` data flag /
  acoustic-alignment word fixtures. None are the feed category. **PASS**
- `_default_allocation` for a multi-category user sums to **30**, no `breaking` key,
  no slot emits `feed_slot_kind == "breaking"`. **PASS**

**DoD: PASS** (production code; stale-test fixups are explicitly SP4's deliverable).

## Concerns for the orchestrator

### 1. Stale tests SP4 MUST update (all assert removed breaking behavior)
- `tests/agents/pipeline/test_feed_assembly.py` — imports `SLOT_KIND_BREAKING`
  (collection-blocking); plus tests `test_breaking_block_sits_at_its_allocation_sort_order`,
  `test_breaking_tier_filled_by_importance_and_not_double_placed`, the DoD-allocation
  test (`("breaking", 2, 0)`), default-allocation test asserting breaking 4, etc.
  Also: `_default_allocation` no longer takes `breaking_slots`; `assemble_user_feed`
  no longer takes `default_breaking_slots`.
- `tests/agents/pipeline/test_produce_caps.py` — `_DEFAULT` includes `"breaking"`;
  unpacks the old 2-tuple (`caps, breaking_headroom = ...`); `test_cap_breaking_headroom_*`;
  `cap_stories_per_category(...)` calls pass the old `breaking_headroom` positional.
- `tests/agents/pipeline/test_detail_templates.py` — `test_all_nine_categories_present`,
  `test_each_template_matches_locked_table[breaking]`,
  `test_coverage_only_on_breaking_and_world`, `test_breaking_wins_over_segment`,
  `test_breaking_wins_and_none_falls_back`.
- `tests/agents/pipeline/test_persist_detail_analytics.py::test_breaking_coverage_report_flags_story_and_category`.
- `tests/agents/pipeline/test_ranking.py` — `test_returns_all_eight_category_keys`
  (now 7), `test_hydrates_category_allocation_rows` (fixture has a breaking row).
- `tests/agents/pipeline/test_ranking_simulation.py` — 3 tests using breaking allocation rows.
- `tests/agents/pipeline/test_phase5a_live_e2e.py` — seeds a `breaking` allocation row.
- `tests/agents/pipeline/test_detail_enrichment.py` — iterates `DETAIL_TEMPLATES.values()`;
  check it doesn't hardcode a breaking-count.

### 2. Out-of-scope production file that will break (NOT touched per scope rule)
- `scripts/run_live_batch.py:448` — `caps, breaking_headroom = compute_category_produce_caps(...)`
  and line 455 prints `breaking_headroom`. This will raise (can't unpack a dict into 2
  names). It is **not** one of the 5 in-scope files and is already modified in the
  working tree, so I left it. **The orchestrator should fold a one-line fix into this
  phase** (change to `caps = compute_category_produce_caps(...)` and drop the
  breaking-headroom print), or assign it to SP4.

### 3. Stale docstring in an out-of-scope file (non-functional)
- `agents/pipeline/stages/ranking.py` `score_and_classify_for_user` docstring still
  says "8 screen categories" / "breaking is left empty here". Functionally correct
  already (it seeds via `empty_category_buckets()` → now 7 keys), but the prose is
  stale. Low priority; flag for a cleanup pass (out of SP1 scope).

### 4. CategoryAllocation now rejects "breaking" at the boundary
The 7-value Literal means any `"breaking"` row from the DB or a fixture now raises a
`ValidationError` in `daily_batch._load_category_allocation`. SP3's migration deletes
the breaking `user_feed_allocation` rows so production never feeds one; **ordering
matters** — SP3 must land before the next live batch, and SP4 must strip breaking
rows from test fixtures.
