"""Acoustic forced alignment of a known script via torchaudio Wav2Vec2 CTC.

WHY THIS MODULE EXISTS
----------------------
``forced_alignment.py`` time-slices words proportionally by character count
inside each measured turn window. That heuristic drifts within a turn because
real speech has pauses, stress, and variable rate — the audible caption lag.
This module replaces the *timing* step with true acoustic forced alignment:
the known transcript is aligned against the actual audio using torchaudio's
``forced_align`` (CTC Viterbi over a Wav2Vec2 emission matrix) — the same
engine WhisperX uses — giving <100ms word boundaries. Fully offline, free,
CPU-only friendly; NO external API is called.

The sentence splitting, highlight selection, and the ``CaptionTrack`` output
contract are reused verbatim from ``forced_alignment`` so downstream (persist,
client renderer) is unchanged. On ANY failure (torch not installed, decode
error, degenerate alignment) the public entry returns ``None`` and the caller
falls back to the heuristic slicer — alignment quality degrades, the pipeline
never breaks.

ALIGNMENT MODEL
---------------
Per turn window (the assembler's real per-chunk audio boundaries):
  1. Slice the decoded 16kHz mono waveform to the window.
  2. Normalize each display token to spoken A-Z' tokens (``num2words`` for
     digits/currency/ordinals — news text is full of "$3.2 billion").
  3. CTC forced-align the spoken tokens within the window slice.
  4. Group char spans back to display words; words with no speakable chars
     (em-dashes) inherit a zero-width slot at the previous word's end.
  5. Extend each word's end to the next word's start so the track stays
     contiguous (highlight persists through intra-turn pauses) — starts are
     the acoustic onsets, which is what visible sync tracks.

PUBLIC ENTRY POINT
------------------
``acoustically_align_turn_windows`` — mirrors
``forced_alignment.align_turn_windows_to_audio`` but takes the audio bytes.
Returns a ``CaptionTrack`` or ``None`` (caller must fall back).
"""

from __future__ import annotations

import io
import re
import unicodedata
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from agents.pipeline.stages.forced_alignment import (
    CaptionTrack,
    CaptionWord,
    TurnAlignmentWindow,
    _choose_highlight_index,
    _split_into_sentences,
    _tokenize_text,
)
from agents.shared.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    import torch

logger = get_logger("pipeline.stages.acoustic_alignment")

# Reason: the alignment model's label set is A-Z + apostrophe + "|" separator;
# anything else in a spoken token must be removed before building CTC targets.
_NON_LABEL_CHARS_REGEX = re.compile(r"[^A-Z']")

# Reason: a maximal numeric run inside a token ("3.2", "1,200", "2026", "1st")
# that num2words must expand before the letter filter strips the digits.
_NUMERIC_RUN_REGEX = re.compile(r"(\d[\d,]*)(\.\d+)?(st|nd|rd|th)?", re.IGNORECASE)

# Reason: currency symbols are read AFTER the amount ("$3.2" → "three point
# two dollars"); map symbol → spoken suffix before stripping non-letters.
_CURRENCY_SUFFIXES: dict[str, str] = {"$": " dollars", "£": " pounds", "€": " euros"}

# Reason: target alignment sample rate of the Wav2Vec2 bundle.
_ALIGN_SAMPLE_RATE: int = 16000

# Reason: minimum visible duration mirrors forced_alignment's guard so a
# zero-width (unspeakable) token never breaks the renderer's monotonic scan.
_MIN_WORD_DURATION_S: float = 0.04


