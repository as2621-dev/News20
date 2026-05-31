"""Grounded Q&A answerer — load corpus → answer in-context → verify (Phase 2b SP2).

ADAPTED from the TLDW donor ``agents/chat/*`` but grounded on the **in-context
per-story corpus** (SP1's :func:`load_grounding_corpus`), not a vector retriever
(the re-scope dropped Pinecone — ``plans/phase-2b-m2-grounded-interrogation.md``).

Flow (``answer_question``):
    1. Load the story's whole grounding corpus (SP1) — caller injects it (the
       endpoint loads + caches it; tests pass a built one).
    2. Ask Gemini to answer ONLY from that corpus's context block, citing the
       passage ids it used (``GROUNDED_ANSWER_PROMPT`` forbids answering without
       the provided context — prototype-port-map.md §7).
    3. Parse the answer + cited passage ids. Off-topic / unsupported / empty →
       refuse with NO fabricated answer.
    4. Re-verify the answer against the corpus (``agents/qa/verification.py``).
       If verification fails, DOWNGRADE to a refusal — an ungrounded answer is
       never surfaced as grounded (Rule 9, zero-tolerance accuracy).
    5. Map the cited passage ids → :class:`AnswerCitation` chips (outlet + URL),
       tracing each citation to that story's ``story_sources`` / corpus passages.

Returns the typed :class:`QuestionAnswer` (``api-contracts.md``). The Gemini call
is mocked at the ``LLMClient`` boundary in every test — no live call, no cost.
"""

from __future__ import annotations

import time
from typing import Any

from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.qa.models import AnswerCitation, GroundingCorpus, QuestionAnswer
from agents.qa.prompts import GROUNDED_ANSWER_PROMPT, REFUSAL_ANSWER_TEXT
from agents.qa.verification import verify_answer_against_corpus
from agents.shared.logger import get_logger

logger = get_logger("qa.agent")

# Reason: a low temperature keeps grounded answers faithful to the corpus and
# the refusal behaviour stable — mirrors the pipeline scripting/verification temps.
ANSWER_TEMPERATURE = 0.2


def build_refusal_answer() -> QuestionAnswer:
    """Build the canonical refusal answer (ungrounded, no citations, no answer).

    The single source of truth for the refusal payload so every refusal branch
    (off-topic, unsupported, verification-failed, corpus error) returns a
    BYTE-IDENTICAL :class:`QuestionAnswer` the UI maps to the
    ``⌀ CAN'T ANSWER FROM SOURCE`` card (Decision #5).

    Returns:
        A refusal :class:`QuestionAnswer` (``answer_is_grounded=False``, fixed
        refusal copy, empty citations).

    Example:
        >>> refusal = build_refusal_answer()
        >>> refusal.answer_is_grounded
        False
        >>> refusal.answer_citations
        []
    """
    return QuestionAnswer(
        answer_text=REFUSAL_ANSWER_TEXT,
        answer_citations=[],
        answer_is_grounded=False,
    )


def _map_citations(
    cited_passage_ids: list[str], corpus: GroundingCorpus
) -> list[AnswerCitation]:
    """Map the model's cited passage ids → :class:`AnswerCitation` chips.

    For each cited passage id that exists in the corpus, resolve the outlet it is
    attributed to (``GroundingPassage.source_outlet_name``) and that outlet's
    article URL from the corpus's ``citation_targets`` — so every citation traces
    to that story's ``story_sources`` / corpus passages (provenance, Rule 9).
    Unknown ids the model hallucinated are dropped (they cannot be grounded).

    Args:
        cited_passage_ids: The passage ids the answer claims to cite.
        corpus: The grounding corpus (passage + citation-target source of truth).

    Returns:
        Deduplicated citations in first-cited order (empty when no cited id maps
        to a real passage).
    """
    passage_by_id = {p.passage_id: p for p in corpus.passages}
    url_by_outlet = {
        target.source_outlet_name: target.source_article_url
        for target in corpus.citation_targets
    }

    citations: list[AnswerCitation] = []
    seen_passage_ids: set[str] = set()
    for passage_id in cited_passage_ids:
        passage = passage_by_id.get(passage_id)
        if passage is None or passage_id in seen_passage_ids:
            # Reason: a cited id with no real passage cannot be grounded — drop it
            # rather than emit a citation that traces to nothing (Rule 9).
            continue
        seen_passage_ids.add(passage_id)
        outlet_name = passage.source_outlet_name
        citations.append(
            AnswerCitation(
                source_url=url_by_outlet.get(outlet_name) if outlet_name else None,
                source_quote=passage.passage_text[:240],
                source_outlet_name=outlet_name,
                passage_id=passage_id,
            )
        )
    return citations


