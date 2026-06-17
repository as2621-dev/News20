"""Unit tests for the YouTube source adapter (Phase 5d SP1).

All external calls are mocked at the boundary (CLAUDE.md): the channel RSS fetch
is a mock httpx client returning a fixture XML body, and yt-dlp is replaced by an
injected ``ytdlp_extractor`` callable returning a fixture ``extract_info`` dict.
No network, no yt-dlp install, fully offline + deterministic.

Covers:
    • fetch_new_items keeps only videos published after the cutoff
    • each new captioned video becomes a CandidateStory with transcript + thumbnail
    • a caption-less video is skipped as failed (never crashes the batch)
    • the WebVTT → plain-text transcript parse (dedup + tag/timing strip)
    • the base ``search()`` shim delegates to the source-keyed path
    • an RSS fetch / parse failure raises AdapterFetchError (loud)

    >>> pytest tests/agents/ingestion/adapters/test_youtube.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agents.ingestion.adapters.youtube import (
    CaptionUnavailableError,
    YouTubeAdapter,
    _vtt_to_plain_text,
)
from agents.shared.exceptions import AdapterFetchError

_CHANNEL_ID = "UCtest_channel_id"
# Cutoff between the two feed entries: NEW_VIDEO is after, OLD_VIDEO is before.
_SINCE = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)

_VTT_CAPTION = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
Breaking news today

00:00:02.000 --> 00:00:04.000
Breaking news today

00:00:04.000 --> 00:00:06.000
<00:00:04.500><c>the market</c> rallied hard
"""


def _channel_feed_xml(
    *,
    new_published: str = "2026-06-15T10:15:00+00:00",
    old_published: str = "2026-06-10T09:00:00+00:00",
) -> str:
    """Build a channel RSS feed with one NEW (post-cutoff) + one OLD (pre-cutoff) video."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Test News Channel</title>
  <entry>
    <yt:videoId>NEWVIDEO123</yt:videoId>
    <title>New market rally explainer</title>
    <published>{new_published}</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/NEWVIDEO123/hqdefault.jpg" width="480" height="360"/>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>OLDVIDEO456</yt:videoId>
    <title>Old video from last week</title>
    <published>{old_published}</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/OLDVIDEO456/hqdefault.jpg" width="480" height="360"/>
    </media:group>
  </entry>
</feed>"""


def _mock_response(text_body: str, status_code: int = 200) -> MagicMock:
    """A mock httpx response whose ``.text`` is the given body."""
    response = MagicMock()
    response.text = text_body
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    return response


