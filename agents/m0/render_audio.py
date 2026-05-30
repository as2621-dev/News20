"""M0 driver: render all 5 digests' real anchor-duo audio via Gemini TTS.

Run as a module:

    python -m agents.m0.render_audio

For each digest in ``agents.m0.digests_input.DIGESTS`` it:
  1. renders the ALEX/JORDAN turns through Gemini multi-speaker TTS
     (ALEX -> Leda, JORDAN -> Sadaltager), chunked under the byte budget;
  2. assembles the rendered chunks with inter-speaker gaps;
  3. writes ``agents/m0/output/audio/digest-{1..5}.mp3`` (best-effort
     FFmpeg loudness-normalized).

This is a REAL render — it makes live Gemini TTS calls. The API key is read
from ``.env`` via ``Settings`` and is NEVER logged.

Sub-phase 2 consumes the per-digest total duration (logged as
``digest_audio_rendered.duration_seconds``) and the on-disk audio files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agents.m0.digests_input import DIGESTS, Digest
from agents.shared.logger import get_logger
from agents.voice.audio import export_digest_audio
from agents.voice.gemini_tts import GeminiTTSClient, render_full_dialogue
from agents.voice.models import AssembledEpisode

logger = get_logger("m0.render_audio")

# Reason: resolve output dir relative to this module so the driver works
# regardless of the current working directory.
OUTPUT_AUDIO_DIR: Path = Path(__file__).parent / "output" / "audio"
AUDIO_FORMAT = "mp3"


async def render_digest_audio(
    digest: Digest, tts_client: GeminiTTSClient
) -> AssembledEpisode:
    """Render and write one digest's assembled audio file.

    Args:
        digest: The digest whose ALEX/JORDAN turns to synthesize.
        tts_client: Initialized GeminiTTSClient (real Gemini calls).

    Returns:
        AssembledEpisode metadata (path, total duration, per-segment timings).
    """
    logger.info(
        "digest_audio_render_started",
        digest_id=digest.digest_id,
        turn_count=len(digest.turns),
    )

    segments, speakers, turn_indices = await render_full_dialogue(
        turns=digest.turns,
        tts_client=tts_client,
    )

    output_path = OUTPUT_AUDIO_DIR / f"{digest.digest_id}.{AUDIO_FORMAT}"
    episode = export_digest_audio(
        speech_segments=segments,
        speakers=speakers,
        output_path=str(output_path),
        episode_title=digest.digest_headline,
        turn_indices=turn_indices,
    )

    logger.info(
        "digest_audio_rendered",
        digest_id=digest.digest_id,
        audio_path=episode.audio_path,
        duration_seconds=round(episode.duration_ms / 1000, 1),
        segment_count=episode.segment_count,
    )
    return episode


async def render_all_digests() -> list[AssembledEpisode]:
    """Render every digest in ``DIGESTS`` to its audio file (sequentially).

    Sequential rather than concurrent to stay friendly to Gemini TTS rate
    limits for a 5-item spike.

    Returns:
        One AssembledEpisode per digest, in DIGESTS order.
    """
    OUTPUT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    tts_client = GeminiTTSClient()

    episodes: list[AssembledEpisode] = []
    for digest in DIGESTS:
        episode = await render_digest_audio(digest, tts_client)
        episodes.append(episode)

    logger.info(
        "all_digests_rendered",
        digest_count=len(episodes),
        durations_seconds=[round(e.duration_ms / 1000, 1) for e in episodes],
    )
    return episodes


def main() -> None:
    """Module entry point: render all 5 digests' audio."""
    episodes = asyncio.run(render_all_digests())
    for episode in episodes:
        logger.info(
            "digest_audio_summary",
            audio_path=episode.audio_path,
            duration_seconds=round(episode.duration_ms / 1000, 1),
        )


if __name__ == "__main__":
    main()
