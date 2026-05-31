"""Stage: single-source verification — the hallucination guardrail (Phase 1d SP2).

PORTED from the TLDW donor (`agents/pipeline/stages/verification.py`) and
retargeted to News20's locked guardrail (Decision #5). The donor grounds a
multi-story briefing's claims against many sources **plus the Google Search
tool**, then runs a cleanup-edit pass and persists to Supabase. News20 grounds
**in-context** against the ONE source article a digest was built from (memory:
news20-qa-incontext-grounding) — no web search, no multi-source corpus, no
Supabase write. A digest is **grounded** only when the source supports every
claim; any UNSUPPORTED or CONTRADICTED claim blocks publishing.

What is kept from the donor: extract-every-claim → classify-each → count the bad
ones → halt. What is dropped: Google-Search grounding, the cleanup/edit pass,
the NEEDS_HEDGE status, and all Supabase I/O.

The Gemini call is mocked at the ``LLMClient`` boundary in every test — no live
call, no cost.

Input:  a ``DigestScript`` (SP2 scripting) + the source ``CanonicalStory``
Output: a ``VerificationReport`` (raises ``VerificationHaltError`` when blocking)

Example:
    >>> from agents.pipeline.stages.verification import run_single_source_verification
    >>> report = await run_single_source_verification(
    ...     script=digest_script, source_story=canonical_story, llm_client=client,
    ... )
    >>> report.is_grounded
    True
"""

from __future__ import annotations

import time
from typing import Any

from agents.ingestion.models import CanonicalStory
from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.models import ClaimVerification, DigestScript, VerificationReport
from agents.pipeline.prompts import DIGEST_VERIFICATION_PROMPT
from agents.shared.exceptions import PipelineStageError, VerificationHaltError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.verification")

# Reason: low temperature keeps claim classification stable/deterministic —
# ported from the donor's verification temperature.
VERIFICATION_TEMPERATURE = 0.2

_VALID_STATUSES = {"SUPPORTED", "UNSUPPORTED", "CONTRADICTED"}
_UNGROUNDED_STATUSES = {"UNSUPPORTED", "CONTRADICTED"}


def _format_script_text(script: DigestScript) -> str:
    """Render the digest script as plain ``SPEAKER: text`` lines for the prompt.

    Args:
        script: The digest script to fact-check.

    Returns:
        One ``ALEX: ...`` / ``JORDAN: ...`` line per turn, newline-joined.
    """
    return "\n".join(f"{turn.speaker}: {turn.text}" for turn in script.turns)


def _parse_verification_response(parsed: dict[str, Any]) -> list[ClaimVerification]:
    """Parse the verification LLM response object into ClaimVerification models.

    Unknown/garbled statuses are conservatively coerced to UNSUPPORTED — the
    guardrail must fail safe (an unparseable verdict must never read as
    SUPPORTED and let a hallucination through).

    Args:
        parsed: The parsed JSON object (expects a ``claims`` array).

    Returns:
        The validated claim list (possibly empty if the model returned none).
    """
    raw_claims = parsed.get("claims", []) or []
    claims: list[ClaimVerification] = []
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            continue
        status_raw = str(raw_claim.get("status", "")).strip().upper()
        # Reason: fail safe — anything not explicitly a known status is UNSUPPORTED.
        status = status_raw if status_raw in _VALID_STATUSES else "UNSUPPORTED"
        claim_text = str(
            raw_claim.get("claim", raw_claim.get("claim_text", ""))
        ).strip()
        if not claim_text:
            continue
        claims.append(
            ClaimVerification(
                claim_text=claim_text,
                status=status,  # type: ignore[arg-type]
                source_evidence=str(raw_claim.get("source_evidence", "")).strip(),
            )
        )
    return claims


