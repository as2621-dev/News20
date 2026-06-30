"""Unit tests for the pure interest-collapse transform (FSR M5 SP2 + SP4 parity).

These encode WHY the collapse matters (Rule 9): M5 collapses every deep
``user_interest_profile`` pointer to its depth-0 ROOT interest so the onboarding
taxonomy is roots-only end-to-end (the picker only emits roots; history is folded up
once). A regression here would either lose a user's interest (wrong root), double-count
it (dedup by sum instead of max), or drift from ``category_for_slug`` (the SQL twin
migration ``0024`` mirrors this exact rule) — each breaking the milestone.

Pure functions / pure data — no DB, no LLM, no clock. The SQL twin (SP3) is asserted
for PARITY against this transform in :class:`TestCollapseParityWithCategoryForSlug`.
"""

from __future__ import annotations

from agents.pipeline.categories import (
    DEFAULT_CATEGORY,
    TOPIC_CATEGORIES,
    ProfileInterestRow,
    category_for_slug,
    collapse_profile_rows_to_roots,
    root_interest_slug_for_category,
)


def _row(
    user: str, slug: str, weight: float, source: str = "typed"
) -> ProfileInterestRow:
    """Build a ProfileInterestRow fixture (terse helper for the cases below)."""
    return ProfileInterestRow(
        profile_user_id=user,
        interest_slug=slug,
        profile_weight=weight,
        profile_source=source,
    )


def _identity_tuples(rows: list[ProfileInterestRow]) -> list[tuple[str, str, float]]:
    """Sorted (user, slug, weight) tuples — an order-independent set comparison key."""
    return sorted(
        (row.profile_user_id, row.interest_slug, row.profile_weight) for row in rows
    )


class TestDeepToRoot:
    """Every deep slug collapses to its depth-0 root segment's category."""

    def test_sport_leaf_collapses_to_sport_root(self) -> None:
        # WHY: a depth-2 leaf must point at its ROOT, not stay deep (the migration's
        # whole job). sport.soccer.epl → sport.
        [collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "sport.soccer.epl", 1.0)]
        )
        assert collapsed.interest_slug == "sport"

    def test_business_leaf_collapses_to_business_root(self) -> None:
        [collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "business.equities.semis", 2.0)]
        )
        assert collapsed.interest_slug == "business"

    def test_source_is_carried_through_unchanged(self) -> None:
        # WHY: collapse repoints the interest, it must NOT rewrite provenance — a
        # 'signal' pick stays a 'signal' pick at its root.
        [collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "ai.interpretability", 1.0, "signal")]
        )
        assert collapsed.profile_source == "signal"
        assert collapsed.interest_slug == "ai"


class TestDedupKeepsHigherWeight:
    """Two deep rows of one user collapsing to the same root → ONE row, MAX weight."""

    def test_same_root_pair_dedups_to_max_weight(self) -> None:
        # WHY (the load-bearing rule): sport.soccer (w=1) + sport.cricket (w=3) both
        # collapse to `sport`, colliding on (user, sport). The kept row's weight must
        # be 3 — the MAX. A sum would give 4, an average 2; both FAIL here. This is the
        # exact unique-constraint collision the SQL migration resolves identically.
        collapsed = collapse_profile_rows_to_roots(
            [
                _row("u1", "sport.soccer", 1.0, "typed"),
                _row("u1", "sport.cricket", 3.0, "signal"),
            ]
        )
        assert len(collapsed) == 1
        assert collapsed[0].interest_slug == "sport"
        assert collapsed[0].profile_weight == 3.0  # MAX, not 4.0 (sum) or 2.0 (avg)
        # The kept (max-weight) row carries ITS source.
        assert collapsed[0].profile_source == "signal"

    def test_lower_weight_row_is_dropped_not_merged(self) -> None:
        # WHY: assert the LOSER vanishes (one row out), not that weights blend.
        collapsed = collapse_profile_rows_to_roots(
            [
                _row("u1", "sport.cricket", 5.0),
                _row("u1", "sport.soccer", 2.0),
            ]
        )
        assert len(collapsed) == 1
        assert collapsed[0].profile_weight == 5.0

    def test_dedup_is_per_user_not_global(self) -> None:
        # WHY: two DIFFERENT users each with a sport row must keep BOTH (the dedup key
        # is (user, root)). A global dedup would wrongly drop one user's interest.
        collapsed = collapse_profile_rows_to_roots(
            [
                _row("u1", "sport.soccer", 1.0),
                _row("u2", "sport.cricket", 1.0),
            ]
        )
        assert len(collapsed) == 2
        assert {row.profile_user_id for row in collapsed} == {"u1", "u2"}

    def test_different_roots_one_user_both_kept(self) -> None:
        # WHY: collapse only merges WITHIN a root; distinct roots stay distinct rows.
        collapsed = collapse_profile_rows_to_roots(
            [
                _row("u1", "sport.soccer", 1.0),
                _row("u1", "business.equities", 1.0),
            ]
        )
        assert {row.interest_slug for row in collapsed} == {"sport", "business"}


