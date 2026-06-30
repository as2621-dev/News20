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
    async def test_no_theme_falls_back_to_default_and_batch_completes(self) -> None:
        """A story with NO themes falls back to DEFAULT_CATEGORY (arts) and the batch
        still completes (fail-loud-per-cell, never a batch abort)."""
        nodes = _m2_interest_nodes()
        adapter = _ThemedAdapter(themes=[])  # no V2Themes on the candidate

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)

        assert len(result.canonical_stories) == 1  # batch completed, not aborted
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        # DEFAULT_CATEGORY == "arts" (categories.py) — the long-tail fallback.
        assert assign_category(story_id, tags_by_story, nodes) == "arts"

    @pytest.mark.asyncio
    async def test_unknown_theme_falls_back_not_keyword(self) -> None:
        """An UNRECOGNIZED theme (not in the whitelist) falls back to DEFAULT, it does
        NOT silently revert to the keyword-inherited geopolitics category."""
        nodes = _m2_interest_nodes()
        adapter = _ThemedAdapter(themes=["WB_9999_NONSENSE_UNMAPPED"])

        result = await ingest_active_interests([_GEO_LEAF_ID], nodes, adapter)
        story_id = result.canonical_stories[0].canonical_story_id
        tags_by_story = _index_tags_by_story(result.story_interest_tags)
        assert assign_category(story_id, tags_by_story, nodes) == "arts"


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
        business; a no-theme one falls back to arts — in a SINGLE batch run."""
        nodes = _m2_interest_nodes()
        adapter = _MultiThemedGkgAdapter(
            rows=[
                # business themes despite the geopolitics keyword match (the bug case)
                (
                    "https://reuters.com/biz",
                    _GEO_LEAF_ID,
                    ["ECON_STOCKMARKET", "ECON_BANKRUPTCY"],
                ),
                # genuinely no themes → fallback
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
        by_url = {s.canonical_url: s.canonical_story_id for s in result.canonical_stories}
        assert cats[by_url["https://reuters.com/biz"]] == "business"
        assert cats[by_url["https://reuters.com/none"]] == "arts"
