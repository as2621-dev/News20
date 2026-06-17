"""Unit tests for the tweet-screenshot renderer (Phase 5d SP2).

Playwright is mocked at the boundary (CLAUDE.md): the ``page_renderer`` seam is an
injected async stub that writes a fake PNG (success) or raises (failure). No real
browser, no network. Screenshots are written to a pytest ``tmp_path`` so the repo
assets dir is never touched.

Covers:
    • parse_tweet_id extracts the status id (happy + missing-id edge)
    • render_tweet_screenshot returns the PNG path when the renderer writes the file
    • a bad URL (no status id) returns None without invoking the renderer
    • a renderer that raises (e.g. Playwright/render failure) returns None (no crash)
    • a renderer that succeeds-without-writing returns None (missing output guard)

    >>> pytest tests/agents/ingestion/test_tweet_screenshot.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.ingestion.tweet_screenshot import (
    parse_tweet_id,
    render_tweet_screenshot,
)

_TWEET_URL = "https://x.com/Reuters/status/1799999999999999999"
_TWEET_ID = "1799999999999999999"


def test_parse_tweet_id_extracts_status_id() -> None:
    """A canonical tweet URL yields its numeric status id."""
    assert parse_tweet_id(_TWEET_URL) == _TWEET_ID


def test_parse_tweet_id_returns_none_without_status_segment() -> None:
    """A profile URL (no /status/<id>) yields None rather than a bogus id."""
    assert parse_tweet_id("https://x.com/Reuters") is None


@pytest.mark.asyncio
async def test_render_returns_path_when_renderer_writes_png(tmp_path: Path) -> None:
    """A renderer that writes the PNG makes render_tweet_screenshot return its path.

    WHY: the adapter sets candidate_social_image_url from this return value, so a
    successful render MUST surface the on-disk path, keyed by the tweet id.
    """
    captured: dict[str, object] = {}

    async def fake_renderer(embed_url: str, output_path: Path, timeout_ms: int) -> None:
        captured["embed_url"] = embed_url
        captured["output_path"] = output_path
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")

    result = await render_tweet_screenshot(
        _TWEET_URL, page_renderer=fake_renderer, output_dir=tmp_path
    )

    assert result == str(tmp_path / f"{_TWEET_ID}.png")
    assert (tmp_path / f"{_TWEET_ID}.png").exists()
    # The embed URL must carry the parsed tweet id (the login-wall-free widget).
    assert _TWEET_ID in str(captured["embed_url"])


@pytest.mark.asyncio
async def test_render_bad_url_returns_none_without_calling_renderer(
    tmp_path: Path,
) -> None:
    """A URL with no status id short-circuits to None and never invokes the renderer."""
    renderer_called = False

    async def fake_renderer(embed_url: str, output_path: Path, timeout_ms: int) -> None:
        nonlocal renderer_called
        renderer_called = True

    result = await render_tweet_screenshot(
        "https://x.com/Reuters", page_renderer=fake_renderer, output_dir=tmp_path
    )

    assert result is None
    assert renderer_called is False


@pytest.mark.asyncio
async def test_render_swallows_renderer_failure_returns_none(tmp_path: Path) -> None:
    """A renderer that raises (Playwright/render error) returns None, never crashes.

    WHY: a screenshot failure must not abort the X ingestion batch — the post is
    kept without an image, per the SP2 DoD (clean failure, no crash).
    """

    async def failing_renderer(
        embed_url: str, output_path: Path, timeout_ms: int
    ) -> None:
        raise RuntimeError("chromium launch failed")

    result = await render_tweet_screenshot(
        _TWEET_URL, page_renderer=failing_renderer, output_dir=tmp_path
    )

    assert result is None


@pytest.mark.asyncio
async def test_render_returns_none_when_no_file_written(tmp_path: Path) -> None:
    """A renderer that returns without writing the PNG yields None (missing-output guard)."""

    async def noop_renderer(embed_url: str, output_path: Path, timeout_ms: int) -> None:
        return None

    result = await render_tweet_screenshot(
        _TWEET_URL, page_renderer=noop_renderer, output_dir=tmp_path
    )

    assert result is None
