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
from structlog.testing import capture_logs

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.adapters.gdelt_bigquery import GdeltBigQueryAdapter
from agents.ingestion.dedup import normalize_url
from agents.ingestion.interest_keyed_pipeline import (
    _DEFAULT_LOOKBACK_DAYS,
    build_active_interest_set,
    ingest_active_interests,
    ingest_trusted_outlets,
)
from agents.ingestion.models import CandidateStory, InterestNode
from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category
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
        """Duplicates collapse; query-less + unknown interests are skipped AND counted.

        WHY: the skip counts are the fail-loud contract (issue #36) — a queryless
        followed interest that vanished without a count is exactly the silent
        empty-section bug this seam exists to prevent.
        """
        followed = [
            interest_ids["arsenal"],
            interest_ids["arsenal"],  # duplicate across users
            interest_ids["soccer"],  # no search query → skipped
            interest_ids["sport"],  # no search query → skipped
            interest_ids["markets"],
            "ghost-interest",  # not in taxonomy → skipped
        ]
        interest_set = build_active_interest_set(followed, interest_nodes)
        slugs = [a.interest_slug for a in interest_set.active_interests]
        assert slugs == ["markets", "sport.soccer.arsenal"]  # sorted by slug, deduped
        assert interest_set.skipped_queryless_count == 2  # soccer + sport

    def test_queryless_skip_emits_warning_with_fix_suggestion(
        self, interest_nodes, interest_ids
    ) -> None:
        """WHY: a queryless followed interest produces NOTHING for its follower —
        that is a data bug and must surface at WARNING (not debug) with a
        fix_suggestion, or the 2026-06-16 feed collapse repeats invisibly."""
        followed = [interest_ids["arsenal"], interest_ids["soccer"]]
        with capture_logs() as logs:
            build_active_interest_set(followed, interest_nodes)
        skips = [log for log in logs if log["event"] == "queryless_interest_skipped"]
        assert len(skips) == 1
        assert skips[0]["log_level"] == "warning"
        assert skips[0]["interest_id"] == interest_ids["soccer"]
        assert skips[0]["interest_slug"] == "sport.soccer"
        assert skips[0]["interest_name"] == "Soccer"
        assert "backfill_queryless_interests" in skips[0]["fix_suggestion"]


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

    @pytest.mark.asyncio
    async def test_batch_summary_reports_explicit_zero_queryless(
        self, interest_nodes, interest_ids
    ) -> None:
        """WHY (issue #36): the summary must carry skipped_queryless_interests=0
        EXPLICITLY on a clean run — the field's absence must never be the only
        evidence that nothing was skipped."""
        with capture_logs() as logs:
            result = await ingest_active_interests(
                [interest_ids["arsenal"], interest_ids["markets"]],
                interest_nodes,
                _FakeAdapter(),
            )
        assert result.skipped_queryless_interests == 0
        summary = next(
            log for log in logs if log["event"] == "interest_keyed_ingestion_completed"
        )
        assert summary["skipped_queryless_interests"] == 0

    @pytest.mark.asyncio
    async def test_batch_summary_counts_queryless_followed_interest(
        self, interest_nodes, interest_ids
    ) -> None:
        """WHY (issue #36): a queryless followed interest must show up in the batch
        summary count (result + summary log), so an empty section is a visible bug."""
        with capture_logs() as logs:
            result = await ingest_active_interests(
                [interest_ids["arsenal"], interest_ids["soccer"]],  # soccer: no query
                interest_nodes,
                _FakeAdapter(),
            )
        assert result.skipped_queryless_interests == 1
        summary = next(
            log for log in logs if log["event"] == "interest_keyed_ingestion_completed"
        )
        assert summary["skipped_queryless_interests"] == 1


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
        assert (
            (before - timedelta(days=1))
            <= adapter.received_since_utc
            <= (after - timedelta(days=1))
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


# ── M2 SP3/SP4 — theme-derived category tagging (the M2 fix) ──────────────────
#
# WHY this matters (the M2 bug, brief WS4): a story's category came from which
# keyword query surfaced it — a retail-takeover story matched a "geopolitics"
# search term and was mis-labelled GEOPOLITICS. The fix: derive the category from
# the story's GDELT V2Themes and make THAT the depth-0 tag assign_category reads,
# so the keyword the query used no longer dictates the bucket.

# A keyword-matched LEAF interest whose ROOT is geopolitics — this is the interest
# whose query surfaced the (mis-)matched story in the bug scenario.
_GEO_LEAF_ID = "leaf-russia-sanctions"

_ROOT_IDS_M2: dict[str, str] = {
    "ai": "root-ai",
    "geopolitics": "root-geopolitics",
    "business": "root-business",
    "environment": "root-environment",
    "politics": "root-politics",
    "tech": "root-tech",
    "sport": "root-sport",
    "arts": "root-arts",
}


def _m2_interest_nodes() -> dict[str, InterestNode]:
    """The 8 depth-0 roots (migration 0023) + one geopolitics-rooted keyword leaf.

    The leaf models the interest whose query surfaced the mis-matched story; the
    roots are what a theme-derived category tag points at. With both present,
    assign_category can resolve EITHER signal — the test proves the theme one wins.
    """
    nodes: dict[str, InterestNode] = {
        interest_id: InterestNode(
            interest_id=interest_id,
            parent_interest_id=None,
            interest_slug=slug,
            interest_label=slug.capitalize(),
            depth_level=0,
            interest_search_query=None,
        )
        for slug, interest_id in _ROOT_IDS_M2.items()
    }
    nodes[_GEO_LEAF_ID] = InterestNode(
        interest_id=_GEO_LEAF_ID,
        parent_interest_id=_ROOT_IDS_M2["geopolitics"],
        interest_slug="geopolitics.russia-sanctions",
        interest_label="Russia sanctions",
        depth_level=1,
        interest_search_query="Russia sanctions",
    )
    return nodes


class _ThemedAdapter(BaseNewsAdapter):
    """A fake whose one story carries the given V2Themes (set on the candidate).

    The query is the geopolitics-leaf query (so the story is keyword-matched to a
    geopolitics interest — the bug's mis-match) but the candidate's themes are
    whatever the test injects, so the theme-vs-keyword contest is exercisable.
    """

    def __init__(self, themes: list[str]) -> None:
        self.themes = themes

    async def search(self, search_query, since_utc, **kwargs):
        if search_query != "Russia sanctions":
            return []
        return [
            CandidateStory(
                candidate_external_id="https://reuters.com/retail-takeover",
                candidate_title="Mega retail chain agrees to private-equity takeover",
                candidate_url="https://reuters.com/retail-takeover",
                candidate_outlet_domain="reuters.com",
                candidate_published_utc=_NOW,
                candidate_themes=list(self.themes),
            )
        ]

    async def extract_body(self, candidate, **kwargs):
        candidate.candidate_body_text = "body"
        return candidate


class TestThemeDerivedCategoryTagging:
    """SP3 — the ingestion-time tag a story carries is THEME-derived, not keyword.

    Each test asserts the DOWNSTREAM assign_category output (the surface the bug
    actually manifested on), so a revert to keyword-inherited category fails it.
    """

    @pytest.mark.asyncio
    async def test_business_theme_beats_geopolitics_keyword(self) -> None:
        """The retail story matched a GEOPOLITICS keyword but carries BUSINESS themes
        → it categorizes BUSINESS. Fails if category reverts to keyword-inherited."""
        nodes = _m2_interest_nodes()
        adapter = _ThemedAdapter(themes=["ECON_STOCKMARKET", "WB_2670_JOBS"])

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)

        assert len(result.canonical_stories) == 1
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        # The category-determining (downstream) signal is the business themes …
        assert assign_category(story_id, tags_by_story, nodes) == "business"
        # … NOT the geopolitics keyword the query matched (the M2 bug).
        assert assign_category(story_id, tags_by_story, nodes) != "geopolitics"

    @pytest.mark.asyncio
    async def test_theme_tag_is_strict_lowest_depth(self) -> None:
        """The theme root tag is emitted at depth 0 and the keyword tags are shifted
        to depth >= 1 — so the theme tag is the unambiguous category winner (not a
        fragile slug tiebreak between two depth-0 tags)."""
        nodes = _m2_interest_nodes()
        adapter = _ThemedAdapter(themes=["ECON_STOCKMARKET"])

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)
        story_id = result.canonical_stories[0].canonical_story_id

        depth_by_interest = {
            t.story_interest_interest_id: t.story_interest_match_depth
            for t in result.story_interest_tags
            if t.story_interest_story_id == story_id
        }
        # The business root carries the sole depth-0 tag …
        assert depth_by_interest[_ROOT_IDS_M2["business"]] == 0
        # … and the keyword geopolitics leaf, naturally depth 0, was shifted to 1 so
        # it still scores (DepthMatch ladder) but never wins categorization.
        assert depth_by_interest[_GEO_LEAF_ID] == 1

    @pytest.mark.asyncio
    async def test_no_theme_falls_back_to_fetching_interest_root(self) -> None:
        """Issue #35: a story with NO themes is categorized by the interest that
        FETCHED it (its root), never the arts default — and the batch still
        completes (fail-loud-per-cell, never a batch abort)."""
        nodes = _m2_interest_nodes()
        adapter = _ThemedAdapter(themes=[])  # no V2Themes on the candidate

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)

        assert len(result.canonical_stories) == 1  # batch completed, not aborted
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        # The geopolitics leaf fetched it → geopolitics, NOT the old arts default.
        assert assign_category(story_id, tags_by_story, nodes) == "geopolitics"

    @pytest.mark.asyncio
    async def test_unmatched_theme_falls_back_to_fetching_interest_root(self) -> None:
        """Issue #35: an UNRECOGNIZED theme (not in the whitelist) carries no
        category signal — the fetching interest's root wins, never arts."""
        nodes = _m2_interest_nodes()
        adapter = _ThemedAdapter(themes=["WB_9999_NONSENSE_UNMAPPED"])

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        assert assign_category(story_id, tags_by_story, nodes) == "geopolitics"
        assert assign_category(story_id, tags_by_story, nodes) != "arts"


