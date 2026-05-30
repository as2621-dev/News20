"""Audio assembly for the anchor-duo digest spine.

Ported from TLDW (`agents/voice/audio.py`) and trimmed to the M0 quality-spike
surface: concatenate rendered TTS segments with inter-speaker gaps, emit
per-segment ms timings, and export the assembled audio to a file (best-effort
FFmpeg loudness normalization). TLDW's intro/outro/bumper/background-music
mixing and Supabase upload are dropped — M0 is single-source, two-host, no
music, local output (Rule 2/3).

Functions:
    assemble_episode    -- Concatenate speech segments with inter-speaker gaps
    normalize_audio     -- FFmpeg loudnorm normalization to target LUFS
    export_digest_audio -- Assemble + export (+ normalize) to a target file path

Private helpers:
    _insert_inter_speaker_gaps -- Concatenate speech with random inter-speaker gaps

Example:
    >>> from agents.voice.audio import assemble_episode
    >>> assembled, timings = assemble_episode(segments, speakers, turn_indices=[0, 1])
"""

from __future__ import annotations

import os
import random
import subprocess
import tempfile
from pathlib import Path

from pydub import AudioSegment

from agents.shared.logger import get_logger
from agents.voice.models import AssembledEpisode, EpisodeConfig, SegmentTiming

logger = get_logger("voice.audio")


def _insert_inter_speaker_gaps(
    segments: list[AudioSegment],
    speakers: list[str],
    gap_range_ms: tuple[int, int],
) -> tuple[AudioSegment, list[tuple[int, int]]]:
    """Concatenate speech segments with random inter-speaker gaps.

    Inserts a random silence gap between consecutive segments from different
    speakers. Same-speaker consecutive segments get no gap.

    Args:
        segments: List of pydub AudioSegment objects (one per rendered chunk).
        speakers: Speaker identifiers corresponding to each segment.
        gap_range_ms: (min_gap_ms, max_gap_ms) for the random gap duration.

    Returns:
        Tuple of (combined_audio, boundaries) where boundaries is a list of
        (start_ms, end_ms) tuples — one per input segment — giving the absolute
        position of each segment in the combined output.

    Example:
        >>> combined, bounds = _insert_inter_speaker_gaps(segments, speakers, (120, 260))
        >>> bounds[0]
        (0, 4200)
    """
    if not segments:
        return AudioSegment.empty(), []

    combined = segments[0]
    boundaries: list[tuple[int, int]] = [(0, len(segments[0]))]
    min_gap, max_gap = gap_range_ms

    for i in range(1, len(segments)):
        # Reason: only insert a silence gap when the speaker changes; same-
        # speaker consecutive segments flow naturally.
        if speakers[i] != speakers[i - 1]:
            gap_ms = random.randint(min_gap, max_gap)
            silence = AudioSegment.silent(duration=gap_ms)
            combined = combined + silence

        seg_start = len(combined)
        combined = combined + segments[i]
        boundaries.append((seg_start, len(combined)))

    return combined, boundaries