async def run_single_source_verification(
    script: DigestScript,
    source_story: CanonicalStory,
    llm_client: LLMClient,
    *,
    raise_on_ungrounded: bool = True,
) -> VerificationReport:
    """Verify a digest script's claims against its single source article.

    Steps: render the script as plain lines → ask Gemini to classify every claim
    against the source body (in-context, no web search) → mark the digest
    ungrounded if ANY claim is UNSUPPORTED or CONTRADICTED.

    Args:
        script: The digest script from the scripting stage.
        source_story: The single canonical story the script was built from; its
            ``canonical_body_text`` is the ONLY grounding corpus.
        llm_client: An initialized ``LLMClient`` (mocked in tests).
        raise_on_ungrounded: When True (default), raise ``VerificationHaltError``
            if the digest is not grounded — the orchestrator (SP3) catches this
            to skip the story. When False, return the report with
            ``is_grounded=False`` for the caller to inspect.

    Returns:
        A :class:`VerificationReport` with every claim's grounding verdict.

    Raises:
        PipelineStageError: If the source has no body text, or the model returns
            a non-object response.
        VerificationHaltError: If the digest is ungrounded and
            ``raise_on_ungrounded`` is True.

    Example:
        >>> report = await run_single_source_verification(
        ...     script=digest_script, source_story=story, llm_client=client,
        ...     raise_on_ungrounded=False,
        ... )
        >>> report.is_grounded
        True
    """
    source_body = (source_story.canonical_body_text or "").strip()
    if not source_body:
        raise PipelineStageError(
            stage="verification",
            message="Source story has no body text to verify claims against",
            fix_suggestion="Ensure SP1 extracted canonical_body_text before verification",
        )

    start_time = time.monotonic()
    logger.info(
        "verification_stage_started",
        story_id=script.digest_story_id,
        turn_count=len(script.turns),
        source_chars=len(source_body),
    )

    script_text = _format_script_text(script)
    system_prompt = DIGEST_VERIFICATION_PROMPT.replace(
        "{SOURCE_BODY}", source_body
    ).replace("{SCRIPT_TEXT}", script_text)
    user_prompt = (
        "Fact-check every claim in DIGEST_SCRIPT against SOURCE_ARTICLE only. "
        "Output ONLY the JSON object with the 'claims' array."
    )

    raw_response = await llm_client.call_gemini(
        prompt=user_prompt,
        system=system_prompt,
        temperature=VERIFICATION_TEMPERATURE,
    )

    parsed = extract_json_from_llm_response(raw_response, stage="verification")
    if not isinstance(parsed, dict):
        raise PipelineStageError(
            stage="verification",
            message="Verification LLM response is not a JSON object",
            fix_suggestion="Model returned non-object output — tighten the verification prompt.",
        )

    claims = _parse_verification_response(parsed)
    ungrounded_count = sum(1 for c in claims if c.status in _UNGROUNDED_STATUSES)
    unsupported_count = sum(1 for c in claims if c.status == "UNSUPPORTED")
    contradicted_count = sum(1 for c in claims if c.status == "CONTRADICTED")
    is_grounded = ungrounded_count == 0

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "verification_stage_completed",
        story_id=script.digest_story_id,
        total_claims=len(claims),
        supported=sum(1 for c in claims if c.status == "SUPPORTED"),
        unsupported=unsupported_count,
        contradicted=contradicted_count,
        is_grounded=is_grounded,
        elapsed_ms=elapsed_ms,
    )

    report = VerificationReport(
        digest_story_id=script.digest_story_id,
        claims=claims,
        is_grounded=is_grounded,
        ungrounded_claim_count=ungrounded_count,
    )

    if not is_grounded and raise_on_ungrounded:
        logger.error(
            "verification_halt_triggered",
            story_id=script.digest_story_id,
            unsupported_count=unsupported_count,
            contradicted_count=contradicted_count,
            fix_suggestion="Digest makes claims the single source does not support; not published.",
        )
        raise VerificationHaltError(
            unsupported_count=unsupported_count,
            contradicted_count=contradicted_count,
        )

    return report
