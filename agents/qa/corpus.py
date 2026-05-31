"""Load one story's grounding corpus into a citeable context block (Phase 2b SP1).

``load_grounding_corpus(story_id, supabase_client)`` reads a **single** story's
grounding text via Supabase-direct reads — ``detail_chunks`` (ordered by
``chunk_index``), ``story_timeline`` (when → what), the current digest's caption
script, and any single extracted source body — and assembles a **labeled,
citeable** :class:`GroundingCorpus`: each passage tagged with a stable passage id
+ its source outlet, plus a citation manifest from ``story_sources``
(``source_outlet_name`` + ``source_article_url``).

There is **no chunking / embedding / vector upsert** — the whole per-story
corpus is small enough for one prompt (see
``plans/phase-2b-m2-grounded-interrogation.md`` re-scope). The assembled block is
**bounded**: its char count is asserted against a budget (fail loud, Rule 12).

DESIGN NOTES (match ``agents/pipeline/persist.py``)
---------------------------------------------------
- The Supabase client is **INJECTED** (the live worker builds the real client;
  tests inject a mock) — this module never reads a secret itself and the test
  suite never touches the network. Same pattern as ``persist.py``.
- Every read is filtered by ``story_id`` and ordered server-side, so the corpus
  is **scoped to exactly one story** — no other story's text can leak in.
- Reads are tolerant: a missing optional table (timeline / digest / source body)
  yields zero passages of that kind, not an error. A story with **no** grounding
  passages at all raises :class:`GroundingCorpusError` (fail loud).
"""

from __future__ import annotations

from typing import Any

from agents.qa.models import (
    CitationTarget,
    GroundingCorpus,
    GroundingPassage,
    PassageOriginKind,
)
from agents.shared.exceptions import CorpusBudgetExceededError, GroundingCorpusError
from agents.shared.logger import get_logger

logger = get_logger("qa.corpus")

# Reason: the per-story corpus is loaded WHOLE into the LLM context, so it must
# stay small. s1's corpus (3 detail_chunks + a few timeline events + a ~140-word
# digest) is well under this; the ceiling exists so an unexpectedly large story
# fails loud (Rule 12) instead of silently blowing the context window. ~24k
# chars ≈ ~6k tokens — comfortably inside any modern context window with room to
# spare for the system prompt + question + answer.
DEFAULT_CORPUS_CHAR_BUDGET = 24_000

# Reason: a cheap chars/token ratio for an ADVISORY token estimate (English text
# averages ~4 chars/token). This is NOT a tokenizer — it is for prompt-budget
# logging only; the asserted budget is the char count, which is exact.
_CHARS_PER_TOKEN_ESTIMATE = 4


def _rows(response: Any) -> list[dict[str, Any]]:
    """Extract the row list from a Supabase response (``[]`` when empty/missing).

    Args:
        response: The object returned by ``...execute()`` (real or mocked).

    Returns:
        The ``.data`` row list, or an empty list when there is no data.
    """
    return getattr(response, "data", None) or []


def _load_detail_chunk_passages(
    supabase_client: Any, story_id: str, primary_outlet_name: str | None
) -> list[GroundingPassage]:
    """Read ``detail_chunks`` (ordered by ``chunk_index``) as grounding passages.

    The detail chunks are the spine of the corpus — the readable body the Detail
    view shows. They are attributed to the story's primary outlet.

    Args:
        supabase_client: The injected (real or mocked) Supabase client.
        story_id: The story to read (filters ``detail_story_id``).
        primary_outlet_name: The outlet to attribute body passages to (None ok).

    Returns:
        Ordered :class:`GroundingPassage` list (empty when no chunks).
    """
    response = (
        supabase_client.table("detail_chunks")
        .select("chunk_index,chunk_text")
        .eq("detail_story_id", story_id)
        .order("chunk_index")
        .execute()
    )
    passages: list[GroundingPassage] = []
    for row in _rows(response):
        chunk_text = (row.get("chunk_text") or "").strip()
        if not chunk_text:
            continue
        chunk_index = int(row["chunk_index"])
        passages.append(
            GroundingPassage(
                passage_id=f"detail_chunk:{chunk_index}",
                passage_text=chunk_text,
                origin_kind=PassageOriginKind.DETAIL_CHUNK,
                passage_order_index=chunk_index,
                source_outlet_name=primary_outlet_name,
            )
        )
    return passages


