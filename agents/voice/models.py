"""Pydantic v2 models for the anchor-duo TTS spine and audio assembly.

Ported from TLDW (`agents/voice/models.py` + the `DialogueTurn` model from
`agents/pipeline/models.py`) and trimmed to the M0 quality-spike surface:
the speaker-tagged dialogue turn, the audio-assembly config, per-segment
timing boundaries, and the assembled-output metadata. TLDW's LLM-structured
``PodcastScript`` / ``DialogueLine`` (HOST_A/HOST_B) models are dropped —
News20 transcribes its scripts by hand into typed turns (Rule 2).

Models:
    DialogueTurn     -- One ALEX/JORDAN turn (speaker + speakable text)
    EpisodeConfig    -- Audio assembly parameters (gaps, loudness target)
    SegmentTiming    -- Real ms boundaries of one rendered segment in the output
    AssembledEpisode -- Final assembled-audio metadata (path, duration, timings)

Example:
    >>> from agents.voice.models import DialogueTurn
    >>> turn = DialogueTurn(speaker="ALEX", text="The U.S. military hit another target overnight.")
    >>> turn.speaker
    'ALEX'
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DialogueTurn(BaseModel):
    """A single turn of dialogue in a digest script.

    Represents one line spoken by either ALEX or JORDAN. Internal speaker
    labels stay ALEX/JORDAN everywhere; the mapping to Gemini's
    ``Person1``/``Person2`` happens only at the TTS call boundary.

    Attributes:
        speaker: Which narrator speaks this turn ("ALEX" or "JORDAN").
        text: The clean speakable dialogue text.

    Example:
        >>> turn = DialogueTurn(speaker="JORDAN", text="And President Trump says a deal is close.")
        >>> turn.speaker
        'JORDAN'
    """

    speaker: Literal["ALEX", "JORDAN"] = Field(
        ...,
        description="Which narrator speaks this turn: ALEX (Leda voice) or JORDAN (Sadaltager voice)",
    )
    text: str = Field(
        ...,
        min_length=1,
        description="Clean speakable dialogue text for this turn (no SSML/bracket tags)",
    )

    @field_validator("text")
    @classmethod
    def text_must_not_be_empty(cls, value: str) -> str:
        """Validate that text is not empty or whitespace-only."""
        if not value.strip():
            raise ValueError("Dialogue text must not be empty or whitespace-only")
        return value


class EpisodeConfig(BaseModel):
    """Configuration for the audio assembly pipeline.

    Controls inter-speaker gap timing and loudness-normalization targets.
    For the M0 spike the defaults produce a tight ~55s two-host segment with
    no intro/music/bumpers.

    Attributes:
        music_reduction_db: dB to duck background music below speech (unused at M0).
        inter_speaker_gap_ms: (min, max) silence inserted between speaker turns.
        tts_bitrate: Output MP3 bitrate.
        target_lufs: Target integrated loudness in LUFS (Apple standard: -16).
        true_peak_dbtp: Maximum true peak level in dBTP.
        loudness_range_lu: Target loudness range in LU.

    Example:
        >>> config = EpisodeConfig()
        >>> config.target_lufs
        -16
    """

    music_reduction_db: float = Field(
        default=20.0,
        ge=0.0,
        le=40.0,
        description="Background music ducking level in dB below speech (unused at M0)",
    )
    inter_speaker_gap_ms: tuple[int, int] = Field(
        default=(120, 260),
        description="Min and max silence between speaker turns in milliseconds",
    )
    tts_bitrate: str = Field(
        default="192k",
        description="Output MP3 bitrate for the assembled digest audio",
    )
    target_lufs: int = Field(
        default=-16,
        ge=-30,
        le=-5,
        description="Target integrated loudness in LUFS (Apple standard: -16)",
    )
    true_peak_dbtp: float = Field(
        default=-1.0,
        ge=-6.0,
        le=0.0,
        description="Maximum true peak level in dBTP",
    )
    loudness_range_lu: int = Field(
        default=11,
        ge=1,
        le=20,
        description="Target loudness range in LU",
    )


class SegmentTiming(BaseModel):
    """Real ms boundaries of a single rendered segment in the assembled audio.

    Emitted by ``assemble_episode`` so downstream stages (sub-phase 2 caption
    alignment) can anchor to actual audio time instead of a character-count
    approximation.

    Attributes:
        turn_index: Index into the source turn list for this segment.
        speaker: Speaker label (ALEX / JORDAN).
        start_ms: Inclusive start position of this segment in the final audio.
        end_ms: Exclusive end position of this segment in the final audio.

    Example:
        >>> SegmentTiming(turn_index=0, speaker="ALEX", start_ms=0, end_ms=4200)
        SegmentTiming(turn_index=0, speaker='ALEX', start_ms=0, end_ms=4200)
    """

    turn_index: int = Field(
        ...,
        description="Index of the source turn (or rendered chunk) this segment corresponds to",
    )
    speaker: Literal["ALEX", "JORDAN"] = Field(
        ...,
        description="Speaker for this segment",
    )
    start_ms: int = Field(
        ...,
        ge=0,
        description="Inclusive start ms position in the final assembled audio",
    )
    end_ms: int = Field(
        ...,
        ge=0,
        description="Exclusive end ms position in the final assembled audio",
    )


class AssembledEpisode(BaseModel):
    """Final assembled-audio metadata with path and duration.

    Produced after concatenating the rendered TTS segments with inter-speaker
    gaps. ``segment_timings`` carries per-segment ms boundaries that sub-phase
    2 uses to time-slice captions across the real audio.

    Attributes:
        episode_title: Title (digest headline) of the assembled audio.
        audio_path: File path to the final audio file.
        duration_ms: Total duration of the audio in milliseconds.
        segment_count: Number of rendered segments assembled.
        target_lufs: The LUFS normalization target used.
        segment_timings: Per-segment ms boundaries in the final audio.

    Example:
        >>> episode = AssembledEpisode(
        ...     episode_title="US strikes Iran again",
        ...     audio_path="agents/m0/output/audio/digest-1.mp3",
        ...     duration_ms=55000,
        ...     segment_count=7,
        ... )
    """

    episode_title: str = Field(
        ...,
        description="Title (digest headline) of the assembled audio",
    )
    audio_path: str = Field(
        ...,
        description="File path to the final assembled audio file",
    )
    duration_ms: int = Field(
        ...,
        ge=0,
        description="Total duration of the assembled audio in milliseconds",
    )
    segment_count: int = Field(
        ...,
        ge=0,
        description="Number of rendered segments assembled",
    )
    target_lufs: int = Field(
        default=-16,
        description="The LUFS normalization target used",
    )
    segment_timings: list[SegmentTiming] = Field(
        default_factory=list,
        description="Per-segment ms boundaries (ALEX/JORDAN) in the final audio",
    )
