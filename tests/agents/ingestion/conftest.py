"""Shared fixtures for the ingestion test suite (Phase 1d SP1).

All external services are mocked at the boundary (CLAUDE.md): the GDELT HTTP
call is a mock httpx client returning a fixture JSON body, and trafilatura is
patched per-test. No network, no live API key — fully offline + deterministic.

Fixtures:
    interest_nodes        -- a 3-level taxonomy (Sport→Soccer→Arsenal) + a sibling chain
    gdelt_articles_json   -- a raw GDELT DOC ArtList JSON body (str)
    make_candidate        -- factory for CandidateStory fixtures
    make_gdelt_response    -- factory for a mock httpx response wrapping a text body
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.ingestion.models import CandidateStory, InterestNode

# Stable test ids for the Sport → Soccer → Arsenal chain + a Markets sibling.
_ARSENAL_ID = "int-arsenal"
_SOCCER_ID = "int-soccer"
_SPORT_ID = "int-sport"
_MARKETS_ID = "int-markets"

_FIXED_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def interest_nodes() -> dict[str, InterestNode]:
    """A small taxonomy: Sport→Soccer→Arsenal (3 levels) + a Markets depth-0 node.

    Arsenal carries the search query (the leaf the daily pipeline ingests on);
    Markets carries its own query so the multi-interest path is exercisable.
    """
    return {
        _ARSENAL_ID: InterestNode(
            interest_id=_ARSENAL_ID,
            parent_interest_id=_SOCCER_ID,
            interest_slug="sport.soccer.arsenal",
            interest_label="Arsenal",
            depth_level=2,
            interest_search_query="Arsenal FC",
        ),
        _SOCCER_ID: InterestNode(
            interest_id=_SOCCER_ID,
            parent_interest_id=_SPORT_ID,
            interest_slug="sport.soccer",
            interest_label="Soccer",
            depth_level=1,
            interest_search_query=None,
        ),
        _SPORT_ID: InterestNode(
            interest_id=_SPORT_ID,
            parent_interest_id=None,
            interest_slug="sport",
            interest_label="Sport",
            depth_level=0,
            interest_search_query=None,
        ),
        _MARKETS_ID: InterestNode(
            interest_id=_MARKETS_ID,
            parent_interest_id=None,
            interest_slug="markets",
            interest_label="Markets",
            depth_level=0,
            interest_search_query="stock market",
        ),
    }


@pytest.fixture
def interest_ids() -> dict[str, str]:
    """Convenience map of human names → fixture interest ids."""
    return {
        "arsenal": _ARSENAL_ID,
        "soccer": _SOCCER_ID,
        "sport": _SPORT_ID,
        "markets": _MARKETS_ID,
    }


@pytest.fixture
def gdelt_articles_json() -> str:
    """A raw GDELT DOC ArtList JSON body with two same-story articles + one junk row.

    Two outlets (cnn.com, bbc.com) report the same Arsenal story with a minor
    title variation (for clustering); one row is missing a URL (must be skipped).
    """
    payload = {
        "articles": [
            {
                "url": "https://www.cnn.com/2026/05/31/arsenal-win",
                "url_mobile": "",
                "title": "Arsenal win at the Emirates",
                "seendate": "20260531T101500Z",
                "socialimage": "https://cnn.com/img/arsenal.jpg",
                "domain": "cnn.com",
                "language": "English",
                "sourcecountry": "UnitedStates",
            },
            {
                "url": "https://www.bbc.com/sport/arsenal-win",
                "url_mobile": "",
                "title": "Arsenal win at the Emirates!",
                "seendate": "20260531T120000Z",
                "socialimage": "",
                "domain": "bbc.com",
                "language": "English",
                "sourcecountry": "UnitedKingdom",
            },
            {
                "url": "",
                "title": "Row with no URL — must be skipped",
                "seendate": "20260531T130000Z",
                "domain": "example.com",
            },
        ]
    }
    return json.dumps(payload)


@pytest.fixture
def make_candidate():
    """Factory for CandidateStory fixtures with sensible defaults."""

    def _make(
        candidate_external_id: str,
        candidate_title: str,
        candidate_url: str,
        candidate_outlet_domain: str,
        *,
        published_utc: datetime | None = None,
        matched_interest_id: str | None = None,
    ) -> CandidateStory:
        return CandidateStory(
            candidate_external_id=candidate_external_id,
            candidate_title=candidate_title,
            candidate_url=candidate_url,
            candidate_outlet_domain=candidate_outlet_domain,
            candidate_outlet_name=candidate_outlet_domain,
            candidate_published_utc=published_utc or _FIXED_NOW,
            candidate_matched_interest_id=matched_interest_id,
        )

    return _make


@pytest.fixture
def make_gdelt_response():
    """Factory for a mock httpx response whose ``.text`` is the given body."""

    def _make(text_body: str, status_code: int = 200) -> MagicMock:
        response = MagicMock()
        response.text = text_body
        response.status_code = status_code
        response.raise_for_status = MagicMock()
        return response

    return _make


@pytest.fixture
def mock_http_client() -> AsyncMock:
    """A mock httpx.AsyncClient (``.get`` is an AsyncMock; ``.aclose`` no-ops)."""
    client = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def make_bq_row():
    """Factory for a GKG BigQuery result row (a plain dict; the adapter calls
    ``dict(row.items())``, which a dict satisfies — no fake Row class needed)."""

    def _make(
        url: str,
        title: str,
        outlet: str,
        *,
        gkg_date: int = 20260531101500,
        interest_id: str = "int-x",
        interest_slug: str = "slug-x",
        sharing_image: str | None = None,
        match_count: int = 1,
        title_match_count: int = 1,
    ) -> dict:
        return {
            "url": url,
            "outlet": outlet,
            "gkg_date": gkg_date,
            "sharing_image": sharing_image,
            "title": title,
            "interest_id": interest_id,
            "interest_slug": interest_slug,
            "match_count": match_count,
            "title_match_count": title_match_count,
        }

    return _make


@pytest.fixture
def make_fake_bq_client():
    """Factory for a fake BigQuery client (mocks ``.query(sql, job_config=).result()``).

    Captures the last call's SQL + job_config on ``client.captured`` so tests can
    assert the query parameters (struct array + scalars) without hitting BigQuery.
    Pass ``raise_exc`` to simulate a BigQuery failure on ``.query()``.
    """

    def _make(
        rows: list | None = None, raise_exc: Exception | None = None
    ) -> MagicMock:
        client = MagicMock()
        captured: dict = {}

        def _query(sql, job_config=None):
            captured["sql"] = sql
            captured["job_config"] = job_config
            if raise_exc is not None:
                raise raise_exc
            job = MagicMock()
            job.result.return_value = list(rows or [])
            return job

        client.query.side_effect = _query
        client.captured = captured
        return client

    return _make
