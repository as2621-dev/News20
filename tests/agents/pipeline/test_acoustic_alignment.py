"""Tests for acoustic forced alignment (pure helpers + fallback wiring).

The Wav2Vec2 model itself is NEVER loaded here (no network, no torch forward
pass) — per the testing rules, the model boundary is mocked. What we verify:
the text normalizer (display token → spoken CTC tokens), the span→interval
post-processing invariants the renderer relies on, and that the orchestrator
falls back to heuristic slicing when acoustic alignment is unavailable.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.pipeline.stages import acoustic_alignment as acoustic
from agents.pipeline.stages.forced_alignment import TurnAlignmentWindow


class TestSpokenWordsForToken:
    """Display tokens must normalize to the aligner's A-Z' label set."""

    def test_plain_word_uppercases_and_strips_punctuation(self) -> None:
        assert acoustic._spoken_words_for_token("Breaking,") == ["BREAKING"]

    def test_currency_decimal_expands_to_spoken_words(self) -> None:
        """"$3.2" must align as the words Gemini TTS actually speaks."""
        assert acoustic._spoken_words_for_token("$3.2") == [
            "THREE",
            "POINT",
            "TWO",
            "DOLLARS",
        ]

    def test_year_expands_as_two_digit_groups(self) -> None:
        """Years are spoken "twenty twenty six", not "two thousand and...".."""
        assert acoustic._spoken_words_for_token("2026") == ["TWENTY", "TWENTY", "SIX"]

    def test_ordinal_expands(self) -> None:
        assert acoustic._spoken_words_for_token("3rd") == ["THIRD"]

    def test_punctuation_only_token_returns_empty(self) -> None:
        """An em-dash has no speakable content — must yield zero CTC targets."""
        assert acoustic._spoken_words_for_token("—") == []

    def test_apostrophe_is_preserved(self) -> None:
        """The label set includes ' so contractions align as one token."""
        assert acoustic._spoken_words_for_token("isn't") == ["ISN'T"]


class TestIntervalsFromWordSpans:
    """The renderer needs contiguous, monotonic, window-clamped intervals."""

    def test_ends_extend_to_next_acoustic_onset(self) -> None:
        """A pause between words must keep the previous word highlighted.

        Why: with raw CTC spans there is a dead gap during pauses where no
        word is lit; extending each end to the next onset preserves the
        contiguous-track behavior the client renderer was built against.
        """
        intervals = acoustic._intervals_from_word_spans(
            [(0.0, 0.3), (0.8, 1.2)], window_start_s=10.0, window_end_s=12.0
        )
        assert intervals == [(10.0, 10.8), (10.8, 11.2)]

    def test_unspeakable_token_gets_min_slot_after_previous(self) -> None:
        """A None span (em-dash) must not break monotonicity."""
        intervals = acoustic._intervals_from_word_spans(
            [(0.0, 0.5), None, (1.0, 1.4)], window_start_s=0.0, window_end_s=2.0
        )
        starts = [interval[0] for interval in intervals]
        ends = [interval[1] for interval in intervals]
        assert starts == sorted(starts)
        for index in range(1, len(intervals)):
            assert intervals[index][0] >= intervals[index - 1][1] - 1e-9
        assert all(end <= 2.0 for end in ends)

    def test_intervals_clamped_to_window_end(self) -> None:
        """A span past the window boundary must clamp, never overflow."""
        intervals = acoustic._intervals_from_word_spans(
            [(0.0, 5.0)], window_start_s=0.0, window_end_s=2.0
        )
        assert intervals == [(0.0, 2.0)]


class TestAcousticAlignFallback:
    """Any acoustic failure must return None so callers use the heuristic."""

    def test_returns_none_when_aligner_unavailable(self) -> None:
        with patch.object(acoustic, "_load_aligner", return_value=None):
            track = acoustic.acoustically_align_turn_windows(
                digest_id="s1",
                audio_bytes=b"not-audio",
                turn_windows=[
                    TurnAlignmentWindow(text="A deal is close.", start_s=0.0, end_s=4.0)
                ],
                audio_duration_s=10.0,
            )
        assert track is None

    def test_returns_none_when_decode_fails(self) -> None:
        """Garbage audio bytes must degrade to the heuristic, not raise."""
        fake_aligner = (object(), (), {"|": 1})
        with patch.object(acoustic, "_load_aligner", return_value=fake_aligner):
            track = acoustic.acoustically_align_turn_windows(
                digest_id="s1",
                audio_bytes=b"garbage-bytes",
                turn_windows=[
                    TurnAlignmentWindow(text="A deal is close.", start_s=0.0, end_s=4.0)
                ],
                audio_duration_s=10.0,
            )
        assert track is None


class TestBuildCaptionTrackAcousticWiring:
    """build_caption_track prefers acoustic timing and falls back loudly."""

    @staticmethod
    def _script():  # noqa: ANN205 - test helper
        from agents.pipeline.models import DialogueTurn, DigestScript

        return DigestScript(
            digest_story_id="s1",
            turns=[
                DialogueTurn(speaker="ALEX", text="Arsenal won at the Emirates."),
                DialogueTurn(speaker="JORDAN", text="Saka scored both goals."),
            ],
            word_count=8,
            estimated_duration_seconds=4,
        )

    def test_acoustic_track_is_returned_when_alignment_succeeds(self) -> None:
        import agents.pipeline.orchestrator as orch
        from agents.pipeline.stages.forced_alignment import CaptionTrack, CaptionWord

        sentinel = CaptionTrack(
            digest_id="s1",
            audio_duration_s=10.0,
            speech_end_s=9.0,
            sentence_count=1,
            words=[
                CaptionWord(
                    word="Arsenal",
                    start_s=0.1,
                    end_s=0.6,
                    sentence_index=0,
                    is_highlight=True,
                )
            ],
        )
        with patch.object(
            orch, "acoustically_align_turn_windows", return_value=sentinel
        ) as mock_align:
            track = orch.build_caption_track(
                self._script(), audio_duration_ms=10_000, audio_bytes=b"mp3-bytes"
            )
        assert track is sentinel
        mock_align.assert_called_once()

    def test_falls_back_to_heuristic_when_acoustic_returns_none(self) -> None:
        """Acoustic failure must yield the heuristic track — never a crash.

        Why: the Railway worker may lack torch; caption quality may degrade
        but publishing must continue (Rule 12 — fail loud, not fatal).
        """
        import agents.pipeline.orchestrator as orch

        with patch.object(
            orch, "acoustically_align_turn_windows", return_value=None
        ):
            track = orch.build_caption_track(
                self._script(), audio_duration_ms=10_000, audio_bytes=b"mp3-bytes"
            )
        assert track.words
        assert track.words[-1].end_s == pytest.approx(10.0, abs=0.01)

    def test_no_audio_bytes_skips_acoustic_entirely(self) -> None:
        import agents.pipeline.orchestrator as orch

        with patch.object(
            orch, "acoustically_align_turn_windows", return_value=None
        ) as mock_align:
            track = orch.build_caption_track(self._script(), audio_duration_ms=10_000)
        mock_align.assert_not_called()
        assert track.words
