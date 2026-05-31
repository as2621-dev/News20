"""Answer-vs-corpus verifier — the Q&A hallucination guardrail (Phase 2b SP2).

MODELED ON ``agents/pipeline/stages/verification.py`` (Phase 1d SP2) but with a
DIFFERENT shape, so it is a NEW file rather than an edit of the pipeline stage
(that file is a sibling agent's territory; this one consumes a free-text *answer*
+ the per-story context block, not a multi-host ``DigestScript`` + a
``CanonicalStory``). Decision recorded for the report: **new file, not import.**

Pattern kept from the donor stage: ask Gemini to grade against the source text
ONLY (in-context, no web search), then fail SAFE — any non-``supported`` verdict,
an empty/garbled response, or an LLM error downgrades the answer to ungrounded.
This is the SECOND line of the guardrail: even if the answerer produced a
plausible-but-ungrounded answer, this gate strips it before it is surfaced as
grounded (Rule 9, zero-tolerance accuracy; an ungrounded answer must NEVER be
surfaced as grounded).

The Gemini call is mocked at the ``LLMClient`` boundary in every test — no live
call, no cost (CLAUDE.md mocking mandate).
"""

from __future__ import annotations

from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.qa.prompts import ANSWER_VERIFICATION_PROMPT
from agents.shared.logger import get_logger

logger = get_logger("qa.verification")

# Reason: low temperature keeps the grounded/ungrounded verdict stable and
# deterministic — same value the pipeline verification stage uses.
ANSWER_VERIFICATION_TEMPERATURE = 0.2

# Reason: the ONLY verdict that lets an answer through as grounded. Anything else
# (including an unparseable verdict) fails safe to ungrounded.
_SUPPORTED_VERDICT = "supported"


async def verify_answer_against_corpus(
    answer_text: str,
    context_block: str,
    llm_client: LLMClient,
) -> bool:
    """Re-grade a generated answer against the per-story context block.

    Asks Gemini whether the CONTEXT supports the ANSWER (in-context, no web
    search), then returns ``True`` ONLY for an explicit ``supported`` verdict.
    Fails safe: an empty answer, an empty/garbled LLM response, a non-object
    response, or any LLM error all return ``False`` so an ungrounded answer can
    never be surfaced as grounded (Rule 9).

    Args:
        answer_text: The answer the grounded answerer produced (the text to
            audit). An empty/whitespace answer is trivially not grounded.
        context_block: The rendered per-story context block
            (``GroundingCorpus.render_context_block()``) — the ONLY ground truth
            the answer is verified against.
        llm_client: An initialized ``LLMClient`` (mocked in tests).

    Returns:
        ``True`` only when the model returns an explicit ``supported`` verdict;
        ``False`` for every other outcome (fail safe).

    Example:
        >>> grounded = await verify_answer_against_corpus(
        ...     answer_text="The strait carries a fifth of the world's oil.",
        ...     context_block="[detail_chunk:0] The strait carries a fifth of the world's oil.",
        ...     llm_client=client,
        ... )  # doctest: +SKIP
        >>> grounded
        True
    """
    if not answer_text.strip():
        # Reason: nothing to verify → not grounded (no fabricated answer).
        return False

    system_prompt = ANSWER_VERIFICATION_PROMPT.replace(
        "{CONTEXT_BLOCK}", context_block
    ).replace("{ANSWER_TEXT}", answer_text)
    user_prompt = (
        "Audit the ANSWER against the CONTEXT only. "
        "Output ONLY the JSON object with 'verdict' and 'evidence'."
    )

    try:
        raw_response = await llm_client.call_gemini(
            prompt=user_prompt,
            system=system_prompt,
            temperature=ANSWER_VERIFICATION_TEMPERATURE,
        )
        parsed = extract_json_from_llm_response(raw_response, stage="qa_verification")
    except Exception as exc:  # noqa: BLE001 — fail safe (incl. PipelineStageError)
        # Reason: the guardrail must fail SAFE — if verification cannot run, the
        # answer is NOT grounded rather than trusted by default (Rule 12 + Rule 9).
        logger.error(
            "answer_verification_failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:200],
            fix_suggestion="Verification could not run; answer downgraded to refusal (fail safe)",
        )
        return False

    if not isinstance(parsed, dict):
        logger.warning(
            "answer_verification_non_object",
            fix_suggestion="Model returned non-object output; treating answer as ungrounded",
        )
        return False

    verdict = str(parsed.get("verdict", "")).strip().lower()
    is_supported = verdict == _SUPPORTED_VERDICT
    logger.info(
        "answer_verification_completed",
        verdict=verdict or "(empty)",
        is_supported=is_supported,
    )
    return is_supported