class _RootedLeafAdapter(BaseNewsAdapter):
    """One story keyword-matched via a leaf under an arbitrary root, with the given
    themes — the table-driven precedence harness for issue #35."""

    def __init__(self, query: str, themes: list[str]) -> None:
        self.query = query
        self.themes = themes

    async def search(self, search_query, since_utc, **kwargs):
        if search_query != self.query:
            return []
        return [
            CandidateStory(
                candidate_external_id="https://example.com/rooted-story",
                candidate_title="A story with themes the whitelist does not know",
                candidate_url="https://example.com/rooted-story",
                candidate_outlet_domain="example.com",
                candidate_published_utc=_NOW,
                candidate_themes=list(self.themes),
            )
        ]

    async def extract_body(self, candidate, **kwargs):
        candidate.candidate_body_text = "body"
        return candidate


class TestFetchingInterestPrecedence:
    """Issue #35, table-driven: when NO whitelisted theme matched, the FETCHING
    interest's root is authoritative — a tech-fetched story is tech, a sport-fetched
    story is sport, NEVER the arts default. These tests FAIL under the old
    arts-at-depth-0 behavior (the theme tag used to override the keyword tags)."""

    @pytest.mark.parametrize(
        ("root_slug", "leaf_slug"),
        [
            ("tech", "tech.semiconductors"),  # the issue's named happy path
            ("ai", "ai.interpretability"),
            ("business", "business.equities"),
            ("sport", "sport.cricket.ipl"),
            ("environment", "environment.climate-policy"),
        ],
    )
    @pytest.mark.parametrize(
        "themes",
        [
            [],  # zero themes at all
            ["WB_9999_NONSENSE_UNMAPPED", "TAX_WEAPONS_FAKE"],  # only unmatched codes
        ],
        ids=["no-themes", "unmatched-themes"],
    )
    @pytest.mark.asyncio
    async def test_fetching_interest_root_wins_without_theme_match(
        self, root_slug: str, leaf_slug: str, themes: list[str]
    ) -> None:
        nodes = _m2_interest_nodes()
        leaf_id = f"leaf-{leaf_slug}"
        nodes[leaf_id] = InterestNode(
            interest_id=leaf_id,
            parent_interest_id=_ROOT_IDS_M2[root_slug],
            interest_slug=leaf_slug,
            interest_label=leaf_slug,
            depth_level=1,
            interest_search_query=f"query {leaf_slug}",
        )
        adapter = _RootedLeafAdapter(query=f"query {leaf_slug}", themes=themes)

        result = await ingest_active_interests([leaf_id], nodes, adapter)

        assert len(result.canonical_stories) == 1
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        got = assign_category(story_id, tags_by_story, nodes)
        assert got == root_slug, (
            f"story fetched by a {root_slug}-root interest with unmatched themes "
            f"must categorize {root_slug}, got {got!r}"
        )
        assert got != "arts" or root_slug == "arts"

    @pytest.mark.asyncio
    async def test_whitelisted_theme_still_beats_fetching_interest(self) -> None:
        """Precedence guard: an ACTUAL whitelist match still wins over the fetching
        interest (existing behavior preserved — the flip only covers no-match)."""
        nodes = _m2_interest_nodes()
        leaf_id = "leaf-tech.semiconductors"
        nodes[leaf_id] = InterestNode(
            interest_id=leaf_id,
            parent_interest_id=_ROOT_IDS_M2["tech"],
            interest_slug="tech.semiconductors",
            interest_label="Semiconductors",
            depth_level=1,
            interest_search_query="query tech.semiconductors",
        )
        adapter = _RootedLeafAdapter(
            query="query tech.semiconductors", themes=["SPORT", "WB_1953_SPORTS"]
        )

        result = await ingest_active_interests([leaf_id], nodes, adapter)
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        assert assign_category(story_id, tags_by_story, nodes) == "sport"


