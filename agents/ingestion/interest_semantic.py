r"""The semantic relevance key — embedding similarity between a story and its interest.

This is the **semantic half** of the two-key relevance lock (PRD decision 5, RC3); it
sits beside the lexical half (:mod:`agents.ingestion.interest_lexical`). A story may
fill an interest's slot only when BOTH keys pass:

  1. **Lexical** (slice #50) — phrase-level anchor matching, gated INSIDE BigQuery, which
     stamps ``candidate_matched_interest_id`` on a candidate only when it clears the
     anchors. After clustering these become ``CanonicalStory.canonical_matched_interest_ids``.
  2. **Semantic** (this module) — the cosine similarity between the story text and the
     matched interest's text must clear :data:`SEMANTIC_SIMILARITY_THRESHOLD`.

Why the semantic half exists. Lexical matching alone admitted the founder's 07-07 / 07-19
RC3 false positives; three of the four (venison→AI, Hawaii→interest-rates, Indonesia→cricket)
are closed lexically, but the fourth — a local **zoning lawsuit that genuinely contains the
phrase "data center"** — clears the lexical key on phrase grounds and can ONLY be closed by a
semantic check (its dominant topic is litigation, not data-center buildout). Semantic alone is
too loose to be a gate (it would admit anything vaguely on-theme); the two keys together close
the whole RC3 class.

**Where this gates (the seam).** The gate filters ``canonical_matched_interest_ids`` on each
canonical story, IN PLACE, right after clustering + body extraction and BEFORE SP1 ancestor
tagging reads that list (``ingest_active_interests``). Filtering at the SOURCE — not at the
built ``story_interests`` tags — is deliberate: SP1 tagging climbs ancestors FROM the matched
ids, so dropping a bogus leaf-match here drops the leaf tag AND its climbed parent/grandparent
category tags in one move. Filtering only the leaf tag would leak the story into the parent
CATEGORY slot (venison would still fill an "AI category" slot). Downstream, the semantic-merge
reconciler unions these already-filtered lists, so the filter survives Stage B.5.

**Failure posture is the contract (never fail open).** If the embedding API fails mid-batch,
admission falls back to STRICT LEXICAL: ``canonical_matched_interest_ids`` is left INTACT (the
lexical key already gated it) and a loud error is logged with a ``fix_suggestion``. The gate
only ever REMOVES from the lexically-admitted set, never adds — so it can never be more
permissive than lexical, failure or not.

**Cost (credit frugality — a founder standing rule).** Each unique story text and each unique
matched-interest text is embedded exactly ONCE per batch, in a SINGLE batched ``embed_texts``
call (which internally chunks at ``DEFAULT_BATCH_SIZE``); the per-pair comparison is a free
pure-Python dot product. A batch of N stories referencing M distinct interests costs N+M
embeddings — not N×M. The per-story embedding cost is logged for the M2 audit.

The Gemini embedding client is mocked at the ``embed_texts`` boundary in every test — no
network, no key, no cost.

Example:
    >>> stats = await apply_semantic_relevance_key(  # doctest: +SKIP
    ...     stories, interest_nodes, llm_client=LLMClient())
    >>> stats.pairs_rejected  # doctest: +SKIP
    3
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode
from agents.pipeline.clustering.embeddings import (
    DEFAULT_BATCH_SIZE,
    cosine_similarity,
    embed_texts,
)
from agents.shared.logger import get_logger

logger = get_logger("ingestion.interest_semantic")

# Reason: the cosine floor for story-vs-interest topical relevance. This is LOOSER than the
# clustering same-event join bar (``online_clusterer.DEFAULT_TAU_ASSIGN`` == 0.75, which
# detects near-duplicate REWORDINGS of one event): a story and its interest are topically
# related, not textually near-identical, so genuine matches land well below 0.75. Setting the
# floor that high would reject real matches (violating the "genuine story still passes"
# criterion); setting it at/below 0 would gate nothing. 0.5 is a first-draft topical floor —
# the ``semantic_relevance_key_completed`` log emits the pass/reject split so the M2 audit can
# calibrate it against real gemini-embedding-001 score distributions before tightening.
SEMANTIC_SIMILARITY_THRESHOLD: float = 0.5

# Reason: how many leading body characters to append to the headline for the story embedding.
# Mirrors ``clustering/reconcile._LEAD_SNIPPET_CHARS`` (300): the headline drives the topical
# signal, a short lead disambiguates without letting full-body drift dominate the vector.
_STORY_LEAD_SNIPPET_CHARS: int = 300


class SemanticRelevanceStats(BaseModel):
    """Per-batch outcome + embedding-cost accounting for the semantic relevance key.

    Attributes:
        stories_checked: Stories that had >= 1 matched interest (the only ones embedded).
        interests_checked: Distinct matched interests that were embedded (deduped).
        pairs_evaluated: (story, interest) pairs that had a semantic verdict computed.
        pairs_rejected: Pairs whose cosine fell below the threshold (matched id dropped).
        embed_call_count: Successful ``embed_texts`` API calls this batch (0 on fallback).
        embed_char_count: Total characters embedded (cost proxy; 0 on fallback).
        fell_back_to_lexical: True when the embedding API failed and admission fell back
            to strict lexical (matched ids left intact) — the loud-fail signal.
    """

    stories_checked: int = Field(default=0, ge=0)
    interests_checked: int = Field(default=0, ge=0)
    pairs_evaluated: int = Field(default=0, ge=0)
    pairs_rejected: int = Field(default=0, ge=0)
    embed_call_count: int = Field(default=0, ge=0)
    embed_char_count: int = Field(default=0, ge=0)
    fell_back_to_lexical: bool = Field(default=False)


def _story_embedding_text(story: CanonicalStory) -> str:
    """The headline (+ short lead) used to embed a story's topic.

    Mirrors the reconcile near-dup text builder: headline carries the dominant signal, a
    bounded lead disambiguates. A story with no extracted body embeds on its title alone.
    """
    if story.canonical_body_text:
        return f"{story.canonical_title}\n{story.canonical_body_text[:_STORY_LEAD_SNIPPET_CHARS]}"
    return story.canonical_title


def _interest_embedding_text(node: InterestNode) -> str:
    """The label + search query used to embed an interest's topic.

    The human label ("Arsenal") plus its news query ("Arsenal FC") together give the
    fullest topical signal; the query alone can be a bag of anchors, the label alone can be
    ambiguous. Falls back gracefully when either is missing.
    """
    parts = [part for part in (node.interest_label, node.interest_search_query) if part]
    return " ".join(parts).strip() or node.interest_slug


async def apply_semantic_relevance_key(
    stories: Sequence[CanonicalStory],
    interest_nodes: Mapping[str, InterestNode],
    *,
    llm_client: Any,
    threshold: float = SEMANTIC_SIMILARITY_THRESHOLD,
) -> SemanticRelevanceStats:
    """Drop each story's matched interests whose semantic similarity is below the floor.

    MUTATES ``canonical_matched_interest_ids`` on each story in place, keeping only the
    matched interests whose story-vs-interest cosine clears ``threshold``. A matched id that
    is absent from ``interest_nodes`` (no interest text to embed) is KEPT — the gate never
    drops on missing signal (that would be arbitrary), it defers to the lexical verdict.
    Stories with no matched interests are skipped entirely (zero cost).

    On an embedding-API failure the whole batch falls back to STRICT LEXICAL: every matched
    id is left intact and a loud error is logged with a ``fix_suggestion``. The gate only
    ever narrows the lexically-admitted set, so it is never more permissive than lexical.

    Args:
        stories: The clustered canonical pool (each carries ``canonical_matched_interest_ids``,
            stamped by the lexical key). Mutated in place.
        interest_nodes: Taxonomy map ``interest_id -> InterestNode`` (interest text source).
        llm_client: The shared ``LLMClient`` whose genai client ``embed_texts`` reuses.
        threshold: The cosine floor (defaults to :data:`SEMANTIC_SIMILARITY_THRESHOLD`).

    Returns:
        A :class:`SemanticRelevanceStats` with the pass/reject split and embedding cost.

    Example:
        >>> stats = await apply_semantic_relevance_key(  # doctest: +SKIP
        ...     stories, nodes, llm_client=client)
        >>> stats.stories_checked  # doctest: +SKIP
        12
    """
    stories_to_check = [
        story for story in stories if story.canonical_matched_interest_ids
    ]
    if not stories_to_check:
        return SemanticRelevanceStats()

    # Distinct matched interests that we CAN embed (present in the taxonomy). A matched id
    # missing from interest_nodes has no text — it is kept, never embedded (see below).
    referenced_interest_ids = sorted(
        {
            interest_id
            for story in stories_to_check
            for interest_id in story.canonical_matched_interest_ids
            if interest_id in interest_nodes
        }
    )

    story_texts = [_story_embedding_text(story) for story in stories_to_check]
    interest_texts = [
        _interest_embedding_text(interest_nodes[interest_id])
        for interest_id in referenced_interest_ids
    ]
    all_texts = story_texts + interest_texts
    embed_char_count = sum(len(text) for text in all_texts)
    planned_call_count = math.ceil(len(all_texts) / DEFAULT_BATCH_SIZE)

    try:
        # Reason: ONE batched call for every unique story + interest text (embed_texts chunks
        # internally at DEFAULT_BATCH_SIZE). The N×M pair comparison below is a free dot
        # product, so cost is N+M embeddings, not N×M.
        vectors = await embed_texts(all_texts, llm_client=llm_client)
    except Exception as exc:
        # Reason: never fail open. The lexical key already gated these ids; on an embedding
        # outage we KEEP them (strict lexical) rather than dropping the paid tightening's
        # worth of stories — but we log LOUD so the skipped spend is visible (Rule 12).
        logger.error(
            "semantic_relevance_embed_failed",
            stories_checked=len(stories_to_check),
            interests_checked=len(referenced_interest_ids),
            embed_char_count=embed_char_count,
            error_type=type(exc).__name__,
            error_message=str(exc)[:200],
            fix_suggestion=(
                "The semantic relevance key fell back to STRICT LEXICAL for this batch — "
                "admission kept every lexically-matched interest UNFILTERED (no fail-open, "
                "but the RC3 semantic tightening was skipped), and categorization ran "
                "WITHOUT the two-key guard (issue #70: a lexical false positive owns its "
                "story's category outright). Restore gemini-embedding-001 "
                "availability/quota BEFORE the next batch."
            ),
        )
        return SemanticRelevanceStats(
            stories_checked=len(stories_to_check),
            interests_checked=len(referenced_interest_ids),
            embed_char_count=embed_char_count,
            fell_back_to_lexical=True,
        )

    story_vector_by_index = list(vectors[: len(story_texts)])
    interest_vector_by_id = dict(
        zip(referenced_interest_ids, vectors[len(story_texts) :], strict=True)
    )

    pairs_evaluated = 0
    pairs_rejected = 0
    for story, story_vector in zip(
        stories_to_check, story_vector_by_index, strict=True
    ):
        surviving: list[str] = []
        for interest_id in story.canonical_matched_interest_ids:
            interest_vector = interest_vector_by_id.get(interest_id)
            if interest_vector is None:
                # No interest text (id not in taxonomy) — keep it (defer to lexical).
                surviving.append(interest_id)
                continue
            pairs_evaluated += 1
            similarity = cosine_similarity(story_vector, interest_vector)
            if similarity >= threshold:
                surviving.append(interest_id)
            else:
                pairs_rejected += 1
        story.canonical_matched_interest_ids = surviving

    stats = SemanticRelevanceStats(
        stories_checked=len(stories_to_check),
        interests_checked=len(referenced_interest_ids),
        pairs_evaluated=pairs_evaluated,
        pairs_rejected=pairs_rejected,
        embed_call_count=planned_call_count,
        embed_char_count=embed_char_count,
        fell_back_to_lexical=False,
    )
    logger.info(
        "semantic_relevance_key_completed",
        stories_checked=stats.stories_checked,
        interests_checked=stats.interests_checked,
        pairs_evaluated=stats.pairs_evaluated,
        pairs_rejected=stats.pairs_rejected,
        embed_call_count=stats.embed_call_count,
        embed_char_count=stats.embed_char_count,
        # Reason: the per-story embedding cost the M2 audit reads — amortized embeddings per
        # checked story (approaches ~1 as a batch reuses interest embeddings across stories).
        embeddings_per_story=round(
            (stats.stories_checked + stats.interests_checked)
            / max(stats.stories_checked, 1),
            3,
        ),
        threshold=threshold,
    )
    return stats