def _spoken_words_for_token(token: str) -> list[str]:
    """Normalize one display token to uppercase A-Z' spoken words.

    Expands numbers/ordinals via ``num2words`` (years 1100-1999 and 2010-2099
    as paired two-digit groups, decimals digit-by-digit after "point"),
    converts "%" / "&" / currency symbols to words, ASCII-folds accents, and
    strips everything outside the alignment label set.

    Args:
        token: A raw display token (may carry punctuation/digits).

    Returns:
        Zero or more spoken words; empty when the token has no speakable
        content (e.g. an em-dash).

    Example:
        >>> _spoken_words_for_token("$3.2")
        ['THREE', 'POINT', 'TWO', 'DOLLARS']
        >>> _spoken_words_for_token("Breaking,")
        ['BREAKING']
    """
    from num2words import num2words

    text = token.replace("%", " percent ").replace("&", " and ")
    for symbol, suffix in _CURRENCY_SUFFIXES.items():
        if symbol in text:
            text = text.replace(symbol, " ") + suffix

    def _expand_number(match: re.Match[str]) -> str:
        integer_part = int(match.group(1).replace(",", ""))
        decimal_part = match.group(2)
        ordinal_suffix = match.group(3)
        if ordinal_suffix and not decimal_part:
            return f" {num2words(integer_part, to='ordinal')} "
        # Reason: years are spoken as two two-digit groups ("2026" → "twenty
        # twenty six"), except 2000-2009 where num2words' form matches speech.
        if decimal_part is None and (
            1100 <= integer_part <= 1999 or 2010 <= integer_part <= 2099
        ):
            spoken = f"{num2words(integer_part // 100)} {num2words(integer_part % 100)}"
            if integer_part % 100 < 10 and integer_part % 100 != 0:
                spoken = f"{num2words(integer_part // 100)} oh {num2words(integer_part % 100)}"
            return f" {spoken} "
        spoken = num2words(integer_part)
        if decimal_part:
            digits = " ".join(num2words(int(d)) for d in decimal_part[1:])
            spoken = f"{spoken} point {digits}"
        return f" {spoken} "

    text = _NUMERIC_RUN_REGEX.sub(_expand_number, text)
    # Reason: fold accents to ASCII so "café" aligns as "CAFE" instead of
    # losing the accented char to the label filter.
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.upper().replace("-", " ")
    words = [
        cleaned
        for raw in text.split()
        if (cleaned := _NON_LABEL_CHARS_REGEX.sub("", raw))
    ]
    return words


@lru_cache(maxsize=1)
def _load_aligner() -> tuple[Any, Any, dict[str, int]] | None:
    """Lazily load the Wav2Vec2 alignment model (singleton; ~360MB first run).

    Returns:
        ``(model, labels, label_to_id)`` or ``None`` when torch/torchaudio is
        unavailable or the model download fails (caller falls back).
    """
    try:
        import torch  # noqa: F401
        import torchaudio

        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        model = bundle.get_model()
        model.eval()
        labels = bundle.get_labels()
        label_to_id = {label: index for index, label in enumerate(labels)}
        logger.info(
            "acoustic_aligner_loaded",
            model="WAV2VEC2_ASR_BASE_960H",
            sample_rate=bundle.sample_rate,
        )
        return model, labels, label_to_id
    except Exception as exc:  # noqa: BLE001 - any load failure means fallback
        logger.warning(
            "acoustic_aligner_unavailable",
            error_message=str(exc),
            fix_suggestion="pip install torch torchaudio (CPU wheels) to enable "
            "acoustic caption alignment; falling back to heuristic slicing",
        )
        return None


def _decode_audio_to_waveform(audio_bytes: bytes) -> "torch.Tensor":
    """Decode audio bytes (MP3/WAV) to a 16kHz mono float32 tensor [1, N].

    Uses pydub (ffmpeg) for decode/resample — already a pipeline dependency —
    so no extra audio I/O package is needed.
    """
    import numpy
    import torch
    from pydub import AudioSegment

    segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
    segment = segment.set_frame_rate(_ALIGN_SAMPLE_RATE).set_channels(1)
    samples = numpy.array(segment.get_array_of_samples(), dtype=numpy.float32)
    # Reason: pydub yields ints scaled by sample width; normalize to [-1, 1].
    samples /= float(1 << (8 * segment.sample_width - 1))
    return torch.from_numpy(samples).unsqueeze(0)


