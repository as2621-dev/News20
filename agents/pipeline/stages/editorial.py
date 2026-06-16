"""Stage: editorial rewrite of the displayed headline + long-form body.

News outlets ship curiosity-gap, question-style headlines and dense prose. News20
republishes neither verbatim. This stage rewrites BOTH the displayed headline and
the long-form article body in our own words — declarative, plain, concise — while
preserving every fact exactly (the single-source rule).

It runs AFTER scripting + verification, which ground the SPOKEN digest against the
ORIGINAL source, so paraphrasing here never weakens the audio's grounding. The
returned headline/body replace ``story.canonical_title`` + ``canonical_body_text``
for the poster concept and the persisted feed headline + ``detail_chunks`` body.

Best-effort: on ANY failure the function returns ``None`` and the caller keeps the
original source headline + body (Rule 12 — fail safe, logged loudly). Mocked at the
``LLMClient`` boundary in tests — no live call, no cost.

Input:  a ``CanonicalStory`` (post-verification) + an ``LLMClient``
Output: an ``EditorialRewrite`` (rewritten headline + body), or ``None`` on failure
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory
from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.prompts import EDITORIAL_REWRITE_PROMPT
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.editorial")

# Reason: faithful paraphrase, not creative writing — keep it low + stable so facts
# are preserved and the wording stays plain.
EDITORIAL_TEMPERATURE = 0.3

# Reason: mirror the scripting stage's source-body cap so a very long article does
# not blow the context budget; the lede carries the substance.
_MAX_SOURCE_BODY_CHARS = 8000


class EditorialRewrite(BaseModel):
    """The rewritten, republish-safe headline + long-form body for one story."""

    headline: str = Field(..., description="Clear, concise, declarative headline (never a question)")
    body: str = Field(..., description="Reworded long-form body; paragraphs separated by blank lines")


def _build_rewrite_prompt(story: CanonicalStory) -> str:
    """Fill the editorial-rewrite prompt with this story's original source article.

    Args:
        story: The canonical story whose original headline/body/outlet seed the prompt.

    Returns:
        The prompt with every ``{PLACEHOLDER}`` substituted.
    """
    body = (story.canonical_body_text or "").strip()
    if len(body) > _MAX_SOURCE_BODY_CHARS:
        body = body[:_MAX_SOURCE_BODY_CHARS]
    published = story.canonical_published_utc.strftime("%B %d, %Y")
    outlet = story.canonical_primary_outlet_name or story.canonical_primary_outlet_domain
    return (
        EDITORIAL_REWRITE_PROMPT.replace("{SOURCE_HEADLINE}", story.canonical_title)
        .replace("{SOURCE_OUTLET}", outlet)
        .replace("{SOURCE_PUBLISHED}", published)
        .replace("{SOURCE_BODY}", body)
    )


async def run_editorial_rewrite(story: CanonicalStory, llm_client: LLMClient) -> EditorialRewrite | None:
    """Rewrite the headline + long-form body in News20's own words.

    Single Gemini call. Preserves every fact (single-source rule), produces a
    declarative non-question headline and clear concise prose. Returns ``None`` on
    any failure so the caller falls back to the original source headline + body.

    Args:
        story: The verified canonical story (carries the original headline + body).
        llm_client: An initialized ``LLMClient`` (mocked in tests).

    Returns:
        An :class:`EditorialRewrite`, or ``None`` if the story has no body or the
        model output was unusable.

    Example:
        >>> rewrite = await run_editorial_rewrite(story, llm_client)  # doctest: +SKIP
        >>> rewrite.headline.endswith("?")
        False
    """
    if not (story.canonical_body_text or "").strip():
        return None

    start_time = time.monotonic()
    system_prompt = _build_rewrite_prompt(story)
    try:
        raw_response = await llm_client.call_gemini(
            prompt='Rewrite now. Output ONLY the JSON object with "headline" and "body".',
            system=system_prompt,
            temperature=EDITORIAL_TEMPERATURE,
        )
        parsed = extract_json_from_llm_response(raw_response, stage="editorial")
        if not isinstance(parsed, dict):
            raise ValueError("editorial rewrite response is not a JSON object")
        headline = str(parsed.get("headline") or "").strip()
        body = str(parsed.get("body") or "").strip()
        if not headline or not body:
            raise ValueError("editorial rewrite missing headline or body")
        # Reason: belt-and-braces — a stray trailing '?' would reintroduce the
        # question style the prompt forbids; strip it (keep the statement).
        headline = headline.rstrip("? ").strip() or headline

        rewrite = EditorialRewrite(headline=headline, body=body)
        logger.info(
            "editorial_rewrite_completed",
            story_id=story.canonical_story_id,
            headline=headline,
            body_chars=len(body),
            elapsed_ms=int((time.monotonic() - start_time) * 1000),
        )
        return rewrite
    except Exception as exc:  # noqa: BLE001 — best-effort; keep original on any failure.
        logger.error(
            "editorial_rewrite_failed",
            story_id=story.canonical_story_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            fix_suggestion="Keeping the original source headline + body for this story.",
        )
        return None