class _MultiThemedGkgAdapter(BaseNewsAdapter):
    """A batched (GKG-style) adapter: returns pre-stamped candidates carrying themes.

    Mirrors GdeltBigQueryAdapter.search_active_interests (the trusted/batch path):
    exposes ``search_active_interests`` so the pipeline ingests the whole active set
    in one call with candidates already stamped + theme-bearing — the closest
    offline proxy for a real GKG pull (which would carry live V2Themes)."""

    def __init__(self, rows: list[tuple[str, str, list[str]]]) -> None:
        # rows: (external_id/url, matched_interest_id, themes)
        self._rows = rows

    async def search(self, search_query, since_utc, **kwargs):
        # Unused: the pipeline calls the batched search_active_interests path instead.
        return []

    async def search_active_interests(self, active_interests, since_utc):
        # Distinct, dissimilar titles per row so the StoryClusterer (>=0.85 title
        # similarity) keeps them as separate canonical stories, not one merged cluster.
        _titles = [
            "Mega retail chain agrees to private-equity buyout deal",
            "Volcano erupts off the southern coast overnight, residents flee",
            "Marathon record shattered at the autumn city championship",
        ]
        out: list[CandidateStory] = []
        for n, (url, interest_id, themes) in enumerate(self._rows):
            out.append(
                CandidateStory(
                    candidate_external_id=url,
                    candidate_title=_titles[n % len(_titles)],
                    candidate_url=url,
                    candidate_outlet_domain="reuters.com",
                    candidate_published_utc=_NOW,
                    candidate_matched_interest_id=interest_id,
                    candidate_matched_interest_slug="geopolitics.russia-sanctions",
                    candidate_themes=list(themes),
                )
            )
        return out

    async def extract_body(self, candidate, **kwargs):
        candidate.candidate_body_text = "body"
        return candidate


