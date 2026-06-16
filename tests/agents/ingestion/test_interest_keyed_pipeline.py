"""Unit tests for the interest-keyed ingestion pipeline (Phase 1d SP1).

Covers the empty-safe active-interest builder (fail-loud DoD), its dedup/skip
rules, and the end-to-end ingest with a fake adapter: cross-outlet clustering +
outlet counts, ancestor tagging, body extraction, and per-interest failure
resilience (one source failure does not abort the batch).

    >>> pytest tests/agents/ingestion/test_interest_keyed_pipeline.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.dedup import normalize_url
from agents.ingestion.interest_keyed_pipeline import (
    _DEFAULT_LOOKBACK_DAYS,
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


class _UrlIdAdapter(BaseNewsAdapter):
    """Adapter mirroring PRODUCTION: candidate_external_id == candidate_url
    (GDELT sets both to the article URL, gdelt_doc.py:313–315). This is what makes
    ``member_candidate_ids`` the member URLs the cross-day resolver looks up.
    """

    def __init__(self) -> None:
        self.extract_calls = 0

    async def search(self, search_query, since_utc, **kwargs):
        if search_query != "Arsenal FC":
            return []
        return [
            CandidateStory(
                candidate_external_id="https://cnn.com/arsenal",
                candidate_title="Arsenal win at the Emirates",
                candidate_url="https://cnn.com/arsenal",
                candidate_outlet_domain="cnn.com",
                candidate_published_utc=_NOW,
            ),
            CandidateStory(
                candidate_external_id="https://bbc.com/arsenal",
                candidate_title="Arsenal win at the Emirates!",
                candidate_url="https://bbc.com/arsenal",
                candidate_outlet_domain="bbc.com",
                candidate_published_utc=_EARLIER,
            ),
        ]

    async def extract_body(self, candidate, **kwargs):
        self.extract_calls += 1
        candidate.candidate_body_text = f"Body for {candidate.candidate_url}"
        return candidate


class TestCrossDayIdentityResolver:
    """D2: a re-clustered multi-day event reuses its original story id (0006).

    WHY this matters: without it, tomorrow's batch derives a NEW
    ``canonical_story_id`` for the same event, the produce-once gate misses, the
    story is re-produced (paid) AND re-allocated — so the user sees it again
    (don't-repeat keys on the story id). Reuse keeps one id per event across days.
    """

    @pytest.mark.asyncio
    async def test_resolver_reuses_existing_id_and_skips_extraction(
        self, interest_nodes, interest_ids
    ) -> None:
        adapter = _UrlIdAdapter()
        # The resolver knows one of the Arsenal event's member URLs was already
        # persisted yesterday as story id 'EXISTING-9'.
        existing = {normalize_url("https://bbc.com/arsenal"): "EXISTING-9"}

        def resolve(normalized_urls):
            return {u: existing[u] for u in normalized_urls if u in existing}

        result = await ingest_active_interests(
            [interest_ids["arsenal"]],
            interest_nodes,
            adapter,
            resolve_existing_story_ids=resolve,
        )

        arsenal = result.canonical_stories[0]
        # The freshly-derived id is REPLACED with yesterday's persisted id …
        assert arsenal.canonical_story_id == "EXISTING-9"
        # … the body fetch is SKIPPED (already produced — saves the paid re-fetch) …
        assert adapter.extract_calls == 0
        assert arsenal.canonical_body_text is None
        # … and the ancestor tags FK to the reused id (so scoring/allocation align).
        assert all(
            tag.story_interest_story_id == "EXISTING-9"
            for tag in result.story_interest_tags
        )

    @pytest.mark.asyncio
    async def test_unknown_event_mints_new_id_and_extracts(
        self, interest_nodes, interest_ids
    ) -> None:
        """A first-seen event (no alias hit) keeps its minted id and IS extracted —
        the resolver must not suppress genuinely new stories."""
        adapter = _UrlIdAdapter()
        result = await ingest_active_interests(
            [interest_ids["arsenal"]],
            interest_nodes,
            adapter,
            resolve_existing_story_ids=lambda _urls: {},
        )
        arsenal = result.canonical_stories[0]
        assert not arsenal.canonical_story_id.startswith("EXISTING")
        assert adapter.extract_calls == 1
        assert arsenal.canonical_body_text is not None


class _SinceRecordingAdapter(BaseNewsAdapter):
    """Records the ``since_utc`` lower bound the pipeline passes to ``search``.

    WHY: the catalog window is only correct if the pipeline derives ``since`` from
    ``_DEFAULT_LOOKBACK_DAYS`` when no override is given. Capturing the value the
    adapter actually receives is the behavioural proof of that window.
    """

    def __init__(self) -> None:
        self.received_since_utc: datetime | None = None

    async def search(self, search_query, since_utc, **kwargs):
        self.received_since_utc = since_utc
        return []

    async def extract_body(self, candidate, **kwargs):
        return candidate


class TestCatalogWindowDefaultLookback:
    """The default ingest window is 24h, and an explicit override is honoured.

    WHY this matters (Phase 7c SP1): the pipeline runs daily at midnight ET and
    should ingest only "today's" news. A wider default would re-surface stale
    stories; a narrower-but-overridable window lets ops widen it deliberately
    (e.g. ``LOOKBACK_DAYS=2`` after a missed run) without a code change.
    """

    def test_default_lookback_constant_is_one_day(self) -> None:
        """The 24h window is encoded in the module constant (DoD: constant == 1)."""
        assert _DEFAULT_LOOKBACK_DAYS == 1

    @pytest.mark.asyncio
    async def test_default_since_is_now_minus_one_day(
        self, interest_nodes, interest_ids
    ) -> None:
        """With no ``since_utc`` override, ``since`` is ~now − 1 day (24h window)."""
        adapter = _SinceRecordingAdapter()
        before = datetime.now(timezone.utc)
        await ingest_active_interests(
            [interest_ids["arsenal"]],
            interest_nodes,
            adapter,
        )
        after = datetime.now(timezone.utc)

        assert adapter.received_since_utc is not None
        # since == now − 1 day, computed at call time; bound it by the call window.
        assert (before - timedelta(days=1)) <= adapter.received_since_utc <= (
            after - timedelta(days=1)
        )

    @pytest.mark.asyncio
    async def test_explicit_since_override_is_honoured(
        self, interest_nodes, interest_ids
    ) -> None:
        """An explicit ``since_utc`` (e.g. a 2-day window) overrides the default.

        WHY: this is the path ``LOOKBACK_DAYS=2`` drives — ``run_live_batch``
        computes ``now − LOOKBACK_DAYS`` and passes it as ``since_utc``.
        """
        adapter = _SinceRecordingAdapter()
        two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
        await ingest_active_interests(
            [interest_ids["arsenal"]],
            interest_nodes,
            adapter,
            since_utc=two_days_ago,
        )
        assert adapter.received_since_utc == two_days_ago
