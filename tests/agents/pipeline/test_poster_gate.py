"""Tests for the DISABLE_POSTER_GEN poster kill switch (issue #32).

WHY (Rule 9): the kill switch exists so a single env var makes image-model spend
IMPOSSIBLE on every production entry point (worker route, live batch, source
reels). These tests pin the promises that matter:

  - Flag on → the orchestrator choke point (``generate_poster_bytes``) forces the
    poster client to None: a NEWS story gets no poster, the builder is never
    called, and the injected client is never touched (zero image-model calls).
  - Flag on → a SOURCE-origin story still gets its supplied-image poster (the
    free download+grade path never needed the client) — the kill switch must not
    strip source reels of their thumbnails.
  - Flag unset/"0" → behavior byte-identical to today: the injected client
    reaches the builder unchanged.
  - The disabled state logs a structured ``poster_generation_disabled`` line
    with a ``fix_suggestion`` ONCE per process, not once per story.

The builder is a stub (no genai, no SERP, no network); the flag is set via
monkeypatch so no test leaks env state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from agents.ingestion.models import CanonicalStory
from agents.pipeline import orchestrator as orch
from agents.pipeline import poster_gate
from agents.pipeline.models import DialogueTurn, DigestScript

_NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
_THUMB_URL = "https://i.ytimg.com/vi/kill123/maxresdefault.jpg"


@pytest.fixture(autouse=True)
def _reset_once_flag(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with the once-per-process log flag cleared."""
    monkeypatch.setattr(poster_gate, "_poster_disabled_logged", False)


def _script(story_id: str) -> DigestScript:
    return DigestScript(
        digest_story_id=story_id,
        turns=[
            DialogueTurn(speaker="ALEX", text="What happened today?"),
            DialogueTurn(speaker="JORDAN", text="A big launch, apparently."),
        ],
        word_count=9,
        estimated_duration_seconds=5,
    )


def _news_story() -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id="kill-news-001",
        canonical_title="Arsenal beat Liverpool 2-1",
        canonical_url="https://bbc.com/sport/arsenal",
        canonical_normalized_url="https://bbc.com/sport/arsenal",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        canonical_primary_outlet_name="BBC",
        canonical_body_text="Arsenal beat Liverpool 2-1 at the Emirates.",
        covering_outlets=["bbc.com"],
        story_outlet_count=3,
    )


def _source_story() -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id="kill-src-001",
        canonical_title="Channel deep dive on the new GPU",
        canonical_url="https://www.youtube.com/watch?v=kill123",
        canonical_normalized_url="https://www.youtube.com/watch?v=kill123",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="youtube.com",
        canonical_primary_outlet_name="Some Channel",
        canonical_body_text="A long transcript about the new GPU launch.",
        canonical_social_image_url=_THUMB_URL,
        covering_outlets=["youtube.com"],
        story_outlet_count=1,
    )


class _RecordingBuilder:
    """A poster-builder stub that records its call and returns a real tmp file."""

    def __init__(self, tmp_path: Path) -> None:
        self.calls: list[dict] = []
        poster_file = tmp_path / "poster.webp"
        poster_file.write_bytes(b"webp-bytes")
        self._poster_path = str(poster_file)

    def __call__(self, digest, client, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"client": client, **kwargs})
        report = MagicMock()
        report.poster_path = self._poster_path
        return report


class TestFlagParsing:
    """The env contract: half-set truthy spellings count as ON; unset/0 is OFF."""

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", " 1 "])
    def test_truthy_values_disable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("DISABLE_POSTER_GEN", value)
        assert poster_gate.poster_generation_disabled() is True

    @pytest.mark.parametrize("value", ["0", "", "false", "no", "off"])
    def test_falsy_values_keep_posters_on(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("DISABLE_POSTER_GEN", value)
        assert poster_gate.poster_generation_disabled() is False

    def test_unset_keeps_posters_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISABLE_POSTER_GEN", raising=False)
        assert poster_gate.poster_generation_disabled() is False

    def test_disabled_logs_once_with_fix_suggestion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One structured warning per process (not per story), with a fix_suggestion."""
        monkeypatch.setenv("DISABLE_POSTER_GEN", "1")
        with capture_logs() as logs:
            poster_gate.poster_generation_disabled()
            poster_gate.poster_generation_disabled()
            poster_gate.poster_generation_disabled()
        disabled_logs = [
            entry for entry in logs if entry["event"] == "poster_generation_disabled"
        ]
        assert len(disabled_logs) == 1
        assert "DISABLE_POSTER_GEN" in disabled_logs[0]["fix_suggestion"]


class TestChokePointKillSwitch:
    """Flag on → generate_poster_bytes never touches the client or the builder."""

    def test_news_story_gets_no_poster_and_client_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DISABLE_POSTER_GEN", "1")
        client = MagicMock()
        builder = _RecordingBuilder(tmp_path)
        story = _news_story()

        result = orch.generate_poster_bytes(
            story=story,
            script=_script(story.canonical_story_id),
            poster_genai_client=client,
            poster_builder=builder,
        )

        assert result is None
        assert builder.calls == []  # no generation attempted
        assert client.method_calls == []  # zero image-model calls
        assert client.mock_calls == []

    def test_source_story_keeps_supplied_image_poster(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Source reels keep their free thumbnail poster with the flag on."""
        monkeypatch.setenv("DISABLE_POSTER_GEN", "1")
        client = MagicMock()
        builder = _RecordingBuilder(tmp_path)
        story = _source_story()

        result = orch.generate_poster_bytes(
            story=story,
            script=_script(story.canonical_story_id),
            poster_genai_client=client,
            poster_builder=builder,
        )

        assert result == b"webp-bytes"
        assert len(builder.calls) == 1
        # The kill switch forced the client to None — the supplied-image path
        # never needed it, so the poster still lands.
        assert builder.calls[0]["client"] is None
        assert builder.calls[0]["supplied_poster_image_url"] == _THUMB_URL
        assert client.mock_calls == []

    def test_flag_off_behavior_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Flag unset → the injected client reaches the builder exactly as today."""
        monkeypatch.delenv("DISABLE_POSTER_GEN", raising=False)
        client = MagicMock()
        builder = _RecordingBuilder(tmp_path)
        story = _news_story()

        result = orch.generate_poster_bytes(
            story=story,
            script=_script(story.canonical_story_id),
            poster_genai_client=client,
            poster_builder=builder,
        )

        assert result == b"webp-bytes"
        assert len(builder.calls) == 1
        assert builder.calls[0]["client"] is client
