"""Tests for the worker source-search endpoint (Phase 5c SP3a).

WHY (Rule 9 — encode the contract, not the call shape):
  • The YouTube path MUST chain the 2-step flow correctly: search.list yields
    channel ids, channels.list enriches them → typed addable results with
    subscriber counts + thumbnails. A broken chain (e.g. not passing the ids to
    step 2) would silently drop every channel, so we assert the enriched result.
  • A MISSING YouTube key MUST return an empty, search_ok=False envelope and LOG
    it — never crash, never a swallowed silent empty (Rule 12). The honest
    search_ok flag is what lets the UI say "unavailable" vs "no matches".
  • The iTunes path MUST build external_id as `itunes-{collectionId}` (the seeder
    convention) so a podcast added here dedups to the same catalog row.
  • An @handle MUST resolve to a PENDING x_account result (is_pending=True) — the
    DoD fallback (no live X lookup wired).
  • Every upstream failure degrades to search_ok=False, never a 5xx.

Everything external is mocked at the `agents.worker.main` boundary — httpx is a
fake async client, the YouTube key is monkeypatched onto Settings, and the X
resolver is exercised through its real (network-free) pending path. No network,
no key, no env vars (CLAUDE.md mocking strategy).

    >>> pytest tests/agents/qa/test_source_search.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agents.worker import main as worker_main

_SEARCH_PATH = "/api/sources/search"


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient for the worker app."""
    return TestClient(worker_main.app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Clear the in-memory per-IP rate-limit state so tests don't pollute each other."""
    worker_main._request_times_by_ip.clear()


def _set_youtube_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    """Force Settings().youtube_api_key to a fixed value (no real env read).

    Patches the Settings class used inside the route so the test controls the
    key-present / key-absent branch without touching the real environment.
    """

    class _FakeSettings:
        youtube_api_key = key

    monkeypatch.setattr(worker_main, "Settings", _FakeSettings)


def _fake_http_client(get_behavior: Any) -> MagicMock:
    """Build a MagicMock standing in for `httpx.AsyncClient(...)` as a context mgr.

    The route uses ``async with httpx.AsyncClient(...) as c: await c.get(...)``, so
    the mock must support the async-context-manager protocol and expose an
    AsyncMock ``get``. ``get_behavior`` is:
      • a ``list`` → one response per successive ``.get`` call (the 2-step flow),
      • an ``Exception`` (instance or class) → raised on ``.get`` (upstream failure),
      • otherwise → returned from every ``.get`` (a single response object).

    Args:
        get_behavior: How the mocked ``.get`` should behave (see above).

    Returns:
        A mock whose ``AsyncClient(...)`` returns an async-context-manager client.
    """
    fake_client = MagicMock()
    is_exception = isinstance(get_behavior, BaseException) or (
        isinstance(get_behavior, type) and issubclass(get_behavior, BaseException)
    )
    if isinstance(get_behavior, list):
        fake_client.get = AsyncMock(side_effect=get_behavior)
    elif is_exception:
        fake_client.get = AsyncMock(side_effect=get_behavior)
    else:
        fake_client.get = AsyncMock(return_value=get_behavior)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=fake_client)
    async_cm.__aexit__ = AsyncMock(return_value=False)

    httpx_mock = MagicMock()
    httpx_mock.AsyncClient = MagicMock(return_value=async_cm)
    return httpx_mock


def _json_response(payload: dict[str, Any]) -> MagicMock:
    """A mock httpx response whose ``.json()`` returns payload + a no-op raise_for_status."""
    response = MagicMock()
    response.json = MagicMock(return_value=payload)
    response.raise_for_status = MagicMock()
    return response