class TestIdempotency:
    """Feeding the function its own output is a fixed point (no further change)."""

    def test_collapsing_collapsed_output_is_a_noop(self) -> None:
        # WHY: the migration must be safe to re-run (and a re-onboard must not re-deepen
        # anything). The second pass over already-root rows must produce the SAME set —
        # same roots, same weights, no new dupes. A non-idempotent rule (e.g. one that
        # re-summed) would change the result on the second apply; this FAILS that.
        once = collapse_profile_rows_to_roots(
            [
                _row("u1", "sport.soccer", 1.0),
                _row("u1", "sport.cricket", 3.0),
                _row("u1", "business.equities.semis", 2.0),
            ]
        )
        twice = collapse_profile_rows_to_roots(once)
        assert _identity_tuples(twice) == _identity_tuples(once)

    def test_already_root_row_is_unchanged(self) -> None:
        # WHY: a row already pointing at its root (`sport`) is a no-op — slug, weight,
        # and source all survive. The migration's WHERE guard skips these in SQL.
        [collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "sport", 4.0, "typed")]
        )
        assert collapsed.interest_slug == "sport"
        assert collapsed.profile_weight == 4.0
        assert collapsed.profile_source == "typed"


class TestUnknownFallback:
    """An unknown-root slug falls back per category_for_slug (→ arts), never crashes."""

    def test_unknown_root_collapses_to_arts_catch_all(self) -> None:
        # WHY: no slug may crash the collapse or orphan a row; the unknown long-tail
        # lands on `arts` (the DEFAULT_CATEGORY catch-all), exactly like the ranker.
        [collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "totally.unknown.thing", 1.0)]
        )
        assert collapsed.interest_slug == "arts"
        assert collapsed.interest_slug == DEFAULT_CATEGORY

    def test_empty_input_is_empty_output(self) -> None:
        assert collapse_profile_rows_to_roots([]) == []


class TestCollapseParityWithCategoryForSlug:
    """SP4 lock: the transform's deep→root mapping == category_for_slug for the 8 roots
    + legacy aliases + the unknown fallback — the EXACT rule the SQL migration mirrors.

    WHY (Rule 7): the Python transform, the SQL migration (0024), and category_for_slug
    must never diverge. This pins the transform's collapse destination to
    category_for_slug across a representative slug set spanning all 8 roots; a drift in
    EITHER FAILS here, flagging the SQL twin to re-check.
    """

    # A representative slug per root (deep where the root has depth) + legacy aliases +
    # an unknown slug — the inputs the SQL `split_part(slug,'.',1)` join must agree on.
    _REPRESENTATIVE_SLUGS: tuple[str, ...] = (
        "ai.interpretability",
        "geopolitics.sanctions",
        "business.equities.semis",
        "environment.climate",
        "politics.elections",
        "tech.semiconductors",
        "sport.soccer.epl",
        "arts.film",
        # Legacy aliases the deep tree may still carry (category_for_slug remaps them):
        "world",  # → geopolitics
        "markets.equities",  # → business
        "climate",  # → environment
        "entertainment",  # → arts
        # Unknown long-tail → arts fallback.
        "totally.unknown.slug",
    )

    def test_collapse_destination_equals_category_for_slug_for_all_cases(self) -> None:
        for slug in self._REPRESENTATIVE_SLUGS:
            [collapsed] = collapse_profile_rows_to_roots([_row("u1", slug, 1.0)])
            # The collapse destination slug MUST equal the category_for_slug root
            # (which == root_interest_slug_for_category of that category).
            expected_category = category_for_slug(slug)
            assert collapsed.interest_slug == expected_category, slug
            assert collapsed.interest_slug == root_interest_slug_for_category(
                expected_category
            ), slug

    def test_every_collapse_destination_is_one_of_the_eight_topic_roots(self) -> None:
        # WHY: a collapsed row may ONLY point at a depth-0 TOPIC root (never youtube/x,
        # never a deep slug) — the invariant the migration + RLS depend on.
        for slug in self._REPRESENTATIVE_SLUGS:
            [collapsed] = collapse_profile_rows_to_roots([_row("u1", slug, 1.0)])
            assert collapsed.interest_slug in TOPIC_CATEGORIES, slug

    def test_legacy_alias_collapse_targets(self) -> None:
        # WHY: pin the two named legacy-alias collapses the phase calls out explicitly.
        [world_collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "world.sanctions", 1.0)]
        )
        assert world_collapsed.interest_slug == "geopolitics"
        [markets_collapsed] = collapse_profile_rows_to_roots(
            [_row("u1", "markets.equities", 1.0)]
        )
        assert markets_collapsed.interest_slug == "business"
