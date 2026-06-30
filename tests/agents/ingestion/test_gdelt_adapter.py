"""Unit tests for the GDELT DOC adapter (Phase 1d SP1).

Mocks the httpx client at the boundary and patches trafilatura — no network, no
live GDELT call. Covers: article parsing (happy), the rate-limit-notice and
non-JSON failure paths, empty response, body extraction success, and the
extract-never-raises guarantee.

    >>> pytest tests/agents/ingestion/test_gdelt_adapter.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from agents.ingestion.adapters.gdelt_doc import GdeltDocAdapter, build_domain_query
from agents.ingestion.models import CandidateStory
from agents.shared.exceptions import AdapterFetchError

_SINCE = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)


def _adapter(http_client: AsyncMock) -> GdeltDocAdapter:
    """Build an adapter with the injected client, no throttle delay, no backoff sleep.

    ``min_request_interval_seconds=0`` skips inter-call spacing and
    ``retry_base_backoff_seconds=0`` makes the backoff-retry paths run instantly,
    so retry behaviour is asserted without real sleeps.
    """
    return GdeltDocAdapter(
        http_client=http_client,
        min_request_interval_seconds=0.0,
        retry_base_backoff_seconds=0.0,
    )


class TestGdeltSearchParsing:
    """search() parses the GDELT ArtList JSON into typed candidates."""

    @pytest.mark.asyncio
    async def test_parses_articles_into_candidates(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """Two valid articles parse; the row with no URL is skipped."""
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response(gdelt_articles_json)
        )
        adapter = _adapter(mock_http_client)

        candidates = await adapter.search("Arsenal FC", _SINCE)

        assert len(candidates) == 2  # the no-URL junk row is dropped
        cnn = next(c for c in candidates if c.candidate_outlet_domain == "cnn.com")
        assert cnn.candidate_title == "Arsenal win at the Emirates"
        assert cnn.candidate_url.startswith("https://www.cnn.com/")
        assert cnn.candidate_published_utc == datetime(
            2026, 5, 31, 10, 15, 0, tzinfo=timezone.utc
        )
        assert cnn.candidate_language == "English"
        assert cnn.candidate_source_country == "UnitedStates"
        assert cnn.candidate_social_image_url == "https://cnn.com/img/arsenal.jpg"
        assert cnn.candidate_body_text is None  # body filled later by extract_body

    @pytest.mark.asyncio
    async def test_missing_social_image_is_none(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """An empty socialimage maps to None, not an empty string."""
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response(gdelt_articles_json)
        )
        adapter = _adapter(mock_http_client)

        candidates = await adapter.search("Arsenal FC", _SINCE)
        bbc = next(c for c in candidates if c.candidate_outlet_domain == "bbc.com")
        assert bbc.candidate_social_image_url is None

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty_list(
        self, mock_http_client, make_gdelt_response
    ) -> None:
        """An empty (but 200) body yields no candidates and does not raise."""
        mock_http_client.get = AsyncMock(return_value=make_gdelt_response("   "))
        adapter = _adapter(mock_http_client)

        assert await adapter.search("Arsenal FC", _SINCE) == []


class TestBuildDomainQuery:
    """build_domain_query — the pure DOC trusted-domain query builder (no HTTP).

    WHY this matters: if the ``domainis:`` clause is dropped or the OR-join breaks,
    the fetch is no longer domain-scoped and GDELT returns random outlets — defeating
    M4's trusted-outlet fetch. An empty set must refuse rather than emit an un-scoped
    query that pulls randoms. These encode exactly those failure modes.
    """

    def test_multi_domain_or_clause_wrapped_in_parens(self) -> None:
        """A domain set yields a parenthesized OR of one domainis: atom per domain,
        in input order."""
        q = build_domain_query(["reuters.com", "apnews.com", "bbc.co.uk"])
        assert q == (
            "(domainis:reuters.com OR domainis:apnews.com OR domainis:bbc.co.uk)"
        )
        # every domain contributes a domainis: atom
        for d in ["reuters.com", "apnews.com", "bbc.co.uk"]:
            assert f"domainis:{d}" in q
        assert q.count(" OR ") == 2  # n-1 joins for 3 atoms

    def test_single_domain_has_no_parens(self) -> None:
        """A single domain emits a bare domainis: atom (no needless parens)."""
        assert build_domain_query(["reuters.com"]) == "domainis:reuters.com"

    def test_order_is_preserved_deterministically(self) -> None:
        """Atom order mirrors input order (deterministic — SP1 owns the ordering)."""
        q = build_domain_query(["b.com", "a.com", "c.com"])
        assert q == "(domainis:b.com OR domainis:a.com OR domainis:c.com)"

    def test_search_query_is_anded_with_domain_clause(self) -> None:
        """A keyword/category query AND-composes with the domain clause."""
        q = build_domain_query(["reuters.com", "apnews.com"], search_query="climate")
        assert q == "climate AND (domainis:reuters.com OR domainis:apnews.com)"

    def test_blank_search_query_omits_the_and(self) -> None:
        """A blank/whitespace search_query yields just the domain clause (no dangling
        AND)."""
        assert build_domain_query(["reuters.com"], search_query="   ") == (
            "domainis:reuters.com"
        )

    def test_empty_domain_set_raises(self) -> None:
        """An empty domain set RAISES — never emit an un-scoped query (M4 fetch must
        stay trusted-outlet-only)."""
        with pytest.raises(ValueError):
            build_domain_query([])


class TestSearchRoutesThroughDomainBuilder:
    """search(domains=[...]) sends the domainis: clause; the keyword path is untouched."""

    @pytest.mark.asyncio
    async def test_domains_kwarg_puts_domainis_in_outgoing_query(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """Passing domains routes the OUTGOING params['query'] through the builder so
        it carries the domainis: clause; response parsing reuses the recorded-fixture
        article path (no live call)."""
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response(gdelt_articles_json)
        )
        adapter = _adapter(mock_http_client)

        candidates = await adapter.search(
            "Arsenal FC", _SINCE, domains=["bbc.com", "cnn.com"]
        )

        # parsing still works over the fixture
        assert len(candidates) == 2
        # the outgoing query carried the domain clause
        sent_params = mock_http_client.get.call_args.kwargs["params"]
        assert sent_params["query"] == (
            "Arsenal FC AND (domainis:bbc.com OR domainis:cnn.com)"
        )

    @pytest.mark.asyncio
    async def test_no_domains_leaves_keyword_query_unchanged(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """Without domains, the outgoing query is the raw keyword string (the existing
        path is byte-for-byte unchanged — surgical, Rule 3)."""
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response(gdelt_articles_json)
        )
        adapter = _adapter(mock_http_client)

        await adapter.search("Arsenal FC", _SINCE)

        sent_params = mock_http_client.get.call_args.kwargs["params"]
        assert sent_params["query"] == "Arsenal FC"


class TestGdeltSearchFailures:
    """search() raises AdapterFetchError on the rate-limit notice and non-JSON."""

    @pytest.mark.asyncio
    async def test_rate_limit_notice_raises(
        self, mock_http_client, make_gdelt_response
    ) -> None:
        """The plaintext rate-limit notice (HTTP 200) is surfaced as a fetch error."""
        notice = "Please limit requests to one every 5 seconds or contact ...\n\n"
        mock_http_client.get = AsyncMock(return_value=make_gdelt_response(notice))
        adapter = _adapter(mock_http_client)

        with pytest.raises(AdapterFetchError):
            await adapter.search("Arsenal FC", _SINCE)

    @pytest.mark.asyncio
    async def test_non_json_body_raises(
        self, mock_http_client, make_gdelt_response
    ) -> None:
        """A non-JSON body (e.g. an HTML error page) raises AdapterFetchError."""
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response("<html>500 error</html>")
        )
        adapter = _adapter(mock_http_client)

        with pytest.raises(AdapterFetchError):
            await adapter.search("Arsenal FC", _SINCE)


class TestGdeltRetry:
    """_throttled_get backs off and retries on GDELT rate-limits / transient errors.

    WHY this matters: GDELT blanket-rate-limits a busy IP (the 4a cloud cron's
    failure mode the handoff hit). Without retry, a single 429/notice fails the
    whole per-interest query and silently shrinks the day's feed. These tests
    encode that a transient throttle is survived, that retries are bounded, and
    that a genuine client error still fails fast (no pointless retry storm).
    """

    @pytest.mark.asyncio
    async def test_http_429_then_success_returns_candidates(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """A 429 on the first call is retried; the second call's JSON parses.

        Fails if 429 is treated as fatal (the old behaviour) instead of retried.
        """
        mock_http_client.get = AsyncMock(
            side_effect=[
                make_gdelt_response("", status_code=429),
                make_gdelt_response(gdelt_articles_json),
            ]
        )
        adapter = _adapter(mock_http_client)

        candidates = await adapter.search("Arsenal FC", _SINCE)

        assert len(candidates) == 2
        assert mock_http_client.get.call_count == 2  # one retry, then success

    @pytest.mark.asyncio
    async def test_rate_limit_notice_then_success_returns_candidates(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """The HTTP-200 'Please limit requests' notice is retried, not surfaced.

        Fails if the 200+notice throttle is parsed as a fatal error before retry.
        """
        notice = "Please limit requests to one every 5 seconds or contact ...\n\n"
        mock_http_client.get = AsyncMock(
            side_effect=[
                make_gdelt_response(notice),
                make_gdelt_response(gdelt_articles_json),
            ]
        )
        adapter = _adapter(mock_http_client)

        candidates = await adapter.search("Arsenal FC", _SINCE)

        assert len(candidates) == 2
        assert mock_http_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_transient_transport_error_then_success(
        self, mock_http_client, make_gdelt_response, gdelt_articles_json
    ) -> None:
        """A transient transport error is retried (not a permanent failure).

        Fails if a one-off network blip aborts the whole query instead of retrying.
        """
        mock_http_client.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("connection reset"),
                make_gdelt_response(gdelt_articles_json),
            ]
        )
        adapter = _adapter(mock_http_client)

        candidates = await adapter.search("Arsenal FC", _SINCE)

        assert len(candidates) == 2
        assert mock_http_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_persistent_429_exhausts_retries_and_raises(
        self, mock_http_client, make_gdelt_response
    ) -> None:
        """A sustained 429 is retried exactly retry_max_attempts times, then raises.

        Encodes the BOUND: retries are capped so a hard-rate-limited IP fails loudly
        instead of looping forever. Fails if the cap is removed or off-by-one.
        """
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response("", status_code=429)
        )
        adapter = _adapter(mock_http_client)

        with pytest.raises(AdapterFetchError):
            await adapter.search("Arsenal FC", _SINCE)
        assert mock_http_client.get.call_count == adapter.retry_max_attempts

    @pytest.mark.asyncio
    async def test_non_retryable_4xx_fails_fast_without_retry(
        self, mock_http_client, make_gdelt_response
    ) -> None:
        """A 400 (bad query) is NOT retried — it fails on the first call.

        Encodes the edge: only rate-limit/transient/5xx conditions retry; a genuine
        client error must not trigger a wasteful retry storm. Fails if every 4xx is
        treated as retryable.
        """
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response("bad request", status_code=400)
        )
        adapter = _adapter(mock_http_client)

        with pytest.raises(AdapterFetchError):
            await adapter.search("Arsenal FC", _SINCE)
        assert mock_http_client.get.call_count == 1  # no retry on a client error


class TestGdeltExtractBody:
    """extract_body() fills body text and never raises on failure."""

    @pytest.mark.asyncio
    async def test_extract_body_success(
        self, mock_http_client, make_gdelt_response, monkeypatch
    ) -> None:
        """A fetched article's HTML is extracted into candidate_body_text."""
        mock_http_client.get = AsyncMock(
            return_value=make_gdelt_response(
                "<html><body>raw article html</body></html>"
            )
        )
        monkeypatch.setattr(
            "trafilatura.extract", lambda html: "Clean extracted article body."
        )
        adapter = _adapter(mock_http_client)
        candidate = CandidateStory(
            candidate_external_id="https://cnn.com/x",
            candidate_title="T",
            candidate_url="https://cnn.com/x",
            candidate_outlet_domain="cnn.com",
            candidate_published_utc=_SINCE,
        )

        enriched = await adapter.extract_body(candidate)
        assert enriched.candidate_body_text == "Clean extracted article body."

    @pytest.mark.asyncio
    async def test_extract_body_never_raises_on_fetch_error(
        self, mock_http_client
    ) -> None:
        """A fetch failure leaves body None and does not raise (batch resilience)."""
        mock_http_client.get = AsyncMock(side_effect=RuntimeError("boom"))
        adapter = _adapter(mock_http_client)
        candidate = CandidateStory(
            candidate_external_id="https://cnn.com/x",
            candidate_title="T",
            candidate_url="https://cnn.com/x",
            candidate_outlet_domain="cnn.com",
            candidate_published_utc=_SINCE,
        )

        enriched = await adapter.extract_body(candidate)
        assert enriched.candidate_body_text is None


class TestGdeltHelpers:
    """Pure helpers: timespan derivation + seendate parsing."""

    def test_timespan_exact_two_days(self) -> None:
        """An exactly-2-days-ago cutoff maps to '2d' (not '3d' from float drift)."""
        adapter = GdeltDocAdapter()
        from datetime import timedelta

        since = datetime.now(timezone.utc) - timedelta(days=2)
        assert adapter._compute_timespan(since) == "2d"

    def test_timespan_clamped_to_three_days(self) -> None:
        """A far-past cutoff clamps to the 3-day ceiling."""
        adapter = GdeltDocAdapter()
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert adapter._compute_timespan(since) == "3d"

    def test_bad_seendate_falls_back_to_now(self) -> None:
        """A malformed seendate falls back to a UTC datetime rather than crashing."""
        adapter = GdeltDocAdapter()
        parsed = adapter._parse_seendate("not-a-date")
        assert parsed.tzinfo == timezone.utc
