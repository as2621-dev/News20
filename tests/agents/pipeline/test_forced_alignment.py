"""Unit tests for the transcript-time-slice forced aligner (sub-phase 2).

These tests are OFFLINE — they never call Whisper / OpenAI / any external API
and the unit assertions use a small SYNTHETIC fixture (no dependency on the
real rendered mp3s). Per Rule 9 they encode WHY the caption logic matters and
must fail if the business logic regresses:

  (a) word timings are strictly monotonic and non-overlapping (the renderer
      binary-searches by start_s; backwards/overlapping intervals would show
      two words at once or skip words);
  (b) every timing is contained within [0, speech_end_s] <= audio_duration_s
      (a caption past end-of-audio would flash over silence / black);
  (c) the transcript word count equals the caption word count (dropping or
      duplicating a word breaks sound-off comprehension);
  (d) exactly one #FACC15 highlight is flagged per sentence — never zero, never
      two (the locked format mandates one highlight keyword per sentence).

A final test additionally asserts (a)-(d) hold on the 5 ACTUALLY-EMITTED JSONs
if they are present on disk (skips cleanly if `align_captions` hasn't run yet),
so the real outputs are verified too.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from agents.m0.align_captions import (
    INPUT_AUDIO_DIR,
    OUTPUT_CAPTIONS_DIR,
    build_caption_track,
)
from agents.m0.digests_input import DIGESTS
from agents.pipeline.stages.forced_alignment import (
    CaptionTrack,
    _choose_highlight_index,
    _split_into_sentences,
    align_transcript_to_audio,
)

# A small synthetic two-sentence transcript with a known preferred keyword.
_SYNTHETIC_SENTENCES = [
    "A deal to end the fighting is close.",
    "But close is not done yet.",
]
_SYNTHETIC_DURATION_S = 10.0
_SYNTHETIC_KEYWORDS = ["deal", "done"]


def _assert_track_invariants(track: CaptionTrack, expected_word_count: int) -> None:
    """Assert the 4 DoD invariants on a caption track (reused by every test)."""
    words = track.words

    # (a) strictly monotonic, non-overlapping.
    for index in range(len(words)):
        assert words[index].end_s >= words[index].start_s, (
            f"word {index} end before start: {words[index]}"
        )
        if index > 0:
            assert words[index].start_s >= words[index - 1].end_s - 1e-9, (
                f"word {index} starts before previous word ends "
                f"({words[index].start_s} < {words[index - 1].end_s})"
            )

    # (b) all within [0, speech_end_s] and speech_end_s <= audio_duration_s.
    assert track.speech_end_s <= track.audio_duration_s + 1e-9
    for index, word in enumerate(words):
        assert word.start_s >= 0.0, f"word {index} starts before zero: {word}"
        assert word.end_s <= track.speech_end_s + 1e-9, (
            f"word {index} ends past speech_end_s ({word.end_s} > {track.speech_end_s})"
        )

    # (c) transcript word count == caption word count.
    assert len(words) == expected_word_count, (
        f"expected {expected_word_count} caption words, got {len(words)}"
    )

    # (d) exactly one highlight per sentence (never zero, never two).
    highlights_per_sentence = Counter(
        word.sentence_index for word in words if word.is_highlight
    )
    sentence_indices = {word.sentence_index for word in words}
    for sentence_index in sentence_indices:
        assert highlights_per_sentence[sentence_index] == 1, (
            f"sentence {sentence_index} has {highlights_per_sentence[sentence_index]} "
            f"highlights, expected exactly 1"
        )


def _expected_word_count(sentences: list[str]) -> int:
    """Count whitespace tokens across all sentences (the transcript word count)."""
    return sum(len(sentence.split()) for sentence in sentences)


# ---------------------------------------------------------------------------
# Happy path — synthetic fixture exercises all 4 invariants at once.
# ---------------------------------------------------------------------------


def test_align_synthetic_transcript_satisfies_all_invariants() -> None:
    """Happy path: a known 2-sentence transcript yields a valid caption track."""
    track = align_transcript_to_audio(
        digest_id="digest-test",
        sentences=_SYNTHETIC_SENTENCES,
        audio_duration_s=_SYNTHETIC_DURATION_S,
        preferred_keywords=_SYNTHETIC_KEYWORDS,
    )
    _assert_track_invariants(track, _expected_word_count(_SYNTHETIC_SENTENCES))


def test_align_prefers_pooled_keyword_for_highlight() -> None:
    """The pooled keyword 'deal'/'done' must be the highlighted word per sentence."""
    track = align_transcript_to_audio(
        digest_id="digest-test",
        sentences=_SYNTHETIC_SENTENCES,
        audio_duration_s=_SYNTHETIC_DURATION_S,
        preferred_keywords=_SYNTHETIC_KEYWORDS,
    )
    highlights = {
        word.sentence_index: word.word for word in track.words if word.is_highlight
    }
    # Sentence 0 contains "deal", sentence 1 contains "done." (punctuation kept).
    assert highlights[0] == "deal"
    assert highlights[1].rstrip(".") == "done"


def test_last_word_ends_exactly_at_speech_end() -> None:
    """Edge: the final caption word ends exactly on speech_end_s, never past it."""
    speech_end = 8.5
    track = align_transcript_to_audio(
        digest_id="digest-test",
        sentences=_SYNTHETIC_SENTENCES,
        audio_duration_s=_SYNTHETIC_DURATION_S,
        speech_end_s=speech_end,
    )
    assert track.words[-1].end_s == pytest.approx(speech_end, abs=1e-6)
    assert track.speech_end_s == pytest.approx(speech_end, abs=1e-6)


def test_trailing_silence_keeps_captions_off_silence() -> None:
    """Edge: a shorter speech_end_s than duration leaves a caption-free tail."""
    track = align_transcript_to_audio(
        digest_id="digest-test",
        sentences=_SYNTHETIC_SENTENCES,
        audio_duration_s=_SYNTHETIC_DURATION_S,
        speech_end_s=_SYNTHETIC_DURATION_S - 2.0,
    )
    # No word may sit in the trailing 2s of silence.
    assert all(word.end_s <= _SYNTHETIC_DURATION_S - 2.0 + 1e-9 for word in track.words)


# ---------------------------------------------------------------------------
# Failure cases — wrong/empty input must raise, not silently produce garbage.
# ---------------------------------------------------------------------------


def test_empty_sentences_raises() -> None:
    """Failure: no sentences to align must raise ValueError (fail loud, Rule 12)."""
    with pytest.raises(ValueError, match="No sentences to align"):
        align_transcript_to_audio(
            digest_id="digest-test",
            sentences=[],
            audio_duration_s=_SYNTHETIC_DURATION_S,
        )


def test_non_positive_duration_raises() -> None:
    """Failure: a non-positive audio duration must raise ValueError."""
    with pytest.raises(ValueError, match="audio_duration_s must be positive"):
        align_transcript_to_audio(
            digest_id="digest-test",
            sentences=_SYNTHETIC_SENTENCES,
            audio_duration_s=0.0,
        )


def test_speech_end_past_audio_end_raises() -> None:
    """Failure: speech_end_s beyond audio_duration_s must raise (no captions past EOA)."""
    with pytest.raises(ValueError, match="speech_end_s"):
        align_transcript_to_audio(
            digest_id="digest-test",
            sentences=_SYNTHETIC_SENTENCES,
            audio_duration_s=5.0,
            speech_end_s=6.0,
        )


# ---------------------------------------------------------------------------
# Highlight selection + sentence splitting units.
# ---------------------------------------------------------------------------


def test_highlight_falls_back_to_longest_content_word() -> None:
    """Edge: with no pooled-keyword hit, the longest non-stopword token wins."""
    tokens = ["The", "superconductor", "is", "the", "key."]
    chosen = _choose_highlight_index(tokens, preferred_keywords=["unrelated"])
    assert tokens[chosen] == "superconductor"


def test_highlight_substring_matches_compound_word() -> None:
    """Keyword 'data' must match the spoken compound 'data-center'."""
    tokens = ["The", "data-center", "business", "doubled."]
    chosen = _choose_highlight_index(tokens, preferred_keywords=["data"])
    assert tokens[chosen] == "data-center"


def test_sentence_splitter_keeps_us_abbreviation_intact() -> None:
    """'U.S.' must not be shattered into separate sentences by the splitter."""
    sentences = _split_into_sentences(
        "The U.S. military hit a target. A deal is close."
    )
    assert len(sentences) == 2
    assert "U.S. military" in sentences[0]


# ---------------------------------------------------------------------------
# Real-data builds — exercise every digest through the actual aligner using
# the on-disk mp3 durations (ffprobe), then verify the emitted JSONs too.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("digest", DIGESTS, ids=[d.digest_id for d in DIGESTS])
def test_real_digest_caption_track_satisfies_invariants(digest) -> None:
    """Each real digest aligned against its true mp3 duration must satisfy DoD."""
    # Reason: build_caption_track ffprobes the real mp3; if it is missing the
    # render hasn't run — skip rather than fail (SP1 owns the audio).
    if not (INPUT_AUDIO_DIR / f"{digest.digest_id}.mp3").exists():
        pytest.skip(f"audio for {digest.digest_id} not rendered yet")

    track = build_caption_track(digest)
    transcript_paragraph = " ".join(turn.text for turn in digest.turns)
    sentences = _split_into_sentences(transcript_paragraph)
    expected = _expected_word_count(sentences)
    _assert_track_invariants(track, expected)


def test_emitted_json_files_validate_and_satisfy_invariants() -> None:
    """The 5 written JSONs (if present) must parse, validate, and satisfy DoD."""
    json_paths = sorted(OUTPUT_CAPTIONS_DIR.glob("digest-*.captions.json"))
    if not json_paths:
        pytest.skip(
            "no caption JSONs emitted yet; run `python -m agents.m0.align_captions`"
        )

    for json_path in json_paths:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        track = CaptionTrack.model_validate(raw)
        _assert_track_invariants(track, len(track.words))
        # Cross-check the word count equals the transcript word count for this id.
        digest = next(d for d in DIGESTS if d.digest_id == track.digest_id)
        transcript_paragraph = " ".join(turn.text for turn in digest.turns)
        sentences = _split_into_sentences(transcript_paragraph)
        assert len(track.words) == _expected_word_count(sentences)
