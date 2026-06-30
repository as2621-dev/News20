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
    ingest_trusted_outlets,
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


# Reason: each candidate must cluster ALONE (the StoryClusterer merges titles at >=0.85
# SequenceMatcher ratio — near-identical titles, even across outlets, would collapse and
# distort a cell's story count). We build each title from TWO disjoint distinct words —
# one keyed on the OUTLET, one on n — so no two candidates (same outlet or cross-outlet)
# share both and every title stays under the merge threshold. A cell's story count then
# faithfully reflects distinct fetched candidates, not title noise.
_OUTLET_WORDS = [
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
]
_STORY_WORDS = [
    "Eclipse",
    "Harvest",
    "Quantum",
    "Lighthouse",
    "Avalanche",
    "Orchard",
    "Tempest",
    "Compass",
    "Meridian",
    "Cascade",
]

# Stable, distinct per-outlet index assigned on first sight (deterministic per run).
_outlet_word_index: dict[str, int] = {}


def _domain_candidate(domain: str, n: int) -> CandidateStory:
    """A distinct candidate from ``domain`` with a dissimilar title (clusters alone)."""
    if domain not in _outlet_word_index:
        _outlet_word_index[domain] = len(_outlet_word_index) % len(_OUTLET_WORDS)
    oi = _outlet_word_index[domain]
    outlet_word = _OUTLET_WORDS[oi]
    # Pick the n-word from a per-outlet-rotated start so two outlets never share the
    # n-word at the same n — every (outlet, n) title differs in BOTH words, keeping the
    # SequenceMatcher ratio under the 0.85 merge threshold so each candidate clusters alone.
    story_word = _STORY_WORDS[(oi * 3 + n) % len(_STORY_WORDS)]
    url = f"https://{domain}/{story_word.lower()}-{n}"
    return CandidateStory(
        candidate_external_id=url,
        candidate_title=f"{outlet_word} {story_word} dispatch {oi}-{n}",
        candidate_url=url,
        candidate_outlet_domain=domain,
        candidate_published_utc=_NOW,
    )


class _CategoryKeyedAdapter(BaseNewsAdapter):
    """A trusted-outlet fake: routes on the ``domains`` kwarg (no network).

    Returns, per domain in the set, ``per_domain`` distinct candidates — so a cell's
    story count is ``len(domains) * per_domain`` and is tunable. ``fail_domains`` forces
    an AdapterFetchError when the set contains a sentinel domain (per-cell resilience).
    ``records`` captures each call's ``(since_utc, domains)`` so gap-fill widening is
    assertable; ``widen_yield`` lets the SECOND call for a domain return more (so the
    widened re-fetch can lift a thin cell over the floor).
    """

    def __init__(
        self,
        *,
        per_domain: int = 1,
        fail_domain: str | None = None,
        widen_yield: int | None = None,
    ) -> None:
        self.per_domain = per_domain
        self.fail_domain = fail_domain
        self.widen_yield = widen_yield
        self.records: list[tuple[datetime, tuple[str, ...]]] = []
        self.calls_by_domainset: dict[tuple[str, ...], int] = {}

    async def search(self, search_query, since_utc, *, domains=None, **kwargs):
        domains = domains or []
        key = tuple(domains)
        self.records.append((since_utc, key))
        call_index = self.calls_by_domainset.get(key, 0)
        self.calls_by_domainset[key] = call_index + 1

        if self.fail_domain is not None and self.fail_domain in domains:
            raise AdapterFetchError(message="forced cell failure", adapter_name="fake")

        # The widened (2nd) call for a domain-set may yield a richer pool.
        per_domain = self.per_domain
        if call_index >= 1 and self.widen_yield is not None:
            per_domain = self.widen_yield

        out: list[CandidateStory] = []
        for domain in domains:
            for n in range(per_domain):
                out.append(_domain_candidate(domain, n))
        return out

    async def extract_body(self, candidate, **kwargs):
        return candidate


_FAKE_DOMAINS = {
    "ai": ["ai-one.com", "ai-two.com"],
    "sport": ["sport-one.com", "sport-two.com"],
    "business": ["biz-one.com"],
}


def _accessor(category: str) -> list[str]:
    """A test domain accessor over the small fixture map (raises on unknown)."""
    return list(_FAKE_DOMAINS[category])


