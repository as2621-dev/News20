"""Unit tests for the canonical feed-category taxonomy (agents/pipeline/categories.py).

These encode WHY the taxonomy matters (Rule 9): SP3 unifies onboarding, "Build your
30", and the reel chip on the **8 picker roots** + 2 source axes with NO folding.
A regression here would silently re-introduce the old fold (ai→tech_science,
politics→world_politics, arts→culture) or re-balance the owner-locked 30-slot split,
breaking onboarding-intent-survives-to-the-reel — the whole point of the phase.

Pure functions / pure data — no DB, no LLM, no clock.
"""

from __future__ import annotations

from agents.pipeline.categories import (
    DEFAULT_CATEGORY,
    DEFAULT_FEED_ALLOCATION,
    SOURCE_CATEGORIES,
    TOPIC_CATEGORIES,
    category_for_slug,
    empty_category_buckets,
)

# The canonical roots SP3 locks: 8 topic roots + 2 source axes = 10 keys total.
_EIGHT_ROOTS: tuple[str, ...] = (
    "ai",
    "geopolitics",
    "business",
    "environment",
    "politics",
    "tech",
    "sport",
    "arts",
)
_SOURCE_AXES: tuple[str, ...] = ("youtube", "x")
_ALL_TEN_KEYS: frozenset[str] = frozenset(_EIGHT_ROOTS) | frozenset(_SOURCE_AXES)


class TestNoFold:
    """Each picker root classifies to ITSELF — the old cross-fold is retired."""

    def test_sport_subcategory_resolves_to_sport_root(self) -> None:
        # A dotted leaf must classify by its depth-0 root segment.
        assert category_for_slug("sport.cricket.india") == "sport"

    def test_ai_is_its_own_root_not_tech_science(self) -> None:
        # Reason: ai must NOT fold into tech (old taxonomy folded ai→tech_science).
        assert category_for_slug("ai.interpretability") == "ai"

    def test_politics_is_its_own_root_not_world_politics(self) -> None:
        # Reason: politics must NOT fold into geopolitics (old: politics→world_politics).
        assert category_for_slug("politics.x") == "politics"

    def test_environment_is_its_own_root_not_folded(self) -> None:
        # Reason: environment is a first-class root (old: climate→world_politics).
        assert category_for_slug("environment.climate") == "environment"

    def test_geopolitics_stays_geopolitics(self) -> None:
        assert category_for_slug("geopolitics.sanctions") == "geopolitics"

    def test_business_leaf_resolves_to_business_not_markets(self) -> None:
        # Old taxonomy folded business→markets; business is now its own root.
        assert category_for_slug("business.equities.semis") == "business"


class TestLegacyAliases:
    """Legacy alias slugs remap deterministically to the new roots (no cross-fold)."""

    def test_world_alias_maps_to_geopolitics(self) -> None:
        assert category_for_slug("world") == "geopolitics"

    def test_climate_alias_maps_to_environment(self) -> None:
        assert category_for_slug("climate") == "environment"

    def test_markets_alias_maps_to_business(self) -> None:
        assert category_for_slug("markets.equities") == "business"

    def test_crypto_alias_maps_to_business(self) -> None:
        assert category_for_slug("crypto") == "business"

    def test_entertainment_alias_maps_to_arts(self) -> None:
        assert category_for_slug("entertainment") == "arts"

    def test_science_alias_maps_to_tech(self) -> None:
        assert category_for_slug("science") == "tech"

    def test_unknown_root_falls_back_to_arts_catch_all(self) -> None:
        # Reason: arts replaces culture as the long-tail catch-all; nothing crashes.
        assert category_for_slug("totally.unknown.slug") == "arts"
        assert DEFAULT_CATEGORY == "arts"

    def test_empty_slug_falls_back_to_default(self) -> None:
        assert category_for_slug("") == DEFAULT_CATEGORY


class TestLockedAllocation:
    """The owner-locked 30-slot split must hold exactly (FAIL on silent re-balance)."""

    def test_allocation_sums_to_thirty(self) -> None:
        assert sum(DEFAULT_FEED_ALLOCATION.values()) == 30

    def test_locked_per_category_counts(self) -> None:
        # Reason: pin the exact owner-locked split so a silent re-balance FAILS here.
        assert DEFAULT_FEED_ALLOCATION["ai"] == 4
        assert DEFAULT_FEED_ALLOCATION["tech"] == 4
        assert DEFAULT_FEED_ALLOCATION["geopolitics"] == 4
        assert DEFAULT_FEED_ALLOCATION["business"] == 4
        assert DEFAULT_FEED_ALLOCATION["politics"] == 2
        assert DEFAULT_FEED_ALLOCATION["environment"] == 2
        assert DEFAULT_FEED_ALLOCATION["sport"] == 3
        assert DEFAULT_FEED_ALLOCATION["arts"] == 3
        assert DEFAULT_FEED_ALLOCATION["youtube"] == 2
        assert DEFAULT_FEED_ALLOCATION["x"] == 2

    def test_allocation_has_exactly_the_ten_keys(self) -> None:
        assert frozenset(DEFAULT_FEED_ALLOCATION.keys()) == _ALL_TEN_KEYS


class TestKeyCompleteness:
    """All 8 roots + 2 source axes must be present everywhere a key set is listed."""

    def test_topic_categories_are_the_eight_roots(self) -> None:
        assert tuple(TOPIC_CATEGORIES) == _EIGHT_ROOTS

    def test_source_categories_are_youtube_and_x(self) -> None:
        assert tuple(SOURCE_CATEGORIES) == _SOURCE_AXES

    def test_empty_buckets_has_all_ten_keys_empty(self) -> None:
        buckets = empty_category_buckets()
        assert frozenset(buckets.keys()) == _ALL_TEN_KEYS
        assert all(bucket_items == [] for bucket_items in buckets.values())