def _load_timeline_passages(
    supabase_client: Any, story_id: str
) -> list[GroundingPassage]:
    """Read ``story_timeline`` events (ordered) as "when → what" passages.

    Timeline events have no single attributing outlet, so ``source_outlet_name``
    is None. The passage text joins the ``when`` label and the ``what`` sentence.

    Args:
        supabase_client: The injected Supabase client.
        story_id: The story to read (filters ``timeline_story_id``).

    Returns:
        Ordered :class:`GroundingPassage` list (empty when no events).
    """
    response = (
        supabase_client.table("story_timeline")
        .select("timeline_event_index,timeline_when_label,timeline_what_text")
        .eq("timeline_story_id", story_id)
        .order("timeline_event_index")
        .execute()
    )
    passages: list[GroundingPassage] = []
    for row in _rows(response):
        what_text = (row.get("timeline_what_text") or "").strip()
        if not what_text:
            continue
        when_label = (row.get("timeline_when_label") or "").strip()
        event_index = int(row["timeline_event_index"])
        passage_text = f"{when_label}: {what_text}" if when_label else what_text
        passages.append(
            GroundingPassage(
                passage_id=f"timeline_event:{event_index}",
                passage_text=passage_text,
                origin_kind=PassageOriginKind.TIMELINE_EVENT,
                passage_order_index=event_index,
                source_outlet_name=None,
            )
        )
    return passages


def _load_digest_passage(
    supabase_client: Any, story_id: str, primary_outlet_name: str | None
) -> GroundingPassage | None:
    """Read the current digest's caption script as a single grounding passage.

    Joins the current digest's ``caption_sentences`` (ordered by
    ``sentence_index``) into one narration passage — the ~140-word audio script.
    Returns None when the story has no current digest / no caption sentences.

    Args:
        supabase_client: The injected Supabase client.
        story_id: The story to read.
        primary_outlet_name: The outlet to attribute the narration to (None ok).

    Returns:
        The digest narration :class:`GroundingPassage`, or None when absent.
    """
    response = (
        supabase_client.table("caption_sentences")
        .select("sentence_index,sentence_text")
        .eq("caption_story_id", story_id)
        .order("sentence_index")
        .execute()
    )
    sentences = [
        (row.get("sentence_text") or "").strip()
        for row in _rows(response)
        if (row.get("sentence_text") or "").strip()
    ]
    if not sentences:
        return None
    return GroundingPassage(
        passage_id="digest_script:0",
        passage_text=" ".join(sentences),
        origin_kind=PassageOriginKind.DIGEST_SCRIPT,
        passage_order_index=0,
        source_outlet_name=primary_outlet_name,
    )


def _load_citation_targets(
    supabase_client: Any, story_id: str
) -> tuple[list[CitationTarget], str | None]:
    """Read ``story_sources`` as citation targets + resolve the primary outlet.

    The primary outlet (the first source carrying a ``source_article_url``, else
    the first source) is the attribution for body / digest passages. Only rows
    flagged ``source_is_citation`` become citation chips.

    Args:
        supabase_client: The injected Supabase client.
        story_id: The story to read (filters ``source_story_id``).

    Returns:
        ``(citation_targets, primary_outlet_name)``. ``primary_outlet_name`` is
        None when the story has no sources.
    """
    response = (
        supabase_client.table("story_sources")
        .select(
            "source_outlet_name,source_article_url,source_bias_lean,source_is_citation"
        )
        .eq("source_story_id", story_id)
        .execute()
    )
    rows = _rows(response)

    targets: list[CitationTarget] = []
    for row in rows:
        outlet_name = (row.get("source_outlet_name") or "").strip()
        if not outlet_name:
            continue
        # Reason: only citation-flagged sources become Q&A citation chips
        # (story_sources.source_is_citation, defaulting True).
        if row.get("source_is_citation", True):
            targets.append(
                CitationTarget(
                    source_outlet_name=outlet_name,
                    source_article_url=row.get("source_article_url"),
                    source_bias_lean=row.get("source_bias_lean"),
                )
            )

    primary_outlet_name = _resolve_primary_outlet_name(rows)
    return targets, primary_outlet_name


