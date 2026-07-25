"""Unit tests for the GDELT ``V2Themes`` → ``FeedCategory`` whitelist (SP1).

These encode WHY the mapping matters (Rule 9): M2's whole point is that a story's
news category comes from what it is *about* (its GDELT themes), not from the keyword
interest that fetched it. A regression here — the tiebreak silently changing, a value
drifting to a source-axis category, or the no-theme path raising instead of falling
back — would re-break the category signal the rest of M2 is built on.

Pure functions / pure data — no DB, no LLM, no clock, no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from types import SimpleNamespace

from agents.pipeline import theme_category
from agents.pipeline.categories import TOPIC_CATEGORIES
from agents.pipeline.theme_category import (
    THEME_CATEGORY_WHITELIST,
    category_for_themes,
)


class TestRepresentativePerCategory:
    """(a) One representative theme code per category resolves to that category.

    WHY: proves the whitelist actually spans all 8 topic roots (representative
    coverage DoD) — not just the easy ones — so every kind of story is classifiable.
    """

    @pytest.mark.parametrize(
        ("theme", "expected"),
        [
            ("TECH_ARTIFICIAL_INTELLIGENCE", "ai"),
            ("ARMEDCONFLICT", "geopolitics"),
            ("ECON_STOCKMARKET", "business"),
            ("ENV_CLIMATECHANGE", "environment"),
            ("ELECTION", "politics"),
            ("TECH_CYBERSECURITY", "tech"),
            ("SPORT", "sport"),
            ("ARTS", "arts"),
        ],
    )
    def test_representative_theme_maps_to_expected_category(
        self, theme: str, expected: str
    ) -> None:
        assert category_for_themes([theme]) == expected


class TestTiebreak:
    """(b) The deterministic multi-theme tiebreak — pinned, fails if the rule changes."""

    def test_highest_hit_count_wins(self) -> None:
        # WHY: business has 2 hits (ECON_STOCKMARKET + WB_2670_JOBS) vs environment's
        # 1 (ENV_CLIMATECHANGE), so hit-count alone decides — business must win.
        themes = ["ENV_CLIMATECHANGE", "ECON_STOCKMARKET", "WB_2670_JOBS"]
        assert category_for_themes(themes) == "business"

    def test_count_tie_broken_by_pinned_priority_order(self) -> None:
        # WHY (Rule 9): a crafted 1-1 tie between geopolitics and sport. The pinned
        # priority order places geopolitics ABOVE sport, so geopolitics must win.
        # If someone reorders _TIEBREAK_PRIORITY (e.g. lexical, or sport-first), this
        # assertion flips and FAILS — the rule is locked here, not just documented.
        themes = ["ARMEDCONFLICT", "SPORT"]
        assert category_for_themes(themes) == "geopolitics"

    def test_tiebreak_is_not_lexical(self) -> None:
        # WHY: guards specifically against "ties broken lexically" (a plausible but
        # wrong rule). Lexically 'business' < 'geopolitics'; but in a business-vs-
        # geopolitics 1-1 tie our priority order ranks geopolitics first, so the
        # winner is 'geopolitics', proving the rule is the priority list, not sort().
        themes = ["ECON_STOCKMARKET", "ARMEDCONFLICT"]
        assert category_for_themes(themes) == "geopolitics"


class TestNoWhitelistedThemeReturnsNone:
    """(c) A list with no whitelisted theme → ``None`` AND a warning.

    WHY (issue #35): returning a category here is exactly the arts-default bug — the
    caller stamped the fallback at depth 0 and overrode the fetching interest. ``None``
    means "themes carry no category signal"; precedence then belongs to the fetching
    interest's root. A regression to returning DEFAULT_CATEGORY re-breaks precedence,
    so these assert ``is None`` (a test passing with the old arts-default is wrong).
    """

    def test_unknown_themes_return_none(self) -> None:
        assert category_for_themes(["TOTALLY_UNKNOWN", "ALSO_FAKE"]) is None

    def test_unknown_themes_return_none_silently_and_pool_build_aggregates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # WHY (#70 review panel): per-call miss logging was memoization-suppressed
        # on the long-lived worker and per-story-noisy otherwise — the resolver is
        # now silent and miss VISIBILITY is ONE aggregate event per pool build in
        # theme_categories_for_stories (still INFO: post-#35 the fetching-interest
        # fallback is the DESIGNED path, not an error). We patch the module logger
        # (structlog→stdlib caplog routing is environment-fragile).
        fake_logger = MagicMock()
        monkeypatch.setattr(theme_category, "logger", fake_logger)

        assert category_for_themes(["NOPE_NOT_A_THEME"]) is None
        fake_logger.info.assert_not_called()

        story = SimpleNamespace(
            canonical_story_id="s-miss",
            canonical_themes=["NOPE_NOT_A_THEME", "ALSO_UNKNOWN"],
        )
        assert theme_category.theme_categories_for_stories([story]) == {}
        fake_logger.info.assert_called_once()
        event = fake_logger.info.call_args.args[0]
        kwargs = fake_logger.info.call_args.kwargs
        assert event == "theme_category_no_whitelisted_theme"
        assert kwargs["miss_story_count"] == 1
        assert kwargs["pool_story_count"] == 1
        assert ("NOPE_NOT_A_THEME", 1) in kwargs["top_unmatched_codes"]
        assert "fix_suggestion" in kwargs
        assert "whitelist" in kwargs["fix_suggestion"].lower()
        fake_logger.warning.assert_not_called()


class TestEmptyListReturnsNone:
    """(d) Empty list → ``None`` + debug only (nothing to whitelist)."""

    def test_empty_list_returns_none_without_whitelist_noise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_logger = MagicMock()
        monkeypatch.setattr(theme_category, "logger", fake_logger)

        result = category_for_themes([])

        assert result is None
        # WHY: a zero-theme story has no code to add — an "extend the whitelist"
        # info/warning here would be 271-per-batch inapplicable noise, and post-#70
        # the resolver itself is fully silent (miss visibility lives in the pool
        # aggregate, which skips zero-theme stories too).
        fake_logger.warning.assert_not_called()
        fake_logger.info.assert_not_called()
        fake_logger.debug.assert_not_called()

        story = SimpleNamespace(canonical_story_id="s-empty", canonical_themes=[])
        assert theme_category.theme_categories_for_stories([story]) == {}
        fake_logger.info.assert_not_called()


class TestWhitelistInvariant:
    """(e) Drift guard: every whitelist value is a topic category, never a source axis."""

    def test_every_value_is_a_topic_category(self) -> None:
        # WHY: a typo or a source-axis value (youtube/x) leaking into the whitelist
        # would mis-bucket stories into a category no interest slug maps to. This
        # FAILS if categories.py and the whitelist drift apart.
        topic_set = set(TOPIC_CATEGORIES)
        for theme, category in THEME_CATEGORY_WHITELIST.items():
            assert category in topic_set, (
                f"{theme!r} maps to {category!r}, not a topic category"
            )

    def test_whitelist_covers_all_eight_categories(self) -> None:
        # WHY: the SP1 DoD requires representative coverage for EACH of the 8 roots;
        # a category with zero whitelist entries means stories of that kind can never
        # be theme-classified (they would always fall back).
        covered = set(THEME_CATEGORY_WHITELIST.values())
        assert covered == set(TOPIC_CATEGORIES)
