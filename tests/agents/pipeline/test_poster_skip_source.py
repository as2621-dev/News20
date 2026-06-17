"""Tests for the source-origin poster-skip branch (Phase 5d SP3).

WHY these matter (Rule 9): the phase locks that a followed-source reel uses the
REAL thumbnail / tweet screenshot, not a synthetic Nano Banana poster. These tests
prove that when a supplied image is passed, the SERP→score→generate pipeline is
SKIPPED (the genai client is never touched) and the supplied image is graded into
the poster — and that a missing image fails loud (no silent wrong-poster).

The genai client is a MagicMock that fails the test if any generation method is
called; image fetch is mocked. No network, no Gemini call.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from agents.m0 import build_poster_from_news
from agents.m0.build_poster_from_news import build_poster_for_digest
from agents.m0.digests_input import Digest
from agents.voice.models import DialogueTurn


def _png_bytes(width: int = 400, height: int = 400) -> bytes:
    """A real decodable PNG (grade_and_brand opens it via PIL)."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 80, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _source_digest(digest_id: str) -> Digest:
    return Digest(
        digest_id=digest_id,
        digest_headline="A followed channel's new upload",
        digest_category="News",
        digest_source="Some Channel",
        digest_source_url="https://www.youtube.com/watch?v=vid1",
        turns=[DialogueTurn(speaker="ALEX", text="Body of the digest.")],
    )


class _ExplodingGenaiClient:
    """A genai client whose every attribute access fails the test.

    The poster-skip path must NOT touch the genai client at all (no SERP, no
    concept extraction, no generation), so any use signals a regression.
    """

    def __getattr__(self, name: str):  # noqa: ANN204
        raise AssertionError(
            f"genai client accessed ('{name}') on the source-origin poster-skip path "
            "— generation must be skipped entirely"
        )


class TestPosterSkipSource:
    """build_poster_for_digest with a supplied source image."""

    def test_supplied_image_used_and_generation_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A supplied thumbnail is graded into the poster; genai is never called."""
        monkeypatch.setattr(build_poster_from_news, "ASSETS_M0_DIR", tmp_path)
        # The thumbnail URL is fetched (mock returns real PNG bytes).
        monkeypatch.setattr(build_poster_from_news, "_fetch", lambda url: _png_bytes())

        report = build_poster_for_digest(
            _source_digest("d-src-1"),
            _ExplodingGenaiClient(),  # any use fails the test
            supplied_poster_image_url="https://i.ytimg.com/vid1.jpg",
        )

        assert report.poster_path is not None
        poster_file = Path(report.poster_path)
        assert poster_file.is_file()
        assert poster_file.read_bytes()  # non-empty graded webp

    def test_supplied_local_screenshot_path_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An X screenshot saved to a local path is read directly (no HTTP fetch)."""
        monkeypatch.setattr(build_poster_from_news, "ASSETS_M0_DIR", tmp_path)

        def _fetch_must_not_run(url: str):  # noqa: ANN202
            raise AssertionError("local path must be read directly, not fetched")

        monkeypatch.setattr(build_poster_from_news, "_fetch", _fetch_must_not_run)

        screenshot = tmp_path / "tweet.png"
        screenshot.write_bytes(_png_bytes())

        report = build_poster_for_digest(
            _source_digest("d-src-2"),
            _ExplodingGenaiClient(),
            supplied_poster_image_url=str(screenshot),
        )

        assert report.poster_path is not None
        assert Path(report.poster_path).is_file()

    def test_missing_image_fails_loud_no_poster(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreachable supplied image → no poster + a note (no silent wrong poster)."""
        monkeypatch.setattr(build_poster_from_news, "ASSETS_M0_DIR", tmp_path)
        monkeypatch.setattr(build_poster_from_news, "_fetch", lambda url: None)

        report = build_poster_for_digest(
            _source_digest("d-src-3"),
            _ExplodingGenaiClient(),
            supplied_poster_image_url="https://i.ytimg.com/gone.jpg",
        )

        assert report.poster_path is None
        assert "could not be read" in report.notes
