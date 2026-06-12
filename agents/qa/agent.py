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
       fall through to the WEB FALLBACK (step 6), never a fabricated answer.
    4. Re-verify the answer against the corpus (``agents/qa/verification.py``).
       If verification fails, fall through to the web fallback — an ungrounded
       answer is never surfaced as corpus-grounded (Rule 9).
    5. Map the cited passage ids → :class:`AnswerCitation` chips (outlet + URL),
       tracing each citation to that story's ``story_sources`` / corpus passages.
    6. WEB FALLBACK (``_answer_from_web``): when the corpus cannot answer, a
       Google-Search-grounded Gemini call first gates on story RELATEDNESS — a
       related question (e.g. a financial metric of a company the story covers)
       is answered from the web with web-source citations; an unrelated question
       gets the ``OFF_TOPIC_ANSWER_TEXT`` pushback (ask about this story).

Returns the typed :class:`QuestionAnswer` (``api-contracts.md``). The Gemini call
is mocked at the ``LLMClient`` boundary in every test — no live call, no cost.
"""

from __future__ import annotations

import time
from typing import Any

from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.qa.models import (
    AnswerCitation,
    ConversationTurn,
    GroundingCorpus,
    QuestionAnswer,
)
from agents.qa.prompts import (
    GROUNDED_ANSWER_PROMPT,
    OFF_TOPIC_ANSWER_TEXT,
    REFUSAL_ANSWER_TEXT,
    WEB_FALLBACK_ANSWER_PROMPT,
)
from agents.qa.verification import verify_answer_against_corpus
from agents.shared.logger import get_logger

logger = get_logger("qa.agent")

# Reason: a low temperature keeps grounded answers faithful to the corpus and
# the refusal behaviour stable — mirrors the pipeline scripting/verification temps.
ANSWER_TEMPERATURE = 0.2

# Reason: bound the prompt's RECENT CONVERSATION block — older turns add tokens
# without improving pronoun resolution. Matches the client's
# MAX_CONVERSATION_TURNS_SENT (src/lib/qa/askQuestion.ts).
MAX_CONVERSATION_TURNS_IN_PROMPT = 6


def _render_conversation_block(
    conversation_turns: list[ConversationTurn] | None,
) -> str:
    """Render the prompt's RECENT CONVERSATION block from prior thread turns.

    Args:
        conversation_turns: Prior turns, most-recent-last (or None/empty on a
            first question).

    Returns:
        ``Reader:``/``Assistant:`` lines for the last
        :data:`MAX_CONVERSATION_TURNS_IN_PROMPT` turns, or the explicit
        first-question marker.

    Example:
        >>> _render_conversation_block(None)
        '(none — this is the first question)'
    """
    if not conversation_turns:
        return "(none — this is the first question)"
    role_label = {"user": "Reader", "model": "Assistant"}
    return "\n".join(
        f"{role_label[turn.role]}: {turn.text}"
        for turn in conversation_turns[-MAX_CONVERSATION_TURNS_IN_PROMPT:]
    )


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


# Reason: bound the web-answer citation chips — search grounding can return many
# chunks; the UI only needs a few attribution chips.
MAX_WEB_CITATIONS = 4


def build_off_topic_answer() -> QuestionAnswer:
    """Build the off-topic pushback answer (unrelated question → steer back).

    Same wire shape as a refusal (``answer_is_grounded=False``, no citations) so
    the UI's refusal card renders it; only the body copy differs — the gentle
    "ask about this story" pushback instead of the can't-answer copy.

    Returns:
        The off-topic :class:`QuestionAnswer`.

    Example:
        >>> build_off_topic_answer().answer_is_grounded
        False
    """
    return QuestionAnswer(
        answer_text=OFF_TOPIC_ANSWER_TEXT,
        answer_citations=[],
        answer_is_grounded=False,
    )


async def _answer_from_web(
    question_text: str,
    corpus: GroundingCorpus,
    llm_client: LLMClient,
    conversation_turns: list[ConversationTurn] | None,
) -> QuestionAnswer:
    """Web-search fallback for a question the story corpus could not answer.

    One Gemini call with the Google Search tool: the prompt first gates on
    story-RELATEDNESS (unrelated → the off-topic pushback, no web answer), then
    answers a related question from live search results. Web sources from the
    response's grounding metadata become the citation chips (``passage_id``
    ``"web:<n>"``), so a web answer is always attributed — never passed off as
    a corpus-grounded one.

    Args:
        question_text: The reader's question.
        corpus: The story's grounding corpus (renders the relatedness context).
        llm_client: An initialized ``LLMClient`` (mocked in tests).
        conversation_turns: Prior thread turns for pronoun resolution.

    Returns:
        A web-answered :class:`QuestionAnswer`, the off-topic pushback, or the
        plain refusal when the web call fails / returns nothing usable.
    """
    system_prompt = (
        WEB_FALLBACK_ANSWER_PROMPT.replace(
            "{CONTEXT_BLOCK}", corpus.render_context_block()
        )
        .replace("{CONVERSATION_BLOCK}", _render_conversation_block(conversation_turns))
        .replace("{QUESTION}", question_text.strip())
    )
    user_prompt = (
        "Apply the relatedness gate, then answer with Google Search if related. "
        "Output ONLY the JSON object with 'is_related' and 'answer'."
    )

    try:
        raw_response, web_sources = await llm_client.call_gemini_with_search(
            prompt=user_prompt,
            system=system_prompt,
            temperature=ANSWER_TEMPERATURE,
        )
        parsed = extract_json_from_llm_response(raw_response, stage="qa_web_fallback")
    except Exception as exc:  # noqa: BLE001 — fail safe to refusal, never a 5xx
        logger.error(
            "qa_web_fallback_failed",
            story_id=corpus.story_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:200],
            fix_suggestion="Web-fallback LLM/parse failed; returning plain refusal",
        )
        return build_refusal_answer()

    if not isinstance(parsed, dict):
        logger.warning(
            "qa_web_fallback_non_object",
            story_id=corpus.story_id,
            fix_suggestion="Web-fallback returned non-object output; refusing",
        )
        return build_refusal_answer()

    is_related = bool(parsed.get("is_related", False))
    answer_text = str(parsed.get("answer", "")).strip()

    if not is_related:
        logger.info("qa_web_fallback_off_topic", story_id=corpus.story_id)
        return build_off_topic_answer()
    if not answer_text:
        # Reason: related but the model produced no answer — the honest outcome
        # is the plain can't-answer refusal, not an empty bubble.
        logger.info("qa_web_fallback_empty_answer", story_id=corpus.story_id)
        return build_refusal_answer()

    citations = [
        AnswerCitation(
            source_url=web_source["source_url"],
            source_quote="",
            source_outlet_name=web_source["source_title"],
            passage_id=f"web:{web_source_index}",
        )
        for web_source_index, web_source in enumerate(web_sources[:MAX_WEB_CITATIONS])
    ]
    logger.info(
        "qa_web_fallback_answered",
        story_id=corpus.story_id,
        web_citation_count=len(citations),
    )
    return QuestionAnswer(
        answer_text=answer_text,
        answer_citations=citations,
        answer_is_grounded=True,
    )


async def answer_question(
    question_text: str,
    corpus: GroundingCorpus,
    llm_client: LLMClient,
    conversation_turns: list[ConversationTurn] | None = None,
) -> QuestionAnswer:
    """Answer a question — story corpus first, web-search fallback for related ones.

    Constrains the model to the corpus's context block, parses its answer +
    cited passage ids, and re-verifies the answer against the corpus. When the
    corpus cannot ground an answer (off-source question, no citation, failed
    verification), falls through to :func:`_answer_from_web`: a story-RELATED
    question is answered via Google Search with web-source citations; an
    unrelated one gets the off-topic pushback. An ungrounded guess is never
    surfaced as an answer (Rule 9) — every answer is corpus-cited or web-cited.

    Args:
        question_text: The reader's question.
        corpus: The story's loaded grounding corpus (SP1). The caller injects it
            (the endpoint loads + caches it per story).
        llm_client: An initialized ``LLMClient`` (mocked in tests).
        conversation_turns: Prior thread turns (most-recent-last) woven into the
            prompt's RECENT CONVERSATION block so follow-ups resolve pronouns;
            None/empty on a first question. NOT source material — verification
            still audits the answer against the corpus only.

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
        conversation_turn_count=len(conversation_turns or []),
    )

    system_prompt = (
        GROUNDED_ANSWER_PROMPT.replace("{CONTEXT_BLOCK}", context_block)
        .replace("{CONVERSATION_BLOCK}", _render_conversation_block(conversation_turns))
        .replace("{QUESTION}", question_text.strip())
    )
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
            fix_suggestion="Answerer returned non-object output; trying web fallback",
        )
        return await _answer_from_web(
            question_text, corpus, llm_client, conversation_turns
        )

    answer_text, cited_ids, claims_grounded = _parse_answer_response(parsed)
    citations = _map_citations(cited_ids, corpus) if claims_grounded else []

    # Reason: the corpus cannot ground this answer (off-source question, empty
    # answer, no real citation) — fall through to the web fallback, which gates
    # on relatedness and either web-answers or pushes back off-topic.
    if not claims_grounded or not citations:
        logger.info(
            "qa_answer_unanswerable_from_corpus",
            story_id=corpus.story_id,
            claims_grounded=claims_grounded,
            mapped_citation_count=len(citations),
        )
        return await _answer_from_web(
            question_text, corpus, llm_client, conversation_turns
        )

    # Reason: SECOND guardrail — re-verify the answer against the corpus; a failed
    # verification downgrades a plausible-but-ungrounded answer to the web fallback
    # (which answers with explicit web attribution or refuses) — never surfaced
    # as corpus-grounded.
    is_verified = await verify_answer_against_corpus(
        answer_text=answer_text,
        context_block=context_block,
        llm_client=llm_client,
    )
    if not is_verified:
        logger.warning(
            "qa_answer_failed_verification",
            story_id=corpus.story_id,
            fix_suggestion="Answer not supported by corpus on re-check; trying web fallback",
        )
        return await _answer_from_web(
            question_text, corpus, llm_client, conversation_turns
        )

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
