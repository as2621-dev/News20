"""M0 driver: build word-by-word caption tracks for the 5 rendered digests.

Run as a module:

    python -m agents.m0.align_captions

For each digest in ``agents.m0.digests_input.DIGESTS`` it:
  1. joins the ordered ALEX/JORDAN turns into one transcript paragraph and
     splits it into sentences;
  2. ffprobes the real rendered audio at
     ``agents/m0/output/audio/digest-{1..5}.mp3`` for its true duration;
  3. heuristically aligns the transcript words across that duration via
     ``agents.pipeline.stages.forced_alignment.align_transcript_to_audio``
     (OFFLINE transcript-time-slice — NO Whisper / no external API; see that
     module's docstring for the deviation from the TLDW Whisper donor);
  4. flags exactly one ``#FACC15`` highlight word per sentence, preferring the
     per-cut "caption keyword" pool from ``documents/m0-digests.md``;
  5. writes the typed caption track to
     ``agents/m0/output/captions/digest-{1..5}.captions.json``.

CAPTION-TRACK JSON CONTRACT (consumed by sub-phase 3's Remotion CaptionTrack /
``captionWordsAtFrame``)::

    {
      "digest_id": "digest-1",
      "audio_duration_s": 50.611,
      "speech_end_s": 50.611,
      "sentence_count": 11,
      "words": [
        {"word": "The", "start_s": 0.0, "end_s": 0.21,
         "sentence_index": 0, "is_highlight": false},
        ...
      ]
    }

Every word interval is monotonic, non-overlapping, and contained within
``[0, speech_end_s]`` (no caption past end-of-audio). The transcript word count
equals the caption word count. Exactly one ``is_highlight: true`` per
``sentence_index``.

This driver reads ONLY existing audio files — it does NOT re-render audio.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agents.m0.digests_input import DIGESTS, Digest
from agents.pipeline.stages.forced_alignment import (
    CaptionTrack,
    align_transcript_to_audio,
    split_transcript_into_sentences,
)
from agents.shared.logger import get_logger

logger = get_logger("m0.align_captions")

# Reason: resolve paths relative to this module so the driver runs from any cwd.
_MODULE_DIR: Path = Path(__file__).parent
INPUT_AUDIO_DIR: Path = _MODULE_DIR / "output" / "audio"
OUTPUT_CAPTIONS_DIR: Path = _MODULE_DIR / "output" / "captions"
AUDIO_FORMAT = "mp3"

# Reason: per-digest pool of the 8 "caption keyword" entries (the bold column
# in documents/m0-digests.md). Transcribed once by hand — markdown parsing is
# out of scope for this spike (same decision as digests_input.py). The pool is
# best-effort: cut-1 headline-card words (e.g. "IRAN") rarely appear in the
# spoken script and simply don't match; sentences with no hit fall back to the
# longest-content-word rule in forced_alignment._choose_highlight_index.
CAPTION_KEYWORD_POOLS: dict[str, list[str]] = {
    "digest-1": [
        "Iran",
        "target",
        "close",
        "satisfied",
        "Hormuz",
        "oil",
        "defying",
        "wins",
    ],
    "digest-2": [
        "owner",
        "part-owner",
        "watching",
        "light rail",
        "baseball",
        "club",
        "wraps",
        "buying",
    ],
    "digest-3": [
        "151",
        "resistance",
        "cold",
        "1993",
        "pushed",
        "pressure",
        "grids",
        "moved",
    ],
    "digest-4": [
        "earnings",
        "billion",
        "data-center",
        "beat",
        "guided",
        "buyback",
        "slipped",
        "perfection",
    ],
    "digest-5": [
        "warnings",
        "down",
        "safeguards",
        "misinformation",
        "weapons",
        "CEOs",
        "cybersecurity",
        "ahead",
    ],
}

# Reason: SP1 measured ~1.07s of trailing padded silence on digest-2 (real
# speech ends ~44.93s) — silencedetect can't isolate it acoustically (the mp3
# tail is continuous low-level codec noise), so we apply SP1's measured fact as
# a documented per-digest correction. All other digests use the full duration
# (SP1 + our ffprobe confirmed no meaningful trailing silence on them).
TRAILING_SILENCE_S: dict[str, float] = {
    "digest-2": 1.07,
}


def probe_audio_duration_s(audio_path: Path) -> float:
    """Return the true duration (seconds) of an audio file via ffprobe.

    Args:
        audio_path: Path to the audio file to probe.

    Returns:
        Duration in seconds (float).

    Raises:
        FileNotFoundError: If the audio file does not exist.
        RuntimeError: If ffprobe fails or returns an unparseable duration.

    Example:
        >>> probe_audio_duration_s(Path("agents/m0/output/audio/digest-1.mp3"))
        50.610975
    """
    if not audio_path.exists():
        logger.error(
            "audio_file_missing",
            audio_path=str(audio_path),
            fix_suggestion="Run `python -m agents.m0.render_audio` first to produce the mp3s",
        )
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        logger.error(
            "ffprobe_failed",
            audio_path=str(audio_path),
            returncode=completed.returncode,
            stderr=completed.stderr.strip(),
            fix_suggestion="Confirm ffprobe (ffmpeg) is installed and the file is valid audio",
        )
        raise RuntimeError(
            f"ffprobe failed for {audio_path}: {completed.stderr.strip()}"
        )

    raw_duration = completed.stdout.strip()
    try:
        return float(raw_duration)
    except ValueError as exc:
        logger.error(
            "ffprobe_duration_unparseable",
            audio_path=str(audio_path),
            raw_value=raw_duration,
            fix_suggestion="ffprobe returned a non-numeric duration; re-render the audio file",
        )
        raise RuntimeError(
            f"Unparseable ffprobe duration {raw_duration!r} for {audio_path}"
        ) from exc


def build_caption_track(digest: Digest) -> CaptionTrack:
    """Build the caption track for one digest from its transcript + real audio.

    Args:
        digest: The digest whose turns to align.

    Returns:
        A validated :class:`CaptionTrack`.

    Example:
        >>> from agents.m0.digests_input import get_digest_by_id
        >>> track = build_caption_track(get_digest_by_id("digest-1"))
        >>> track.digest_id
        'digest-1'
    """
    audio_path = INPUT_AUDIO_DIR / f"{digest.digest_id}.{AUDIO_FORMAT}"
    audio_duration_s = probe_audio_duration_s(audio_path)

    # Reason: join turns with a space (sentence boundaries come from terminal
    # punctuation already in the text, not from turn boundaries).
    transcript_paragraph = " ".join(turn.text for turn in digest.turns)
    sentences = split_transcript_into_sentences(transcript_paragraph)

    trailing_silence_s = TRAILING_SILENCE_S.get(digest.digest_id, 0.0)
    speech_end_s = audio_duration_s - trailing_silence_s
    keyword_pool = CAPTION_KEYWORD_POOLS.get(digest.digest_id, [])

    track = align_transcript_to_audio(
        digest_id=digest.digest_id,
        sentences=sentences,
        audio_duration_s=audio_duration_s,
        preferred_keywords=keyword_pool,
        speech_end_s=speech_end_s,
    )

    logger.info(
        "caption_track_built",
        digest_id=digest.digest_id,
        sentence_count=track.sentence_count,
        word_count=len(track.words),
        trailing_silence_s=trailing_silence_s,
    )
    return track


def write_caption_track(track: CaptionTrack) -> Path:
    """Write a caption track to its JSON file and return the path.

    Args:
        track: The caption track to serialize.

    Returns:
        The path the JSON was written to.
    """
    OUTPUT_CAPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_CAPTIONS_DIR / f"{track.digest_id}.captions.json"
    # Reason: pydantic v2 model_dump_json gives a stable, typed serialization.
    output_path.write_text(
        json.dumps(track.model_dump(), indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "caption_track_written",
        digest_id=track.digest_id,
        output_path=str(output_path),
        word_count=len(track.words),
    )
    return output_path


def align_all_digests() -> list[Path]:
    """Build and write caption tracks for every digest in ``DIGESTS``.

    Returns:
        One JSON output path per digest, in DIGESTS order.
    """
    output_paths: list[Path] = []
    for digest in DIGESTS:
        track = build_caption_track(digest)
        output_paths.append(write_caption_track(track))

    logger.info(
        "all_caption_tracks_written",
        digest_count=len(output_paths),
        output_paths=[str(path) for path in output_paths],
    )
    return output_paths


def main() -> None:
    """Module entry point: align and write all 5 caption tracks."""
    align_all_digests()


if __name__ == "__main__":
    main()