class TestThemeCategoryEndToEnd:
    """SP4 — the closed loop: mocked GKG batch adapter (themes) → ingest → tags →
    assign_category returns the theme-expected category. Proves SP2-parse →
    SP1-map → SP3-tag → assign_category end-to-end, happy + no-theme paths."""

    @pytest.mark.asyncio
    async def test_gkg_batch_themes_drive_category_end_to_end(self) -> None:
        """Two stories from the batched GKG path: a business-themed one categorizes
        business; a no-theme one falls back to its FETCHING interest's root
        (geopolitics — issue #35, never arts) — in a SINGLE batch run."""
        nodes = _m2_interest_nodes()
        adapter = _MultiThemedGkgAdapter(
            rows=[
                # business themes despite the geopolitics keyword match (the bug case)
                (
                    "https://reuters.com/biz",
                    _GEO_LEAF_ID,
                    ["ECON_STOCKMARKET", "ECON_BANKRUPTCY"],
                ),
                # genuinely no themes → the fetching interest's root owns category
                ("https://reuters.com/none", _GEO_LEAF_ID, []),
            ]
        )

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)

        assert len(result.canonical_stories) == 2  # batch completed for both
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        cats = {
            s.canonical_story_id: assign_category(
                s.canonical_story_id, tags_by_story, nodes
            )
            for s in result.canonical_stories
        }
        by_url = {
            s.canonical_url: s.canonical_story_id for s in result.canonical_stories
        }
        assert cats[by_url["https://reuters.com/biz"]] == "business"
        assert cats[by_url["https://reuters.com/none"]] == "geopolitics"