class TestYouTubeSearch:
    """The 2-step YouTube channel search (search.list → channels.list)."""

    def test_two_step_chain_returns_enriched_results(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """search.list ids → channels.list enrich → typed result with subs + thumbnail."""
        _set_youtube_key(monkeypatch, "fake-key")

        search_body = _json_response(
            {
                "items": [
                    {
                        "id": {"channelId": "UC_lex"},
                        "snippet": {
                            "title": "Lex Fridman",
                            "description": "Conversations.",
                            "thumbnails": {
                                "default": {"url": "https://yt/lex_default.jpg"}
                            },
                        },
                    }
                ]
            }
        )
        channels_body = _json_response(
            {
                "items": [
                    {
                        "id": "UC_lex",
                        "snippet": {
                            "title": "Lex Fridman",
                            "description": "Conversations about AI.",
                            "thumbnails": {"high": {"url": "https://yt/lex_high.jpg"}},
                        },
                        "statistics": {"subscriberCount": "4200000"},
                    }
                ]
            }
        )
        # First .get() = search.list, second = channels.list.
        monkeypatch.setattr(
            worker_main, "httpx", _fake_http_client([search_body, channels_body])
        )

        response = client.post(
            _SEARCH_PATH, json={"query": "lex", "kind": "youtube_channel"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["search_ok"] is True
        assert len(body["results"]) == 1
        result = body["results"][0]
        assert result["source_name"] == "Lex Fridman"
        assert result["external_id"] == "UC_lex"
        assert result["content_source_type"] == "youtube_channel"
        assert (
            result["thumbnail_url"] == "https://yt/lex_high.jpg"
        )  # hi-res from step 2
        assert result["subscriber_count"] == 4200000

    def test_missing_api_key_returns_unavailable_not_empty(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No YouTube key → search_ok=False (UNAVAILABLE), logged, never crashed.

        WHY (Rule 12): a missing key means the search could not RUN. Returning
        search_ok=False (not an empty "no matches") lets the UI honestly say
        "search unavailable" instead of falsely "no channels found".
        """
        _set_youtube_key(monkeypatch, None)
        # httpx must never be called when the key is missing (we don't patch it).

        response = client.post(
            _SEARCH_PATH, json={"query": "lex", "kind": "youtube_channel"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        assert body["search_ok"] is False  # could not run, not "no matches"

    def test_hidden_subscriber_count_maps_to_none(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hidden subscriber count is None, never a fake zero."""
        _set_youtube_key(monkeypatch, "fake-key")
        search_body = _json_response(
            {
                "items": [
                    {
                        "id": {"channelId": "UC_x"},
                        "snippet": {"title": "Ch", "thumbnails": {}},
                    }
                ]
            }
        )
        channels_body = _json_response(
            {
                "items": [
                    {
                        "id": "UC_x",
                        "snippet": {"title": "Ch", "thumbnails": {}},
                        "statistics": {
                            "subscriberCount": "100",
                            "hiddenSubscriberCount": True,
                        },
                    }
                ]
            }
        )
        monkeypatch.setattr(
            worker_main, "httpx", _fake_http_client([search_body, channels_body])
        )

        response = client.post(
            _SEARCH_PATH, json={"query": "ch", "kind": "youtube_channel"}
        )
        assert response.json()["results"][0]["subscriber_count"] is None

    def test_upstream_failure_returns_unavailable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A search.list HTTP error → search_ok=False (unavailable), never a 5xx."""
        _set_youtube_key(monkeypatch, "fake-key")
        monkeypatch.setattr(
            worker_main, "httpx", _fake_http_client(RuntimeError("youtube 403"))
        )

        response = client.post(
            _SEARCH_PATH, json={"query": "x", "kind": "youtube_channel"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        assert body["search_ok"] is False  # could not run, not "no matches"


class TestPodcastSearch:
    """The keyless iTunes podcast search."""

    def test_itunes_external_id_uses_seeder_convention(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """external_id is `itunes-{collectionId}` so it dedups to the catalog row."""
        body = _json_response(
            {
                "results": [
                    {
                        "collectionId": 12345,
                        "collectionName": "The Daily",
                        "artistName": "The New York Times",
                        "trackCount": 2000,
                        "artworkUrl600": "https://itunes/daily600.jpg",
                    }
                ]
            }
        )
        monkeypatch.setattr(worker_main, "httpx", _fake_http_client(body))

        response = client.post(
            _SEARCH_PATH, json={"query": "the daily", "kind": "podcast"}
        )

        assert response.status_code == 200
        result = response.json()["results"][0]
        assert result["external_id"] == "itunes-12345"
        assert result["source_name"] == "The Daily"
        assert result["content_source_type"] == "podcast"
        assert result["thumbnail_url"] == "https://itunes/daily600.jpg"
        assert "2,000 episodes" in result["description"]

    def test_entry_missing_collection_id_is_skipped_search_still_ok(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An entry without a collectionId/name is dropped — a genuine "no matches".

        The search RAN successfully (iTunes responded), it just had no usable
        entries → empty results BUT search_ok=True (not an outage).
        """
        body = _json_response({"results": [{"collectionName": "No Id Podcast"}]})
        monkeypatch.setattr(worker_main, "httpx", _fake_http_client(body))

        response = client.post(_SEARCH_PATH, json={"query": "x", "kind": "podcast"})
        result_body = response.json()
        assert result_body["results"] == []
        assert result_body["search_ok"] is True  # ran fine, just no matches

    def test_itunes_failure_returns_unavailable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An iTunes outage → search_ok=False (unavailable), never a 5xx."""
        monkeypatch.setattr(
            worker_main, "httpx", _fake_http_client(RuntimeError("itunes 503"))
        )

        response = client.post(_SEARCH_PATH, json={"query": "x", "kind": "podcast"})
        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        assert body["search_ok"] is False  # could not run, not "no matches"


class TestXAccountSearch:
    """The build-fresh X resolver path (pending free-text follow fallback)."""

    def test_handle_resolves_to_pending_x_account(self, client: TestClient) -> None:
        """An @handle resolves to a single PENDING x_account result (the DoD fallback)."""
        response = client.post(
            _SEARCH_PATH, json={"query": "@Reuters", "kind": "x_account"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["search_ok"] is True
        assert len(body["results"]) == 1
        result = body["results"][0]
        assert result["content_source_type"] == "x_account"
        assert result["external_id"] == "reuters"  # lower-cased id
        assert result["source_name"] == "Reuters"
        assert result["is_pending"] is True
        assert result["description"] == "@Reuters"

    def test_unparseable_handle_returns_empty(self, client: TestClient) -> None:
        """Garbage X input returns empty results (user typed garbage), not an error."""
        response = client.post(
            _SEARCH_PATH, json={"query": "https://x.com/home", "kind": "x_account"}
        )
        assert response.status_code == 200
        assert response.json()["results"] == []


class TestRequestValidation:
    """The Pydantic boundary rejects malformed requests with 422."""

    def test_unsupported_kind_is_rejected(self, client: TestClient) -> None:
        """`personality` is a client-side catalog read, not a worker search → 422."""
        response = client.post(_SEARCH_PATH, json={"query": "x", "kind": "personality"})
        assert response.status_code == 422

    def test_empty_query_is_rejected(self, client: TestClient) -> None:
        """An empty query violates min_length=1 → 422 (fail loud at the boundary)."""
        response = client.post(_SEARCH_PATH, json={"query": "", "kind": "podcast"})
        assert response.status_code == 422
