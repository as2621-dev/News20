"""Unit tests for the interest-keyed ingestion pipeline (Phase 1d SP1).

Covers the empty-safe active-interest builder (fail-loud DoD), its dedup/skip
rules, and the end-to-end ingest with a fake adapter: cross-outlet clustering +
outlet counts, ancestor tagging, body extraction, and per-interest failure
resilience (one source failure does not abort the batch).

    >>> pytest tests/agents/ingestion/test_interest_keyed_pipeline.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.interest_keyed_pipeline import (
    build_active_interest_set,
    ingest_active_interests,
)
from agents.ingestion.models import CandidateStory
from agents.shared.exceptions import AdapterFetchError, IngestionError

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
_EARLIER = datetime(2026, 5, 31, 9, 0, 0, tzinfo=timezone.utc)


class _FakeAdapter(BaseNewsAdapter):
    """A deterministic in-memory adapter: query → fixed candidates, no network.

    ``fail_queries`` lets a test force an AdapterFetchError for specific queries
    to exercise per-interest resilience.
    """

    def __init__(self, fail_queries: set[str] | None = None) -> None:
        self.fail_queries = fail_queries or set()
        self.extract_calls = 0

    async def search(
        self, search_query: str, since_utc, **kwargs
    ) -> list[CandidateStory]:
        if search_query in self.fail_queries:
            raise AdapterFetchError(message="forced", adapter_name="fake")
        if search_query == "Arsenal FC":
            return [
                CandidateStory(
                    candidate_external_id="cnn-1",
                    candidate_title="Arsenal win at the Emirates",
                    candidate_url="https://cnn.com/arsenal",
                    candidate_outlet_domain="cnn.com",
                    candidate_published_utc=_NOW,
                ),
                CandidateStory(
                    candidate_external_id="bbc-1",
                    candidate_title="Arsenal win at the Emirates!",
                    candidate_url="https://bbc.com/arsenal",
                    candidate_outlet_domain="bbc.com",
                    candidate_published_utc=_EARLIER,
                ),
            ]
        if search_query == "stock market":
            return [
                CandidateStory(
                    candidate_external_id="reuters-1",
                    candidate_title="Markets rally on rate-cut hopes",
                    candidate_url="https://reuters.com/markets",
                    candidate_outlet_domain="reuters.com",
                    candidate_published_utc=_NOW,
                ),
            ]
        return []

    async def extract_body(self, candidate: CandidateStory, **kwargs) -> CandidateStory:
        self.extract_calls += 1
        candidate.candidate_body_text = f"Body for {candidate.candidate_url}"
        return candidate


class TestBuildActiveInterestSet:
    """The distinct active-interest set + the empty-safe fail-loud."""

    def test_empty_followed_ids_raises(self, interest_nodes) -> None:
        """No profiles → IngestionError (DoD empty-safe)."""
        with pytest.raises(IngestionError):
            build_active_interest_set([], interest_nodes)

    def test_dedups_skips_no_query_and_unknown(
        self, interest_nodes, interest_ids
    ) -> None:
        """Duplicates collapse; query-less + unknown interests are skipped."""
        followed = [
            interest_ids["arsenal"],
            interest_ids["arsenal"],  # duplicate across users
            interest_ids["soccer"],  # no search query → skipped
            interest_ids["sport"],  # no search query → skipped
            interest_ids["markets"],
            "ghost-interest",  # not in taxonomy → skipped
        ]
        active = build_active_interest_set(followed, interest_nodes)
        slugs = [a.interest_slug for a in active]
        assert slugs == ["markets", "sport.soccer.arsenal"]  # sorted by slug, deduped


class TestIngestActiveInterests:
    """End-to-end ingest with the fake adapter."""

    @pytest.mark.asyncio
    async def test_clusters_counts_outlets_and_tags(
        self, interest_nodes, interest_ids
    ) -> None:
        """Arsenal + Markets ingest → 2 stories, outlet counts + ancestor tags."""
        result = await ingest_active_interests(
            [interest_ids["arsenal"], interest_ids["markets"]],
            interest_nodes,
            _FakeAdapter(),
        )

        assert result.total_candidates_fetched == 3
        assert len(result.canonical_stories) == 2

        arsenal_story = next(
            s
            for s in result.canonical_stories
            if interest_ids["arsenal"] in s.canonical_matched_interest_ids
        )
        assert arsenal_story.story_outlet_count == 2
        assert arsenal_story.covering_outlets == ["bbc.com", "cnn.com"]
        assert arsenal_story.canonical_body_text is not None  # extracted

        # Arsenal story → 3 tags (self/parent/grandparent); Markets → 1 tag.
        tags_by_story: dict[str, list[int]] = {}
        for tag in result.story_interest_tags:
            tags_by_story.setdefault(tag.story_interest_story_id, []).append(
                tag.story_interest_match_depth
            )
        assert sorted(tags_by_story[arsenal_story.canonical_story_id]) == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_one_source_failure_does_not_abort_batch(
        self, interest_nodes, interest_ids
    ) -> None:
        """If Arsenal's query fails, Markets still ingests (resilience)."""
        adapter = _FakeAdapter(fail_queries={"Arsenal FC"})
        result = await ingest_active_interests(
            [interest_ids["arsenal"], interest_ids["markets"]],
            interest_nodes,
            adapter,
        )
        assert len(result.canonical_stories) == 1
        assert (
            result.canonical_stories[0].canonical_primary_outlet_domain == "reuters.com"
        )

    @pytest.mark.asyncio
    async def test_extract_bodies_false_skips_extraction(
        self, interest_nodes, interest_ids
    ) -> None:
        """With extract_bodies=False, no extract call runs and body stays None."""
        adapter = _FakeAdapter()
        result = await ingest_active_interests(
            [interest_ids["markets"]],
            interest_nodes,
            adapter,
            extract_bodies=False,
        )
        assert adapter.extract_calls == 0
        assert result.canonical_stories[0].canonical_body_text is None
