"""Unit tests for the X theme reel story builder (FSR slice #24).

Intent pinned: the v1 reel image is the top-tweet screenshot (PRD #32), and a
screenshot render FAILURE must degrade to a synthetic poster (image left None) so the
reel still ships (slice #24 acceptance) — never a crash, never a dropped reel.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.ingestion.cluster_sweep import ClusterTheme
from agents.ingestion.x_theme_reel import X_OUTLET_DOMAIN, build_theme_reel_story


def _theme() -> ClusterTheme:
    return ClusterTheme(
        theme_summary="AI model launch reactions",
        supporting_handles=["alice", "bob"],
        supporting_tweet_urls=[
            "https://x.com/alice/status/1",
            "https://x.com/bob/status/2",
        ],
    )


@pytest.mark.asyncio
async def test_top_tweet_screenshot_becomes_the_reel_image() -> None:
    """The top (first) tweet is screenshotted and set as the reel's social image (PRD #32)."""
    seen: list[str] = []

    async def _renderer(tweet_url: str) -> str | None:
        seen.append(tweet_url)
        return "/assets/sources/tweets/1.png"

    story = await build_theme_reel_story(
        _theme(), cluster_id="c1", sweep_date=date(2026, 7, 5), screenshot_renderer=_renderer
    )

    assert seen == ["https://x.com/alice/status/1"]  # the TOP tweet
    assert story.canonical_social_image_url == "/assets/sources/tweets/1.png"
    assert story.canonical_primary_outlet_domain == X_OUTLET_DOMAIN
    assert story.canonical_title == "AI model launch reactions"
    # Trust number = distinct supporting handles.
    assert story.story_outlet_count == 2


@pytest.mark.asyncio
async def test_screenshot_failure_leaves_image_none_for_synthetic_fallback() -> None:
    """Render failure → image None (downstream synthesizes a poster) so the reel ships."""

    async def _failing_renderer(_tweet_url: str) -> str | None:
        return None

    story = await build_theme_reel_story(
        _theme(), cluster_id="c1", sweep_date=date(2026, 7, 5), screenshot_renderer=_failing_renderer
    )

    # The reel still builds; the None image is the signal that triggers the synthetic
    # poster fallback in orchestrator.generate_poster_bytes.
    assert story.canonical_social_image_url is None
    assert story.canonical_primary_outlet_domain == X_OUTLET_DOMAIN


@pytest.mark.asyncio
async def test_story_id_is_deterministic_per_cluster_day_theme() -> None:
    """The same (cluster, day, theme) yields the same story id (idempotent produce)."""

    async def _renderer(_tweet_url: str) -> str | None:
        return None

    first = await build_theme_reel_story(
        _theme(), cluster_id="c1", sweep_date=date(2026, 7, 5), screenshot_renderer=_renderer
    )
    second = await build_theme_reel_story(
        _theme(), cluster_id="c1", sweep_date=date(2026, 7, 5), screenshot_renderer=_renderer
    )
    assert first.canonical_story_id == second.canonical_story_id


@pytest.mark.asyncio
async def test_theme_with_no_tweets_skips_render_and_still_builds() -> None:
    """A theme with no tweet urls (edge) builds with a None image, no render attempted."""
    theme = ClusterTheme(
        theme_summary="quiet theme", supporting_handles=["a", "b"], supporting_tweet_urls=[]
    )
    rendered = False

    async def _renderer(_tweet_url: str) -> str | None:
        nonlocal rendered
        rendered = True
        return "x.png"

    story = await build_theme_reel_story(
        theme, cluster_id="c1", sweep_date=date(2026, 7, 5), screenshot_renderer=_renderer
    )
    assert rendered is False
    assert story.canonical_social_image_url is None
