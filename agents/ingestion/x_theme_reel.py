"""Build an X theme reel's story object from an extracted cluster theme (slice #24).

Turns one attribution-verified :class:`~agents.ingestion.cluster_sweep.ClusterTheme`
(from the shared once-daily sweep, ``x_cluster_sweeps`` / slice #23) into a
:class:`~agents.ingestion.models.CanonicalStory` that the produce pipeline
(``orchestrate_story``) turns into a real reel, then the X theme ladder
(:mod:`agents.pipeline.x_theme_ladder`) places into a user's ``x`` slot.

v1 reel image = **screenshot of the top tweet** (PRD story #32) via the existing
:func:`agents.ingestion.tweet_screenshot.render_tweet_screenshot`. On render failure
the screenshot url is left ``None``, which the existing poster path
(``orchestrator.generate_poster_bytes``) already reads as "no supplied image" and
falls through to the synthetic poster (slice #24 acceptance: render failure →
synthetic-poster fallback, reel still ships). The domain is stamped ``x.com`` so the
downstream source-slot/theme machinery recognizes it as an X reel.

The screenshot renderer is an INJECTABLE seam so the builder is unit-testable without
Playwright/network (mocked in tests) — the same seam pattern the sweeper uses.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timezone
from typing import Awaitable, Callable

from agents.ingestion.cluster_sweep import ClusterTheme
from agents.ingestion.models import CanonicalStory
from agents.ingestion.tweet_screenshot import render_tweet_screenshot
from agents.shared.logger import get_logger

logger = get_logger(__name__)

# The X reel outlet domain — the marker the feed machinery uses to recognize an X reel.
X_OUTLET_DOMAIN = "x.com"
_THEME_BODY_MAX_CHARS = 1200

# An async top-tweet screenshot renderer: tweet_url -> local png path or None on failure.
# Injected so the Playwright render is fully mockable (no live render in tests).
TweetScreenshotRenderer = Callable[[str], Awaitable[str | None]]


def _theme_story_id(cluster_id: str, sweep_date: date, theme_summary: str) -> str:
    """Deterministic, stable story id for a theme reel (idempotent produce).

    Keyed by (cluster, day, theme) so re-running the day reuses the same story id (the
    produce pipeline's cross-day dedup then skips re-paying). The theme summary is
    hashed (not embedded) to keep the id bounded and free of unsafe characters.

    Args:
        cluster_id: The followed cluster the theme came from.
        sweep_date: The sweep day.
        theme_summary: The theme summary (hashed into the id).

    Returns:
        A ``stories.story_id``-safe deterministic id.
    """
    digest = hashlib.sha1(theme_summary.strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"xtheme-{cluster_id}-{sweep_date.isoformat()}-{digest}"


async def build_theme_reel_story(
    theme: ClusterTheme,
    *,
    cluster_id: str,
    sweep_date: date,
    outlet_name: str | None = None,
    screenshot_renderer: TweetScreenshotRenderer | None = None,
) -> CanonicalStory:
    """Build the :class:`CanonicalStory` for one X theme reel (top-tweet screenshot image).

    The top tweet (first supporting url) is screenshotted for the reel image; on render
    failure the image is left ``None`` so the downstream poster path falls back to a
    synthetic poster and the reel still ships (slice #24 acceptance).

    Args:
        theme: The attribution-verified theme (summary + handles + tweet urls).
        cluster_id: The followed cluster the theme came from.
        sweep_date: The sweep day (drives the deterministic story id + published time).
        outlet_name: Optional display name for the source (e.g. the cluster label).
        screenshot_renderer: Optional ``(tweet_url) -> path|None`` seam; the real
            Playwright renderer when ``None``.

    Returns:
        A :class:`CanonicalStory` (domain ``x.com``) ready for ``orchestrate_story``.
    """
    top_tweet_url = theme.supporting_tweet_urls[0] if theme.supporting_tweet_urls else None

    screenshot_url: str | None = None
    if top_tweet_url:
        render = screenshot_renderer or render_tweet_screenshot
        screenshot_url = await render(top_tweet_url)
        if screenshot_url is None:
            # Reason: honest degradation — the downstream poster path reads a None image
            # as "no supplied image" and synthesizes a poster, so the reel still ships.
            logger.warning(
                "x_theme_reel_screenshot_missing",
                cluster_id=cluster_id,
                sweep_date=sweep_date.isoformat(),
                top_tweet_url=top_tweet_url,
                fix_suggestion="Top-tweet screenshot render returned None; the reel falls "
                "back to a synthetic poster (generate_poster_bytes). Verify Playwright is "
                "installed on the produce host if screenshots are expected.",
            )

    published_utc = datetime.combine(sweep_date, time.min, tzinfo=timezone.utc)
    story_id = _theme_story_id(cluster_id, sweep_date, theme.theme_summary)
    body = ("\n\n".join(theme.supporting_tweet_urls))[:_THEME_BODY_MAX_CHARS] or None

    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=theme.theme_summary,
        canonical_url=top_tweet_url or f"https://{X_OUTLET_DOMAIN}",
        canonical_normalized_url=story_id,
        canonical_published_utc=published_utc,
        canonical_primary_outlet_domain=X_OUTLET_DOMAIN,
        canonical_primary_outlet_name=outlet_name,
        canonical_social_image_url=screenshot_url,
        canonical_body_text=body,
        canonical_representative_external_id=story_id,
        covering_outlets=[X_OUTLET_DOMAIN],
        story_outlet_count=len(theme.supporting_handles),
    )
