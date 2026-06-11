"""Pydantic v2 models for the per-story grounding corpus (Phase 2b SP1).

These models describe the **assembled, bounded, citeable context block** that
``agents/qa/corpus.py`` builds from a single story's grounding text (its
``detail_chunks`` + ``story_timeline`` + the digest script + any single-source
body) plus the ``story_sources`` citation targets. No vector store / retrieval —
the whole per-story corpus is small enough to load directly into the LLM context
(see ``plans/phase-2b-m2-grounded-interrogation.md`` re-scope).

SP2 (the grounded answer endpoint) consumes :class:`GroundingCorpus`:
    - ``corpus.render_context_block()`` → the labeled, passage-id-tagged text the
      system prompt forbids the model to answer outside of.
    - ``corpus.citation_targets`` → the ``story_sources`` outlet + URL chips an
      answer maps its ``answer_citations`` onto (``api-contracts.md``
      ``QuestionAnswer`` / ``AnswerCitation``).

Column names that map to Supabase are transcribed verbatim from
``reference/supabase-schema.md`` (``detail_chunks``, ``story_timeline``,
``story_sources``, ``digests``).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PassageOriginKind(str, Enum):
    """Which grounding table a :class:`GroundingPassage` was read from.

    Lets SP2 (and the answer-citation mapper) tell apart body paragraphs from
    timeline events / the digest narration / a single-source body — e.g. to
    weight or label them differently in the system prompt — without re-querying.

    Values:
        DETAIL_CHUNK: A ``detail_chunks`` readable-body paragraph (the spine of
            the corpus; ordered by ``chunk_index``).
        TIMELINE_EVENT: A ``story_timeline`` "when → what" development event.
        DIGEST_SCRIPT: The generated audio digest narration (~140 words).
        SOURCE_BODY: An optional single extracted source-article body.
    """

    DETAIL_CHUNK = "detail_chunk"
    TIMELINE_EVENT = "timeline_event"
    DIGEST_SCRIPT = "digest_script"
    SOURCE_BODY = "source_body"


class GroundingPassage(BaseModel):
    """One labeled, citeable unit of a story's grounding corpus.

    Every passage carries a **stable passage id** (e.g. ``"detail_chunk:0"``) so
    the answer model can cite the exact passage it grounded a claim on and the
    verifier can re-check that claim against this passage's text. ``origin_kind``
    records which grounding table it came from; ``source_outlet_name`` is the
    outlet to attribute the passage to when known (the story's primary outlet for
    body/digest passages, ``None`` for timeline events that have no single
    outlet).

    Attributes:
        passage_id: Stable, unique-within-corpus id (``"<origin>:<index>"``).
        passage_text: The passage's plain text (stripped, non-empty).
        origin_kind: Which grounding table this passage was read from.
        passage_order_index: 0-based order within its ``origin_kind`` group
            (``chunk_index`` for detail chunks, event index for timeline, etc.).
        source_outlet_name: Outlet to attribute this passage to, or ``None`` when
            no single outlet applies (e.g. a timeline event).

    Example:
        >>> passage = GroundingPassage(
        ...     passage_id="detail_chunk:0",
        ...     passage_text="Iran's parliament voted to consider closing Hormuz.",
        ...     origin_kind=PassageOriginKind.DETAIL_CHUNK,
        ...     passage_order_index=0,
        ...     source_outlet_name="Reuters",
        ... )
        >>> passage.passage_id
        'detail_chunk:0'
    """

    passage_id: str = Field(
        ...,
        min_length=1,
        description="Stable, unique-within-corpus passage id ('<origin>:<index>').",
    )
    passage_text: str = Field(
        ...,
        min_length=1,
        description="The passage's stripped, non-empty plain text.",
    )
    origin_kind: PassageOriginKind = Field(
        ..., description="Which grounding table this passage came from."
    )
    passage_order_index: int = Field(
        ...,
        ge=0,
        description="0-based order within this passage's origin_kind group.",
    )
    source_outlet_name: str | None = Field(
        default=None,
        description="Outlet to attribute the passage to, or None when not single-outlet.",
    )


class CitationTarget(BaseModel):
    """A ``story_sources`` citation chip target (outlet name + article URL).

    These are the citation-chip destinations an answer's ``answer_citations``
    map onto (``api-contracts.md`` ``AnswerCitation.source_url`` ←
    ``source_article_url``). They are **citation targets, not body text** —
    ``story_sources`` provides attribution, the passages provide the grounding
    text (``supabase-schema.md`` § ``story_sources`` open-question #1).

    Attributes:
        source_outlet_name: Denormalized outlet name (``source_outlet_name``).
        source_article_url: Canonical article link, or ``None`` when the row has
            no URL (only the primary source carries one in M1's static persist).
        source_bias_lean: Resolved ``'left' | 'center' | 'right'`` lean, or
            ``None`` when unknown (sort key for the Detail sources list).

    Example:
        >>> target = CitationTarget(
        ...     source_outlet_name="Reuters",
        ...     source_article_url="https://reuters.com/world/hormuz",
        ...     source_bias_lean="center",
        ... )
        >>> target.source_outlet_name
        'Reuters'
    """

    source_outlet_name: str = Field(
        ..., min_length=1, description="Denormalized story_sources.source_outlet_name."
    )
    source_article_url: str | None = Field(
        default=None,
        description="story_sources.source_article_url (None when the row has no URL).",
    )
    source_bias_lean: str | None = Field(
        default=None,
        description="Resolved bias lean ('left'|'center'|'right') or None when unknown.",
    )


class GroundingCorpus(BaseModel):
    """The assembled, bounded, citeable grounding corpus for one story.

    This is the whole per-story corpus SP2 loads into the LLM context (no
    retrieval). It is **scoped to exactly one ``story_id``** — every passage and
    citation target is read with a ``story_id``-filtered query, so no other
    story's text can leak in. The block is **bounded**: ``total_char_count`` is
    asserted against a budget at load time (fail loud, Rule 12) so an
    unexpectedly large story can't silently blow the context window.

    Attributes:
        story_id: The story this corpus grounds (the only story it contains).
        passages: Ordered grounding passages — detail chunks first (in
            ``chunk_index`` order), then timeline events, then the digest, then
            any single-source body.
        citation_targets: The ``story_sources`` citation-chip targets for this
            story (outlet name + URL + lean).
        total_char_count: Total characters across all ``passage_text`` values
            (the bounded budget metric).
        char_budget: The maximum allowed ``total_char_count`` (the asserted
            ceiling; exceeding it raises at load time).
        approx_token_count: A cheap chars/4 token estimate for prompt-budget
            logging (NOT a tokenizer — advisory only).

    Example:
        >>> corpus = GroundingCorpus(
        ...     story_id="s1",
        ...     passages=[],
        ...     citation_targets=[],
        ...     total_char_count=0,
        ...     char_budget=24000,
        ...     approx_token_count=0,
        ... )
        >>> corpus.story_id
        's1'
    """

    story_id: str = Field(
        ..., min_length=1, description="The single story this corpus grounds."
    )
    passages: list[GroundingPassage] = Field(
        default_factory=list,
        description="Ordered grounding passages (detail chunks first).",
    )
    citation_targets: list[CitationTarget] = Field(
        default_factory=list,
        description="story_sources citation-chip targets for this story.",
    )
    total_char_count: int = Field(
        ..., ge=0, description="Total characters across all passage texts."
    )
    char_budget: int = Field(
        ..., gt=0, description="Maximum allowed total_char_count (asserted ceiling)."
    )
    approx_token_count: int = Field(
        ..., ge=0, description="Advisory chars/4 token estimate (not a tokenizer)."
    )

    def render_context_block(self) -> str:
        """Render the labeled, passage-id-tagged context block for the prompt.

        Each passage is emitted on its own ``[<passage_id>] <text>`` line so the
        answer model can cite a passage id and the verifier can locate the exact
        grounding text. SP2 wraps this block in a system prompt that forbids
        answering outside it.

        Returns:
            The newline-joined labeled passage block (empty string when there
            are no passages).

        Example:
            >>> corpus = GroundingCorpus(
            ...     story_id="s1",
            ...     passages=[
            ...         GroundingPassage(
            ...             passage_id="detail_chunk:0",
            ...             passage_text="Hormuz carries ~20% of global oil.",
            ...             origin_kind=PassageOriginKind.DETAIL_CHUNK,
            ...             passage_order_index=0,
            ...         )
            ...     ],
            ...     citation_targets=[],
            ...     total_char_count=33,
            ...     char_budget=24000,
            ...     approx_token_count=8,
            ... )
            >>> corpus.render_context_block()
            '[detail_chunk:0] Hormuz carries ~20% of global oil.'
        """
        return "\n".join(
            f"[{passage.passage_id}] {passage.passage_text}"
            for passage in self.passages
        )


class ConversationTurn(BaseModel):
    """One prior turn of the typed Q&A thread, sent with a follow-up question.

    Stateless-server multi-turn (Bug 3): the client holds the thread and ships
    the recent turns with each request; the worker weaves them into the answer
    prompt's RECENT CONVERSATION block so follow-ups ("what about its
    margins?") resolve against the thread. Conversation text is NEVER treated
    as source material — the grounding rule still binds answers to the corpus.

    Mirrors the TS contract ``src/types/qa.ts`` ``QaConversationTurn``.
    """

    role: Literal["user", "model"] = Field(
        ..., description="Who spoke: the reader ('user') or the answerer ('model')."
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The turn's text — the question, or the answer/refusal copy.",
    )


class AnswerCitation(BaseModel):
    """One citation chip backing a grounded answer (``api-contracts.md``).

    Maps a passage the answer grounded a claim on to a user-facing citation
    chip: the outlet it is attributed to + that outlet's article URL (the chip
    destination) + a short supporting quote. The frontend renders one
    ``.cite-chip`` per :class:`AnswerCitation` on a grounded ``.qa-bubble-a``
    (``prototype-port-map.md`` §7).

    The TS contract (``api-contracts.md`` ``AnswerCitation``) is
    ``{ source_url, source_quote }``; we additionally carry
    ``source_outlet_name`` (the chip label) and the originating ``passage_id``
    (provenance — which corpus passage this citation traces to) so the chip can
    show the outlet name and a test can prove the citation traces to that story's
    ``story_sources`` / corpus passages (Rule 9).

    Attributes:
        source_url: The cited outlet's article URL (the chip destination), or
            ``None`` when that outlet row carried no URL (M1's static persist
            only attaches a URL to the primary source).
        source_quote: A short supporting quote / locator from the grounding
            passage (may be empty when the model returned none).
        source_outlet_name: The outlet this citation is attributed to (the chip
            label), or ``None`` when the cited passage has no single outlet (a
            timeline event).
        passage_id: The ``GroundingPassage.passage_id`` this citation traces to
            (provenance; lets a test/verifier re-locate the grounding text).

    Example:
        >>> citation = AnswerCitation(
        ...     source_url="https://reuters.com/world/hormuz",
        ...     source_quote="carries a fifth of the world's oil",
        ...     source_outlet_name="Reuters",
        ...     passage_id="detail_chunk:0",
        ... )
        >>> citation.source_outlet_name
        'Reuters'
    """

    source_url: str | None = Field(
        default=None,
        description="The cited outlet's article URL (chip destination), or None.",
    )
    source_quote: str = Field(
        default="",
        description="Short supporting quote/locator from the grounding passage.",
    )
    source_outlet_name: str | None = Field(
        default=None,
        description="Outlet this citation is attributed to (chip label), or None.",
    )
    passage_id: str = Field(
        ...,
        min_length=1,
        description="The GroundingPassage.passage_id this citation traces to.",
    )


class QuestionAnswer(BaseModel):
    """The grounded answer returned by the Q&A endpoint (``api-contracts.md``).

    This is the SP2 contract the SP3 frontend and SP4 cache consume. It mirrors
    the TS ``QuestionAnswer { answer_text, answer_citations[], answer_is_grounded }``
    exactly. The trust-critical invariant (Decision #5, Rule 9): when
    ``answer_is_grounded`` is ``False`` the answer is a refusal — ``answer_text``
    is the fixed refusal copy and ``answer_citations`` is empty; the UI renders
    the ``⌀ CAN'T ANSWER FROM SOURCE`` blush card and NEVER an answer bubble. An
    ungrounded guess must never be surfaced as grounded.

    Attributes:
        answer_text: The grounded answer body, or the fixed refusal copy when
            ``answer_is_grounded`` is ``False``.
        answer_citations: One citation per grounding passage the answer used
            (always empty on a refusal). The frontend renders one chip each.
        answer_is_grounded: ``True`` only when the answer is grounded in the
            corpus AND verified; ``False`` → render the refusal state.

    Example:
        >>> grounded = QuestionAnswer(
        ...     answer_text="The strait carries about a fifth of the world's oil.",
        ...     answer_citations=[
        ...         AnswerCitation(passage_id="detail_chunk:0", source_outlet_name="Reuters")
        ...     ],
        ...     answer_is_grounded=True,
        ... )
        >>> grounded.answer_is_grounded
        True
    """

    answer_text: str = Field(
        ...,
        description="The grounded answer body, or the fixed refusal copy on refusal.",
    )
    answer_citations: list[AnswerCitation] = Field(
        default_factory=list,
        description="Citations backing a grounded answer (empty on a refusal).",
    )
    answer_is_grounded: bool = Field(
        ...,
        description="True only when grounded AND verified; False → refusal state.",
    )


class StoryQaCacheRow(BaseModel):
    """One ``story_qa`` cache row — a verified Q&A turn persisted for reuse (SP4).

    The Q&A endpoint persists ONE of these per ``(story_id, question_text)`` after
    answering (grounded answers AND verified refusals — a refusal is a cacheable
    result), then on a repeat of the same pair serves the cached row instead of
    re-running the LLM + verification. This answer cache **layers on top of** the
    SP2 per-story corpus context cache (a different thing — the corpus cache skips
    Supabase reads; this skips the whole LLM+verification answer round-trip).

    Column names map verbatim to ``reference/supabase-schema.md`` ``story_qa``.
    ``qa_source_kind`` is the legacy enum label ``'rag_cached'`` — retained to
    avoid a migration; it now means "model-generated, verified, cached" (the
    re-scope dropped RAG). The ``(qa_story_id, qa_question_text)`` unique
    constraint is the cache key.

    Attributes:
        qa_story_id: The story this turn was answered for (``story_qa.qa_story_id``).
        qa_question_text: The exact question text (the cache key with the story id).
        qa_answer_text: The grounded answer body, or the fixed refusal copy.
        qa_is_grounded: Preserved from ``QuestionAnswer.answer_is_grounded`` —
            ``False`` → the row re-serves the refusal state.
        qa_source_kind: Always ``'rag_cached'`` here (legacy label; see above).
        qa_citation_outlet_names: The grounded answer's citation outlet names (the
            chip labels), empty on a refusal.

    Example:
        >>> row = StoryQaCacheRow(
        ...     qa_story_id="s1",
        ...     qa_question_text="Why does Hormuz matter?",
        ...     qa_answer_text="It carries a fifth of the world's oil.",
        ...     qa_is_grounded=True,
        ...     qa_citation_outlet_names=["Reuters"],
        ... )
        >>> row.qa_source_kind
        'rag_cached'
    """

    qa_story_id: str = Field(
        ..., min_length=1, description="story_qa.qa_story_id (the story answered for)."
    )
    qa_question_text: str = Field(
        ...,
        min_length=1,
        description="story_qa.qa_question_text (the cache key with the story id).",
    )
    qa_answer_text: str = Field(
        ..., description="story_qa.qa_answer_text (grounded answer or refusal copy)."
    )
    qa_is_grounded: bool = Field(
        ...,
        description="story_qa.qa_is_grounded (False → re-serves the refusal state).",
    )
    qa_source_kind: str = Field(
        default="rag_cached",
        description="story_qa.qa_source_kind — legacy 'rag_cached' = verified+cached.",
    )
    qa_citation_outlet_names: list[str] = Field(
        default_factory=list,
        description="story_qa.qa_citation_outlet_names (chip labels; empty on refusal).",
    )

    def to_insert_payload(self) -> dict[str, Any]:
        """Return the ``story_qa`` INSERT payload (column → value).

        Only the columns this writer owns are included; the table's defaults
        (``story_qa_id``, ``qa_created_at``) are left to Postgres. ``model_dump``
        gives exactly the column-named fields above.

        Returns:
            A dict keyed by ``story_qa`` column name, ready for
            ``supabase.table('story_qa').insert(payload)``.

        Example:
            >>> row = StoryQaCacheRow(
            ...     qa_story_id="s1", qa_question_text="q?",
            ...     qa_answer_text="a", qa_is_grounded=True,
            ... )
            >>> sorted(row.to_insert_payload())[:2]
            ['qa_answer_text', 'qa_citation_outlet_names']
        """
        return self.model_dump()

    @classmethod
    def from_question_answer(
        cls, *, story_id: str, question_text: str, answer: QuestionAnswer
    ) -> StoryQaCacheRow:
        """Build a cache row from a live :class:`QuestionAnswer` (the write side).

        Preserves ``answer.answer_is_grounded`` into ``qa_is_grounded`` and flattens
        the answer's citation outlet names into ``qa_citation_outlet_names`` (the
        chip labels; outlet-less citations — e.g. timeline events — are skipped).
        Refusals map cleanly too (``qa_is_grounded=False``, empty outlets).

        Args:
            story_id: The story the answer was grounded on.
            question_text: The exact question asked (the cache key).
            answer: The live answer to cache (grounded or refusal).

        Returns:
            A :class:`StoryQaCacheRow` ready to insert.
        """
        outlet_names = [
            citation.source_outlet_name
            for citation in answer.answer_citations
            if citation.source_outlet_name
        ]
        return cls(
            qa_story_id=story_id,
            qa_question_text=question_text,
            qa_answer_text=answer.answer_text,
            qa_is_grounded=answer.answer_is_grounded,
            qa_citation_outlet_names=outlet_names,
        )

    def to_question_answer(self) -> QuestionAnswer:
        """Map this cached row back to the endpoint's :class:`QuestionAnswer` (read side).

        On a cache hit the endpoint returns this WITHOUT re-running the LLM +
        verification. Citation chips are reconstructed from the persisted outlet
        names — the cache stores outlet labels only (not the per-passage
        ``source_url`` / ``passage_id`` provenance), which is what the SP3 chip
        renders; a grounded cached row therefore yields one
        :class:`AnswerCitation` per stored outlet name. A refusal row yields zero
        citations, matching :func:`agents.qa.agent.build_refusal_answer`.

        Returns:
            The :class:`QuestionAnswer` to return on a cache hit.
        """
        if not self.qa_is_grounded:
            # Reason: a cached refusal must re-serve the byte-identical refusal
            # contract (no citations) — Rule 9 / the trust-critical invariant.
            return QuestionAnswer(
                answer_text=self.qa_answer_text,
                answer_citations=[],
                answer_is_grounded=False,
            )
        citations = [
            AnswerCitation(source_outlet_name=outlet_name, passage_id="cache")
            for outlet_name in self.qa_citation_outlet_names
        ]
        return QuestionAnswer(
            answer_text=self.qa_answer_text,
            answer_citations=citations,
            answer_is_grounded=True,
        )
