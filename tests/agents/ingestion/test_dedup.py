"""Unit tests for URL/title primitives + the StoryClusterer (Phase 1d SP1).

Covers the ported primitives (normalize_url, compute_title_similarity) and the
adapted clustering behaviour: near-duplicate articles from different outlets
collapse into one canonical story whose story_outlet_count is the distinct
covering-outlet count, and matched interests aggregate across the cluster.

    >>> pytest tests/agents/ingestion/test_dedup.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.ingestion.dedup import (
    StoryClusterer,
    compute_title_similarity,
    normalize_url,
    provisional_story_id,
)

_EARLIER = datetime(2026, 5, 31, 9, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


class TestNormalizeUrl:
    """Ported URL normalization primitive."""

    def test_strips_tracking_and_www_and_scheme_and_slash(self) -> None:
        assert (
            normalize_url("http://www.example.com/article/?utm_source=x&ref=home")
            == "https://example.com/article"
        )

    def test_preserves_real_query_params(self) -> None:
        normalized = normalize_url("https://example.com/s?q=ai&page=2&fbclid=z")
        assert (
            "q=" in normalized and "page=" in normalized and "fbclid" not in normalized
        )

    def test_empty_url_returns_empty(self) -> None:
        assert normalize_url("") == ""


class TestTitleSimilarity:
    """Ported title-similarity primitive."""

    def test_minor_punctuation_diff_above_threshold(self) -> None:
        assert compute_title_similarity("Arsenal win", "Arsenal win!") >= 0.85

    def test_unrelated_titles_below_threshold(self) -> None:
        assert (
            compute_title_similarity(
                "Arsenal win at the Emirates", "Fed holds rates steady"
            )
            < 0.85
        )

    def test_empty_title_is_zero(self) -> None:
        assert compute_title_similarity("", "Arsenal") == 0.0


class TestProvisionalStoryId:
    """The provisional id must be deterministic + stable per normalized URL."""

    def test_stable_for_same_input(self) -> None:
        assert provisional_story_id("https://cnn.com/x") == provisional_story_id(
            "https://cnn.com/x"
        )

    def test_differs_for_different_input(self) -> None:
        assert provisional_story_id("https://cnn.com/x") != provisional_story_id(
            "https://bbc.com/y"
        )


class TestStoryClusterer:
    """The adapted clustering + outlet-count behaviour."""

    def test_cross_outlet_same_story_merges_with_outlet_count(
        self, make_candidate
    ) -> None:
        """Two outlets, near-identical titles → one story, outlet_count = 2."""
        candidates = [
            make_candidate(
                "cnn-1",
                "Arsenal win at the Emirates",
                "https://cnn.com/a",
                "cnn.com",
                published_utc=_LATER,
            ),
            make_candidate(
                "bbc-1",
                "Arsenal win at the Emirates!",
                "https://bbc.com/b",
                "bbc.com",
                published_utc=_EARLIER,
            ),
        ]
        stories = StoryClusterer().cluster_candidates(candidates)

        assert len(stories) == 1
        story = stories[0]
        assert story.story_outlet_count == 2
        assert story.covering_outlets == ["bbc.com", "cnn.com"]  # sorted distinct
        # Representative is the earliest-published member (bbc, _EARLIER).
        assert story.canonical_primary_outlet_domain == "bbc.com"
        assert story.canonical_published_utc == _EARLIER

    def test_distinct_stories_stay_separate(self, make_candidate) -> None:
        """Unrelated titles + URLs → two separate canonical stories."""
        candidates = [
            make_candidate(
                "a", "Arsenal win at the Emirates", "https://cnn.com/a", "cnn.com"
            ),
            make_candidate(
                "b", "Fed holds interest rates steady", "https://wsj.com/b", "wsj.com"
            ),
        ]
        assert len(StoryClusterer().cluster_candidates(candidates)) == 2

    def test_same_outlet_twice_counts_once(self, make_candidate) -> None:
        """Two articles from the same domain on one story → outlet_count = 1."""
        candidates = [
            make_candidate(
                "a", "Arsenal win at the Emirates", "https://cnn.com/a", "cnn.com"
            ),
            make_candidate(
                "b",
                "Arsenal win at the Emirates today",
                "https://cnn.com/a2",
                "cnn.com",
            ),
        ]
        stories = StoryClusterer().cluster_candidates(candidates)
        assert len(stories) == 1
        assert stories[0].story_outlet_count == 1
        assert stories[0].covering_outlets == ["cnn.com"]

    def test_matched_interests_aggregate_across_cluster(self, make_candidate) -> None:
        """A story surfaced by two interests carries both matched interest ids."""
        candidates = [
            make_candidate(
                "cnn-1",
                "Arsenal win at the Emirates",
                "https://cnn.com/a",
                "cnn.com",
                matched_interest_id="int-arsenal",
            ),
            make_candidate(
                "bbc-1",
                "Arsenal win at the Emirates!",
                "https://bbc.com/b",
                "bbc.com",
                matched_interest_id="int-soccer",
            ),
        ]
        stories = StoryClusterer().cluster_candidates(candidates)
        assert stories[0].canonical_matched_interest_ids == [
            "int-arsenal",
            "int-soccer",
        ]

    def test_empty_input_returns_empty(self) -> None:
        assert StoryClusterer().cluster_candidates([]) == []