def _resolve_primary_outlet_name(rows: list[dict[str, Any]]) -> str | None:
    """Pick the primary outlet name: first source with a URL, else first source.

    Args:
        rows: The raw ``story_sources`` rows.

    Returns:
        The primary outlet name, or None when there are no named sources.
    """
    first_named: str | None = None
    for row in rows:
        outlet_name = (row.get("source_outlet_name") or "").strip()
        if not outlet_name:
            continue
        if first_named is None:
            first_named = outlet_name
        if row.get("source_article_url"):
            return outlet_name
    return first_named


def load_grounding_corpus(
    story_id: str,
    supabase_client: Any,
    char_budget: int = DEFAULT_CORPUS_CHAR_BUDGET,
) -> GroundingCorpus:
    """Assemble one story's whole grounding corpus into a citeable context block.

    Reads (all ``story_id``-filtered, scoped to exactly this story):
      1. ``detail_chunks`` ordered by ``chunk_index`` — the readable body spine.
      2. ``story_timeline`` ordered by ``timeline_event_index`` — "when → what".
      3. The current digest's ``caption_sentences`` joined into one narration.
      4. ``story_sources`` — the citation-chip manifest + primary-outlet attribution.

    No chunking / embedding / vector upsert — the corpus is small enough to load
    whole. The assembled block is **bounded**: its total char count is asserted
    against ``char_budget`` and raises :class:`CorpusBudgetExceededError` when
    over (fail loud, Rule 12).

    Args:
        story_id: The story to ground on (the only story the corpus contains).
        supabase_client: An injected Supabase client (mocked in tests, real in
            the worker). This function never reads a secret itself.
        char_budget: Maximum allowed total passage-text characters (defaults to
            :data:`DEFAULT_CORPUS_CHAR_BUDGET`).

    Returns:
        The assembled, bounded, citeable :class:`GroundingCorpus`.

    Raises:
        GroundingCorpusError: When the story has no grounding passages at all.
        CorpusBudgetExceededError: When the assembled corpus exceeds the budget.

    Example:
        >>> corpus = load_grounding_corpus("s1", supabase_client)  # doctest: +SKIP
        >>> corpus.passages[0].origin_kind
        <PassageOriginKind.DETAIL_CHUNK: 'detail_chunk'>
    """
    logger.info("load_grounding_corpus_started", story_id=story_id)

    # Reason: sources first — they resolve the primary outlet used to attribute
    # the body / digest passages, and the citation manifest.
    citation_targets, primary_outlet_name = _load_citation_targets(
        supabase_client, story_id
    )

    passages: list[GroundingPassage] = []
    passages.extend(
        _load_detail_chunk_passages(supabase_client, story_id, primary_outlet_name)
    )
    passages.extend(_load_timeline_passages(supabase_client, story_id))
    digest_passage = _load_digest_passage(
        supabase_client, story_id, primary_outlet_name
    )
    if digest_passage is not None:
        passages.append(digest_passage)

    # Reason: a story with no grounding text at all cannot ground any answer —
    # fail loud rather than return an empty, useless corpus (Rule 12).
    if not passages:
        raise GroundingCorpusError(
            story_id=story_id,
            message="no grounding passages found (no detail_chunks, timeline, or digest)",
            fix_suggestion="Confirm detail_chunks are seeded for this story (M1 Phase 1b)",
        )

    total_char_count = sum(len(passage.passage_text) for passage in passages)

    # Reason: the bounded-budget assertion — fail loud if the corpus is too large
    # to load whole into context (Rule 12; the escape hatch is retrieval).
    if total_char_count > char_budget:
        logger.error(
            "grounding_corpus_over_budget",
            story_id=story_id,
            total_char_count=total_char_count,
            char_budget=char_budget,
            fix_suggestion="Trim grounding text or reintroduce retrieval (escape hatch)",
        )
        raise CorpusBudgetExceededError(
            story_id=story_id,
            total_char_count=total_char_count,
            char_budget=char_budget,
        )

    corpus = GroundingCorpus(
        story_id=story_id,
        passages=passages,
        citation_targets=citation_targets,
        total_char_count=total_char_count,
        char_budget=char_budget,
        approx_token_count=total_char_count // _CHARS_PER_TOKEN_ESTIMATE,
    )

    logger.info(
        "load_grounding_corpus_completed",
        story_id=story_id,
        passage_count=len(corpus.passages),
        citation_target_count=len(corpus.citation_targets),
        total_char_count=corpus.total_char_count,
        approx_token_count=corpus.approx_token_count,
        char_budget=char_budget,
    )
    return corpus
