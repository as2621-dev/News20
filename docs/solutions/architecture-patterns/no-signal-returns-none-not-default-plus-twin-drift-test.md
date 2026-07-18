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

## #43 resolution — derive the identity half, pin the keys list (b1deef2)

The pinned-key map was the second live instance of this drift class. The fix
that KILLS the class (vs. hand-adding the 4 missing entries):

- **Derive identity entries from the canonical keys literal** — `{
  ...Object.fromEntries(ARCHETYPE_CATEGORY_KEYS.map((k) => [k, k])) }` — and
  keep only genuine legacy alias folds literal. A new root can then never be
  missing from the identity half.
- **The derivation just moves the drift target**: now the KEYS LITERAL
  (`archetypeMatch.ts` `ARCHETYPE_CATEGORY_KEYS`) must be pinned
  cross-language. A regex parse can't see spread/derived entries, so the
  Python twin test pins the keys array (`== TOPIC_CATEGORIES`) and a TS-side
  test pins the map's identity property (`map[key] === key` per key — also
  catches an alias shadowing an identity entry).
- Advisory (same file, same shape, no live drift yet):
  `ENTITY_ROOT_TO_PINNED_KEY` is still a hand-listed identity map over the
  same 8 keys — derive it the same way if it's ever touched.

## #44 follow-up — deleting a default is a caller-contract change (b0623f8)

Same class, second instance: `persist_helpers.resolve_segment_from_tags` returned
`"wildcard"` when nothing resolved, so a stale 5-set silently mislabelled every
ai/business/environment/politics/arts story (0 of 67 correct in the prod 07-07
batch). Turning it into `str | None` is easy; the cost is entirely at the callers.

- **The default's real consumers are the call sites that never had a signal.**
  Deleting it doesn't just change an error path — it *rejects* every caller that
  was riding the fallback. Here that was source-axis (YouTube/X) and X-theme
  reels, produced with an empty `story_interest_tags` list (`daily_batch.py`
  merge points, `scripts/produce_source_reels.py`, the SP3 e2e harness). None of
  them appear in a grep for the changed function — they surface only by asking
  "who reaches this with no input?" Filed as #61 rather than expanding the slice.
- **Tests that rode the default must be re-pointed, or they pass for the wrong
  reason.** A `pytest.raises(PipelineStageError)` test asserting an *insert*
  failure stayed green because the new `SegmentResolutionError` subclasses it and
  fired first. Green-after-a-contract-change is not evidence.
- **Reject at every boundary the function is callable from, and log distinctly at
  each.** The nightly batch calls `write_phase` directly (never
  `orchestrate_story`), so its generic `except Exception` reported segment
  rejections as `produce_write_failed` / "Script/verify failed" — re-creating the
  conflation the guard existed to prevent, one layer up.
- **Fold legacy values, don't validate against them.** Retired enum values
  (`markets`, `wildcard`) still arrive from old rows: map them through
  `SLUG_TO_CATEGORY` and never emit them. Do NOT reuse `category_for_slug` for
  this — its `arts` catch-all is right for the LAST resolver and poisonous here.
- **Prove a drift test can fail.** Both new twin tests were mutation-checked
  (add `"markets"` to `SegmentKey`; set `FEED_SLOT_BUDGET = 26`) before shipping.
  A pinning test nobody has seen fail is a decoration.
