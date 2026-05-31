"""Orchestrator (Phase 1d SP3): turn ONE gated story into a persisted digest.

ADAPTED from the TLDW donor (`agents/pipeline/orchestrator.py`). The donor was a
4-stage Supabase state machine (ranking → scripting → verification → tts_handoff)
that persisted ``briefing_jobs`` state between stages. News20's per-story job is
linear and reuses the locked M0 modules for the heavy steps; it chains:

    script (SP2 reuse)   → run_single_source_scripting
    verify (SP2 reuse)   → run_single_source_verification  (HALT → skip, never publish)
    TTS    (M0 reuse)    → agents.voice.gemini_tts.render_full_dialogue + audio.export
    caption (M0 reuse)   → agents.pipeline.stages.forced_alignment.align_transcript_to_audio
    poster (M0 reuse)    → agents.m0.build_poster_from_news.build_poster_for_digest
    persist (SP3 NEW)    → agents.pipeline.persist.persist_digest

The verification guardrail (Decision #5) is the hard gate: a ``VerificationHaltError``
is caught here and the story is SKIPPED (logged, never persisted) — an ungrounded
digest must never publish.

All heavy clients (LLM, TTS, supabase, poster genai client) are INJECTED so the
orchestrator is testable with mocks and the live e2e wires the real ones.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydub import AudioSegment

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.models import DigestScript
from agents.pipeline.persist import PersistResult, make_story_id, persist_digest
from agents.pipeline.stages.forced_alignment import (
    CaptionTrack,
    align_transcript_to_audio,
    split_transcript_into_sentences,
)
from agents.pipeline.stages.scripting import run_single_source_scripting
from agents.pipeline.stages.verification import run_single_source_verification
from agents.shared.exceptions import VerificationHaltError
from agents.shared.logger import get_logger
from agents.voice.audio import assemble_episode
from agents.voice.gemini_tts import GeminiTTSClient, render_full_dialogue
from agents.voice.models import DialogueTurn as VoiceDialogueTurn

logger = get_logger("pipeline.orchestrator")

# Reason: the assembled-audio export format the persist layer uploads. MP3 keeps
# the digest-audio object small for the client live-render.
AUDIO_EXPORT_FORMAT = "mp3"
AUDIO_EXPORT_BITRATE = "192k"


class OrchestratorResult(BaseModel):
    """The outcome of running the per-story orchestrator on one story.

    Attributes:
        story_id: The canonical story id the orchestrator ran on.
        published: True when the digest was grounded, rendered, and persisted.
        skip_reason: Why the story was skipped (e.g. ``verification_halt``), else "".
        persist_result: The persist audit record (None when not published).

    Example:
        >>> result = OrchestratorResult(story_id="s1", published=True)
        >>> result.published
        True
    """

    story_id: str = Field(..., description="The canonical story id processed")
    published: bool = Field(
        default=False, description="True when the digest was persisted"
    )
    skip_reason: str = Field(
        default="", description="Why the story was skipped, else empty"
    )
    persist_result: PersistResult | None = Field(
        default=None, description="The persist audit record when published"
    )


def _to_voice_turns(script: DigestScript) -> list[VoiceDialogueTurn]:
    """Convert pipeline ``DialogueTurn``s to the voice-module turn shape.

    Reason: the M0 TTS renderer (``agents.voice.gemini_tts``) consumes
    ``agents.voice.models.DialogueTurn``; the SP2 script carries the pipeline
    ``DialogueTurn``. Same fields (speaker/text); convert at the boundary rather
    than couple the two modules.
    """
    return [
        VoiceDialogueTurn(speaker=turn.speaker, text=turn.text) for turn in script.turns
    ]


async def render_audio_bytes(
    script: DigestScript,
    tts_client: GeminiTTSClient,
) -> tuple[bytes, int]:
    """Render a script to assembled MP3 audio bytes + duration via M0 TTS.

    Reuses the M0 spine: ``render_full_dialogue`` (Gemini multi-speaker TTS) →
    ``assemble_episode`` (concatenate with inter-speaker gaps) → in-memory MP3
    export. Returns bytes (not a file path) so persist uploads them directly.

    Args:
        script: The grounded digest script.
        tts_client: An initialized ``GeminiTTSClient`` (mocked in tests).

    Returns:
        ``(mp3_bytes, duration_ms)``.

    Raises:
        TTSRenderError: When rendering fails (propagated from the M0 module).
    """
    voice_turns = _to_voice_turns(script)
    segments, speakers, turn_indices = await render_full_dialogue(
        turns=voice_turns,
        tts_client=tts_client,
    )
    assembled, _segment_timings = assemble_episode(
        speech_segments=segments,
        speakers=speakers,
        turn_indices=turn_indices,
    )
    buffer = io.BytesIO()
    assembled.export(buffer, format=AUDIO_EXPORT_FORMAT, bitrate=AUDIO_EXPORT_BITRATE)
    audio_bytes = buffer.getvalue()
    return audio_bytes, len(assembled)


def build_caption_track(
    script: DigestScript,
    audio_duration_ms: int,
) -> CaptionTrack:
    """Time-slice the (known) script transcript across the real audio duration.

    Reuses M0's offline transcript-time-slice forced alignment (Open-Q1: reuse
    the M0 time-slice path for M1). The transcript is the script turns joined;
    sentences are split with the M0 splitter; one highlight keyword per sentence
    is chosen deterministically (no preferred-keyword pool at M1).

    Args:
        script: The grounded digest script (its turns are the transcript).
        audio_duration_ms: The real assembled audio duration in ms.

    Returns:
        A :class:`CaptionTrack` (word timings + one highlight/sentence).
    """
    transcript = " ".join(turn.text for turn in script.turns)
    sentences = split_transcript_into_sentences(transcript)
    return align_transcript_to_audio(
        digest_id=script.digest_story_id,
        sentences=sentences,
        audio_duration_s=max(audio_duration_ms / 1000.0, 0.001),
    )


def _read_poster_bytes(poster_path: str | None) -> bytes | None:
    """Read the graded poster PNG bytes from disk (None if absent/unreadable)."""
    if not poster_path:
        return None
    path = Path(poster_path)
    if not path.is_file():
        logger.warning(
            "orchestrator_poster_missing",
            poster_path=poster_path,
            fix_suggestion="Poster generation reported a path that does not exist; persisting without a poster.",
        )
        return None
    return path.read_bytes()


def generate_poster_bytes(
    story: CanonicalStory,
    script: DigestScript,
    poster_genai_client: Any | None,
    poster_builder: Any | None = None,
) -> bytes | None:
    """Generate a poster for the story via the reused M0 poster pipeline.

    Reuses ``agents.m0.build_poster_from_news.build_poster_for_digest``, which
    takes an M0 ``Digest`` + a ``google.genai`` client and writes a graded PNG to
    disk; we read the bytes back. Poster failure is NON-fatal (the digest still
    publishes with audio + captions) — the reel renders an ambient wash and a
    missing poster degrades gracefully.

    Args:
        story: The canonical story (headline seeds the poster concept).
        script: The grounded script (its dialogue seeds the poster summary).
        poster_genai_client: A ``google.genai.Client`` (None disables posters).
        poster_builder: Injectable builder fn (defaults to the M0 entry); tests
            pass a stub returning a report with ``poster_path``.

    Returns:
        The graded poster PNG bytes, or None when disabled/failed.
    """
    if poster_genai_client is None:
        logger.info(
            "orchestrator_poster_skipped",
            story_id=story.canonical_story_id,
            reason="no_poster_client_injected",
        )
        return None

    # Reason: import + build the M0 Digest shape lazily so the orchestrator
    # imports cleanly without the M0 poster deps in non-poster runs.
    from agents.m0.build_poster_from_news import build_poster_for_digest
    from agents.m0.digests_input import Digest

    builder = poster_builder or build_poster_for_digest
    m0_digest = Digest(
        digest_id=story.canonical_story_id,
        digest_headline=story.canonical_title,
        digest_category="News",
        digest_source=story.canonical_primary_outlet_name
        or story.canonical_primary_outlet_domain,
        turns=_to_voice_turns(script),
    )
    try:
        report = builder(m0_digest, poster_genai_client)
    except Exception as exc:  # noqa: BLE001 — poster failure must not block publish
        logger.error(
            "orchestrator_poster_failed",
            story_id=story.canonical_story_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            fix_suggestion="Poster generation errored; publishing the digest without a poster.",
        )
        return None
    return _read_poster_bytes(getattr(report, "poster_path", None))


async def orchestrate_story(
    story: CanonicalStory,
    story_interest_tags: list[StoryInterestTag],
    llm_client: LLMClient,
    tts_client: GeminiTTSClient,
    supabase_client: Any,
    poster_genai_client: Any | None = None,
    poster_builder: Any | None = None,
    story_id: str | None = None,
    suggested_questions: list[str] | None = None,
) -> OrchestratorResult:
    """Run the full per-story pipeline: script → verify → TTS → caption → poster → persist.

    The verification guardrail is the hard gate: a ``VerificationHaltError`` is
    caught and the story is skipped (never persisted). Every other stage reuses a
    locked M0/SP2 module. All clients are injected.

    Args:
        story: The gated canonical story (must carry ``canonical_body_text``).
        story_interest_tags: The story's ``story_interests`` tag payloads (SP1).
        llm_client: Gemini text client (scripting + verification).
        tts_client: Gemini multi-speaker TTS client (audio).
        supabase_client: Service-role supabase client (persist).
        poster_genai_client: ``google.genai`` client for the poster (None to
            skip posters — the digest still publishes).
        poster_builder: Optional poster-builder override (tests inject a stub).
        story_id: Optional explicit ``stories.story_id`` (the live e2e passes a
            ``FIXTURE-SP3-`` id).
        suggested_questions: Optional suggested-question strings.

    Returns:
        An :class:`OrchestratorResult`. ``published`` is True only when the digest
        was grounded, rendered, and persisted.

    Raises:
        PipelineStageError / TTSRenderError: On non-verification stage failures
            (these are real errors, not the graceful verification skip).

    Example:
        >>> result = await orchestrate_story(story, tags, llm, tts, supabase)  # doctest: +SKIP
        >>> result.published
        True
    """
    start_time = time.monotonic()
    logger.info(
        "orchestrate_story_started",
        story_id=story.canonical_story_id,
        interest_tag_count=len(story_interest_tags),
    )

    # ── 1. Script (SP2) ──
    script = await run_single_source_scripting(story=story, llm_client=llm_client)

    # ── 2. Verify (SP2) — HALT on ungrounded; skip, never publish ──
    try:
        await run_single_source_verification(
            script=script, source_story=story, llm_client=llm_client
        )
    except VerificationHaltError as halt:
        logger.error(
            "orchestrate_story_verification_halt",
            story_id=story.canonical_story_id,
            unsupported_count=halt.unsupported_count,
            contradicted_count=halt.contradicted_count,
            fix_suggestion="Digest ungrounded vs its single source; skipped (never published).",
        )
        return OrchestratorResult(
            story_id=story.canonical_story_id,
            published=False,
            skip_reason="verification_halt",
        )

    # ── 3. TTS (M0) → audio bytes + duration ──
    audio_bytes, audio_duration_ms = await render_audio_bytes(script, tts_client)

    # ── 4. Caption timing (M0 forced alignment) ──
    caption_track = build_caption_track(script, audio_duration_ms)

    # ── 5. Poster (M0) — non-fatal ──
    poster_bytes = generate_poster_bytes(
        story=story,
        script=script,
        poster_genai_client=poster_genai_client,
        poster_builder=poster_builder,
    )

    # ── 6. Persist (SP3) ──
    persist_result = persist_digest(
        supabase_client=supabase_client,
        story=story,
        script=script,
        caption_track=caption_track,
        audio_bytes=audio_bytes,
        audio_duration_ms=audio_duration_ms,
        story_interest_tags=story_interest_tags,
        poster_bytes=poster_bytes,
        suggested_questions=suggested_questions,
        story_id=story_id,
    )

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "orchestrate_story_completed",
        story_id=story.canonical_story_id,
        persisted_story_id=persist_result.story_id,
        digest_id=persist_result.digest_id,
        published=True,
        elapsed_ms=elapsed_ms,
    )
    return OrchestratorResult(
        story_id=story.canonical_story_id,
        published=True,
        persist_result=persist_result,
    )


# Reason: re-export a couple of names the live e2e script + tests import from a
# single place (keeps their imports stable if internals move).
__all__ = [
    "OrchestratorResult",
    "orchestrate_story",
    "render_audio_bytes",
    "build_caption_track",
    "generate_poster_bytes",
    "make_story_id",
    "AudioSegment",
]