def _parse_answer_response(parsed: dict[str, Any]) -> tuple[str, list[str], bool]:
    """Parse the answerer's JSON object into ``(answer_text, cited_ids, claims_grounded)``.

    Fails safe: a missing/false ``is_grounded`` flag, an empty answer, or no
    citations all yield ``claims_grounded=False`` so the caller refuses.

    Args:
        parsed: The parsed JSON object from the answerer LLM.

    Returns:
        ``(answer_text, cited_passage_ids, claims_grounded)``.
    """
    answer_text = str(parsed.get("answer", "")).strip()
    raw_citations = parsed.get("citations", []) or []
    cited_ids = [str(c).strip() for c in raw_citations if str(c).strip()]
    # Reason: the model's own grounded flag, AND the structural requirements
    # (non-empty answer + at least one citation). Any miss → not grounded.
    claims_grounded = (
        bool(parsed.get("is_grounded", False)) and bool(answer_text) and bool(cited_ids)
    )
    return answer_text, cited_ids, claims_grounded


async def answer_question(
    question_text: str,
    corpus: GroundingCorpus,
    llm_client: LLMClient,
) -> QuestionAnswer:
    """Answer a question grounded ONLY in a story's in-context corpus.

    Constrains the model to the corpus's context block, parses its answer +
    cited passage ids, refuses on any ungrounded signal, then re-verifies the
    answer against the corpus and DOWNGRADES to a refusal if verification fails —
    so an ungrounded answer is never surfaced as grounded (Rule 9).

    Args:
        question_text: The reader's question.
        corpus: The story's loaded grounding corpus (SP1). The caller injects it
            (the endpoint loads + caches it per story).
        llm_client: An initialized ``LLMClient`` (mocked in tests).

    Returns:
        A grounded :class:`QuestionAnswer` with >=1 citation, or the refusal
        payload (``answer_is_grounded=False``) with no fabricated answer.

    Example:
        >>> answer = await answer_question("Why does Hormuz matter?", corpus, client)  # doctest: +SKIP
        >>> answer.answer_is_grounded
        True
    """
    start_time = time.monotonic()
    context_block = corpus.render_context_block()
    logger.info(
        "qa_answer_started",
        story_id=corpus.story_id,
        question_length=len(question_text),
        passage_count=len(corpus.passages),
    )

    system_prompt = GROUNDED_ANSWER_PROMPT.replace(
        "{CONTEXT_BLOCK}", context_block
    ).replace("{QUESTION}", question_text.strip())
    user_prompt = (
        "Answer the QUESTION using only the CONTEXT passages. "
        "Output ONLY the JSON object with 'answer', 'citations', and 'is_grounded'."
    )

    try:
        raw_response = await llm_client.call_gemini(
            prompt=user_prompt,
            system=system_prompt,
            temperature=ANSWER_TEMPERATURE,
        )
        parsed = extract_json_from_llm_response(raw_response, stage="qa_answer")
    except Exception as exc:  # noqa: BLE001 — fail safe to refusal (incl. PipelineStageError)
        # Reason: an LLM/parse error must refuse, never surface a fabricated
        # answer (Rule 12 fail loud + Rule 9). The endpoint catches nothing here.
        logger.error(
            "qa_answer_llm_failed",
            story_id=corpus.story_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:200],
            fix_suggestion="Answerer LLM/parse failed; returning refusal (no fabricated answer)",
        )
        return build_refusal_answer()

    if not isinstance(parsed, dict):
        logger.warning(
            "qa_answer_non_object",
            story_id=corpus.story_id,
            fix_suggestion="Answerer returned non-object output; refusing",
        )
        return build_refusal_answer()

    answer_text, cited_ids, claims_grounded = _parse_answer_response(parsed)
    citations = _map_citations(cited_ids, corpus) if claims_grounded else []

    # Reason: refuse before verifying if the answer is structurally ungrounded
    # (off-topic, empty, no real citation) — no need to spend a verification call.
    if not claims_grounded or not citations:
        logger.info(
            "qa_answer_refused_pre_verification",
            story_id=corpus.story_id,
            claims_grounded=claims_grounded,
            mapped_citation_count=len(citations),
        )
        return build_refusal_answer()

    # Reason: SECOND guardrail — re-verify the answer against the corpus; a failed
    # verification downgrades a plausible-but-ungrounded answer to a refusal.
    is_verified = await verify_answer_against_corpus(
        answer_text=answer_text,
        context_block=context_block,
        llm_client=llm_client,
    )
    if not is_verified:
        logger.warning(
            "qa_answer_failed_verification",
            story_id=corpus.story_id,
            fix_suggestion="Answer not supported by corpus on re-check; downgraded to refusal",
        )
        return build_refusal_answer()

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "qa_answer_grounded",
        story_id=corpus.story_id,
        citation_count=len(citations),
        elapsed_ms=elapsed_ms,
    )
    return QuestionAnswer(
        answer_text=answer_text,
        answer_citations=citations,
        answer_is_grounded=True,
    )
