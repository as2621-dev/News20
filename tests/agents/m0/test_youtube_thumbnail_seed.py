"""Tests for the YouTube-thumbnail poster seed (Workstream I, 2026-06-12).

When a story's source is a YouTube video/podcast, the poster pipeline seeds
image generation from the video's own thumbnail (prepended ahead of the SERP
candidates). These tests cover the PURE helpers only — no network, no Serper.
"""

from __future__ import annotations

from agents.m0.serper_image_search import (
    youtube_thumbnail_candidate,
    youtube_video_id_from_url,
)


class TestYoutubeVideoIdFromUrl:
    """URL-shape parsing for the canonical 11-char video id."""

    def test_happy_path_watch_url(self) -> None:
        """The standard watch?v= URL yields the id."""
        assert (
            youtube_video_id_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            == "dQw4w9WgXcQ"
        )

    def test_failure_non_youtube_url_returns_none(self) -> None:
        """A regular news-article URL is not a video source.

        WHY: the seed must ONLY activate for YouTube sources — a false positive
        would fetch a nonsense i.ytimg.com URL for every article story.
        """
        assert youtube_video_id_from_url("https://www.reuters.com/some-article") is None
        assert youtube_video_id_from_url("") is None

    def test_edge_short_link_shorts_and_extra_params(self) -> None:
        """youtu.be short links, /shorts/ paths, and extra query params all parse."""
        assert (
            youtube_video_id_from_url("https://youtu.be/dQw4w9WgXcQ?t=42")
            == "dQw4w9WgXcQ"
        )
        assert (
            youtube_video_id_from_url("https://www.youtube.com/shorts/abcDEF12345")
            == "abcDEF12345"
        )
        assert (
            youtube_video_id_from_url(
                "https://m.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&list=PL1"
            )
            == "dQw4w9WgXcQ"
        )


class TestYoutubeThumbnailCandidate:
    """The prepended seed candidate's shape."""

    def test_happy_path_builds_maxres_with_hq_fallback(self) -> None:
        """maxresdefault is the full image; hqdefault is the download fallback.

        WHY: download_candidate tries full_image_url then thumbnail_url — older
        uploads have no maxres frame, so the always-present hqdefault must sit in
        the fallback slot or the seed silently vanishes for those videos.
        """
        candidate = youtube_thumbnail_candidate(
            "https://youtu.be/dQw4w9WgXcQ", "digest-1"
        )
        assert candidate is not None
        assert candidate.candidate_id == "digest-1-cand-youtube"
        assert (
            candidate.full_image_url
            == "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
        )
        assert (
            candidate.thumbnail_url
            == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        )

    def test_failure_non_youtube_source_returns_none(self) -> None:
        """Non-YouTube sources produce no candidate (SERP-only seeding)."""
        assert (
            youtube_thumbnail_candidate("https://apnews.com/article/x", "digest-1")
            is None
        )