def _align_window_word_spans(
    model: Any,
    label_to_id: dict[str, int],
    window_waveform: "torch.Tensor",
    spoken_words_per_token: list[list[str]],
) -> list[tuple[float, float] | None]:
    """CTC forced-align one window's spoken tokens; return per-display-token spans.

    Args:
        model: The loaded Wav2Vec2 acoustic model.
        label_to_id: Label → CTC class id map (blank=0, "|" separator).
        window_waveform: The window's 16kHz mono slice, shape [1, N].
        spoken_words_per_token: For each display token, its spoken words
            (possibly empty for unspeakable tokens).

    Returns:
        One ``(start_s, end_s)`` span per display token, relative to the
        window start; ``None`` for tokens with no speakable content.

    Raises:
        ValueError: When the alignment output shape does not match the targets
            (caller treats this as a window failure and falls back).
    """
    import torch
    import torchaudio.functional as torchaudio_functional

    separator_id = label_to_id["|"]
    target_ids: list[int] = []
    # Reason: record each display token's (start, end) slice into target_ids
    # so char spans can be grouped back to display words afterwards.
    token_target_slices: list[tuple[int, int] | None] = []
    for token_index, spoken_words in enumerate(spoken_words_per_token):
        if not spoken_words:
            token_target_slices.append(None)
            continue
        if target_ids:
            target_ids.append(separator_id)
        slice_start = len(target_ids)
        for word_index, word in enumerate(spoken_words):
            if word_index > 0:
                target_ids.append(separator_id)
            target_ids.extend(label_to_id[char] for char in word if char in label_to_id)
        token_target_slices.append((slice_start, len(target_ids)))

    if not target_ids:
        return [None] * len(spoken_words_per_token)

    with torch.inference_mode():
        emission, _ = model(window_waveform)
        log_probs = torch.log_softmax(emission, dim=-1)
        targets = torch.tensor([target_ids], dtype=torch.int32)
        aligned_tokens, alignment_scores = torchaudio_functional.forced_align(
            log_probs, targets, blank=0
        )
        token_spans = torchaudio_functional.merge_tokens(
            aligned_tokens[0], alignment_scores[0], blank=0
        )

    if len(token_spans) != len(target_ids):
        raise ValueError(
            f"forced_align span count {len(token_spans)} != target count {len(target_ids)}"
        )

    frames_to_seconds = (
        window_waveform.size(1) / emission.size(1) / float(_ALIGN_SAMPLE_RATE)
    )
    spans: list[tuple[float, float] | None] = []
    for target_slice in token_target_slices:
        if target_slice is None:
            spans.append(None)
            continue
        slice_start, slice_end = target_slice
        char_spans = token_spans[slice_start:slice_end]
        start_s = char_spans[0].start * frames_to_seconds
        end_s = char_spans[-1].end * frames_to_seconds
        spans.append((start_s, end_s))
    return spans


def _intervals_from_word_spans(
    word_spans: list[tuple[float, float] | None],
    window_start_s: float,
    window_end_s: float,
) -> list[tuple[float, float]]:
    """Convert per-token acoustic spans to a contiguous monotonic interval list.

    Word starts are the acoustic onsets (what visible karaoke sync tracks);
    each end is extended to the next word's start so the highlight persists
    through intra-window pauses and the track stays non-overlapping. Tokens
    with no span (``None``) get a minimal slot at the previous word's end.
    All times are absolute (window offset applied), rounded to milliseconds.

    Args:
        word_spans: Per display token ``(start_s, end_s)`` relative to the
            window start, or ``None`` for unspeakable tokens.
        window_start_s: Absolute window start in the assembled audio.
        window_end_s: Absolute window end (clamp ceiling).

    Returns:
        One absolute, contiguous ``(start_s, end_s)`` per display token.

    Example:
        >>> _intervals_from_word_spans([(0.0, 0.3), (0.5, 0.9)], 10.0, 12.0)
        [(10.0, 10.5), (10.5, 10.9)]
    """
    intervals: list[tuple[float, float]] = []
    previous_end = window_start_s
    span_count = len(word_spans)
    for index, span in enumerate(word_spans):
        if span is None:
            start_s = previous_end
            end_s = min(previous_end + _MIN_WORD_DURATION_S, window_end_s)
        else:
            # Reason: never move a start before the previous end — keeps the
            # track strictly monotonic even if CTC spans touch.
            start_s = max(window_start_s + span[0], previous_end)
            end_s = max(window_start_s + span[1], start_s)
        # Reason: extend the end to the NEXT word's acoustic onset so there is
        # no dead gap during pauses (previous word stays highlighted).
        next_start = None
        for next_span in word_spans[index + 1 :]:
            if next_span is not None:
                next_start = max(window_start_s + next_span[0], start_s)
                break
        if next_start is not None:
            end_s = max(end_s, next_start)
        elif index == span_count - 1 and span is not None:
            end_s = min(max(end_s, start_s), window_end_s)
        end_s = min(max(end_s, start_s), window_end_s)
        start_s = min(start_s, end_s)
        intervals.append((round(start_s, 3), round(end_s, 3)))
        previous_end = end_s
    return intervals


