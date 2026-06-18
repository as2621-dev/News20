# Execution Report — phase-m2-demand-pool-sizing, Sub-phase 2

**Status:** COMPLETE
**Date:** 2026-06-18

## Mission
Implement the pure per-user (category, subcategory) demand split in a new
`agents/pipeline/demand.py`, plus its test file. Additive only; no DB, no wiring.

## Files touched (created only — nothing modified)
- `agents/pipeline/demand.py` (new) — `derive_user_subcategory_demand` + two private
  helpers (`_subcategory_key_for_slug`, `_even_split_with_remainder`,
  `_followed_subcategories_by_category`).
- `tests/agents/pipeline/test_demand.py` (new) — 4 intent-asserting tests.

No modification to `categories.py`, `models.py`, or `produce_caps.py` (per instruction).

## Validation — PASS
- `python -m pytest tests/agents/pipeline/test_demand.py -q` → **4 passed in 0.02s**
- `ruff check agents/pipeline/demand.py tests/agents/pipeline/test_demand.py` → **All checks passed!**
- (Env note: had to `source .venv/bin/activate`; bare `python`/`ruff` not on PATH.)

## DoD — PASS
All three SP2 DoD asserts encoded + green, plus the remainder case (d):
- (a) markets=6 + follows crypto+stocks → `{(markets,'markets.crypto'):3, (markets,'markets.stocks'):3}`
- (b) sport=4 + root-only 'sport' → `{(sport,'_all'):4}`
- (c) no-row user → `DEFAULT_FEED_ALLOCATION` as `'_all'` cells summing to 30
- (d) markets=7 across crypto+stocks → crypto 4, stocks 3 (lexicographic remainder, input-order-independent)

## Concerns / decisions for the orchestrator (and SP4)
1. **`followed_interest_nodes` param type = `list[InterestNode]`** (as instructed).
   IMPORTANT for SP4: `daily_batch.load_active_user_inputs` exposes
   `interests_by_user: dict[str, list[UserProfileInterest]]`, and
   `UserProfileInterest` carries ONLY `profile_interest_id` — **no slug**. The slug
   lives on `InterestNode`. So SP4 must resolve each user's
   `UserProfileInterest.profile_interest_id` through the existing
   `{interest_id: InterestNode}` taxonomy lookup (`interest_nodes`) to build the
   `list[InterestNode]` this function expects. This is documented in the function
   docstring. (`allocation_by_user` is already `list[CategoryAllocation]`, a clean
   match for `category_allocation`.)
2. **Mapping helper reused:** `category_for_slug` from `agents/pipeline/categories.py`
   (slug-root → `FeedCategory`, the same path `assign_category` ultimately calls). I
   did NOT reuse `assign_category` directly — it classifies a *story* via
   `story_interests` tags, whereas SP2 has interest slugs in hand, so
   `category_for_slug` is the correct, narrower reuse. The unknown-category guard
   checks membership in `SLUG_TO_CATEGORY.values()` and logs a structured warning
   with `fix_suggestion` (fail-loud, Rule 12).
3. **Subcategory = first two dotted segments**; depth-0 (single-segment) follow →
   `None` → routes to the `"_all"` sentinel cell. Sentinel exported as
   `ALL_SUBCATEGORY_SENTINEL = "_all"` for SP3/SP4 reuse.
4. No commit made (per instruction).
