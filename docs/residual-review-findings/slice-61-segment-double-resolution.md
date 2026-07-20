# Slice #61 — residual review findings (deferred)

Slice: #61 (thread `write_result.segment_slug` into `persist_digest`; deterministic
segment tie-break). Branch `claude/feed-source-revamp-plan-388edf`.

The B9 multi-agent review panel (correctness, contract/architecture, data-integrity,
simplicity) returned no blocking defects. All actionable findings were applied except
the one below, deferred with rationale.

## LOW — `persist_digest` trusts a supplied non-None `segment_slug` without re-validation

**File:** `agents/pipeline/persist.py` (the `if segment_slug is None:` guard).

**Finding (raised independently by the correctness and contract lenses):** when a
caller passes a non-None `segment_slug`, `persist_digest` skips
`_resolve_segment_slug`, so it no longer runs `canonical_segment_root` folding /
validation on that value. A future caller that passed a non-canonical string (e.g. a
legacy `"markets"`/`"wildcard"` slug, or garbage) would flow unvalidated into
`build_story_row` and `detail_category_for_segment`.

**Why deferred (not a live bug):**
- The only production supplier is `render_phase`, which passes
  `write_result.segment_slug`. `write_phase` obtains that from
  `resolve_segment_from_tags`, whose contract returns *only* a canonical 8-root slug
  or `None`, and raises `SegmentResolutionError` on `None` before the
  `WritePhaseResult` is ever constructed. So the threaded value is always a valid root.
- `stories.story_segment_slug` is a NOT-NULL enum FK: an unexpected value fails **loud**
  at INSERT (Rule 12 satisfied) rather than corrupting data silently.
- Adding cross-module validation (`_VALID_SEGMENT_SLUGS` membership) for a caller that
  does not exist is speculative code (Rule 2: nothing speculative). It also risks a
  fold-vs-passthrough subtlety (`canonical_segment_root` folds legacy slugs, so a naive
  guard could persist an unfolded legacy value).

**If a second pre-resolving caller of `persist_digest` is ever added:** harden the
guard to re-resolve on an out-of-taxonomy value —
`if segment_slug is None or segment_slug not in set(TOPIC_CATEGORIES): segment_slug = _resolve_segment_slug(...)`
— so an unexpected value re-derives from tags instead of being trusted.
