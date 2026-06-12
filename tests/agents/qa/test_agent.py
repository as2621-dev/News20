"""Tests for the grounded Q&A answerer — the hallucination guardrail (Phase 2b SP2).

WHY these tests exist (Rule 9, zero-tolerance accuracy): the answerer must
surface a grounded answer ONLY when the corpus supports it, and must NEVER
surface an ungrounded guess as grounded. So we assert BOTH branches and, for the
grounded branch, that every citation's outlet/URL traces to s1's ``story_sources``
/ corpus passages. A regression that let an off-source or unverified answer
through as grounded fails here — not merely "a function ran".

The LLM is mocked at the ``LLMClient.call_gemini`` /
``LLMClient.call_gemini_with_search`` boundaries (no live call, no cost). The
answerer makes TWO ``call_gemini`` calls on the grounded path — the answer, then
the verification — so that mock returns a SEQUENCE of canned responses
(``side_effect``). When the corpus cannot ground an answer the agent falls
through to ONE ``call_gemini_with_search`` call (the web fallback); its stub
defaults to raising so legacy refusal paths stay deterministic unless a test
opts into a web result.

    >>> pytest tests/agents/qa/test_agent.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agents.pipeline.llm_clients import LLMClient
from agents.qa.agent import answer_question
from agents.qa.prompts import OFF_TOPIC_ANSWER_TEXT, REFUSAL_ANSWER_TEXT


def _answer_response(answer: str, citations: list[str], is_grounded: bool) -> str:
    """A grounded-answerer JSON response string."""
    return json.dumps(
        {"answer": answer, "citations": citations, "is_grounded": is_grounded}
    )


def _verification_response(verdict: str) -> str:
    """An answer-verifier JSON response string."""
    return json.dumps({"verdict": verdict, "evidence": "carries a fifth of the oil"})


def _web_response(is_related: bool, answer: str) -> str:
    """A web-fallback answerer JSON response string."""
    return json.dumps({"is_related": is_related, "answer": answer})


def _llm_client_returning(*canned_responses: str) -> LLMClient:
    """An ``LLMClient`` whose ``call_gemini`` returns the canned responses in order.

    The answerer calls the LLM twice on the grounded path (answer → verify); the
    sequence lets a test script both calls. ``call_gemini_with_search`` (the web
    fallback) is stubbed to RAISE by default — tests that exercise the web path
    overwrite it with :func:`_stub_web_search`.
    """
    client = LLMClient()
    client.call_gemini = AsyncMock(side_effect=list(canned_responses))  # type: ignore[method-assign]
    client.call_gemini_with_search = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("web fallback not stubbed for this test")
    )
    return client


def _stub_web_search(
    client: LLMClient,
    raw_response: str,
    web_sources: list[dict[str, str]] | None = None,
) -> None:
    """Point the client's web-fallback call at a canned ``(text, sources)`` result."""
    client.call_gemini_with_search = AsyncMock(  # type: ignore[method-assign]
        return_value=(raw_response, web_sources or [])
    )


class TestGroundedBranch:
    """An on-topic, verified answer → answer_is_grounded=True with traced citations."""

    @pytest.mark.asyncio
    async def test_on_topic_question_returns_grounded_with_traced_citation(
        self, s1_corpus
    ) -> None:
        """'Why does Hormuz matter?' → grounded answer citing s1's real passage.

        The mocked answerer cites ``detail_chunk:0`` (a real s1 passage); the
        mocked verifier returns 'supported'. The result MUST be grounded with at
        least one citation WHOSE OUTLET/URL TRACES to s1's story_sources (Reuters
        + its url) — provenance, not a fabricated chip (Rule 9).
        """
        client = _llm_client_returning(
            _answer_response(
                "It carries about a fifth of the world's oil.",
                ["detail_chunk:0"],
                True,
            ),
            _verification_response("supported"),
        )

        answer = await answer_question("Why does Hormuz matter?", s1_corpus, client)

        assert answer.answer_is_grounded is True
        assert answer.answer_text  # non-empty grounded answer
        assert len(answer.answer_citations) >= 1
        citation = answer.answer_citations[0]
        # Provenance: the cited passage id + its outlet/url trace to s1's corpus.
        assert citation.passage_id == "detail_chunk:0"
        assert citation.source_outlet_name == "Reuters"
        assert citation.source_url == "https://reuters.com/world/hormuz"
        # The two LLM calls were the answer + the verification.
        assert client.call_gemini.await_count == 2

    @pytest.mark.asyncio
    async def test_citation_outlet_is_one_of_story_sources(self, s1_corpus) -> None:
        """Every grounded citation's outlet is one of s1's story_sources outlets.

        Encodes the trust contract: a citation chip can only point at an outlet
        that actually covers this story (Rule 9) — never an invented outlet.
        """
        valid_outlets = {t.source_outlet_name for t in s1_corpus.citation_targets}
        client = _llm_client_returning(
            _answer_response("Oil prices reacted.", ["detail_chunk:1"], True),
            _verification_response("supported"),
        )

        answer = await answer_question("What happened to oil?", s1_corpus, client)

        assert answer.answer_is_grounded is True
        for citation in answer.answer_citations:
            if citation.source_outlet_name is not None:
                assert citation.source_outlet_name in valid_outlets


