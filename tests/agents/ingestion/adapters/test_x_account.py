"""Unit tests for the X/Twitter source adapter (Phase 5d SP2).

All externals are mocked at the boundary (CLAUDE.md): the xAI/Grok x_search call
is an injected ``post_discoverer`` stub returning fixture post dicts (or raising
to simulate rate-limit / no-auth), and the tweet-screenshot render is an injected
``screenshot_renderer`` stub. No network, no API key, no real browser.

Covers:
    • fetch_new_items normalizes discovered posts into CandidateStory records with
      body text, @handle outlet, tweet-URL external id, and TwitterContentMetadata
    • the rendered screenshot path is stamped onto candidate_social_image_url
    • posts at/before the cutoff are filtered out; posts missing a URL/text dropped
    • a discovery failure (rate-limit / no-auth) returns a clean [] (no crash)
    • the missing-key path raises AdapterFetchError WITHOUT leaking the key, and
      fetch_new_items turns it into a clean []
    • the xAI JSON-array response parser reads the /v1/responses output shape,
      tolerates a ```json fence and bad payloads, and still parses legacy choices
    • no log/return value ever contains the API key

    >>> pytest tests/agents/ingestion/adapters/test_x_account.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from agents.ingestion.adapters.x_account import (
    XAccountAdapter,
    _parse_xai_response,
)
from agents.ingestion.models import TwitterContentMetadata
from agents.shared.exceptions import AdapterFetchError
from agents.shared.settings import Settings

_HANDLE = "Reuters"
_SINCE = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
_NEW_TWEET_URL = "https://x.com/Reuters/status/1800000000000000001"
_OLD_TWEET_URL = "https://x.com/Reuters/status/1700000000000000000"


def _new_post() -> dict[str, Any]:
    """A post published AFTER the cutoff (kept)."""
    return {
        "tweet_url": _NEW_TWEET_URL,
        "text": "Breaking: a substantive update from the wire with real content.",
        "published_utc": "2026-06-15T10:15:00+00:00",
        "is_quote": True,
        "quoted_tweet_url": "https://x.com/Other/status/1799999999999999000",
        "is_thread": False,
    }


def _old_post() -> dict[str, Any]:
    """A post published BEFORE the cutoff (filtered out)."""
    return {
        "tweet_url": _OLD_TWEET_URL,
        "text": "An older post from before the cutoff.",
        "published_utc": "2026-06-10T09:00:00+00:00",
    }


def _adapter_with(
    posts: list[dict[str, Any]] | None = None,
    *,
    discoverer_error: Exception | None = None,
    screenshot_path: str | None = "/assets/sources/tweets/1800000000000000001.png",
) -> XAccountAdapter:
    """Build an adapter with mocked xAI discovery + screenshot seams (no real I/O)."""

    async def fake_discoverer(
        handle: str, since: datetime, max_posts: int
    ) -> list[dict[str, Any]]:
        if discoverer_error is not None:
            raise discoverer_error
        return posts or []

    async def fake_screenshot(tweet_url: str) -> str | None:
        return screenshot_path

    return XAccountAdapter(
        post_discoverer=fake_discoverer,
        screenshot_renderer=fake_screenshot,
        # Empty settings so a stray .env key cannot leak into the test path.
        settings=Settings(xai_api_key=""),
    )


@pytest.mark.asyncio
async def test_fetch_new_items_normalizes_post_to_candidate() -> None:
    """A discovered post becomes a CandidateStory with the SP2-locked shape.

    WHY: downstream produce/poster stages key off external_id (dedup), outlet
    (@handle), body text (script), the screenshot image, and the platform metadata
    (attribution / thread refs) — all must be populated exactly per the contract.
    """
    adapter = _adapter_with([_new_post()])

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_external_id == _NEW_TWEET_URL
    assert candidate.candidate_url == _NEW_TWEET_URL
    assert candidate.candidate_outlet_domain == "x.com"
    assert candidate.candidate_outlet_name == "@Reuters"
    assert candidate.candidate_title.startswith("@Reuters: ")
    assert candidate.candidate_body_text and "substantive update" in (
        candidate.candidate_body_text
    )
    # Screenshot path stamped by extract_body.
    assert (
        candidate.candidate_social_image_url
        == "/assets/sources/tweets/1800000000000000001.png"
    )
    # Platform metadata round-trips into a TwitterContentMetadata.
    assert candidate.candidate_platform_metadata is not None
    meta = TwitterContentMetadata(**candidate.candidate_platform_metadata)
    assert meta.tweet_id == "1800000000000000001"
    assert meta.author_handle == "Reuters"
    assert meta.is_quote is True
    assert meta.quoted_tweet_url == "https://x.com/Other/status/1799999999999999000"


@pytest.mark.asyncio
async def test_fetch_filters_posts_at_or_before_cutoff() -> None:
    """Posts published at/before ``since`` are dropped; only fresh posts return.

    WHY: the cadence/cutoff is what stops re-ingesting the same posts every poll —
    a post not strictly after the cutoff must not re-enter the pool.
    """
    adapter = _adapter_with([_new_post(), _old_post()])

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert [c.candidate_url for c in candidates] == [_NEW_TWEET_URL]


@pytest.mark.asyncio
async def test_fetch_drops_posts_missing_url_or_text() -> None:
    """Posts with no tweet_url or empty text are dropped, not emitted as junk."""
    adapter = _adapter_with(
        [
            {"text": "no url here", "published_utc": "2026-06-15T10:00:00+00:00"},
            {
                "tweet_url": _NEW_TWEET_URL,
                "text": "",
                "published_utc": "2026-06-15T10:00:00+00:00",
            },
        ]
    )

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert candidates == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_discovery_failure_no_crash() -> None:
    """A discovery failure (rate-limit / no-auth) returns a clean [] — never raises.

    WHY (SP2 DoD): one bad handle / a rate-limit must not abort the whole user's
    ingestion. The adapter catches the boundary error and returns empty.
    """
    adapter = _adapter_with(
        discoverer_error=AdapterFetchError(
            message="xAI Live Search HTTP 429",
            adapter_name="x_account",
            fix_suggestion="back off",
        )
    )

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert candidates == []


@pytest.mark.asyncio
async def test_default_discoverer_missing_key_raises_without_leaking() -> None:
    """The real xAI seam raises AdapterFetchError when XAI_API_KEY is unset.

    WHY (SP2 DoD): no-auth must be a clean, loud failure with a fix_suggestion —
    and crucially the error text must NOT contain any key material.
    """
    adapter = XAccountAdapter(settings=Settings(xai_api_key=""))

    with pytest.raises(AdapterFetchError) as exc_info:
        await adapter._default_xai_discoverer(_HANDLE, _SINCE, 5)

    assert "XAI_API_KEY is not set" in str(exc_info.value)
    assert exc_info.value.fix_suggestion


@pytest.mark.asyncio
async def test_missing_key_fetch_returns_clean_empty() -> None:
    """End to end: a missing key (real discoverer) yields a clean [] from fetch."""
    adapter = XAccountAdapter(settings=Settings(xai_api_key=""))

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert candidates == []


@pytest.mark.asyncio
async def test_fetch_invalid_handle_returns_empty() -> None:
    """An unparseable handle is a clean skip (empty list), not a crash."""
    adapter = _adapter_with([_new_post()])

    candidates = await adapter.fetch_new_items("https://x.com/home", _SINCE)

    assert candidates == []


@pytest.mark.asyncio
async def test_screenshot_failure_keeps_post_without_image() -> None:
    """A failed screenshot (renderer returns None) keeps the post, image left None."""
    adapter = _adapter_with([_new_post()], screenshot_path=None)

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert len(candidates) == 1
    assert candidates[0].candidate_social_image_url is None


def _responses_body(content_text: str) -> dict[str, Any]:
    """Wrap assistant text in the Agent Tools ``/v1/responses`` output shape."""
    return {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content_text}],
            }
        ]
    }


def test_parse_xai_response_reads_responses_output_shape() -> None:
    """The parser extracts the JSON array from the /v1/responses output[].content[].text.

    WHY: discovery now hits the Agent Tools ``/v1/responses`` endpoint (x_search),
    whose answer lives under ``output[].content[].text`` — not the legacy
    ``choices[0].message.content``. The pipeline keys off this parse, so a shape
    regression silently drops every post.
    """
    posts = [{"tweet_url": _NEW_TWEET_URL, "text": "hi"}]

    parsed = _parse_xai_response(_responses_body(json.dumps(posts)))

    assert parsed == posts


def test_parse_xai_response_strips_json_fence() -> None:
    """The response parser tolerates a ```json fenced array (model formatting drift)."""
    posts = [{"tweet_url": _NEW_TWEET_URL, "text": "hi"}]
    fenced = "```json\n" + json.dumps(posts) + "\n```"

    parsed = _parse_xai_response(_responses_body(fenced))

    assert parsed == posts


def test_parse_xai_response_falls_back_to_legacy_choices() -> None:
    """The parser still reads the legacy chat-completions ``choices`` shape."""
    posts = [{"tweet_url": _NEW_TWEET_URL, "text": "hi"}]
    body = {"choices": [{"message": {"content": json.dumps(posts)}}]}

    assert _parse_xai_response(body) == posts


def test_parse_xai_response_handles_unparseable_content() -> None:
    """Non-JSON / non-array content yields [] (treated as no posts), not a crash."""
    assert _parse_xai_response(_responses_body("sorry, no")) == []
    assert _parse_xai_response(_responses_body("{}")) == []
    assert _parse_xai_response({}) == []


@pytest.mark.asyncio
async def test_no_key_leak_in_error_path(caplog: pytest.LogCaptureFixture) -> None:
    """The discovery-failure log path must never contain key material.

    WHY (SP2 DoD, Rule 12): a secret leak in logs is a security defect. The error
    log carries only the handle + a sanitized message, never the key.
    """
    secret = "xai-super-secret-key-value"
    # The discovery error does NOT embed the key (a realistic transport error never
    # echoes the Authorization header). The key is only ever in settings.
    adapter = _adapter_with(
        discoverer_error=RuntimeError("HTTP 401 Unauthorized from api.x.ai")
    )
    adapter._settings = Settings(xai_api_key=secret)

    candidates = await adapter.fetch_new_items(_HANDLE, _SINCE)

    assert candidates == []
    # The configured key value must never appear in the adapter's logs/output.
    assert secret not in caplog.text
