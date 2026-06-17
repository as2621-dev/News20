"""Tests for the followed-source ingestion pipeline (Phase 5d SP3).

WHY these matter (Rule 9): this is the wiring that turns a followed channel's new
upload into a story-pool candidate tagged to the user. The tests encode the
business rules the phase locks: cadence blocks re-fetch, dedup drops an
already-ingested item, a substantive item is promoted as a source-origin
CanonicalStory carrying its thumbnail, and one source's failure never aborts the
user's batch. ALL adapters are mocked — no network, no yt-dlp, no xAI, no browser.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from agents.ingestion.dedup import is_source_origin_domain, source_item_dedup_key
from agents.ingestion.models import CandidateStory
from agents.ingestion.source_pipeline import (
    FollowedSource,
    run_source_ingestion,
)
from agents.shared.exceptions import AdapterFetchError

_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
_LONG_BODY = "x" * 200  # clears the substance floor


def _yt_candidate(video_id: str, *, body: str = _LONG_BODY) -> CandidateStory:
    """Build a YouTube-shaped CandidateStory (as YouTubeAdapter would emit)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    return CandidateStory(
        candidate_external_id=url,
        candidate_title=f"Video {video_id}",
        candidate_url=url,
        candidate_outlet_domain="youtube.com",
        candidate_outlet_name="Some Channel",
        candidate_published_utc=_NOW,
        candidate_social_image_url=f"https://i.ytimg.com/{video_id}.jpg",
        candidate_body_text=body,
    )


def _yt_source(source_id: str, *, last_fetched_at: datetime | None) -> FollowedSource:
    return FollowedSource(
        source_id=source_id,
        content_source_type="youtube_channel",
        external_id="UCabc",
        source_name="Some Channel",
        last_fetched_at=last_fetched_at,
    )


def _mock_youtube_adapter(items: list[CandidateStory]) -> AsyncMock:
    """A mock YouTubeAdapter whose fetch_new_items returns the given items."""
    adapter = AsyncMock()
    adapter.fetch_new_items = AsyncMock(return_value=items)
    return adapter


@pytest.mark.asyncio
class TestRunSourceIngestion:
    """run_source_ingestion — the per-user promote-to-pool flow."""

    async def test_new_upload_becomes_source_origin_pool_candidate(self) -> None:
        """A fresh upload is promoted as a source-origin story tagged to the user."""
        adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        source = _yt_source("src1", last_fetched_at=_NOW - timedelta(hours=8))

        result = await run_source_ingestion(
            "user-1",
            [source],
            youtube_adapter=adapter,
            now_utc=_NOW,
        )

        assert result.items_fetched == 1
        assert len(result.promoted_stories) == 1
        promoted = result.promoted_stories[0]
        # Tagged to the user + back-linked to the source it came from.
        assert promoted.user_id == "user-1"
        assert promoted.source_id == "src1"
        # Source-origin: outlet domain marks it so produce_gate exempts it.
        assert is_source_origin_domain(promoted.story.canonical_primary_outlet_domain)
        # Carries its thumbnail so the poster stage skips generation.
        assert promoted.source_image_url == "https://i.ytimg.com/vid1.jpg"
        assert (
            promoted.story.canonical_social_image_url == "https://i.ytimg.com/vid1.jpg"
        )
        # The adapter was polled with the source's last_fetched_at as the cutoff.
        adapter.fetch_new_items.assert_awaited_once()
        _, called_since = adapter.fetch_new_items.await_args.args
        assert called_since == source.last_fetched_at

    async def test_cadence_blocks_refetch_within_window(self) -> None:
        """A source polled 2h ago (6h cadence) is skipped — the adapter is NOT called."""
        adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        source = _yt_source("src1", last_fetched_at=_NOW - timedelta(hours=2))

        result = await run_source_ingestion(
            "user-1",
            [source],
            youtube_adapter=adapter,
            now_utc=_NOW,
        )

        adapter.fetch_new_items.assert_not_awaited()
        assert result.skipped_not_due_source_ids == ["src1"]
        assert result.promoted_stories == []

    async def test_dedup_drops_already_ingested_item(self) -> None:
        """An item whose key is in already_ingested_keys is dropped (re-run safety)."""
        candidate = _yt_candidate("vid1")
        adapter = _mock_youtube_adapter([candidate])
        source = _yt_source("src1", last_fetched_at=None)

        result = await run_source_ingestion(
            "user-1",
            [source],
            youtube_adapter=adapter,
            already_ingested_keys={source_item_dedup_key(candidate)},
            now_utc=_NOW,
        )

        assert result.items_fetched == 1
        assert result.items_dropped_dedup == 1
        assert result.promoted_stories == []

    async def test_intra_batch_duplicate_dropped(self) -> None:
        """The same upload surfaced twice this run is promoted only once."""
        dup = _yt_candidate("vid1")
        adapter = _mock_youtube_adapter([dup, _yt_candidate("vid1")])
        source = _yt_source("src1", last_fetched_at=None)

        result = await run_source_ingestion(
            "user-1", [source], youtube_adapter=adapter, now_utc=_NOW
        )

        assert result.items_fetched == 2
        assert result.items_dropped_dedup == 1
        assert len(result.promoted_stories) == 1

    async def test_non_substantive_item_dropped(self) -> None:
        """A caption-less / one-word item (too-short body) is not promoted."""
        adapter = _mock_youtube_adapter([_yt_candidate("vid1", body="hi")])
        source = _yt_source("src1", last_fetched_at=None)

        result = await run_source_ingestion(
            "user-1", [source], youtube_adapter=adapter, now_utc=_NOW
        )

        assert result.items_fetched == 1
        assert result.items_dropped_not_substantive == 1
        assert result.promoted_stories == []

    async def test_one_source_failure_does_not_abort_batch(self) -> None:
        """A failing source is recorded + skipped; the healthy source still promotes."""
        good_adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        bad_adapter = AsyncMock()
        bad_adapter.fetch_new_items = AsyncMock(
            side_effect=AdapterFetchError(message="boom", adapter_name="youtube")
        )
        good = FollowedSource(
            source_id="good",
            content_source_type="youtube_channel",
            external_id="UCgood",
            last_fetched_at=None,
        )
        bad = FollowedSource(
            source_id="bad",
            content_source_type="x_account",
            external_id="@bad",
            last_fetched_at=None,
        )

        result = await run_source_ingestion(
            "user-1",
            [good, bad],
            youtube_adapter=good_adapter,
            x_adapter=bad_adapter,
            now_utc=_NOW,
        )

        assert result.failed_source_ids == ["bad"]
        assert "good" in result.polled_source_ids
        assert len(result.promoted_stories) == 1

    async def test_unsupported_type_skipped(self) -> None:
        """An out-of-scope source type (podcast) is skipped, never dispatched."""
        podcast = FollowedSource(
            source_id="pod1",
            content_source_type="podcast",
            external_id="feed-url",
            last_fetched_at=None,
        )
        result = await run_source_ingestion("user-1", [podcast], now_utc=_NOW)
        assert result.promoted_stories == []
        assert result.polled_source_ids == []

    async def test_dispatch_routes_by_type(self) -> None:
        """youtube_channel → YouTube adapter, x_account → X adapter (each gets its own)."""
        yt_adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        x_item = CandidateStory(
            candidate_external_id="https://x.com/Reuters/status/123",
            candidate_title="@Reuters: news",
            candidate_url="https://x.com/Reuters/status/123",
            candidate_outlet_domain="x.com",
            candidate_outlet_name="@Reuters",
            candidate_published_utc=_NOW,
            candidate_social_image_url="/tmp/tweet.png",
            candidate_body_text=_LONG_BODY,
        )
        x_adapter = _mock_youtube_adapter([x_item])

        yt_src = _yt_source("yt", last_fetched_at=None)
        x_src = FollowedSource(
            source_id="x",
            content_source_type="x_account",
            external_id="Reuters",
            last_fetched_at=None,
        )

        result = await run_source_ingestion(
            "user-1",
            [yt_src, x_src],
            youtube_adapter=yt_adapter,
            x_adapter=x_adapter,
            now_utc=_NOW,
        )

        yt_adapter.fetch_new_items.assert_awaited_once()
        x_adapter.fetch_new_items.assert_awaited_once()
        assert len(result.promoted_stories) == 2
        domains = {
            p.story.canonical_primary_outlet_domain for p in result.promoted_stories
        }
        assert domains == {"youtube.com", "x.com"}