class TestRefusalBranch:
    """An off-source / unsupported answer never surfaces as corpus-grounded."""

    @pytest.mark.asyncio
    async def test_unrelated_question_gets_off_topic_pushback(self, s1_corpus) -> None:
        """'What's the weather?' → corpus refuses → web gate says UNRELATED → pushback.

        The mocked answerer returns is_grounded=false (the off-source case) and
        the web-fallback gate judges the question unrelated. The result MUST be
        the off-topic pushback: not grounded, the pushback copy, ZERO citations —
        and the verifier is never called (nothing to verify). The guardrail
        contract (Rule 9): no answer is fabricated for an unrelated question.
        """
        client = _llm_client_returning(_answer_response("", [], False))
        _stub_web_search(client, _web_response(False, ""))

        answer = await answer_question("What's the weather today?", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_text == OFF_TOPIC_ANSWER_TEXT
        assert answer.answer_citations == []
        # Refused before verification — only the answer call happened on call_gemini.
        assert client.call_gemini.await_count == 1
        assert client.call_gemini_with_search.await_count == 1

    @pytest.mark.asyncio
    async def test_answer_failing_verification_never_surfaces_as_corpus_grounded(
        self, s1_corpus
    ) -> None:
        """A plausible answer the verifier rejects → NEVER surfaced as grounded.

        THE zero-tolerance test: the answerer returns a confident, well-formed
        answer with a real citation (is_grounded=true) — but the verifier returns
        'unsupported'. The unverified answer must be DROPPED (here the web
        fallback also fails, so the plain refusal surfaces) — the unverified
        corpus claim itself must never reach the user (Rule 9).
        """
        client = _llm_client_returning(
            _answer_response(
                "The strait was fully closed to all shipping yesterday.",
                ["detail_chunk:0"],
                True,
            ),
            _verification_response("unsupported"),
        )

        answer = await answer_question("Was the strait closed?", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_text == REFUSAL_ANSWER_TEXT
        assert answer.answer_citations == []
        # Both calls happened (answer + verify), but the verdict downgraded it.
        assert client.call_gemini.await_count == 2

    @pytest.mark.asyncio
    async def test_grounded_claim_with_no_real_citation_refuses(
        self, s1_corpus
    ) -> None:
        """is_grounded=true but the cited id is hallucinated → refusal (edge case).

        The model claims grounding and cites ``detail_chunk:99`` — a passage that
        does not exist in s1's corpus. It maps to ZERO real citations, so the
        answer cannot be corpus-grounded; with the web fallback also failing, the
        plain refusal surfaces — and the verifier is never called.
        """
        client = _llm_client_returning(
            _answer_response("Some confident claim.", ["detail_chunk:99"], True)
        )

        answer = await answer_question("Tell me something.", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_citations == []
        assert client.call_gemini.await_count == 1


class TestWebFallbackBranch:
    """Corpus can't answer + question is story-RELATED → web-search answer."""

    @pytest.mark.asyncio
    async def test_related_question_gets_web_answer_with_web_citations(
        self, s1_corpus
    ) -> None:
        """'Forward P/E of a covered company?' → web answer with web-source chips.

        WHY: the product contract is corpus-first, web for RELATED gaps — a
        reader asking about a metric of a company the story covers must get an
        answer attributed to web sources (passage_id 'web:<n>'), never a refusal
        and never an unattributed guess.
        """
        client = _llm_client_returning(_answer_response("", [], False))
        _stub_web_search(
            client,
            _web_response(True, "CoreWeave trades around 40x forward earnings."),
            web_sources=[
                {
                    "source_title": "reuters.com",
                    "source_url": "https://reuters.com/crwv",
                }
            ],
        )

        answer = await answer_question(
            "What is the forward PE of CoreWeave?", s1_corpus, client
        )

        assert answer.answer_is_grounded is True
        assert "forward earnings" in answer.answer_text
        assert len(answer.answer_citations) == 1
        citation = answer.answer_citations[0]
        assert citation.passage_id == "web:0"
        assert citation.source_outlet_name == "reuters.com"
        assert citation.source_url == "https://reuters.com/crwv"

    @pytest.mark.asyncio
    async def test_web_answer_without_sources_still_surfaces(self, s1_corpus) -> None:
        """A related web answer with no grounding chunks → answer, zero chips (edge)."""
        client = _llm_client_returning(_answer_response("", [], False))
        _stub_web_search(
            client, _web_response(True, "Roughly 40x, per recent reports.")
        )

        answer = await answer_question("Forward PE?", s1_corpus, client)

        assert answer.answer_is_grounded is True
        assert answer.answer_citations == []

    @pytest.mark.asyncio
    async def test_related_but_empty_web_answer_refuses(self, s1_corpus) -> None:
        """is_related=true but an empty answer → the honest refusal, not an empty bubble."""
        client = _llm_client_returning(_answer_response("", [], False))
        _stub_web_search(client, _web_response(True, ""))

        answer = await answer_question("Forward PE?", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_text == REFUSAL_ANSWER_TEXT

    @pytest.mark.asyncio
    async def test_web_call_raising_falls_back_to_plain_refusal(
        self, s1_corpus
    ) -> None:
        """The web-fallback LLM raising → plain refusal (fail safe), never a crash."""
        client = _llm_client_returning(_answer_response("", [], False))
        # call_gemini_with_search keeps its default raising stub.

        answer = await answer_question("Forward PE?", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_text == REFUSAL_ANSWER_TEXT


class TestAnswererFailSafe:
    """LLM/parse failures fail safe to a refusal, never a 5xx / fabricated answer."""

    @pytest.mark.asyncio
    async def test_unparseable_answer_response_refuses(self, s1_corpus) -> None:
        """A non-JSON answerer response → refusal (fail safe), not a crash."""
        client = _llm_client_returning("not json at all — model went rogue")

        answer = await answer_question("Why does Hormuz matter?", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_text == REFUSAL_ANSWER_TEXT

    @pytest.mark.asyncio
    async def test_llm_call_raising_refuses_without_web_fallback(
        self, s1_corpus
    ) -> None:
        """The answerer LLM raising → refusal directly (no web call — Gemini is down)."""
        client = LLMClient()
        client.call_gemini = AsyncMock(side_effect=RuntimeError("gemini down"))  # type: ignore[method-assign]
        client.call_gemini_with_search = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("web fallback must not be called")
        )

        answer = await answer_question("Why does Hormuz matter?", s1_corpus, client)

        assert answer.answer_is_grounded is False
        assert answer.answer_citations == []
        assert client.call_gemini_with_search.await_count == 0


class TestConversationThreading:
    """Bug 3: prior turns reach the prompt's RECENT CONVERSATION block — and
    ONLY as reference context (the grounding rule is restated beside it)."""

    @pytest.mark.asyncio
    async def test_turns_render_in_system_prompt(self, s1_corpus) -> None:
        """Passed turns appear as Reader:/Assistant: lines in the system prompt."""
        from agents.qa.models import ConversationTurn

        client = _llm_client_returning(
            _answer_response("Margins are discussed.", ["detail_chunk:0"], True),
            _verification_response("supported"),
        )
        turns = [
            ConversationTurn(role="user", text="What is the current PE of TSMC?"),
            ConversationTurn(role="model", text="About 24x forward earnings."),
        ]

        await answer_question(
            "What about its margins?", s1_corpus, client, conversation_turns=turns
        )

        system_prompt = client.call_gemini.await_args_list[0].kwargs["system"]
        assert "Reader: What is the current PE of TSMC?" in system_prompt
        assert "Assistant: About 24x forward earnings." in system_prompt

    @pytest.mark.asyncio
    async def test_no_turns_renders_first_question_marker(self, s1_corpus) -> None:
        """Without turns the block states this is the first question."""
        client = _llm_client_returning(
            _answer_response("It matters.", ["detail_chunk:0"], True),
            _verification_response("supported"),
        )

        await answer_question("Why does Hormuz matter?", s1_corpus, client)

        system_prompt = client.call_gemini.await_args_list[0].kwargs["system"]
        assert "(none — this is the first question)" in system_prompt

    @pytest.mark.asyncio
    async def test_only_last_six_turns_are_rendered(self, s1_corpus) -> None:
        """The prompt is bounded: only the most recent 6 turns are included."""
        from agents.qa.models import ConversationTurn

        client = _llm_client_returning(
            _answer_response("Bounded.", ["detail_chunk:0"], True),
            _verification_response("supported"),
        )
        turns = [
            ConversationTurn(role="user", text=f"question number {index}")
            for index in range(8)
        ]

        await answer_question("Latest?", s1_corpus, client, conversation_turns=turns)

        system_prompt = client.call_gemini.await_args_list[0].kwargs["system"]
        assert "question number 0" not in system_prompt
        assert "question number 1" not in system_prompt
        assert "question number 2" in system_prompt
        assert "question number 7" in system_prompt
