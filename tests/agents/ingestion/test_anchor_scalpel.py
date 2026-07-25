"""Unit tests for the per-anchor GDELT DOC scalpel (issue #16).

The scalpel is the SEQUENTIAL, gap-filling DOC loop that turns a micro-interest's
persisted WHO anchor terms into niche candidates tagged to the interest node. These
tests pin the ACs (Rule 9 — each encodes WHY the behaviour matters), with GDELT
mocked at the boundary (CLAUDE.md): no live DOC call, no penalty box, no real waits.

Covered:
  • split_anchor_terms / build_anchor_doc_query — pure helpers, incl. injection safety.
  • Happy: an uncovered anchor → candidates STAMPED to the interest id/slug (AC1).
  • Penalty box: one anchor errors → the loop RESUMES sequentially, never parallel-
    retries; the failing anchor is attempted exactly once (AC2).
  • Zero hits: an anchor with no articles is valid and contributes nothing (AC3).
  • Junk at volume: the per-anchor cap bounds the pool (AC4).
  • DOC outage: EVERY anchor errors → returns whatever it has, never raises, logs
    loudly at error level; the backbone is unaffected (AC5).
  • Sequentiality: two anchors are never in flight at once (structural AC2 guard).
  • The DOC adapter's >=5s spacing + >=5s penalty-box backoff DURATIONS (the pacing
    contract the scalpel leans on), asserted with a fake sleep — no real waits.

    >>> pytest tests/agents/ingestion/test_anchor_scalpel.py -v
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.adapters.gdelt_doc import GdeltDocAdapter
from agents.ingestion.anchor_scalpel import (
    _DEFAULT_PER_ANCHOR_CAP,
    build_anchor_doc_query,
    run_anchor_scalpel,
    split_anchor_terms,
)
from agents.ingestion.interest_keyed_pipeline import ingest_active_interests
from agents.ingestion.models import ActiveInterest, CandidateStory, InterestNode
from agents.pipeline.feed_assembly import assemble_niche_feed
from agents.pipeline.niche_allocation import NicheAllocationRow
from agents.pipeline.stages.ranking import UserProfileInterest
from agents.shared.exceptions import AdapterFetchError

_SINCE = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _candidate(external_id: str, outlet: str = "espncricinfo.com") -> CandidateStory:
    return CandidateStory(
        candidate_external_id=external_id,
        candidate_title=f"Story {external_id}",
        candidate_url=f"https://{outlet}/{external_id}",
        candidate_outlet_domain=outlet,
        candidate_published_utc=_NOW,
    )


def _active(interest_id: str, slug: str, query: str) -> ActiveInterest:
    return ActiveInterest(
        interest_id=interest_id, interest_slug=slug, interest_search_query=query
    )


class _FakeDocAdapter(BaseNewsAdapter):
    """In-memory DOC adapter: query → candidates / forced error, with a concurrency guard.

    ``by_query`` maps a DOC query string to the candidates it returns; ``fail_queries``
    forces an AdapterFetchError (a penalty box). ``_in_flight`` proves the scalpel never
    runs two searches at once — a second concurrent entry raises (sequentiality is the
    contract). ``calls`` records the exact query sequence + a per-query attempt count.
    """

    def __init__(
        self,
        by_query: dict[str, list[CandidateStory]] | None = None,
        fail_queries: set[str] | None = None,
    ) -> None:
        self.by_query = by_query or {}
        self.fail_queries = fail_queries or set()
        self.calls: list[str] = []
        self.attempts: dict[str, int] = {}
        self._in_flight = False

    async def search(
        self, search_query: str, since_utc, **kwargs
    ) -> list[CandidateStory]:
        assert not self._in_flight, (
            f"concurrent DOC call for {search_query!r} — the scalpel must be sequential"
        )
        self._in_flight = True
        try:
            await asyncio.sleep(0)  # yield: a truly parallel caller would overlap here
            self.calls.append(search_query)
            self.attempts[search_query] = self.attempts.get(search_query, 0) + 1
            if search_query in self.fail_queries:
                raise AdapterFetchError(message="penalty box", adapter_name="fake_doc")
            return list(self.by_query.get(search_query, []))
        finally:
            self._in_flight = False

    async def extract_body(self, candidate: CandidateStory, **kwargs) -> CandidateStory:
        return candidate


# ── Pure helpers ─────────────────────────────────────────────────────────────


class TestSplitAnchorTerms:
    """split_anchor_terms recovers the joined ``interest_search_query`` back to phrases."""

    def test_splits_joined_query_into_phrases(self) -> None:
        assert split_anchor_terms("Vaibhav Suryavanshi, IPL auction") == [
            "Vaibhav Suryavanshi",
            "IPL auction",
        ]

    def test_dedups_case_insensitively_and_drops_empties(self) -> None:
        # WHY: a duplicate anchor would double the (throttled) DOC spend for no gain.
        assert split_anchor_terms("IPL, ipl , , IPL auction") == ["IPL", "IPL auction"]

    def test_none_or_blank_yields_empty(self) -> None:
        assert split_anchor_terms(None) == []
        assert split_anchor_terms("   ") == []


class TestBuildAnchorDocQuery:
    """build_anchor_doc_query wraps an anchor as an EXACT phrase, injection-safe."""

    def test_wraps_exact_phrase_in_quotes(self) -> None:
        assert build_anchor_doc_query("Vaibhav Suryavanshi") == '"Vaibhav Suryavanshi"'

    def test_strips_embedded_quotes_so_no_operator_injection(self) -> None:
        # WHY: an anchor carrying a `"` could otherwise close the phrase early and
        # inject raw DOC operators (e.g. domain:) — B7. The `"` is neutralised.
        assert build_anchor_doc_query('foo" OR domain:evil.com') == (
            '"foo OR domain:evil.com"'
        )

    def test_empty_after_sanitize_returns_empty_string(self) -> None:
        assert build_anchor_doc_query('  "  ') == ""


# ── The scalpel loop ─────────────────────────────────────────────────────────


class TestRunAnchorScalpel:
    """The sequential per-anchor DOC loop (the ACs)."""

    @pytest.mark.asyncio
    async def test_happy_uncovered_anchor_yields_stamped_candidates(self) -> None:
        """AC1: an anchor the backbone missed → candidates stamped to the interest node.

        WHY: the niche-first assembler surfaces a story ONLY via its
        ``candidate_matched_interest_id`` tag. If the scalpel returned untagged
        candidates they would never reach the user's niche section — a silent miss.
        """
        adapter = _FakeDocAdapter(
            by_query={
                '"Vaibhav Suryavanshi"': [_candidate("vs-1")],
                '"IPL auction"': [_candidate("ipl-1")],
            }
        )
        active = [
            _active("int-vs", "sport.cricket.vs", "Vaibhav Suryavanshi, IPL auction")
        ]

        candidates = await run_anchor_scalpel(active, adapter, _SINCE)

        assert {c.candidate_external_id for c in candidates} == {"vs-1", "ipl-1"}
        assert all(c.candidate_matched_interest_id == "int-vs" for c in candidates)
        assert all(
            c.candidate_matched_interest_slug == "sport.cricket.vs" for c in candidates
        )
        assert adapter.calls == ['"Vaibhav Suryavanshi"', '"IPL auction"']

    @pytest.mark.asyncio
    async def test_covered_interest_is_skipped(self) -> None:
        """The DOC budget targets only gaps — a BigQuery-covered interest is not queried.

        WHY: DOC is ~1 req/5s; spending it on interests the unthrottled BigQuery batch
        already filled wastes the throttled budget (performance lens).
        """
        adapter = _FakeDocAdapter(by_query={'"anchor one"': [_candidate("a-1")]})
        active = [
            _active("int-covered", "s.a", "anchor one, anchor two"),
            _active("int-gap", "s.b", "gap anchor"),
        ]

        candidates = await run_anchor_scalpel(
            active, adapter, _SINCE, covered_interest_ids={"int-covered"}
        )

        assert adapter.calls == ['"gap anchor"']  # covered interest never queried
        assert all(c.candidate_matched_interest_id == "int-gap" for c in candidates)

    @pytest.mark.asyncio
    async def test_penalty_box_on_one_anchor_resumes_loop_without_parallel_retry(
        self,
    ) -> None:
        """AC2: an anchor's penalty box → resume at the next anchor; attempted once.

        WHY: the multi-minute penalty box means a parallel retry (or aborting the batch)
        would burn the whole night. The failing anchor must be tried exactly once, then
        the loop continues — sequentially — to still harvest the healthy anchors.
        """
        adapter = _FakeDocAdapter(
            by_query={'"good anchor"': [_candidate("good-1")]},
            fail_queries={'"bad anchor"'},
        )
        active = [_active("int-x", "s.x", "bad anchor, good anchor")]

        candidates = await run_anchor_scalpel(active, adapter, _SINCE)

        assert adapter.attempts['"bad anchor"'] == 1  # tried once, NOT parallel-retried
        assert [c.candidate_external_id for c in candidates] == ["good-1"]

    @pytest.mark.asyncio
    async def test_zero_hits_anchor_is_valid_and_contributes_nothing(self) -> None:
        """AC3: an anchor with no articles is a valid outcome (the ladder handles it)."""
        adapter = _FakeDocAdapter(by_query={'"quiet anchor"': []})
        active = [_active("int-q", "s.q", "quiet anchor, other")]

        candidates = await run_anchor_scalpel(active, adapter, _SINCE)

        assert candidates == []
        assert adapter.calls == ['"quiet anchor"', '"other"']  # still swept both

    @pytest.mark.asyncio
    async def test_per_anchor_cap_bounds_junk_volume(self) -> None:
        """AC4: an over-broad anchor is capped, bounding the pool before dedup."""
        noisy = [_candidate(f"junk-{i}") for i in range(50)]
        adapter = _FakeDocAdapter(by_query={'"common surname"': noisy})
        active = [_active("int-n", "s.n", "common surname, second")]

        candidates = await run_anchor_scalpel(active, adapter, _SINCE, per_anchor_cap=5)

        assert len(candidates) == 5  # 50 hits trimmed to the cap
        assert all(c.candidate_matched_interest_id == "int-n" for c in candidates)

    @pytest.mark.asyncio
    async def test_doc_outage_returns_gathered_and_logs_loud_without_raising(
        self, caplog
    ) -> None:
        """AC5: every anchor errors (DOC outage) → no raise, loud error log, backbone safe.

        WHY: a BigQuery-only night is valid. If the scalpel raised, it would abort the
        whole ingest and lose the (healthy) BigQuery pool too — the opposite of additive.
        """
        adapter = _FakeDocAdapter(fail_queries={'"a"', '"b"'})
        active = [_active("int-out", "s.o", "a, b")]

        with caplog.at_level(logging.ERROR):
            candidates = await run_anchor_scalpel(active, adapter, _SINCE)

        assert candidates == []  # nothing gathered, but no exception propagated
        assert "anchor_scalpel_doc_outage" in caplog.text

    @pytest.mark.asyncio
    async def test_partial_failure_is_not_flagged_as_outage(self, caplog) -> None:
        """A mixed run (some anchors OK) is NOT a total outage — no error-level log."""
        adapter = _FakeDocAdapter(
            by_query={'"ok"': [_candidate("ok-1")]}, fail_queries={'"down"'}
        )
        active = [_active("int-m", "s.m", "down, ok")]

        with caplog.at_level(logging.ERROR):
            candidates = await run_anchor_scalpel(active, adapter, _SINCE)

        assert [c.candidate_external_id for c in candidates] == ["ok-1"]
        assert "anchor_scalpel_doc_outage" not in caplog.text

    @pytest.mark.asyncio
    async def test_interest_without_anchor_terms_is_skipped(self) -> None:
        """An interest whose query is blank contributes no anchors (no DOC call)."""
        adapter = _FakeDocAdapter()
        active = [_active("int-blank", "s.blank", "   ")]

        candidates = await run_anchor_scalpel(active, adapter, _SINCE)

        assert candidates == []
        assert adapter.calls == []

    def test_default_per_anchor_cap_is_bounded(self) -> None:
        """The default cap is a small, sane gap-filler bound (not unbounded)."""
        assert 1 <= _DEFAULT_PER_ANCHOR_CAP <= 100


# ── The DOC pacing contract the scalpel relies on ────────────────────────────


class TestDocPacingContract:
    """The >=5s spacing + >=5s penalty-box backoff DURATIONS, asserted without real waits.

    The scalpel itself never sleeps — it leans on the injected GdeltDocAdapter to honour
    GDELT's ~1-req/5s limit and to back off on the penalty box. These assert those
    DURATIONS deterministically by recording (not performing) each ``asyncio.sleep``.
    """

    @pytest.mark.asyncio
    async def test_sequential_calls_are_spaced_at_least_five_seconds(
        self, mock_http_client, make_gdelt_response, monkeypatch
    ) -> None:
        """Back-to-back searches request a >=5s spacing sleep before the second call."""
        sleeps: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _record_sleep)
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response('{"articles": []}')
        )
        adapter = GdeltDocAdapter(
            http_client=mock_http_client, min_request_interval_seconds=5.0
        )

        await adapter.search('"anchor a"', _SINCE)
        await adapter.search('"anchor b"', _SINCE)

        # First call has no predecessor (no spacing sleep); the second spaces ~5s (a hair
        # under, since real monotonic advanced a sliver between the two calls).
        assert any(s >= 4.9 for s in sleeps), sleeps

    @pytest.mark.asyncio
    async def test_penalty_box_notice_triggers_at_least_five_second_backoff(
        self, mock_http_client, make_gdelt_response, monkeypatch
    ) -> None:
        """A rate-limit notice → a >=5s backoff, then a sequential retry succeeds."""
        sleeps: list[float] = []

        async def _record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _record_sleep)
        # First GET: GDELT's HTTP-200 "Please limit requests" notice; second: valid JSON.
        mock_http_client.get = AsyncMock(
            side_effect=[
                make_gdelt_response("Please limit requests to one every 5 seconds"),
                make_gdelt_response('{"articles": []}'),
            ]
        )
        adapter = GdeltDocAdapter(
            http_client=mock_http_client,
            min_request_interval_seconds=0.0,
            retry_base_backoff_seconds=5.0,
        )

        result = await adapter.search('"throttled anchor"', _SINCE)

        assert result == []  # retry after backoff succeeded
        assert mock_http_client.get.await_count == 2  # backed off then retried, once
        assert any(s >= 5.0 for s in sleeps), sleeps


# ── End-to-end: scalpel tag → ingest → REAL niche-first assembly (B3.5) ──────


class _EmptyBatchAdapter(BaseNewsAdapter):
    """A BigQuery-shaped adapter that COVERS NOTHING — the backbone missed every anchor.

    Exposes ``search_active_interests`` (so ``ingest_active_interests`` takes the batch
    path) but returns no candidates, forcing the scalpel to fill the gap.
    """

    async def search_active_interests(self, active_interests, since_utc, **kwargs):
        return []

    async def search(self, search_query: str, since_utc, **kwargs):
        return []

    async def extract_body(self, candidate: CandidateStory, **kwargs) -> CandidateStory:
        candidate.candidate_body_text = f"Body for {candidate.candidate_url}"
        return candidate


class TestScalpelToNicheAssemblyEndToEnd:
    """The load-bearing chain: a scalpel-tagged candidate must SURFACE in the niche feed.

    No mock of the assembler — the REAL ``assemble_niche_feed`` runs over the REAL
    ``ingest_active_interests`` output. WHY: AC1 fails silently if the candidate is not
    tagged to the interest node (it would never reach the user's niche section). This
    proves the full contract: scalpel stamp → cross-outlet cluster → ancestor tag →
    niche section fill.
    """

    _VS = "int-vs"
    _CRICKET = "int-cricket"
    _SPORT = "int-sport"

    def _nodes(self) -> dict[str, InterestNode]:
        return {
            self._VS: InterestNode(
                interest_id=self._VS,
                parent_interest_id=self._CRICKET,
                interest_slug="sport.cricket.vs",
                interest_label="Vaibhav Suryavanshi",
                depth_level=2,
                interest_search_query="Vaibhav Suryavanshi, IPL auction",
            ),
            self._CRICKET: InterestNode(
                interest_id=self._CRICKET,
                parent_interest_id=self._SPORT,
                interest_slug="sport.cricket",
                interest_label="Cricket",
                depth_level=1,
            ),
            self._SPORT: InterestNode(
                interest_id=self._SPORT,
                interest_slug="sport",
                interest_label="Sport",
                depth_level=0,
            ),
        }

    @pytest.mark.asyncio
    async def test_scalpel_candidate_surfaces_in_its_niche_section(self) -> None:
        nodes = self._nodes()
        scalpel = _FakeDocAdapter(
            by_query={'"Vaibhav Suryavanshi"': [_candidate("vs-scoop")]}
        )

        result = await ingest_active_interests(
            followed_interest_ids=[self._VS],
            interest_nodes=nodes,
            adapter=_EmptyBatchAdapter(),
            doc_scalpel_adapter=scalpel,
            since_utc=_SINCE,
        )

        # The backbone (empty batch) found nothing; the scalpel supplied the story,
        # tagged to the leaf + ancestors by the ingest ancestor tagger at NATURAL
        # depth (issue #70: leaf 0 — the theme signal is a side-channel, not a tag).
        assert len(result.canonical_stories) == 1
        leaf_tags = [
            tag
            for tag in result.story_interest_tags
            if tag.story_interest_interest_id == self._VS
            and tag.story_interest_match_depth == 0
        ]
        assert leaf_tags, "scalpel story was not leaf-tagged to its interest node"

        slots = assemble_niche_feed(
            profile_interests=[
                UserProfileInterest(profile_interest_id=self._VS, profile_weight=3.0)
            ],
            niche_allocation=[
                NicheAllocationRow(
                    allocation_category="sport",
                    allocation_interest_id=self._VS,
                    allocation_section_label="Vaibhav Suryavanshi",
                    allocation_slot_count=3,
                    allocation_sort_order=0,
                )
            ],
            stories=result.canonical_stories,
            story_interest_tags=result.story_interest_tags,
            interest_nodes=nodes,
            score_threshold=0.0,  # this test asserts ROUTING, not the qualifying bar
            now_utc=_NOW,
        )

        surfaced = [s for s in slots if s.feed_section_interest_id == self._VS]
        assert surfaced, "scalpel story never reached its niche section"
        story_ids = {s.feed_story_id for s in surfaced}
        assert result.canonical_stories[0].canonical_story_id in story_ids
        # Filled DIRECTLY from the leaf (no dishonest ladder climb needed).
        assert all(s.feed_matched_interest_id == self._VS for s in surfaced)