class TestIngestTrustedOutlets:
    """SP4: the trusted-outlet (category + domain-set) rekey.

    Each test encodes a user-facing intent: the fetch is domain-scoped per category;
    one bad outlet must not blank the feed; a thin category must widen exactly once,
    not silently under-deliver.
    """

    @pytest.mark.asyncio
    async def test_each_category_cell_fetches_its_domains(self) -> None:
        """A multi-category run fetches each cell and the pool carries stories from
        that category's injected fixture domains (the fetch IS domain-scoped)."""
        adapter = _CategoryKeyedAdapter(per_domain=3)
        result = await ingest_trusted_outlets(
            adapter,
            categories=["ai", "sport"],
            domain_accessor=_accessor,
            min_stories_per_category=1,  # high enough yield; no gap-fill needed
        )

        assert set(result.canonical_stories_by_category) == {"ai", "sport"}
        ai_outlets = {
            s.canonical_primary_outlet_domain
            for s in result.canonical_stories_by_category["ai"]
        }
        assert ai_outlets == {"ai-one.com", "ai-two.com"}
        sport_outlets = {
            s.canonical_primary_outlet_domain
            for s in result.canonical_stories_by_category["sport"]
        }
        assert sport_outlets == {"sport-one.com", "sport-two.com"}
        assert result.failed_categories == []

    @pytest.mark.asyncio
    async def test_one_cell_failure_does_not_abort_batch(self) -> None:
        """A cell whose fetch raises is skipped (failed count 1); the OTHER cells'
        stories are still present — one bad outlet must not blank the feed."""
        adapter = _CategoryKeyedAdapter(per_domain=3, fail_domain="ai-one.com")
        result = await ingest_trusted_outlets(
            adapter,
            categories=["ai", "sport"],
            domain_accessor=_accessor,
            min_stories_per_category=1,
        )

        assert result.failed_categories == ["ai"]
        assert len(result.failed_categories) == 1
        # the surviving category still produced its pool
        assert "sport" in result.canonical_stories_by_category
        assert len(result.canonical_stories_by_category["sport"]) == 6  # 2 domains × 3
        # the failed category is absent from the pool (not a silent empty)
        assert "ai" not in result.canonical_stories_by_category

    @pytest.mark.asyncio
    async def test_under_filled_cell_triggers_one_widened_refetch(self) -> None:
        """A cell below the floor re-fetches ONCE with an earlier ``since`` (by the
        bounded delta) and, when the widen lifts it over the floor, is NOT flagged
        under_filled."""
        from datetime import timedelta as _td

        # business has 1 domain → first call yields 1 story (< floor 5); the widened
        # call yields 8 per domain → over the floor.
        adapter = _CategoryKeyedAdapter(per_domain=1, widen_yield=8)
        since = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
        widen = _td(days=1)
        result = await ingest_trusted_outlets(
            adapter,
            categories=["business"],
            domain_accessor=_accessor,
            since_utc=since,
            min_stories_per_category=5,
            gap_fill_widen=widen,
        )

        # exactly two calls for the business domain-set: first + ONE widen
        biz_key = ("biz-one.com",)
        assert adapter.calls_by_domainset[biz_key] == 2
        # the second call's since is earlier by exactly the bounded delta
        first_since, _ = adapter.records[0]
        second_since, _ = adapter.records[1]
        assert second_since == first_since - widen
        # the widen lifted it over the floor → not under-filled
        assert result.under_filled_categories == []
        assert len(result.canonical_stories_by_category["business"]) == 8

    @pytest.mark.asyncio
    async def test_still_short_after_widen_is_flagged_not_crashed(self) -> None:
        """If a cell is STILL short after the single widen it is flagged
        under_filled (fail loud) rather than crashing or silently under-delivering."""
        # 1 domain, no widen boost → stays at 1 story (< floor 5) even after widening.
        adapter = _CategoryKeyedAdapter(per_domain=1)
        result = await ingest_trusted_outlets(
            adapter,
            categories=["business"],
            domain_accessor=_accessor,
            min_stories_per_category=5,
        )

        assert result.under_filled_categories == ["business"]
        # still returned a (thin) pool — did not crash
        assert "business" in result.canonical_stories_by_category

    @pytest.mark.asyncio
    async def test_gap_fill_is_bounded_to_one_refetch(self) -> None:
        """Gap-fill never re-fetches a cell more than once (no unbounded loop) even
        when the cell stays under the floor."""
        adapter = _CategoryKeyedAdapter(per_domain=1)  # stays under floor 5
        await ingest_trusted_outlets(
            adapter,
            categories=["business"],
            domain_accessor=_accessor,
            min_stories_per_category=5,
        )
        # first fetch + exactly one widened re-fetch == 2; never more.
        assert adapter.calls_by_domainset[("biz-one.com",)] == 2