def _mock_http_client(response: MagicMock) -> AsyncMock:
    """A mock httpx.AsyncClient whose ``.get`` returns the given response."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.aclose = AsyncMock()
    return client


def _info_with_captions() -> dict:
    """A yt-dlp extract_info dict carrying an English auto-caption with inline vtt data."""
    return {
        "id": "NEWVIDEO123",
        "title": "New market rally explainer",
        "thumbnail": "https://i.ytimg.com/vi/NEWVIDEO123/maxresdefault.jpg",
        "automatic_captions": {
            "en": [{"ext": "vtt", "url": "https://x/cc.vtt", "data": _VTT_CAPTION}],
        },
        "subtitles": {},
    }


def _info_without_captions() -> dict:
    """A yt-dlp extract_info dict with no caption tracks (caption-less video)."""
    return {
        "id": "NEWVIDEO123",
        "title": "New market rally explainer",
        "thumbnail": "https://i.ytimg.com/vi/NEWVIDEO123/maxresdefault.jpg",
        "automatic_captions": {},
        "subtitles": {},
    }


class TestFetchNewItems:
    """fetch_new_items detects post-cutoff uploads and enriches captioned ones."""

    @pytest.mark.asyncio
    async def test_returns_only_new_captioned_video(self) -> None:
        """Only the post-cutoff video is returned, with transcript + thumbnail set."""
        client = _mock_http_client(_mock_response(_channel_feed_xml()))
        adapter = YouTubeAdapter(
            http_client=client,
            ytdlp_extractor=lambda url: _info_with_captions(),
        )

        candidates = await adapter.fetch_new_items(_CHANNEL_ID, _SINCE)

        assert len(candidates) == 1  # OLDVIDEO456 is before the cutoff, dropped
        cand = candidates[0]
        assert cand.candidate_url == "https://www.youtube.com/watch?v=NEWVIDEO123"
        assert cand.candidate_external_id == cand.candidate_url
        assert cand.candidate_title == "New market rally explainer"
        assert cand.candidate_outlet_domain == "youtube.com"
        assert cand.candidate_outlet_name == "Test News Channel"
        assert cand.candidate_published_utc == datetime(
            2026, 6, 15, 10, 15, 0, tzinfo=timezone.utc
        )
        # yt-dlp's higher-res thumbnail overrides the RSS one.
        assert (
            cand.candidate_social_image_url
            == "https://i.ytimg.com/vi/NEWVIDEO123/maxresdefault.jpg"
        )
        # Transcript parsed from the vtt: dedup + tag strip.
        assert cand.candidate_body_text == "Breaking news today the market rallied hard"

    @pytest.mark.asyncio
    async def test_caption_less_video_is_skipped_not_raised(self) -> None:
        """A post-cutoff video with no captions is skipped; the batch does not crash."""
        client = _mock_http_client(_mock_response(_channel_feed_xml()))
        adapter = YouTubeAdapter(
            http_client=client,
            ytdlp_extractor=lambda url: _info_without_captions(),
        )

        candidates = await adapter.fetch_new_items(_CHANNEL_ID, _SINCE)

        # The only new video is caption-less → returned list is empty, no exception.
        assert candidates == []

    @pytest.mark.asyncio
    async def test_naive_cutoff_is_treated_as_utc(self) -> None:
        """A tz-naive ``since`` is assumed UTC (no crash comparing to aware feed times)."""
        client = _mock_http_client(_mock_response(_channel_feed_xml()))
        adapter = YouTubeAdapter(
            http_client=client,
            ytdlp_extractor=lambda url: _info_with_captions(),
        )

        naive_since = datetime(2026, 6, 14, 0, 0, 0)  # no tzinfo
        candidates = await adapter.fetch_new_items(_CHANNEL_ID, naive_since)
        assert len(candidates) == 1

    @pytest.mark.asyncio
    async def test_transient_ytdlp_failure_is_swallowed(self) -> None:
        """A non-caption yt-dlp error drops the one video without failing the batch."""

        def _boom(url: str) -> dict:
            raise RuntimeError("yt-dlp transient network error")

        client = _mock_http_client(_mock_response(_channel_feed_xml()))
        adapter = YouTubeAdapter(http_client=client, ytdlp_extractor=_boom)

        candidates = await adapter.fetch_new_items(_CHANNEL_ID, _SINCE)
        assert candidates == []  # swallowed, no raise

    @pytest.mark.asyncio
    async def test_empty_channel_id_raises(self) -> None:
        """An empty channel external_id raises AdapterFetchError (loud, Rule 12)."""
        adapter = YouTubeAdapter(
            http_client=_mock_http_client(_mock_response("")),
            ytdlp_extractor=lambda url: _info_with_captions(),
        )
        with pytest.raises(AdapterFetchError):
            await adapter.fetch_new_items("   ", _SINCE)


class TestFeedFailurePaths:
    """RSS-level failures raise AdapterFetchError so the channel poll fails loud."""

    @pytest.mark.asyncio
    async def test_http_error_raises_adapter_error(self) -> None:
        """An httpx transport error on the RSS fetch surfaces as AdapterFetchError."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
        client.aclose = AsyncMock()
        adapter = YouTubeAdapter(
            http_client=client, ytdlp_extractor=lambda url: _info_with_captions()
        )
        with pytest.raises(AdapterFetchError):
            await adapter.fetch_new_items(_CHANNEL_ID, _SINCE)

    @pytest.mark.asyncio
    async def test_malformed_xml_raises_adapter_error(self) -> None:
        """A non-XML RSS body raises AdapterFetchError (the channel cannot be polled)."""
        client = _mock_http_client(_mock_response("<<<not xml>>>"))
        adapter = YouTubeAdapter(
            http_client=client, ytdlp_extractor=lambda url: _info_with_captions()
        )
        with pytest.raises(AdapterFetchError):
            await adapter.fetch_new_items(_CHANNEL_ID, _SINCE)


class TestSearchShim:
    """The base search() contract delegates to the source-keyed path coherently."""

    @pytest.mark.asyncio
    async def test_search_delegates_to_fetch_new_items(self) -> None:
        """search(channel_id, since) returns the same candidates as fetch_new_items."""
        client = _mock_http_client(_mock_response(_channel_feed_xml()))
        adapter = YouTubeAdapter(
            http_client=client, ytdlp_extractor=lambda url: _info_with_captions()
        )
        candidates = await adapter.search(_CHANNEL_ID, _SINCE)
        assert len(candidates) == 1
        assert candidates[0].candidate_outlet_domain == "youtube.com"


class TestExtractBody:
    """extract_body fills the transcript and raises on caption-less videos."""

    @pytest.mark.asyncio
    async def test_caption_less_raises_caption_unavailable(
        self,
    ) -> None:
        """extract_body raises CaptionUnavailableError when no captions carry text."""
        from agents.ingestion.models import CandidateStory

        adapter = YouTubeAdapter(ytdlp_extractor=lambda url: _info_without_captions())
        candidate = CandidateStory(
            candidate_external_id="https://www.youtube.com/watch?v=NEWVIDEO123",
            candidate_title="x",
            candidate_url="https://www.youtube.com/watch?v=NEWVIDEO123",
            candidate_outlet_domain="youtube.com",
            candidate_published_utc=_SINCE,
        )
        with pytest.raises(CaptionUnavailableError):
            await adapter.extract_body(candidate)


class TestVttParsing:
    """The WebVTT → plain-text parser strips markup/timing and dedups."""

    def test_strips_header_timing_and_dedups(self) -> None:
        """Header, cue timings, inline tags removed; consecutive duplicates collapsed."""
        assert (
            _vtt_to_plain_text(_VTT_CAPTION)
            == "Breaking news today the market rallied hard"
        )

    def test_empty_vtt_returns_empty_string(self) -> None:
        """A vtt with only a header yields empty text (treated as caption-less upstream)."""
        assert _vtt_to_plain_text("WEBVTT\n\n") == ""
