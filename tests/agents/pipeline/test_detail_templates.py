"""Unit tests for the per-category Detail panel templates + resolvers.

These encode WHY (Rule 9): the Detail page's whole correctness rests on each
category mapping to EXACTLY the owner-locked ordered panels — Culture must carry
no Coverage, Markets must carry MARKET IMPACT + BY THE NUMBERS, sources must carry
no timeline. A test that only checked "a template exists" could not catch a wrong
panel; these assert the exact kinds, labels, and order, and the resolver's
culture-fallback rule. (phase-SP1 removed the breaking detail template; the
``is_breaking`` arg is now accepted-but-ignored and resolves to the topic category.)

    >>> pytest tests/agents/pipeline/test_detail_templates.py -v
"""

from __future__ import annotations

import pytest

from agents.pipeline.detail_templates import (
    DEFAULT_DETAIL_CATEGORY,
    DETAIL_TEMPLATES,
    analytic_panel_specs,
    detail_category_for,
    detail_category_for_segment,
)

# The owner-locked table (2026-06-16), transcribed as (kind | None, label | None)
# per slot. `None` kind = a non-analytic panel; the second element is the coverage
# mode for a coverage slot, or "timeline". This is the single assertion of truth the
# TS twin (`src/lib/detailTemplates.test.ts`) must mirror exactly.
_EXPECTED: dict[str, list[tuple]] = {
    "world": [("timeline", None), ("stakes", "STAKES"), ("coverage", "partisan")],
    "markets": [("timeline", None), ("market_impact", "MARKET IMPACT"), ("by_the_numbers", "BY THE NUMBERS")],
    "tech": [("timeline", None), ("why_it_matters", "WHY IT MATTERS"), ("the_concept", "THE CONCEPT")],
    "sport": [("timeline", None), ("stat_line", "STAT LINE"), ("recent_form", "RECENT FORM")],
    "culture": [("timeline", None), ("subject_profile", "PROFILE"), ("why_it_matters", "WHY IT MATTERS")],
    "youtube": [("source_context", "THE VIDEO"), ("key_points", "KEY POINTS"), ("implications", "IMPLICATIONS")],
    "podcasts": [("source_context", "THE EPISODE"), ("key_points", "KEY POINTS"), ("implications", "IMPLICATIONS")],
    "x": [("source_context", "THE GIST"), ("key_points", "KEY POINTS"), ("implications", "IMPLICATIONS")],
}


class TestTemplateShape:
    """Every category renders exactly the locked ordered triple of panels."""

    def test_all_eight_categories_present(self) -> None:
        """The template map covers exactly the 8 detail categories — no more, no less.

        phase-SP1 removed the ``breaking`` detail template, leaving 8 (5 topic + 3
        source).
        """
        assert set(DETAIL_TEMPLATES) == set(_EXPECTED)
        assert "breaking" not in DETAIL_TEMPLATES
        assert len(DETAIL_TEMPLATES) == 8

    @pytest.mark.parametrize("category", list(_EXPECTED))
    def test_each_template_matches_locked_table(self, category) -> None:
        """Each category's panels match the owner table exactly (kind, label, order).

        WHY: this is THE place a wrong panel (e.g. Coverage leaking onto Culture, or
        MARKET IMPACT onto Sport) is caught. A drift here is a product-visible bug.
        """
        specs = DETAIL_TEMPLATES[category]
        actual: list[tuple] = []
        for spec in specs:
            if spec.panel_kind == "timeline":
                actual.append(("timeline", None))
            elif spec.panel_kind == "coverage":
                actual.append(("coverage", spec.coverage_mode))
            else:
                actual.append((spec.analytic_kind, spec.analytic_tab_label))
        assert actual == _EXPECTED[category]

    def test_sources_have_no_timeline_or_coverage(self) -> None:
        """Source categories are single-creator content — no timeline, no coverage."""
        for category in ("youtube", "podcasts", "x"):
            kinds = {spec.panel_kind for spec in DETAIL_TEMPLATES[category]}
            assert kinds == {"analytic"}

    def test_coverage_only_on_world(self) -> None:
        """Coverage earns its slot only where it is meaningful (contested topics).

        phase-SP1 removed the breaking template, so ``world`` is now the SOLE
        category carrying a coverage panel.
        """
        with_coverage = {
            category
            for category, specs in DETAIL_TEMPLATES.items()
            if any(spec.panel_kind == "coverage" for spec in specs)
        }
        assert with_coverage == {"world"}

    def test_analytic_panel_specs_are_in_slot_order(self) -> None:
        """`analytic_panel_specs` returns only analytic panels, in slot order."""
        assert [s.analytic_kind for s in analytic_panel_specs("markets")] == [
            "market_impact",
            "by_the_numbers",
        ]
        # Sources: all three slots are analytic panels.
        assert len(analytic_panel_specs("youtube")) == 3


class TestDetailCategoryForSegment:
    """The segment→category resolver (the call site for GDELT topic stories)."""

    @pytest.mark.parametrize(
        ("segment_slug", "expected"),
        [
            ("geopolitics", "world"),
            ("markets", "markets"),
            ("tech", "tech"),
            ("sport", "sport"),
            ("wildcard", "culture"),
        ],
    )
    def test_each_segment_maps_to_its_topic_category(self, segment_slug, expected) -> None:
        """Each of the 5 segments maps to its topic detail category (wildcard→culture)."""
        assert detail_category_for_segment(segment_slug, is_breaking=False) == expected

    def test_is_breaking_is_ignored_and_resolves_to_topic(self) -> None:
        """``is_breaking`` is accepted-but-ignored (phase-SP1) — the story keeps its
        topic detail category instead of routing to a removed Breaking template."""
        assert detail_category_for_segment("geopolitics", is_breaking=True) == "world"
        assert detail_category_for_segment("markets", is_breaking=True) == "markets"

    def test_unknown_segment_falls_back_to_culture(self) -> None:
        """An unknown/future segment falls back to the Culture catch-all (never crashes)."""
        assert detail_category_for_segment("health.policy", is_breaking=False) == DEFAULT_DETAIL_CATEGORY
        assert detail_category_for_segment("", is_breaking=False) == "culture"


class TestDetailCategoryForFeedCategory:
    """The feed_category→category resolver (the general path; source buckets pass through)."""

    @pytest.mark.parametrize(
        ("feed_category", "expected"),
        [
            ("world_politics", "world"),
            ("tech_science", "tech"),
            ("markets", "markets"),
            ("sport", "sport"),
            ("culture", "culture"),
            ("youtube", "youtube"),
            ("x", "x"),
            ("podcasts", "podcasts"),
        ],
    )
    def test_each_feed_category_maps(self, feed_category, expected) -> None:
        """Each feed_category maps to its detail category; source buckets pass through."""
        assert detail_category_for(feed_category, is_breaking=False) == expected

    def test_is_breaking_ignored_and_none_falls_back(self) -> None:
        """``is_breaking`` is ignored (phase-SP1); a None/unknown feed_category falls
        back to Culture."""
        assert detail_category_for("world_politics", is_breaking=True) == "world"
        assert detail_category_for(None, is_breaking=False) == "culture"
        assert detail_category_for("not-a-category", is_breaking=False) == "culture"
