# No-signal classifiers return None, not a default — and pin cross-language twins with a parse test

**Date:** 2026-07-07 · **Slice:** #35 (commits 00bc294, 29c4fe7) · **Files:**
`agents/pipeline/theme_category.py`, `agents/ingestion/interest_keyed_pipeline.py`,
`agents/pipeline/stages/ranking.py`, `tests/agents/pipeline/test_categories.py`

## Problem

`category_for_themes` returned the arts default when NO whitelisted GDELT theme
matched — and its caller stamped that "answer" as the authoritative depth-0
category tag, overriding the interest that actually fetched the story. 884 of
2,368 stories in a real batch (37%) were mis-bucketed into arts. The default was
correct at the FINAL fallback layer but poisonous one layer earlier, where it
masqueraded as a positive classification.

## Rules

1. **A classifier that cannot classify returns `None`, never its consumer's
   fallback.** Only the LAST resolver in the chain (here `assign_category`) may
   apply the catch-all default — and it must log it (`category_fallback_no_tags`)
   so a silent bucket never forms. Precedence contract: derived signal (theme)
   wins only on an ACTUAL match; provenance signal (fetching interest's root) is
   the authoritative fallback.
2. **If a field does double duty, keep its transform unconditional.**
   `story_interest_match_depth` encodes BOTH category precedence (lowest wins)
   and DepthMatch scoring (0→1.0, 1→0.6, 2→0.3). Shifting keyword tags +1 only
   when a theme tag existed gave theme-MISS stories a systematic scoring boost
   over theme-matched siblings from the same interest. The shift must be
   uniform; only the theme tag's presence/absence may vary.
3. **Whitelist expansion is evidence-first:** tally real-batch misses
   (`scripts/theme_miss_counts.py`, story-level counts) and map only codes with
   ONE crisp category meaning. Generic role/sentiment/crisis codes
   (`TAX_FNCACT_*`, `CRISISLEX_*`, `LEADER`, `AFFECT`…) stay unmapped — with the
   None-fallback, a missing entry is safe, a wrong entry is not.
4. **Per-story diagnostics called in per-user loops need dedup.**
   `assign_category` runs O(users × call-sites); log via an
   `lru_cache(maxsize=N)`-wrapped helper keyed on the story-stable args (tests
   call `.cache_clear()`).

## Twin drift test (Python ↔ TS)

`TestTypescriptTwinDrift` regex-parses `src/lib/feedBuckets.ts` from pytest and
compares literals to `categories.py` (the 2026-06-17 `SLUG_TO_CATEGORY` drift,
f58cdc4, now automated). Hard-won details:

- **Strip comments first** (`/*…*/` then `//…`) so a `;` in a comment can't
  truncate the block and commented-out entries don't parse as live data.
- **Assert bidirectional set equality** (`set(parsed) == set(expected)`), never
  just "parsed ⊆ python" — a partial regex parse must fail loudly, not shrink
  the check.
- Pin ALL the twin maps: the panel found a second TS map
  (`interestVector.ts` `INTEREST_ROOT_TO_PINNED_KEY`) with live drift the new
  test didn't cover (→ issue #43). One map per concern, or one test per map.