def _param(job_config, name):
    """Fetch a query parameter by name from a captured QueryJobConfig."""
    return next(p for p in job_config.query_parameters if p.name == name)


class TestBigQueryNicheSeamIntegration:
    """Slice #3 — the REAL GdeltBigQueryAdapter wired through ingest_active_interests.

    Unlike TestThemeCategoryEndToEnd (which uses a fake ``search_active_interests``),
    these run the actual adapter — SQL build, struct-array params, row→candidate
    mapping — with ONLY the BigQuery *client* mocked at the boundary. So they pin the
    contract the daily batch depends on: ALL active micro-interests are served by ONE
    batched pass (not per-interest fan-out), candidates land in the pool stamped with
    their interest node + a match depth, the per-interest cap is bound in-SQL, a niche
    with zero matches yields nothing (no error), and a BigQuery credential/billing
    failure fails the niche pass LOUD-but-safe (empty pool, batch survives) so the
    DOC-backed backbone/census is never taken down with it.
    """

    @staticmethod
    def _two_query_bearing_nodes() -> dict[str, InterestNode]:
        """Two followed micro-interests carrying CONCRETE anchor-term queries (so the
        matcher keeps ≥1 term each — the conftest 'markets' query is all stopwords)."""
        return {
            "int-arsenal": InterestNode(
                interest_id="int-arsenal",
                parent_interest_id=None,
                interest_slug="sport.soccer.arsenal",
                interest_label="Arsenal",
                depth_level=2,
                interest_search_query="Arsenal Premier League",
            ),
            "int-chips": InterestNode(
                interest_id="int-chips",
                parent_interest_id=None,
                interest_slug="tech.semiconductors",
                interest_label="Semiconductors",
                depth_level=1,
                interest_search_query="TSMC semiconductor",
            ),
        }

    @pytest.mark.asyncio
    async def test_all_niches_served_by_one_batched_pass_and_tagged(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """Two followed micro-interests → ONE BigQuery query (not two), and the pool
        carries StoryInterestTag rows stamped with the matched node + a depth."""
        nodes = self._two_query_bearing_nodes()
        client = make_fake_bq_client(
            rows=[
                make_bq_row(
                    "https://cnn.com/arsenal",
                    "Arsenal win at the Emirates",
                    "cnn.com",
                    interest_id="int-arsenal",
                    interest_slug="sport.soccer.arsenal",
                ),
                make_bq_row(
                    "https://reuters.com/tsmc",
                    "TSMC ramps chip output",
                    "reuters.com",
                    interest_id="int-chips",
                    interest_slug="tech.semiconductors",
                ),
            ]
        )
        adapter = GdeltBigQueryAdapter(client=client, per_interest_limit=75)

        result = await ingest_active_interests(
            followed_interest_ids=["int-arsenal", "int-chips"],
            interest_nodes=nodes,
            adapter=adapter,
            extract_bodies=False,
        )

        # ONE batched pass covered BOTH active interests (fan-out would be 2 queries).
        assert client.query.call_count == 1
        # Both interests contributed their anchor terms into the single @interest_terms
        # struct-array param (proves the batch, not two separate queries).
        term_slugs = {
            struct.struct_values["interest_slug"]
            for struct in _param(client.captured["job_config"], "interest_terms").values
        }
        assert term_slugs == {"sport.soccer.arsenal", "tech.semiconductors"}
        # The in-SQL per-interest cap is bound (one noisy niche cannot flood the pool).
        assert _param(client.captured["job_config"], "per_interest_limit").value == 75

        # Both stories reached the shared pool, each stamped to its interest node.
        assert len(result.canonical_stories) == 2
        tags_by_interest = {
            (t.story_interest_interest_id, t.story_interest_match_depth)
            for t in result.story_interest_tags
        }
        # Leaf-matched tags (depth 0) exist for both followed interests → node + depth.
        assert ("int-arsenal", 0) in tags_by_interest
        assert ("int-chips", 0) in tags_by_interest

    @pytest.mark.asyncio
    async def test_zero_match_niche_yields_no_candidates_no_error(
        self, make_fake_bq_client, interest_nodes, interest_ids
    ) -> None:
        """A niche the batched pull returns nothing for produces an empty pool and no
        error — empty is valid, downstream fallback handles it."""
        client = make_fake_bq_client(rows=[])
        adapter = GdeltBigQueryAdapter(client=client)

        result = await ingest_active_interests(
            followed_interest_ids=[interest_ids["arsenal"]],
            interest_nodes=interest_nodes,
            adapter=adapter,
            extract_bodies=False,
        )

        assert client.query.call_count == 1  # the pass ran; it just matched nothing
        assert result.canonical_stories == []
        assert result.story_interest_tags == []

    @pytest.mark.asyncio
    async def test_batch_row_cap_scales_with_active_set_not_a_fixed_ceiling(
        self, make_fake_bq_client
    ) -> None:
        """The batched pass sizes ``@max_rows`` to used_interests × per_interest_limit,
        NOT a fixed default. WHY: the SQL applies the global ``LIMIT @max_rows`` AFTER
        the per-interest window and ORDERs BY interest_id, so a fixed cap below the
        batch's legitimate ceiling would silently STARVE whole late-ordered interests
        as the active set grows. A deliberately tiny fixed max_rows must be overridden
        by the batch-sized cap."""
        nodes = self._two_query_bearing_nodes()
        client = make_fake_bq_client(rows=[])
        # Fixed ceiling of 1 would truncate to a single row for the whole batch.
        adapter = GdeltBigQueryAdapter(client=client, per_interest_limit=75, max_rows=1)

        await ingest_active_interests(
            followed_interest_ids=["int-arsenal", "int-chips"],
            interest_nodes=nodes,
            adapter=adapter,
            extract_bodies=False,
        )

        # 2 interests × 75 cap = 150, which overrides the fixed max_rows=1.
        assert _param(client.captured["job_config"], "max_rows").value == 150

    @pytest.mark.asyncio
    async def test_bigquery_failure_fails_loud_but_leaves_pool_empty_not_aborted(
        self, make_fake_bq_client, interest_nodes, interest_ids
    ) -> None:
        """A BigQuery credential/billing failure normalizes to AdapterFetchError, which
        the pipeline catches → the niche pass is skipped (empty pool), the batch is NOT
        aborted, and the DOC-backed backbone/census (a separate adapter) is untouched.
        """
        client = make_fake_bq_client(
            raise_exc=RuntimeError("403 Access Denied: BigQuery billing not enabled")
        )
        adapter = GdeltBigQueryAdapter(client=client)

        result = await ingest_active_interests(
            followed_interest_ids=[interest_ids["arsenal"], interest_ids["markets"]],
            interest_nodes=interest_nodes,
            adapter=adapter,
            extract_bodies=False,
        )

        # Fail-safe: no exception escapes, the pool is simply empty this run.
        assert result.canonical_stories == []
        assert result.story_interest_tags == []


class TestBackboneRegressionGuard:
    """Slice #3 — pin the trusted-outlet BACKBONE output so the niche-ingestion wiring
    cannot silently perturb it.

    The backbone (``ingest_trusted_outlets``) is deliberately UNTOUCHED by this slice:
    the daily batch's niche door swaps to BigQuery while the backbone/coverage census
    stays on the DOC adapter. This characterization test snapshots the backbone's exact
    per-category output for a fixed adapter input; if a future edit to the shared
    pipeline module perturbs the backbone, this fails loud (byte-identical guard)."""

    @pytest.mark.asyncio
    async def test_trusted_outlet_output_is_byte_identical_snapshot(self) -> None:
        adapter = _CategoryKeyedAdapter(per_domain=1)
        result = await ingest_trusted_outlets(
            adapter,
            categories=["ai", "sport", "business"],
            domain_accessor=_accessor,
            min_stories_per_category=1,  # yields clear the floor; no gap-fill
        )

        # Exact per-category outlet snapshot (the fetch is domain-scoped, deterministic).
        outlets_by_category = {
            category: sorted(s.canonical_primary_outlet_domain for s in stories)
            for category, stories in result.canonical_stories_by_category.items()
        }
        assert outlets_by_category == {
            "ai": ["ai-one.com", "ai-two.com"],
            "sport": ["sport-one.com", "sport-two.com"],
            "business": ["biz-one.com"],
        }
        assert result.failed_categories == []
        assert result.under_filled_categories == []
        assert result.total_candidates_fetched == 5  # 2 + 2 + 1 domain candidates
