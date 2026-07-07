"""Unit tests for the canonical feed-category taxonomy (agents/pipeline/categories.py).

These encode WHY the taxonomy matters (Rule 9): SP3 unifies onboarding, "Build your
30", and the reel chip on the **8 picker roots** + 2 source axes with NO folding.
A regression here would silently re-introduce the old fold (ai→tech_science,
politics→world_politics, arts→culture) or re-balance the owner-locked 30-slot split,
breaking onboarding-intent-survives-to-the-reel — the whole point of the phase.

Pure functions / pure data — no DB, no LLM, no clock.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from agents.pipeline.categories import (
    DEFAULT_CATEGORY,
    DEFAULT_FEED_ALLOCATION,
    SLUG_TO_CATEGORY,
    SOURCE_CATEGORIES,
    TOPIC_CATEGORIES,
    FeedCategory,
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


class TestTypescriptTwinDrift:
    """Automated Python ↔ TS twin drift check (issue #35, 2026-06-17 precedent).

    WHY: on 2026-06-17 the backend ``SLUG_TO_CATEGORY`` drifted from the TS twin
    (``src/lib/feedBuckets.ts`` was missing ai/politics/environment parity) and AI
    feeds silently collapsed into culture/markets (fixed in f58cdc4 by eyeball).
    These tests parse the CHECKED-IN TS source, so any future divergence between
    the Python taxonomy and the frontend twin fails CI instead of waiting for a
    human to notice mis-bucketed feeds. Regex-on-source is deliberately the least
    brittle mechanism available here: pytest cannot execute TS, and both literals
    are plain data blocks pinned by these very tests.
    """

    _FEED_BUCKETS_TS = (
        Path(__file__).resolve().parents[3] / "src" / "lib" / "feedBuckets.ts"
    )
    _ARCHETYPE_MATCH_TS = (
        Path(__file__).resolve().parents[3] / "src" / "lib" / "archetypeMatch.ts"
    )

    def _ts_source(self, ts_path: Path | None = None) -> str:
        ts_file = ts_path if ts_path is not None else self._FEED_BUCKETS_TS
        assert ts_file.is_file(), (
            f"TS twin missing at {ts_file} — if the file moved, update this drift check"
        )
        return ts_file.read_text(encoding="utf-8")

    def _ts_block(self, source: str, marker: str) -> str:
        """Extract the literal block that starts at ``marker`` (up to the closing ``;``).

        Comments are stripped FIRST (``/* … */`` then ``// …``) so (a) a ``;``
        inside a comment cannot truncate the block and (b) commented-out entries
        are never parsed as live data (review-panel finding: a disabled
        ``// ["sport", 3],`` line must not keep the twin test green).
        """
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        source = re.sub(r"//.*$", "", source, flags=re.MULTILINE)
        start = source.index(marker)
        return source[start : source.index(";", start)]

    def test_design_bucket_ids_match_feed_category_literal(self) -> None:
        """The TS ``DesignBucketId`` union == the Python ``FeedCategory`` Literal."""
        block = self._ts_block(self._ts_source(), "export type DesignBucketId")
        ts_ids = set(re.findall(r'"([a-z_]+)"', block))
        py_ids = set(get_args(FeedCategory))
        assert ts_ids == py_ids, (
            f"DesignBucketId (TS) != FeedCategory (Py): "
            f"TS-only={sorted(ts_ids - py_ids)} Py-only={sorted(py_ids - ts_ids)}"
        )

    def test_picker_roots_all_present_in_slug_to_category(self) -> None:
        """Every TS picker root maps identically in Python ``SLUG_TO_CATEGORY``.

        This is EXACTLY the 2026-06-17 drift: SLUG_TO_CATEGORY missing
        ai/politics/environment while the TS twin carried them.
        """
        block = self._ts_block(
            self._ts_source(), "export const PICKER_ROOT_TO_CATEGORY_BUCKET"
        )
        ts_roots = dict(
            re.findall(r'^\s*([a-z_]+):\s*"([a-z_]+)"', block, re.MULTILINE)
        )
        # BOTH directions + completeness: a partial regex parse or a root missing
        # from EITHER side fails loudly (review-panel finding: a subset parse would
        # otherwise vacuously pass).
        assert set(ts_roots) == set(TOPIC_CATEGORIES), (
            f"PICKER_ROOT_TO_CATEGORY_BUCKET keys != the 8 topic roots: "
            f"TS-only={sorted(set(ts_roots) - set(TOPIC_CATEGORIES))} "
            f"Py-only={sorted(set(TOPIC_CATEGORIES) - set(ts_roots))}"
        )
        for root_slug, bucket_id in ts_roots.items():
            assert root_slug in SLUG_TO_CATEGORY, (
                f"TS picker root {root_slug!r} missing from Python SLUG_TO_CATEGORY "
                "(the 2026-06-17 drift class — add it)"
            )
            assert SLUG_TO_CATEGORY[root_slug] == bucket_id, (
                f"root {root_slug!r}: TS maps to {bucket_id!r} but Python maps to "
                f"{SLUG_TO_CATEGORY[root_slug]!r}"
            )

    def test_archetype_category_keys_match_topic_roots(self) -> None:
        """TS ``ARCHETYPE_CATEGORY_KEYS`` == the 8 Python ``TOPIC_CATEGORIES`` roots.

        WHY (issue #43): ``src/lib/interestVector.ts`` DERIVES the identity
        entries of ``INTEREST_ROOT_TO_PINNED_KEY`` from ``ARCHETYPE_CATEGORY_KEYS``
        (post-SP3 the picker roots ARE the pinned archetype keys), so the whole
        interest-vector roll-up hangs off that literal. If it drifts from the
        Python taxonomy roots, follows rooted at the divergent root are silently
        dropped from the vector — the Thirty-tab phantom-block regression. The
        TS-side identity check lives in ``tests/lib/interestVector.test.ts``; this
        test pins the cross-language half of the chain.
        """
        block = self._ts_block(
            self._ts_source(self._ARCHETYPE_MATCH_TS),
            "export const ARCHETYPE_CATEGORY_KEYS",
        )
        ts_keys = set(re.findall(r'"([a-z_]+)"', block))
        assert ts_keys == set(TOPIC_CATEGORIES), (
            f"ARCHETYPE_CATEGORY_KEYS (TS) != TOPIC_CATEGORIES (Py): "
            f"TS-only={sorted(ts_keys - set(TOPIC_CATEGORIES))} "
            f"Py-only={sorted(set(TOPIC_CATEGORIES) - ts_keys)}"
        )

    def test_default_allocation_twin_matches_ordered(self) -> None:
        """``DEFAULT_ALLOCATION_SEGMENTS`` (TS) == ``DEFAULT_FEED_ALLOCATION`` (Py),
        same keys, counts AND order (both sides document the order as meaningful)."""
        block = self._ts_block(
            self._ts_source(), "export const DEFAULT_ALLOCATION_SEGMENTS"
        )
        ts_segments = [
            (key, int(count))
            for key, count in re.findall(r'\[\s*"([a-z_]+)"\s*,\s*(\d+)\s*\]', block)
        ]
        assert ts_segments, "failed to parse DEFAULT_ALLOCATION_SEGMENTS from TS"
        py_segments = list(DEFAULT_FEED_ALLOCATION.items())
        assert ts_segments == py_segments, (
            f"allocation twins drifted: TS={ts_segments} Py={py_segments}"
        )