def acoustically_align_turn_windows(
    digest_id: str,
    audio_bytes: bytes,
    turn_windows: list[TurnAlignmentWindow],
    audio_duration_s: float,
    preferred_keywords: list[str] | None = None,
) -> CaptionTrack | None:
    """Acoustically align a known transcript to its audio bytes.

    Mirrors :func:`forced_alignment.align_turn_windows_to_audio` (same window
    semantics, same ``CaptionTrack`` shape, same one-highlight-per-sentence
    rule) but derives word timing from CTC forced alignment of the actual
    audio instead of char-weight slicing.

    Args:
        digest_id: Stable digest id used in logs and the returned track.
        audio_bytes: The assembled audio (MP3/WAV bytes) the windows index into.
        turn_windows: Ordered, non-overlapping speech windows (one per rendered
            TTS chunk) with their real assembler boundaries.
        audio_duration_s: The real assembled audio duration (s).
        preferred_keywords: Optional flat pool of preferred caption keywords.

    Returns:
        A validated ``CaptionTrack``, or ``None`` on ANY failure — the caller
        MUST fall back to the heuristic slicer.
    """
    aligner = _load_aligner()
    if aligner is None:
        return None
    model, _labels, label_to_id = aligner
    keywords = preferred_keywords or []

    try:
        waveform = _decode_audio_to_waveform(audio_bytes)
        logger.info(
            "acoustic_alignment_started",
            digest_id=digest_id,
            window_count=len(turn_windows),
            audio_duration_s=round(audio_duration_s, 3),
        )

        caption_words: list[CaptionWord] = []
        sentence_index_offset = 0
        for window in turn_windows:
            window_sentences = _split_into_sentences(window.text)
            flat_tokens: list[str] = []
            flat_sentence_index: list[int] = []
            flat_is_highlight: list[bool] = []
            for sentence_offset, sentence in enumerate(window_sentences):
                sentence_tokens = _tokenize_text(sentence)
                if not sentence_tokens:
                    continue
                highlight_index = _choose_highlight_index(sentence_tokens, keywords)
                for token_index, token in enumerate(sentence_tokens):
                    flat_tokens.append(token)
                    flat_sentence_index.append(sentence_index_offset + sentence_offset)
                    flat_is_highlight.append(token_index == highlight_index)
            if not flat_tokens:
                continue
            sentence_index_offset += len(window_sentences)

            sample_start = int(window.start_s * _ALIGN_SAMPLE_RATE)
            sample_end = min(
                int(window.end_s * _ALIGN_SAMPLE_RATE), waveform.size(1)
            )
            window_waveform = waveform[:, sample_start:sample_end]
            spoken_words_per_token = [
                _spoken_words_for_token(token) for token in flat_tokens
            ]
            word_spans = _align_window_word_spans(
                model, label_to_id, window_waveform, spoken_words_per_token
            )
            intervals = _intervals_from_word_spans(
                word_spans, window.start_s, window.end_s
            )
            caption_words.extend(
                CaptionWord(
                    word=flat_tokens[index],
                    start_s=intervals[index][0],
                    end_s=intervals[index][1],
                    sentence_index=flat_sentence_index[index],
                    is_highlight=flat_is_highlight[index],
                )
                for index in range(len(flat_tokens))
            )

        if not caption_words:
            raise ValueError(f"No tokens acoustically aligned for {digest_id!r}")

        track = CaptionTrack(
            digest_id=digest_id,
            audio_duration_s=round(audio_duration_s, 3),
            speech_end_s=round(caption_words[-1].end_s, 3),
            sentence_count=sentence_index_offset,
            words=caption_words,
        )
        logger.info(
            "acoustic_alignment_completed",
            digest_id=digest_id,
            word_count=len(caption_words),
            sentence_count=sentence_index_offset,
            speech_end_s=track.speech_end_s,
        )
        return track
    except Exception as exc:  # noqa: BLE001 - any failure means heuristic fallback
        logger.error(
            "acoustic_alignment_failed",
            digest_id=digest_id,
            error_message=str(exc),
            fix_suggestion="Falling back to heuristic char-weight slicing; "
            "inspect the audio bytes / transcript normalization for this digest",
        )
        return None
