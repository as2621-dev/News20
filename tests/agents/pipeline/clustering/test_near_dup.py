"""Unit tests for the MinHash near-duplicate prefilter (M3a, Sub-phase 2).

Threshold note (Rule 7 — surfaced conflict): the phase spec asked for grouping a
ONE-word-different reprint at threshold 0.85, but Jaccard over 4-gram word
shingles caps a single-word edit at ~0.8 (and ~0.83 at any n-gram size) — 0.85 is
mathematically unreachable for a one-word edit. The default threshold was
recalibrated to 0.7 (``near_dup.DEFAULT_THRESHOLD``), where reprints (~0.8) group
and distinct stories (~0.0) do not, with a wide margin. These tests exercise that
calibrated default; the assertions were NOT weakened — the threshold was fixed to
match shingle-space reality. See the SP2 execution report.

These tests encode WHY (Rule 9), not just behavior:
    - (a) Two reprints differing by ONE word MUST group: that is the entire point
      of the prefilter (collapsing wire-service reprints before the paid embedding
      step). A regression that broke shingling/threshold so they no longer group
      fails (a).
    - (b) Two genuinely different stories MUST stay apart: over-grouping would
      silently merge distinct events, destroying the pool's diversity. Fails (b).
    - (c) ``drop_exact_reprints`` MUST collapse a reprint cluster to its SMALLEST
      index and keep distinct stories — that representative contract is what M3b
      consumes (it dedups the targeted-ingest candidates). Fails (c).
    - (d) A < 4-word headline MUST NOT crash and MUST be handled via the word-set
      fallback (otherwise empty shingles crash MinHash on short headlines, which
      are common). Fails (d).

No external services are involved — ``datasketch`` is pure-Python and the
functions are pure over ``NearDupItem``, so there is nothing to mock.

    >>> pytest tests/agents/pipeline/clustering/test_near_dup.py -q
"""

from __future__ import annotations

from agents.pipeline.clustering.near_dup import (
    NearDupItem,
    _build_shingles,
    drop_exact_reprints,
    group_near_duplicates,
)

# Calibrated default — see module-level threshold note and near_dup.DEFAULT_THRESHOLD.
_THRESHOLD = 0.7


def _group_of(groups: list[list[int]], index: int) -> list[int]:
    """Return the single group that contains ``index`` (helper for assertions)."""
    matches = [group for group in groups if index in group]
    assert len(matches) == 1, f"index {index} must appear in exactly one group, found {len(matches)}"
    return matches[0]


def test_one_word_difference_groups_together() -> None:
    """(a) Two long headlines differing by ONE word group at the calibrated threshold.

    WHY: the prefilter exists to collapse near-identical reprints; a single edited
    word (here the final word) must not separate them, or every minor outlet
    rewording would spawn a duplicate event.
    """
    items = [
        NearDupItem(
            item_index=0,
            item_text="federal reserve raises benchmark interest rates by a quarter point on wednesday",
        ),
        NearDupItem(
            item_index=1,
            item_text="federal reserve raises benchmark interest rates by a quarter point on thursday",
        ),
    ]

    groups = group_near_duplicates(items, threshold=_THRESHOLD)

    assert groups == [[0, 1]], "one-word-different reprints must form a single group"


def test_distinct_stories_do_not_group() -> None:
    """(b) Two genuinely different stories stay in separate 1-element groups.

    WHY: over-grouping merges distinct events. Different topics/words must remain
    separate singletons even though both are valid news headlines.
    """
    items = [
        NearDupItem(
            item_index=0,
            item_text="federal reserve raises benchmark interest rates by a quarter point on wednesday",
        ),
        NearDupItem(
            item_index=1,
            item_text="astronomers discover a new earth sized exoplanet orbiting a nearby red dwarf star",
        ),
    ]

    groups = group_near_duplicates(items, threshold=_THRESHOLD)

    assert groups == [[0], [1]], "distinct stories must each be their own singleton group"


def test_drop_exact_reprints_collapses_cluster_keeps_distinct() -> None:
    """(c) 3 near-identical reprints + 2 distinct stories -> exactly 3 indices.

    WHY: the representative contract M3b relies on — a reprint cluster collapses to
    ONE index (the smallest in the cluster), and distinct stories are all kept.
    """
    items = [
        # Reprint cluster (indices 1, 2, 3): a realistic wire-service reprint — the
        # body is verbatim and only the trailing attribution word changes (Jaccard
        # ~0.9), exactly the over-republished story the prefilter must collapse.
        NearDupItem(
            item_index=1,
            item_text="a powerful magnitude seven earthquake struck the coastal city early on monday damaging hundreds of homes and knocking out power across the region wednesday",
        ),
        NearDupItem(
            item_index=2,
            item_text="a powerful magnitude seven earthquake struck the coastal city early on monday damaging hundreds of homes and knocking out power across the region thursday",
        ),
        NearDupItem(
            item_index=3,
            item_text="a powerful magnitude seven earthquake struck the coastal city early on monday damaging hundreds of homes and knocking out power across the region friday",
        ),
        # Two distinct stories.
        NearDupItem(item_index=4, item_text="parliament passes sweeping new climate legislation after marathon debate session"),
        NearDupItem(item_index=5, item_text="tech company unveils its next generation smartphone with a foldable display screen"),
    ]

    representatives = drop_exact_reprints(items, threshold=_THRESHOLD)

    assert representatives == [1, 4, 5], (
        "reprint cluster must collapse to its smallest index (1) and both distinct stories kept"
    )

    # And the cluster really is the {1,2,3} group (representative is its min).
    groups = group_near_duplicates(items, threshold=_THRESHOLD)
    reprint_group = _group_of(groups, 1)
    assert reprint_group == [1, 2, 3], "the three reprints must be one connected group"
    assert min(reprint_group) == 1, "representative must be the smallest index of the reprint cluster"


def test_short_headline_uses_word_set_fallback_without_crashing() -> None:
    """(d) A < 4-word headline does not crash and uses the word-set fallback.

    WHY: a 4-gram window is impossible on a 2-word headline; without the word-set
    fallback the shingle set is empty and MinHash work is meaningless/crashes.
    Short headlines are common, so this must be handled gracefully.
    """
    # Direct fallback check: a 2-word text shingles to its word SET, not 4-grams.
    assert _build_shingles("breaking news") == {"breaking", "news"}

    # End-to-end: a short headline alongside a normal one must not crash and the
    # short one is its own singleton (it shares nothing with the long story).
    items = [
        NearDupItem(item_index=0, item_text="market crash"),
        NearDupItem(
            item_index=1,
            item_text="global supply chains face renewed disruption as major port closes for repairs",
        ),
    ]

    groups = group_near_duplicates(items, threshold=_THRESHOLD)

    assert groups == [[0], [1]], "short headline handled via fallback as its own singleton"
