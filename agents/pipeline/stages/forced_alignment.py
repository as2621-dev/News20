"""Transcript-time-slice forced alignment of a known script to its audio.

DEVIATION FROM THE TLDW DONOR (flagged per Rule 12)
----------------------------------------------------
The TLDW donor ``forced_alignment.py`` recovered per-word timing by calling
the **OpenAI Whisper API** (``client.audio.transcriptions.create`` with
``timestamp_granularities=["word"]``) and then reconciling the heard words
back onto the script with ``difflib``. That made sense for TLDW because its
TTS provider returned audio bytes with no timing metadata AND the script was
not guaranteed to match the audio exactly.

News20's M0 spike is different on both counts and the user directive is
explicit: *"we already have the transcript; use the transcript to show the
subtitle caption."* So this module does **NOT** call Whisper / OpenAI / any
external API. It is **offline**. Instead it performs *heuristic* forced
alignment: it takes the KNOWN, ground-truth transcript words (from
``agents.m0.digests_input``) and time-slices them proportionally across each
digest's real, ffprobe-measured audio duration.

This is still "forced alignment" in the precise sense — we align a known
transcript to an audio track — only the alignment is heuristic (proportional
to word length) rather than acoustic (Whisper's spectrogram). The phase
explicitly named this as the Open-Q3 fallback when acoustic alignment is too
heavy for a 5-item spike.

ALIGNMENT MODEL
---------------
Each word's on-screen interval is sized proportional to its *weight* — a
char-count-based proxy for spoken length (longer words take longer to say) —
distributed monotonically and non-overlapping across the speech span
``[0, speech_end_s]``. ``speech_end_s`` defaults to the full ffprobe duration
but can be set shorter when a digest has measured trailing padded silence (SP1
measured ~1.07s of trailing silence on digest-2) so no caption is shown over
silence.

HIGHLIGHT KEYWORDS
------------------
Exactly one ``#FACC15`` highlight word per sentence (a DoD requirement). The
caller supplies the per-digest pool of preferred caption keywords (the bold
"caption keyword" column from ``documents/m0-digests.md``). For each sentence
we flag the first spoken word that matches a preferred keyword; if none
matches, we fall back deterministically to the longest content word in the
sentence. Either way: never zero, never two highlights per sentence.

PUBLIC ENTRY POINT
------------------
``align_transcript_to_audio`` is the single public alignment entry point (the
contract SP4 expects at this path). It returns a typed :class:`CaptionTrack`.

Example:
    >>> track = align_transcript_to_audio(
    ...     digest_id="digest-1",
    ...     sentences=["Breaking news from the gulf.", "A deal is close."],
    ...     audio_duration_s=50.61,
    ...     preferred_keywords=["news", "close"],  # flat pool, not 1:1 sentences
    ... )
    >>> track.words[0].word
    'Breaking'
    >>> sum(1 for w in track.words if w.is_highlight)  # one per sentence
    2
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator

from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.forced_alignment")

# Reason: tokenize on whitespace, keeping adjacent punctuation glued to the
# token (so "done." stays one displayable caption token). The renderer shows
# these verbatim, including punctuation, so karaoke reads naturally.
_WORD_TOKEN_REGEX = re.compile(r"\S+")

# Reason: split a paragraph into sentences on terminal punctuation (. ! ?),
# tolerating trailing quotes/brackets and an ellipsis. Kept deliberately
# simple — the M0 scripts are clean prose with no abbreviations like "U.S."
# that would fool a naive splitter (see _split_into_sentences for the guard).
_SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z\"'(…])")

# Reason: normalize a token for keyword matching — lowercase, strip every
# non-alphanumeric char so "Hormuz." matches the keyword "Hormuz" and
# "data-center" matches "data".
_NORMALIZE_REGEX = re.compile(r"[^a-z0-9]+")

# Reason: stopwords excluded from the longest-content-word fallback so the
# highlight never lands on "the"/"and"/"that" when no preferred keyword
# matches a sentence.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "but",
        "for",
        "that",
        "this",
        "with",
        "from",
        "into",
        "they",
        "their",
        "them",
        "your",
        "you",
        "are",
        "was",
        "were",
        "has",
        "had",
        "have",
        "his",
        "her",
        "its",
        "our",
        "out",
        "over",
        "just",
        "not",
        "now",
        "all",
        "any",
        "one",
        "two",
        "who",
        "what",
        "when",
        "where",
        "which",
        "about",
        "after",
        "before",
        "still",
        "isnt",
        "didnt",
        "arent",
        "hes",
        "shes",
        "its",
    }
)

# Reason: minimum gap (seconds) the proportional slicer keeps between adjacent
# words so two words never share an identical start (strict monotonicity that
# the downstream renderer's binary search relies on).
_MIN_WORD_DURATION_S: float = 0.04


class CaptionWord(BaseModel):
    """One displayable caption token with its on-screen interval.

    Attributes:
        word: The verbatim transcript token (original casing + punctuation).
        start_s: Inclusive on-screen start time in seconds from audio start.
        end_s: Exclusive on-screen end time in seconds.
        sentence_index: Zero-based index of the sentence this word belongs to.
        is_highlight: True for exactly one word per sentence (the ``#FACC15``
            highlight keyword the renderer paints yellow).

    Example:
        >>> CaptionWord(
        ...     word="Breaking", start_s=0.0, end_s=0.32,
        ...     sentence_index=0, is_highlight=True,
        ... ).is_highlight
        True
    """

    word: str = Field(..., min_length=1, description="Verbatim transcript token")
    start_s: float = Field(
        ..., ge=0.0, description="Inclusive on-screen start time (s)"
    )
    end_s: float = Field(..., ge=0.0, description="Exclusive on-screen end time (s)")
    sentence_index: int = Field(..., ge=0, description="Zero-based sentence index")
    is_highlight: bool = Field(
        ...,
        description="True for the single #FACC15 highlight keyword of this sentence",
    )

    @model_validator(mode="after")
    def end_after_start(self) -> CaptionWord:
        """Validate the interval is well-formed (end strictly after start)."""
        if self.end_s < self.start_s:
            raise ValueError(
                f"CaptionWord end_s ({self.end_s}) must be >= start_s ({self.start_s})"
            )
        return self


class CaptionTrack(BaseModel):
    """A full caption track for one digest — the contract SP3 consumes.

    Attributes:
        digest_id: Stable digest id ("digest-1" .. "digest-5").
        audio_duration_s: The digest's real ffprobe-measured audio duration.
        speech_end_s: The end of actual speech (== audio_duration_s unless the
            file has measured trailing silence). No caption sits past this.
        sentence_count: Number of sentences the transcript was split into.
        words: Ordered, monotonic, non-overlapping caption words.

    Example:
        >>> track = CaptionTrack(
        ...     digest_id="digest-1", audio_duration_s=50.61, speech_end_s=50.61,
        ...     sentence_count=1,
        ...     words=[CaptionWord(word="Hi", start_s=0.0, end_s=0.5,
        ...                        sentence_index=0, is_highlight=True)],
        ... )
        >>> track.digest_id
        'digest-1'
    """

    digest_id: str = Field(..., description="Stable digest id and filename stem")
    audio_duration_s: float = Field(
        ..., gt=0.0, description="Real ffprobe-measured audio duration in seconds"
    )
    speech_end_s: float = Field(
        ...,
        gt=0.0,
        description="End of actual speech; captions never extend past this point",
    )
    sentence_count: int = Field(
        ..., ge=0, description="Number of sentences in the transcript"
    )
    words: list[CaptionWord] = Field(
        default_factory=list,
        description="Ordered caption words with timing + highlight flags",
    )


def _tokenize_text(text: str) -> list[str]:
    """Split text into whitespace-delimited tokens, preserving punctuation."""
    return _WORD_TOKEN_REGEX.findall(text)


def _normalize_for_match(token: str) -> str:
    """Lowercase + strip non-alphanumerics so matching ignores casing/punctuation."""
    return _NORMALIZE_REGEX.sub("", token.lower())


def _split_into_sentences(paragraph: str) -> list[str]:
    """Split a paragraph into sentences on terminal punctuation.

    Reason: the M0 scripts use "U.S." which a naive split-on-period would
    shatter. ``_SENTENCE_SPLIT_REGEX`` only splits when the period is followed
    by whitespace and a capital/quote/ellipsis start, so "U.S. military" stays
    intact (the char after "U.S." is lowercase or a space+lowercase). Ellipsis
    "..." that joins two turns is treated as a soft break and kept with its
    leading clause.

    Args:
        paragraph: The full transcript text for one digest (turns joined).

    Returns:
        Ordered, non-empty sentence strings.

    Example:
        >>> _split_into_sentences("A deal is close. But close isn't done.")
        ['A deal is close.', "But close isn't done."]
    """
    parts = _SENTENCE_SPLIT_REGEX.split(paragraph.strip())
    return [part.strip() for part in parts if part.strip()]


def _word_weight(token: str) -> float:
    """Return a positive spoken-length proxy for a token.

    Reason: time-on-screen tracks roughly with how long a word takes to say,
    which tracks with its alphanumeric character count. We use
    ``len(normalized) + 1`` so even a single-letter or pure-punctuation token
    (e.g. "—") gets a non-zero weight and never collapses to a zero-width
    caption interval.

    Args:
        token: A raw transcript token (may carry punctuation).

    Returns:
        A strictly-positive weight.
    """
    return float(len(_normalize_for_match(token)) + 1)


def _choose_highlight_index(
    sentence_tokens: list[str],
    preferred_keywords: list[str],
) -> int:
    """Pick the index of the single highlight word within a sentence.

    Cut->sentence mapping rule (documented per Rule 12): the M0 scripts have 8
    per-cut "caption keywords" (``documents/m0-digests.md``) but split into
    10-11 sentences, so keywords are passed as a flat POOL rather than 1:1 with
    sentences. For each sentence we scan its tokens left-to-right and highlight
    the FIRST token that matches ANY pooled keyword (exact normalized match, or
    the keyword as a normalized substring so "data" matches "data-center"). A
    sentence with no pooled-keyword hit falls back deterministically.

    Selection rule:
      1. First token matching any pooled keyword (earliest token wins).
      2. Else the LONGEST non-stopword content token; earliest on a length tie.
      3. Else (all stopwords/punctuation) the longest token overall, then 0.

    Args:
        sentence_tokens: Tokens of one sentence (verbatim, ordered).
        preferred_keywords: The digest's flat pool of caption keywords.

    Returns:
        The index into ``sentence_tokens`` to flag as the highlight. Always a
        valid index for a non-empty sentence (never -1). Guarantees exactly one
        highlight per sentence — never zero, never two.
    """
    if not sentence_tokens:
        raise ValueError("Cannot choose a highlight word for an empty sentence")

    keyword_norms = [
        normalized
        for keyword in preferred_keywords
        if (normalized := _normalize_for_match(keyword))
    ]
    if keyword_norms:
        for index, token in enumerate(sentence_tokens):
            token_norm = _normalize_for_match(token)
            if not token_norm:
                continue
            for keyword_norm in keyword_norms:
                if token_norm == keyword_norm or keyword_norm in token_norm:
                    return index

    # Reason: deterministic fallback — longest content (non-stopword) token,
    # earliest on a length tie, so the choice is stable and reproducible.
    best_index = -1
    best_length = -1
    for index, token in enumerate(sentence_tokens):
        token_norm = _normalize_for_match(token)
        if not token_norm or token_norm in _STOPWORDS:
            continue
        if len(token_norm) > best_length:
            best_length = len(token_norm)
            best_index = index
    if best_index != -1:
        return best_index

    # Reason: degenerate sentence (all stopwords/punctuation) — longest token
    # overall, then index 0. Guarantees exactly one highlight, never zero.
    best_index = 0
    best_length = -1
    for index, token in enumerate(sentence_tokens):
        if len(_normalize_for_match(token)) > best_length:
            best_length = len(_normalize_for_match(token))
            best_index = index
    return best_index


def align_transcript_to_audio(
    digest_id: str,
    sentences: list[str],
    audio_duration_s: float,
    preferred_keywords: list[str] | None = None,
    speech_end_s: float | None = None,
) -> CaptionTrack:
    """Heuristically align a known transcript to its audio (the public entry).

    Time-slices every transcript word proportional to its spoken-length proxy
    across ``[0, speech_end_s]``, yielding a monotonic, non-overlapping caption
    track. Flags exactly one ``#FACC15`` highlight word per sentence.

    NOTE: This is the offline transcript-time-slice replacement for the TLDW
    donor's Whisper-based ``align_words`` — see the module docstring for the
    deviation rationale. No external API is called.

    Args:
        digest_id: Stable digest id ("digest-1" .. "digest-5").
        sentences: The transcript split into ordered sentences. Each sentence
            contributes one highlight word.
        audio_duration_s: The digest's real ffprobe-measured duration (s).
        preferred_keywords: Optional flat POOL of preferred caption keywords for
            the whole digest (the 8 bold "caption keyword" entries from
            ``documents/m0-digests.md``). Each sentence highlights the first of
            its tokens matching any pooled keyword; sentences with no hit fall
            back to the longest-content-word rule.
        speech_end_s: Optional end-of-speech (s); defaults to
            ``audio_duration_s``. Set shorter for files with measured trailing
            silence so no caption sits over silence. Must be <= duration.

    Returns:
        A validated :class:`CaptionTrack`.

    Raises:
        ValueError: If inputs are inconsistent (no sentences, non-positive
            duration, or speech_end past the audio end).

    Example:
        >>> track = align_transcript_to_audio(
        ...     "digest-1", ["A deal is close."], 10.0, ["close"],
        ... )
        >>> [w.word for w in track.words]
        ['A', 'deal', 'is', 'close.']
        >>> track.words[-1].is_highlight
        True
    """
    speech_end = audio_duration_s if speech_end_s is None else speech_end_s

    if audio_duration_s <= 0.0:
        raise ValueError(f"audio_duration_s must be positive, got {audio_duration_s}")
    if not sentences or not any(sentence.strip() for sentence in sentences):
        logger.error(
            "forced_alignment_no_sentences",
            digest_id=digest_id,
            fix_suggestion="Pass a non-empty list of transcript sentences to align",
        )
        raise ValueError(f"No sentences to align for {digest_id!r}")
    if not (0.0 < speech_end <= audio_duration_s):
        logger.error(
            "forced_alignment_bad_speech_end",
            digest_id=digest_id,
            speech_end_s=speech_end,
            audio_duration_s=audio_duration_s,
            fix_suggestion="speech_end_s must be in (0, audio_duration_s]",
        )
        raise ValueError(
            f"speech_end_s ({speech_end}) must be in (0, audio_duration_s={audio_duration_s}]"
        )

    keywords = preferred_keywords or []

    logger.info(
        "forced_alignment_started",
        digest_id=digest_id,
        sentence_count=len(sentences),
        audio_duration_s=round(audio_duration_s, 3),
        speech_end_s=round(speech_end, 3),
    )

    # Reason: build a flat list of (token, sentence_index, is_highlight) first
    # so timing is a single proportional pass over ALL words — keeps the span
    # math global (no per-sentence drift) and guarantees one highlight/sentence.
    flat_tokens: list[str] = []
    flat_sentence_index: list[int] = []
    flat_is_highlight: list[bool] = []

    for sentence_index, sentence in enumerate(sentences):
        sentence_tokens = _tokenize_text(sentence)
        if not sentence_tokens:
            continue
        highlight_index = _choose_highlight_index(sentence_tokens, keywords)
        for token_index, token in enumerate(sentence_tokens):
            flat_tokens.append(token)
            flat_sentence_index.append(sentence_index)
            flat_is_highlight.append(token_index == highlight_index)

    word_count = len(flat_tokens)
    if word_count == 0:
        raise ValueError(f"No tokens to align for {digest_id!r}")

    # Reason: proportional time-slice. Each word's share of the speech span is
    # its weight / total weight. We walk a cumulative cursor so intervals are
    # contiguous (word i.end == word i+1.start) and strictly monotonic; a small
    # minimum duration guard prevents zero-width intervals on tiny tokens.
    weights = [_word_weight(token) for token in flat_tokens]
    total_weight = sum(weights)
    caption_words: list[CaptionWord] = []
    cumulative_weight = 0.0
    previous_end = 0.0

    for index in range(word_count):
        start_s = previous_end
        cumulative_weight += weights[index]
        # Reason: derive end from the cumulative fraction of the whole span so
        # rounding never accumulates drift past speech_end.
        end_s = (cumulative_weight / total_weight) * speech_end
        if end_s < start_s + _MIN_WORD_DURATION_S:
            end_s = min(start_s + _MIN_WORD_DURATION_S, speech_end)
        # Reason: clamp the final word exactly to speech_end so the track ends
        # on the speech boundary, never past end-of-audio.
        if index == word_count - 1:
            end_s = speech_end
        caption_words.append(
            CaptionWord(
                word=flat_tokens[index],
                start_s=round(start_s, 3),
                end_s=round(end_s, 3),
                sentence_index=flat_sentence_index[index],
                is_highlight=flat_is_highlight[index],
            )
        )
        previous_end = end_s

    track = CaptionTrack(
        digest_id=digest_id,
        audio_duration_s=round(audio_duration_s, 3),
        speech_end_s=round(speech_end, 3),
        sentence_count=len(sentences),
        words=caption_words,
    )

    highlight_count = sum(1 for word in caption_words if word.is_highlight)
    logger.info(
        "forced_alignment_completed",
        digest_id=digest_id,
        word_count=word_count,
        highlight_count=highlight_count,
        last_word_end_s=caption_words[-1].end_s,
    )
    return track


def split_transcript_into_sentences(paragraph: str) -> list[str]:
    """Public wrapper around the internal sentence splitter (used by the driver).

    Args:
        paragraph: The full transcript text for one digest (turns joined).

    Returns:
        Ordered, non-empty sentence strings.

    Example:
        >>> split_transcript_into_sentences("One. Two.")
        ['One.', 'Two.']
    """
    return _split_into_sentences(paragraph)
