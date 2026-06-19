# Phase SP3 — Sub-phase R2 (fixture remediation: test_ranking.py) execution report

**Status:** SUCCESS
**Date:** 2026-06-19
**Scope:** migrate the OLD-taxonomy assertions in `tests/agents/pipeline/test_ranking.py` to the new 10-key SP3 taxonomy so its tests are green. Intent-preserving (Rule 9), surgical (Rule 3). Touched ONLY this one file. No commit.

## Old → new mapping applied (same as sub-R)
`markets → business`, `world_politics → geopolitics`, `culture → arts`. `sport`/`youtube`/`x` unchanged. Interest **slugs** (`business.equities.semis`, `sport.cricket.mumbai`, `world`, `obscure.thing`) left intact — they are retained aliases resolving via `SLUG_TO_CATEGORY`. Only **category keys** (bucket lookups, `feed_category` assertions, allocation-row keys, the all-keys set) migrated.

## Per-change detail

1. **`_SEMIS_NODE` comment** — "maps up to the 'markets' screen category … lands in markets" → "business". The slug root `business` resolves to `business` under SP3 `SLUG_TO_CATEGORY`.

2. **`test_nvidia_follower_scores_strictly_higher_and_lands_in_markets`** → renamed `…_lands_in_business`. `with_entity["markets"]` / `baseline["markets"]` → `["business"]`; `assert boosted.feed_category == "markets"` → `== "business"`; docstring + inline comment updated. **Intent re-interpreted (Rule 9):** "the followed entity lifts its story WITHIN its category, classifying into markets" is now "…into **business**". The behavioral guarantee (strictly-higher score = exactly `ENTITY_BONUS_WEIGHT`, base α/β/γ untouched) is unchanged.

3. **`test_no_matching_entity_is_byte_identical_to_baseline`** — `with_entity["markets"]` / `baseline["markets"]` → `["business"]` (the `business.equities.semis` leaf-tagged AMD story now buckets to `business`). Assertion intent (bonus inert when nothing matches; byte-identical model_dump) unchanged.

4. **`test_custom_source_follow_beats_seed_source_follow`** — `buckets["markets"]` → `buckets["business"]` (both stories are semis-tagged → `business`). Intent (custom-weighted bonus > seed) unchanged.

5. **`test_unknown_root_slug_falls_back_to_culture`** → renamed `…_falls_back_to_arts`; assertion `== "culture"` → `== "arts"`; docstring updated. `DEFAULT_CATEGORY` is now `arts`. Intent (unmapped root → catch-all default) unchanged.

6. **`test_no_tags_falls_back_to_culture_not_crash`** → renamed `…_falls_back_to_arts_not_crash`; `== "culture"` → `== "arts"`. Intent (untagged story → default, never raises) unchanged.

7. **`TestScoreAndClassifyReturnsAllEightKeys` / `test_returns_all_seven_category_keys`** → renamed class `…ReturnsAllTenKeys`, method `test_returns_all_ten_category_keys`. The pinned key-set `{world_politics, tech_science, youtube, markets, sport, x, culture}` (7) → the full SP3 set `{ai, geopolitics, business, environment, politics, tech, sport, arts, youtube, x}` (10). `buckets["markets"]` story-presence check → `buckets["business"]`; `youtube`/`x` empty-source assertions kept; `"breaking" not in buckets` kept. **Intent re-interpreted (Rule 9):** the contract is now "all **10** keys present, source axes empty" — the actual SP3 handoff contract per `empty_category_buckets()`. This is the strongest form: it pins the exact 10-key set, so a missing or stray key still fails. No coverage lost (the no-breaking guard is retained).

8. **`test_hydrates_category_allocation_rows`** — allocation rows re-keyed `"markets" → "business"`, `"world_politics" → "geopolitics"`; the three `allocs[...]` assertions remapped to match. Intent (rows hydrate to `CategoryAllocation`, grouped per user, fields carried through) unchanged. These keys must be valid `FeedCategory` Literal members for the Pydantic model to validate — `business`/`geopolitics` are; the old keys are no longer in the enum.

## Tests whose intent was re-interpreted (Rule 9, no silent loss)
Three: #2 (markets→business category landing), #7 (7-keys→10-keys contract), and #5/#6 (culture→arts default). In each the behavioral invariant is fully preserved; only the taxonomy label changed. No assertion was weakened, deleted, or gutted to go green. Renamed test/method names track the new label so the names stay truthful (Rule 9).

## Validation
- `ruff check tests/agents/pipeline/test_ranking.py` → **All checks passed!**
- `pytest tests/agents/pipeline/test_ranking.py -q`:
  - **Before:** 20 passed, **7 failed** (the 7 SP1-pinned old-taxonomy tests).
  - **After:** **27 passed, 0 failed.**

## Remaining red
None. All 27 tests in this file pass. No LIVE-DB / enum-gated stragglers in this file (unlike `test_phase5a_live_e2e.py`, which is out of scope here and remains blocked on the missing migration 0020 per the sub-R report — not this file's concern).

## Concerns
1. **Slug aliases remain load-bearing.** This file relies on `business.equities.semis → business` and `obscure.thing → arts (default)` via `SLUG_TO_CATEGORY` / `DEFAULT_CATEGORY`. If a later phase removes those mappings, these tests break again.
2. No interaction with the missing **migration 0020** enum-add blocker — this file is pure-data (mocked client), so it does not exercise the live Postgres `feed_category` enum. SP4's parity smoke must still not assume the live DB accepts the 8 new roots until 0020 is written + applied.