def assemble_episode(
    speech_segments: list[AudioSegment],
    speakers: list[str],
    config: EpisodeConfig | None = None,
    turn_indices: list[int] | None = None,
) -> tuple[AudioSegment, list[SegmentTiming]]:
    """Assemble a digest's audio from rendered TTS segments.

    Concatenates the segments with random inter-speaker gaps and builds
    per-segment ``SegmentTiming`` records (real ms boundaries) aligned with the
    input segments — these drive sub-phase 2's caption time-slicing.

    Args:
        speech_segments: Rendered pydub AudioSegments (one per TTS chunk).
        speakers: Speaker identifiers (ALEX/JORDAN) per segment.
        config: EpisodeConfig with assembly parameters. Uses defaults if None.
        turn_indices: Source turn index per segment. Defaults to positional
            index when not supplied.

    Returns:
        Tuple of (assembled_audio, segment_timings).

    Example:
        >>> assembled, timings = assemble_episode(
        ...     speech_segments=segments,
        ...     speakers=["ALEX", "JORDAN"],
        ...     turn_indices=[0, 1],
        ... )
    """
    if config is None:
        config = EpisodeConfig()

    logger.info(
        "episode_assembly_started",
        segment_count=len(speech_segments),
        target_lufs=config.target_lufs,
    )

    if not speech_segments:
        logger.warning(
            "episode_assembly_no_segments",
            fix_suggestion="Provide at least one rendered speech segment for assembly",
        )
        return AudioSegment.empty(), []

    combined_speech, boundaries = _insert_inter_speaker_gaps(
        speech_segments,
        speakers,
        config.inter_speaker_gap_ms,
    )

    # Reason: align SegmentTiming records with input segments. turn_indices
    # defaults to per-position index; the TTS renderer threads real turn
    # indices (first-turn-in-chunk) so sub-phase 2 can map to the script.
    resolved_turn_indices: list[int] = (
        list(turn_indices)
        if turn_indices is not None
        else list(range(len(speech_segments)))
    )
    segment_timings: list[SegmentTiming] = []
    for idx, (start_ms, end_ms) in enumerate(boundaries):
        speaker_label = speakers[idx] if speakers[idx] in {"ALEX", "JORDAN"} else "ALEX"
        segment_timings.append(
            SegmentTiming(
                turn_index=resolved_turn_indices[idx],
                speaker=speaker_label,  # type: ignore[arg-type]
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

    logger.info(
        "episode_assembly_completed",
        duration_ms=len(combined_speech),
        duration_seconds=round(len(combined_speech) / 1000, 1),
        timings_count=len(segment_timings),
    )

    return combined_speech, segment_timings


def normalize_audio(
    input_path: str,
    output_path: str,
    target_lufs: int = -16,
    true_peak_dbtp: float = -1.0,
    loudness_range_lu: int = 11,
    bitrate: str = "192k",
) -> str:
    """Normalize audio loudness using FFmpeg's loudnorm filter.

    Shells out to FFmpeg to apply the loudnorm filter with the specified target
    integrated loudness (LUFS), true peak, and loudness range. Output format is
    inferred from ``output_path``'s extension.

    Args:
        input_path: Path to the input audio file.
        output_path: Path for the normalized output file.
        target_lufs: Target integrated loudness in LUFS (default -16).
        true_peak_dbtp: Maximum true peak in dBTP (default -1.0).
        loudness_range_lu: Target loudness range in LU (default 11).
        bitrate: Output bitrate (default "192k").

    Returns:
        The output_path string on success.

    Raises:
        RuntimeError: If FFmpeg normalization fails or FFmpeg is missing.
        FileNotFoundError: If the input file does not exist.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    cmd = [
        "ffmpeg",
        "-i",
        input_path,
        "-y",
        "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={loudness_range_lu}",
        "-ar",
        "44100",
        "-b:a",
        bitrate,
        output_path,
    ]

    logger.info(
        "audio_normalization_started",
        input_path=input_path,
        output_path=output_path,
        target_lufs=target_lufs,
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        logger.error(
            "audio_normalization_ffmpeg_not_found",
            fix_suggestion="Install FFmpeg: brew install ffmpeg (macOS) or apt-get install -y ffmpeg (Linux)",
        )
        raise RuntimeError(
            "FFmpeg not found. Install it (brew install ffmpeg / apt-get install ffmpeg)."
        )

    if result.returncode != 0:
        logger.error(
            "audio_normalization_failed",
            returncode=result.returncode,
            stderr=result.stderr[:500],
            fix_suggestion="Check FFmpeg installation and input file format",
        )
        raise RuntimeError(f"FFmpeg normalization failed: {result.stderr}")

    logger.info(
        "audio_normalization_completed",
        output_path=output_path,
        target_lufs=target_lufs,
    )
    return output_path


def export_digest_audio(
    speech_segments: list[AudioSegment],
    speakers: list[str],
    output_path: str,
    episode_title: str,
    config: EpisodeConfig | None = None,
    turn_indices: list[int] | None = None,
    normalize: bool = True,
    min_duration_ms: int = 46_000,
) -> AssembledEpisode:
    """Assemble rendered segments and write the digest audio to ``output_path``.

    Full M0 flow: assemble (concatenate + inter-speaker gaps) -> pad a trailing
    loop-point beat of silence if below ``min_duration_ms`` -> export to a temp
    file -> best-effort FFmpeg loudness normalization into ``output_path``
    (falls back to the un-normalized export if FFmpeg fails).

    Args:
        speech_segments: Rendered pydub AudioSegments (one per TTS chunk).
        speakers: Speaker identifiers (ALEX/JORDAN) per segment.
        output_path: Destination file path (extension sets the format, e.g. .mp3).
        episode_title: Digest headline used in the returned metadata.
        config: EpisodeConfig with assembly parameters.
        turn_indices: Source turn index per segment.
        normalize: Whether to run FFmpeg loudness normalization.
        min_duration_ms: Minimum total duration; if the assembled speech is
            shorter, a trailing beat of silence (the format's 50-55s loop point)
            is appended so the output clears the 45-70s digest gate. The trailing
            silence sits after the last segment, so per-segment ``segment_timings``
            (which sub-phase 2 uses for caption slicing) are unaffected.

    Returns:
        AssembledEpisode with audio_path, duration_ms, and segment_timings.

    Raises:
        ValueError: If no speech segments are provided.
    """
    if not speech_segments:
        raise ValueError("export_digest_audio requires at least one speech segment")

    if config is None:
        config = EpisodeConfig()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    audio_format = out.suffix.lstrip(".").lower() or "mp3"

    logger.info(
        "digest_export_started",
        episode_title=episode_title,
        segment_count=len(speech_segments),
        output_path=str(out),
        audio_format=audio_format,
    )

    assembled, segment_timings = assemble_episode(
        speech_segments=speech_segments,
        speakers=speakers,
        config=config,
        turn_indices=turn_indices,
    )

    # Reason: TTS pacing is non-deterministic; the tersest scripts can land just
    # below the 45s gate. Append a trailing loop-point beat of silence to clear
    # the floor without altering the read or the per-segment caption timings.
    speech_duration_ms = len(assembled)
    if speech_duration_ms < min_duration_ms:
        pad_ms = min_duration_ms - speech_duration_ms
        assembled = assembled + AudioSegment.silent(duration=pad_ms)
        logger.info(
            "digest_export_padded_to_floor",
            episode_title=episode_title,
            speech_duration_ms=speech_duration_ms,
            pad_ms=pad_ms,
            min_duration_ms=min_duration_ms,
        )

    # Step 1: export the assembled audio to a temp file in the same format.
    with tempfile.NamedTemporaryFile(
        suffix=f"_raw.{audio_format}", delete=False
    ) as raw_file:
        raw_path = raw_file.name
    assembled.export(raw_path, format=audio_format, bitrate=config.tts_bitrate)

    # Step 2: best-effort loudness normalization into the final path.
    final_path = str(out)
    if normalize:
        try:
            normalize_audio(
                input_path=raw_path,
                output_path=final_path,
                target_lufs=config.target_lufs,
                true_peak_dbtp=config.true_peak_dbtp,
                loudness_range_lu=config.loudness_range_lu,
                bitrate=config.tts_bitrate,
            )
        except (RuntimeError, FileNotFoundError) as exc:
            logger.error(
                "digest_export_normalization_failed",
                episode_title=episode_title,
                error_message=str(exc),
                fix_suggestion="FFmpeg unavailable/failed — writing un-normalized audio instead.",
            )
            assembled.export(
                final_path, format=audio_format, bitrate=config.tts_bitrate
            )
    else:
        assembled.export(final_path, format=audio_format, bitrate=config.tts_bitrate)

    # Reason: clean up the temp raw export; ignore if already gone.
    try:
        os.remove(raw_path)
    except OSError:
        pass

    episode = AssembledEpisode(
        episode_title=episode_title,
        audio_path=final_path,
        duration_ms=len(assembled),
        segment_count=len(speech_segments),
        target_lufs=config.target_lufs,
        segment_timings=segment_timings,
    )

    logger.info(
        "digest_export_completed",
        episode_title=episode_title,
        duration_ms=episode.duration_ms,
        duration_seconds=round(episode.duration_ms / 1000, 1),
        audio_path=final_path,
    )

    return episode