@pytest.mark.asyncio
class TestLastFetchedWriteBack:
    """The `mark_source_polled` write-back (Phase 5d SP4).

    WHY (Rule 9): without stamping `content_sources.last_fetched_at` after a poll, the
    CadenceScheduler can never throttle re-fetch in production — every run would
    re-poll every source. The callback must fire EXACTLY ONCE per successfully-polled
    source, with that source's id + the run clock, and must NOT fire for a source that
    was skipped (cadence not due) or whose adapter fetch failed (so a transient
    failure is retried next run).
    """

    async def test_marks_polled_source_with_run_clock(self) -> None:
        """A successfully-polled source triggers mark_source_polled(source_id, now)."""
        adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        source = _yt_source("src1", last_fetched_at=None)
        mark = AsyncMock()

        await run_source_ingestion(
            "user-1",
            [source],
            youtube_adapter=adapter,
            now_utc=_NOW,
            mark_source_polled=mark,
        )

        mark.assert_awaited_once_with("src1", _NOW)

    async def test_does_not_mark_a_not_due_source(self) -> None:
        """A cadence-skipped source is never marked (its adapter was not polled)."""
        adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        # Polled 2h ago against the 6h YouTube cadence → not due.
        source = _yt_source("src1", last_fetched_at=_NOW - timedelta(hours=2))
        mark = AsyncMock()

        result = await run_source_ingestion(
            "user-1",
            [source],
            youtube_adapter=adapter,
            now_utc=_NOW,
            mark_source_polled=mark,
        )

        assert result.skipped_not_due_source_ids == ["src1"]
        mark.assert_not_awaited()

    async def test_does_not_mark_a_failed_source(self) -> None:
        """A source whose adapter fetch raises is NOT marked (retried next run)."""
        bad_adapter = AsyncMock()
        bad_adapter.fetch_new_items = AsyncMock(
            side_effect=AdapterFetchError(message="boom", adapter_name="youtube")
        )
        source = _yt_source("src1", last_fetched_at=None)
        mark = AsyncMock()

        result = await run_source_ingestion(
            "user-1",
            [source],
            youtube_adapter=bad_adapter,
            now_utc=_NOW,
            mark_source_polled=mark,
        )

        assert result.failed_source_ids == ["src1"]
        mark.assert_not_awaited()

    async def test_write_back_is_optional(self) -> None:
        """With no callback injected the pipeline stays pure (no DB) and still promotes."""
        adapter = _mock_youtube_adapter([_yt_candidate("vid1")])
        source = _yt_source("src1", last_fetched_at=None)

        result = await run_source_ingestion(
            "user-1", [source], youtube_adapter=adapter, now_utc=_NOW
        )

        assert len(result.promoted_stories) == 1
