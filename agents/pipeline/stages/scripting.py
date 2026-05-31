"""Stage: single-source dialogue scripting (Phase 1d SP2).

ADAPTED from the TLDW donor (`agents/pipeline/stages/scripting.py`). The donor
condenses a *list* of multi-source ``RankedStory`` items into a ~2050-word,
12-minute, multi-story briefing and writes it to Supabase. News20 inverts that
to the locked Decision #4 format: **one ``CanonicalStory`` → one ~140-word,
~55-second, two-host (ALEX/JORDAN) digest, constrained to that single source**.
No Supabase write here (SP2 is mock-only; persistence is SP3).

What is kept from the donor: the JSON-array ``{"speaker","text"}`` output
contract, the ALEX/JORDAN persona split, the unsafe-bracket-tag stripping, and
the word-count → estimated-duration metric. What is dropped: the multi-story
depth-target math, the SCRATCHPAD/contrast-angle layer, the XML parse path, and
all Supabase I/O.

The Gemini call is mocked at the ``LLMClient`` boundary in every test — no live
call, no cost.

Input:  a ``CanonicalStory`` (SP1) + an ``LLMClient``
Output: a ``DigestScript`` (ALEX/JORDAN turns, word count, est. duration)

Example:
    >>> from agents.pipeline.stages.scripting import run_single_source_scripting
    >>> script = await run_single_source_scripting(story=canonical_story, llm_client=client)
    >>> all(turn.speaker in ("ALEX", "JORDAN") for turn in script.turns)
    True
"""

from __future__ import annotations

import re
import time

from agents.ingestion.models import CanonicalStory
from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.models import DialogueTurn, DigestScript
from agents.pipeline.prompts import DIGEST_SCRIPTING_PROMPT
from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.scripting")

# Reason: words-per-minute calibrated to Gemini multi-speaker TTS (~170 WPM),
# ported from the donor's empirical measurement.
SPOKEN_WPM = 170

# Reason: News20's locked digest budget (Decision #4 / reference/reuse-map.md):
# ~50-55s of audio. At 170 WPM that is ~140 words; the ceiling caps padding.
TARGET_WORDS = 140
MAX_WORDS = 160
TARGET_SECONDS = 55
MIN_TURNS = 4
MAX_TURNS = 10

# Reason: the donor uses 0.7 for natural dialogue variety; kept.
SCRIPTING_TEMPERATURE = 0.7

# Reason: the single-source body fed to the writer is capped so a very long
# article doesn't blow the context budget; the lede carries the digest-worthy
# facts. Trafilatura bodies are typically well under this.
_MAX_SOURCE_BODY_CHARS = 8000

# Reason: bracket tags the prompt forbids but the model occasionally emits; we
# strip them before TTS sees the text. Ellipses are kept (safe pauses).
_BRACKET_TAG_REGEX = re.compile(
    r"\[(?:LAUGH|PAUSE(?:_[A-Z]+)?|EMPHASIS|SIGH|SOURCE_ADDED)\]",
    re.IGNORECASE,
)

_VALID_SPEAKERS = {"ALEX", "JORDAN"}


def _strip_unsafe_brackets(text: str) -> str:
    """Drop forbidden bracket tags the model occasionally emits; keep ellipses.

    Args:
        text: Raw turn text from the model.

    Returns:
        The text with ``[LAUGH]`` / ``[PAUSE]`` / ``[EMPHASIS]`` / ``[SIGH]`` /
        ``[SOURCE_ADDED]`` removed and collapsed double-spaces, trimmed.
    """
    cleaned = _BRACKET_TAG_REGEX.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _parse_json_dialogue(raw_text: str) -> list[DialogueTurn]:
    """Parse a JSON array of ``{"speaker", "text"}`` objects into DialogueTurn.

    The scripting prompt instructs Gemini to emit a single JSON array of
    ``{"speaker": "ALEX"|"JORDAN", "text": "..."}`` objects. Bracket tags are
    stripped defensively even though the prompt forbids them. Entries with an
    invalid speaker or empty text are skipped.

    Args:
        raw_text: Raw text response from the Gemini scripting call.

    Returns:
        Validated DialogueTurn models (ALEX/JORDAN only, non-empty text).

    Raises:
        PipelineStageError: If the response is not a JSON array, or yields no
            valid ALEX/JORDAN turns.
    """
    parsed = extract_json_from_llm_response(raw_text, stage="scripting")
    if not isinstance(parsed, list):
        raise PipelineStageError(
            stage="scripting",
            message="Scripting LLM response is not a JSON array",
            fix_suggestion="Model returned an object or prose instead of the required "
            "JSON array of turns — inspect the raw response.",
        )

    validated: list[DialogueTurn] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        speaker = str(entry.get("speaker", "")).strip().upper()
        text = _strip_unsafe_brackets(str(entry.get("text", "")))
        if speaker not in _VALID_SPEAKERS or not text:
            continue
        validated.append(DialogueTurn(speaker=speaker, text=text))  # type: ignore[arg-type]

    if not validated:
        raise PipelineStageError(
            stage="scripting",
            message="Scripting LLM emitted no valid ALEX/JORDAN turns",
            fix_suggestion="Re-run scripting; confirm the model honored the speaker/text "
            "contract. Raise temperature only if blank turns persist.",
        )

    speakers_present = {turn.speaker for turn in validated}
    if len(speakers_present) < 2:
        logger.warning(
            "scripting_single_speaker",
            speakers=sorted(speakers_present),
            fix_suggestion="Both ALEX and JORDAN should appear — check the persona contract.",
        )

    return validated


