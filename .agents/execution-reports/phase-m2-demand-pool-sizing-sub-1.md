# Execution report — phase-m2-demand-pool-sizing, Sub-phase 1

**Sub-phase:** 1 — Add pool-sizing config (BUFFER + CATEGORY_FLOOR)
**Status:** SUCCESS
**Date:** 2026-06-18

## What shipped
1. `agents/shared/settings.py` — added a `pool_buffer: float` field to the existing
   pydantic `Settings` class. Default `1.5`, env-overridable via `POOL_BUFFER` (the
   class uses case-insensitive env names, so the field name maps to `POOL_BUFFER`),
   range-constrained with `Field(ge=1.0, le=3.0)` — matching how the existing
   `youtube_pace_seconds` field constrains a float with `ge=`. Documented with a
   `# Reason:` block + description citing `reference/shared-pool-pipeline.md §2A`.
2. `agents/pipeline/categories.py` — added module-level constant
   `CATEGORY_FLOOR: dict[FeedCategory, int]` placed right after
   `DEFAULT_FEED_ALLOCATION` (same style/placement as the sibling dicts). Floor of
   `3` for each of the 5 topic categories, `0` for the 2 source categories.
   Documented with a `# Reason:` block.

## Source vs topic classification
- **Source categories (floor 0):** `youtube`, `x`.
- **Why:** the file already declares `SOURCE_CATEGORIES = ("youtube", "x")` (line 68)
  with a docstring stating they are "source-axis categories that no interest slug
  maps to … fed by source ingestion (phase-5d), not interest slugs." They are
  follow-gated (a story only exists there if the user follows a YouTube channel / X
  handle), so there is nothing to floor-ingest. Unambiguous — confirmed against the
  `SOURCE_CATEGORIES` / `TOPIC_CATEGORIES` constants and the module docstring.
- **Topic categories (floor 3):** `world_politics`, `tech_science`, `markets`,
  `sport`, `culture` — exactly the members of `TOPIC_CATEGORIES`.

## Validation
- `Settings().pool_buffer` → `1.5` (PASS). NOTE: the DoD command references
  `get_settings()`, but that accessor does NOT exist in the codebase — the
  convention is to instantiate `Settings()` directly (confirmed via grep: every
  consumer does `Settings()` / `settings or Settings()`; e.g.
  `agents/pipeline/llm_clients.py:81`). Validated against `Settings()` per the
  task's documented fallback. Did NOT add a `get_settings()` factory (out of scope;
  Rules 2/3).
- Env override: `POOL_BUFFER=2.0` → `2.0` (PASS).
- Range guard: `POOL_BUFFER=5.0` → pydantic `less_than_equal` ValidationError (PASS).
- `CATEGORY_FLOOR` → `{'world_politics': 3, 'tech_science': 3, 'markets': 3,
  'sport': 3, 'culture': 3, 'youtube': 0, 'x': 0}`; keys ⊆ `FeedCategory` members,
  all values ≥ 0 (PASS).
- `ruff check agents/shared/settings.py agents/pipeline/categories.py` → All checks
  passed.

## DoD
PASS — both `python -c` asserts succeed (using `Settings()` in place of the
non-existent `get_settings()`), ruff clean on both files.

## Concerns / flags
- The phase-file DoD snippet uses `get_settings()`, which does not exist. Either the
  validation snippets in the phase doc should be updated to `Settings()`, or a
  `get_settings()` accessor should be added in a later sub-phase if a cached
  singleton is desired. Flagged, not silently fixed (Rule 12).

## Files touched
- `agents/shared/settings.py`
- `agents/pipeline/categories.py`
