"""Render a tweet as a PNG screenshot via Playwright headless Chromium (Phase 5d SP2).

Source-origin X posts do NOT get a generated Nano Banana poster (locked decision,
plans/phase-5d-source-ingestion.md): their reel image is a **screenshot of the
tweet card**. This module loads X's official publish/embed widget for a tweet URL
in headless Chromium, waits for the rendered ``<twitter-widget>`` card, and
screenshots just that element to a PNG saved under the shared assets dir.

Why the embed widget (not the raw tweet page): the embed HTML
(``publish.twitter.com`` syndication / ``platform.twitter.com/embed``) renders the
tweet card without a login wall, so a headless browser can capture it server-side.

The actual Playwright call is behind an **injectable seam** (``page_renderer``) so
tests stub it — no real browser, no network (CLAUDE.md mocking strategy). On any
render failure the function returns ``None`` with a loud structured error log
carrying a ``fix_suggestion`` — it never raises past the adapter, never crashes the
batch.

Assets path convention matches the poster pipeline
(``agents/m0/build_poster_from_news.py``: ``<repo>/assets/<stage>/...``): tweet
screenshots land under ``<repo>/assets/sources/tweets/<tweet_id>.png``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable

from agents.shared.logger import get_logger

logger = get_logger(__name__)

_MODULE_NAME = "tweet_screenshot"

# Assets dir, mirroring agents/m0/build_poster_from_news.py:ASSETS_M0_DIR
# (``Path(__file__).resolve().parents[2] / "assets" / <stage>``). This file lives
# at agents/ingestion/, so parents[2] is the repo root.
ASSETS_TWEETS_DIR: Path = (
    Path(__file__).resolve().parents[2] / "assets" / "sources" / "tweets"
)

# X's official oEmbed/publish widget host renders the tweet card without a login
# wall. The widget needs a referenceable URL; the publish endpoint serves the
# embeddable card HTML for a tweet URL.
_PUBLISH_EMBED_TEMPLATE = "https://platform.twitter.com/embed/Tweet.html?id={tweet_id}"

_TWEET_ID_PATTERN = re.compile(r"/status/(\d+)")
_DEFAULT_RENDER_TIMEOUT_MS = 30_000

# An async renderer that, given the embed URL + output PNG path, drives a headless
# browser to screenshot the tweet card to that path. Injected so the Playwright
# dependency is fully mockable and only imported at real render time.
TweetPageRenderer = Callable[[str, Path, int], Awaitable[None]]


def parse_tweet_id(tweet_url: str) -> str | None:
    """Extract the numeric status id from a canonical tweet URL, or None.

    Args:
        tweet_url: A tweet URL such as ``https://x.com/Reuters/status/1799999999999999999``.

    Returns:
        The status id digits (e.g. ``"1799999999999999999"``), or None when the
        URL carries no ``/status/<digits>`` segment.

    Example:
        >>> parse_tweet_id("https://x.com/Reuters/status/1799999999999999999")
        '1799999999999999999'
        >>> parse_tweet_id("https://x.com/Reuters") is None
        True
    """
    match = _TWEET_ID_PATTERN.search(tweet_url)
    return match.group(1) if match else None


async def render_tweet_screenshot(
    tweet_url: str,
    page_renderer: TweetPageRenderer | None = None,
    output_dir: Path | None = None,
    timeout_ms: int = _DEFAULT_RENDER_TIMEOUT_MS,
) -> str | None:
    """Render a tweet's card to a PNG and return its path, or None on failure.

    Loads X's publish/embed widget for the tweet in headless Chromium (via the
    injected ``page_renderer``, defaulting to the real Playwright seam) and
    screenshots the rendered card to ``<output_dir>/<tweet_id>.png``. Any failure
    (bad URL, no Playwright, render error) is caught, logged loud with a
    ``fix_suggestion``, and surfaced as ``None`` — never raised, never a crash.

    Args:
        tweet_url: Canonical ``x.com/<handle>/status/<id>`` URL to capture.
        page_renderer: Optional async ``(embed_url, output_path, timeout_ms) -> None``
            renderer. Injected by tests to stub Playwright; defaults to the real
            headless-Chromium renderer.
        output_dir: Optional override for where the PNG is written; defaults to
            ``ASSETS_TWEETS_DIR``.
        timeout_ms: Per-render navigation/selector timeout in milliseconds.

    Returns:
        The string filesystem path of the written PNG on success, else None.

    Example:
        >>> # path = await render_tweet_screenshot(
        ... #     "https://x.com/Reuters/status/1799999999999999999")
    """
    tweet_id = parse_tweet_id(tweet_url)
    if tweet_id is None:
        logger.warning(
            "tweet_screenshot_bad_url",
            module=_MODULE_NAME,
            tweet_url=tweet_url[:120],
            fix_suggestion="Pass a canonical x.com/<handle>/status/<id> URL; "
            "screenshot skipped (the post keeps no social image)",
        )
        return None

    renderer = page_renderer or _default_playwright_renderer
    target_dir = output_dir or ASSETS_TWEETS_DIR
    output_path = target_dir / f"{tweet_id}.png"
    embed_url = _PUBLISH_EMBED_TEMPLATE.format(tweet_id=tweet_id)

    logger.info(
        "tweet_screenshot_started",
        module=_MODULE_NAME,
        tweet_id=tweet_id,
    )
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        await renderer(embed_url, output_path, timeout_ms)
    except Exception as exc:  # noqa: BLE001 — boundary: a render failure must not crash the batch
        logger.error(
            "tweet_screenshot_failed",
            module=_MODULE_NAME,
            tweet_id=tweet_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            fix_suggestion="Tweet screenshot render failed; the post is kept without "
            "an image. Confirm the worker has Playwright + chromium installed "
            "(`playwright install chromium`) and outbound network to platform.twitter.com",
        )
        return None

    if not output_path.exists():
        logger.error(
            "tweet_screenshot_missing_output",
            module=_MODULE_NAME,
            tweet_id=tweet_id,
            fix_suggestion="Renderer returned without writing the PNG; verify the "
            "embed widget rendered and the screenshot selector matched",
        )
        return None

    logger.info(
        "tweet_screenshot_completed",
        module=_MODULE_NAME,
        tweet_id=tweet_id,
        output_path=str(output_path),
    )
    return str(output_path)


async def _default_playwright_renderer(
    embed_url: str,
    output_path: Path,
    timeout_ms: int,
) -> None:
    """Real Playwright seam: load the embed widget in headless Chromium → PNG.

    Imported lazily so this module loads (and tests run) without Playwright
    installed, and so the heavy browser dependency is only required at real render
    time on the worker. Navigates to the embed URL, waits for X's rendered
    ``<twitter-widget>`` card, and screenshots just that element.

    Args:
        embed_url: The ``platform.twitter.com/embed/Tweet.html?id=...`` URL.
        output_path: Destination PNG path (its parent dir already exists).
        timeout_ms: Navigation + selector wait timeout in milliseconds.

    Raises:
        ImportError: When Playwright is not installed (caught by the caller and
            surfaced as a clean None + fix_suggestion).
        Exception: Any Playwright navigation / render error (caught by the caller).
    """
    from playwright.async_api import async_playwright  # noqa: PLC0415 — lazy heavy dep

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 600, "height": 900},
                device_scale_factor=2,
            )
            page.set_default_timeout(timeout_ms)
            await page.goto(embed_url, wait_until="networkidle")
            # Reason: X renders the tweet inside a <twitter-widget> custom element;
            # screenshot that element so the PNG is just the card, not the page chrome.
            widget = page.locator("twitter-widget")
            await widget.wait_for(state="visible", timeout=timeout_ms)
            # Brief settle for fonts/images inside the iframe to paint.
            await asyncio.sleep(0.5)
            await widget.screenshot(path=str(output_path))
        finally:
            await browser.close()