def _compute_script_metrics(turns: list[DialogueTurn]) -> tuple[int, int]:
    """Compute total word count and estimated spoken seconds for a digest.

    Args:
        turns: The dialogue turns.

    Returns:
        ``(total_words, estimated_seconds)`` at ``SPOKEN_WPM``.
    """
    total_words = sum(len(turn.text.split()) for turn in turns)
    estimated_seconds = int((total_words / SPOKEN_WPM) * 60)
    return total_words, estimated_seconds


def _build_system_prompt(story: CanonicalStory) -> str:
    """Fill the single-source scripting prompt with this story's source article.

    Args:
        story: The canonical story whose body/headline/outlet seed the prompt.

    Returns:
        The system prompt with every ``{PLACEHOLDER}`` substituted.
    """
    body = (story.canonical_body_text or "").strip()
    if len(body) > _MAX_SOURCE_BODY_CHARS:
        body = body[:_MAX_SOURCE_BODY_CHARS]
    published = story.canonical_published_utc.strftime("%B %d, %Y")
    outlet = (
        story.canonical_primary_outlet_name or story.canonical_primary_outlet_domain
    )

    return (
        DIGEST_SCRIPTING_PROMPT.replace("{TARGET_WORDS}", str(TARGET_WORDS))
        .replace("{MAX_WORDS}", str(MAX_WORDS))
        .replace("{TARGET_SECONDS}", str(TARGET_SECONDS))
        .replace("{MIN_TURNS}", str(MIN_TURNS))
        .replace("{MAX_TURNS}", str(MAX_TURNS))
        .replace("{SOURCE_HEADLINE}", story.canonical_title)
        .replace("{SOURCE_OUTLET}", outlet)
        .replace("{SOURCE_PUBLISHED}", published)
        .replace("{SOURCE_BODY}", body)
    )


async def run_single_source_scripting(
    story: CanonicalStory,
    llm_client: LLMClient,
) -> DigestScript:
    """Generate a single-source ALEX/JORDAN digest script for one canonical story.

    Single Gemini call. The story's ``canonical_body_text`` is the ONLY source of
    facts (the single-source constraint is enforced in the system prompt and
    re-checked by the verification stage). Output is a length-bounded,
    speaker-tagged :class:`DigestScript`.

    Args:
        story: The deduped canonical story to narrate. Must carry
            ``canonical_body_text`` (trafilatura body from SP1).
        llm_client: An initialized ``LLMClient`` (mocked in tests).

    Returns:
        A validated :class:`DigestScript` with ALEX/JORDAN turns, word count,
        and estimated duration.

    Raises:
        PipelineStageError: If the story has no body text, or the model returns
            no usable turns.

    Example:
        >>> script = await run_single_source_scripting(story=canonical_story, llm_client=client)
        >>> script.word_count > 0
        True
    """
    if not (story.canonical_body_text or "").strip():
        raise PipelineStageError(
            stage="scripting",
            message="Canonical story has no body text to script from",
            fix_suggestion="Ensure SP1 extracted canonical_body_text (trafilatura) before scripting",
        )

    start_time = time.monotonic()
    logger.info(
        "scripting_stage_started",
        story_id=story.canonical_story_id,
        source_outlet=story.canonical_primary_outlet_domain,
        body_chars=len(story.canonical_body_text or ""),
    )

    system_prompt = _build_system_prompt(story)
    user_prompt = (
        "Write the single-source digest now. Use ONLY the SOURCE_ARTICLE. "
        'Output ONLY a JSON array of {"speaker", "text"} turn objects.'
    )

    raw_response = await llm_client.call_gemini(
        prompt=user_prompt,
        system=system_prompt,
        temperature=SCRIPTING_TEMPERATURE,
    )

    turns = _parse_json_dialogue(raw_response)
    word_count, estimated_duration = _compute_script_metrics(turns)

    # Reason: over-budget scripts risk both a long reel and hallucinated padding;
    # log loudly so SP3 can decide to regenerate. We do NOT silently truncate —
    # that could cut a turn mid-sentence and break the caption alignment (SP3).
    if word_count > MAX_WORDS:
        logger.warning(
            "scripting_over_word_budget",
            story_id=story.canonical_story_id,
            word_count=word_count,
            max_words=MAX_WORDS,
            fix_suggestion="Digest exceeded the ~55s budget; SP3 may regenerate with a tighten nudge.",
        )

    script = DigestScript(
        digest_story_id=story.canonical_story_id,
        turns=turns,
        word_count=word_count,
        estimated_duration_seconds=estimated_duration,
        source_url=story.canonical_url,
    )

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "scripting_stage_completed",
        story_id=story.canonical_story_id,
        turn_count=len(turns),
        word_count=word_count,
        estimated_duration_seconds=estimated_duration,
        elapsed_ms=elapsed_ms,
    )
    return script
