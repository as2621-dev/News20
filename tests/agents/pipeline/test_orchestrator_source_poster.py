"""Tests for the orchestrator's source-origin poster pass-through (Phase 5d SP4).

WHY (Rule 9): SP3 built the poster-SKIP branch inside `build_poster_for_digest`
(supplied image → grade it, never generate), but the skip only FIRES end-to-end if
the orchestrator actually hands the supplied image down for a source-origin story.
These tests pin that wiring:

  - A source-origin story (youtube.com / x.com outlet domain) MUST pass
    `supplied_poster_image_url = story.canonical_social_image_url` to the builder
    (so a followed channel's reel uses its thumbnail, not a synthetic poster).
  - A source-origin story produces a poster EVEN when no genai client is injected
    (generation is skipped, so the client is irrelevant) — without this the source
    reel would silently lose its image on a client-less run.
  - A NEWS story (e.g. bbc.com) MUST NOT pass the kwarg — the news generation path
    is byte-for-byte unchanged.

The builder is a stub recording its call (no genai, no SERP, no Nano Banana, no
network, no disk reads beyond the tmp poster file).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from agents.ingestion.models import CanonicalStory
from agents.pipeline import orchestrator as orch
from agents.pipeline.models import DialogueTurn, DigestScript

_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
_THUMB_URL = "https://i.ytimg.com/vi/abc123/maxresdefault.jpg"


def _script() -> DigestScript:
    """A minimal two-turn grounded script (the poster summary seed)."""
    return DigestScript(
        digest_story_id="src-yt-001",
        turns=[
            DialogueTurn(speaker="ALEX", text="What did the channel cover today?"),
            DialogueTurn(speaker="JORDAN", text="A deep dive on the new GPU launch."),
        ],
        word_count=12,
        estimated_duration_seconds=6,
    )


def _source_story(image_url: str | None = _THUMB_URL) -> CanonicalStory:
    """A YouTube source-origin canonical story (outlet domain youtube.com)."""
    return CanonicalStory(
        canonical_story_id="src-yt-001",
        canonical_title="Channel deep dive on the new GPU",
        canonical_url="https://www.youtube.com/watch?v=abc123",
        canonical_normalized_url="https://www.youtube.com/watch?v=abc123",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="youtube.com",
        canonical_primary_outlet_name="Some Channel",
        canonical_body_text="A long-form transcript about the new GPU launch.",
        canonical_social_image_url=image_url,
        covering_outlets=["youtube.com"],
        story_outlet_count=1,
    )


def _news_story() -> CanonicalStory:
    """A plain news canonical story (outlet domain bbc.com — NOT source-origin)."""
    return CanonicalStory(
        canonical_story_id="news-001",
        canonical_title="Arsenal beat Liverpool 2-1",
        canonical_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_normalized_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        canonical_primary_outlet_name="BBC",
        canonical_body_text="Arsenal beat Liverpool 2-1 at the Emirates.",
        canonical_social_image_url="https://bbc.com/some-image.jpg",
        covering_outlets=["bbc.com", "reuters.com"],
        story_outlet_count=2,
    )


def _recording_builder(poster_path: str):
    """A builder stub that records its kwargs and returns a report with poster_path."""
    calls: list[dict] = []

    def builder(digest, client, **kwargs):  # noqa: ARG001
        calls.append({"client": client, **kwargs})
        report = MagicMock()
        report.poster_path = poster_path
        return report

    return builder, calls


class TestSourceOriginPosterPassThrough:
    def test_source_story_passes_supplied_image_url(self, tmp_path) -> None:
        """A youtube.com story hands its thumbnail down as supplied_poster_image_url."""
        poster_file = tmp_path / "poster.webp"
        poster_file.write_bytes(b"RIFF-FAKE-WEBP")
        builder, calls = _recording_builder(str(poster_file))

        result = orch.generate_poster_bytes(
            story=_source_story(),
            script=_script(),
            poster_genai_client=MagicMock(),
            poster_builder=builder,
        )

        assert result == b"RIFF-FAKE-WEBP"
        assert len(calls) == 1
        assert calls[0]["supplied_poster_image_url"] == _THUMB_URL

    def test_source_story_produces_poster_without_genai_client(self, tmp_path) -> None:
        """No genai client still yields a poster for a source story (generation skipped)."""
        poster_file = tmp_path / "poster.webp"
        poster_file.write_bytes(b"RIFF-FAKE-WEBP")
        builder, calls = _recording_builder(str(poster_file))

        result = orch.generate_poster_bytes(
            story=_source_story(),
            script=_script(),
            poster_genai_client=None,  # source path does not need the client
            poster_builder=builder,
        )

        assert result == b"RIFF-FAKE-WEBP"
        assert len(calls) == 1
        assert calls[0]["client"] is None
        assert calls[0]["supplied_poster_image_url"] == _THUMB_URL

    def test_news_story_does_not_pass_supplied_image(self, tmp_path) -> None:
        """A bbc.com news story uses the generation path — no supplied_poster_image_url."""
        poster_file = tmp_path / "poster.webp"
        poster_file.write_bytes(b"RIFF-FAKE-WEBP")
        builder, calls = _recording_builder(str(poster_file))

        result = orch.generate_poster_bytes(
            story=_news_story(),
            script=_script(),
            poster_genai_client=MagicMock(),
            poster_builder=builder,
        )

        assert result == b"RIFF-FAKE-WEBP"
        assert len(calls) == 1
        assert "supplied_poster_image_url" not in calls[0]

    def test_news_story_without_client_skips_poster(self) -> None:
        """A news story with no genai client returns None (unchanged behaviour)."""
        builder, calls = _recording_builder("unused")

        result = orch.generate_poster_bytes(
            story=_news_story(),
            script=_script(),
            poster_genai_client=None,
            poster_builder=builder,
        )

        assert result is None
        assert calls == []
