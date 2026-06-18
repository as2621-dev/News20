"""Gemini embedding adapter for the online clusterer (Milestone M3a, Sub-phase 1).

Vectorizes story text into L2-normalized 768-d embeddings using Gemini's
``text-embedding-004`` model. The adapter REUSES the existing
``agents.pipeline.llm_clients.LLMClient`` — its lazily-built ``google.genai``
client and its ``_retry_with_backoff`` wrapper — so there is exactly one genai
client in the pipeline and one retry policy (CLAUDE.md Rule 2 / Rule 3; phase
spec §14).

Design notes:
    - The new ``google-genai`` SDK (v2.7 installed) embeds via
      ``client.aio.models.embed_content(model=..., contents=<list[str]>)`` and
      returns an ``EmbedContentResponse`` whose ``.embeddings`` is a list of
      ``ContentEmbedding`` (one per input), each exposing ``.values``
      (``list[float]``). The response is parsed DEFENSIVELY: a missing/short
      ``embeddings`` list or a missing ``.values`` raises a clear error with a
      ``fix_suggestion`` log.
    - Every returned vector is L2-normalized so cosine similarity reduces to a
      plain dot product (see ``cosine_similarity``).
    - Text content is NEVER logged (only counts), per the structured-logging
      convention.

The genai client is mocked at this boundary in every test — no network, no key,
no cost (CLAUDE.md §6 mocking mandate).

Example:
    >>> from agents.pipeline.llm_clients import LLMClient
    >>> vectors = await embed_texts(["headline a", "headline b"], llm_client=LLMClient())  # doctest: +SKIP
    >>> len(vectors), len(vectors[0])
    (2, 768)
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger

if TYPE_CHECKING:
    from agents.pipeline.llm_clients import LLMClient

logger = get_logger("pipeline.clustering.embeddings")

# Reason: text-embedding-004 emits stable 768-d vectors (phase spec §3, owner-
# approved). Pinned as the default so the migration's vector(768) column matches.
DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
DEFAULT_BATCH_SIZE = 100


async def embed_texts(
    texts: list[str],
    *,
    llm_client: "LLMClient",
    model: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Embed texts into L2-normalized vectors via Gemini, preserving input order.

    Batches ``texts`` into chunks of ``batch_size`` and calls the Gemini
    embeddings API once per batch through the injected ``llm_client``'s genai
    client, wrapped in the client's ``_retry_with_backoff`` (the shared retry
    policy). Each returned vector is L2-normalized so cosine similarity equals a
    dot product. The output is aligned 1:1 with ``texts``.

    Args:
        texts: The texts to embed (e.g. ``headline + " " + lead`` per story).
            An empty list returns ``[]`` without any API call.
        llm_client: The shared ``LLMClient`` whose ``_get_gemini_client`` and
            ``_retry_with_backoff`` are reused. Injected so tests can mock the
            genai boundary.
        model: The Gemini embedding model id (defaults to ``text-embedding-004``,
            768-d).
        batch_size: Maximum number of texts per ``embed_content`` call.

    Returns:
        One L2-normalized embedding (``list[float]``) per input text, in the same
        order as ``texts``. With ``text-embedding-004`` each vector is 768-d.

    Raises:
        PipelineStageError: When the genai response shape is unexpected (missing
            embeddings, count mismatch, or missing ``.values``), or when all
            retries are exhausted.

    Example:
        >>> vectors = await embed_texts(["a", "b"], llm_client=client)  # doctest: +SKIP
        >>> len(vectors)
        2
    """
    if not texts:
        return []

    batches = [texts[start : start + batch_size] for start in range(0, len(texts), batch_size)]
    logger.info(
        "embed_texts_started",
        count=len(texts),
        batch_size=batch_size,
        batches=len(batches),
        model=model,
    )

    embeddings: list[list[float]] = []
    for batch_index, batch in enumerate(batches):

        async def _call(batch_to_embed: list[str] = batch) -> Any:
            # Reason: reuse the LLMClient's lazily-built genai client + its async
            # embed surface — do NOT construct a second client (phase spec §14).
            client = llm_client._get_gemini_client()
            return await client.aio.models.embed_content(model=model, contents=batch_to_embed)

        try:
            response = await llm_client._retry_with_backoff("gemini_embed", _call)
        except Exception as exc:
            logger.error(
                "embed_texts_failed",
                count=len(texts),
                batch_index=batch_index,
                model=model,
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
                fix_suggestion="Verify the Gemini API key, quota, and that the embedding model id is valid",
            )
            raise

        embeddings.extend(_parse_embed_response(response, expected_count=len(batch), batch_index=batch_index))

    logger.info("embed_texts_completed", count=len(embeddings), batches=len(batches))
    return embeddings


def _parse_embed_response(response: Any, *, expected_count: int, batch_index: int) -> list[list[float]]:
    """Defensively parse a genai ``embed_content`` response into normalized vectors.

    The installed ``google-genai`` (v2.7) returns an ``EmbedContentResponse``
    with ``.embeddings`` (a list of ``ContentEmbedding``), each carrying
    ``.values`` (``list[float]``). Every level is checked so a future SDK shape
    change fails loudly rather than silently producing wrong vectors.

    Args:
        response: The object returned by ``embed_content``.
        expected_count: How many embeddings this batch must contain.
        batch_index: The batch ordinal, for the error log.

    Returns:
        One L2-normalized vector per embedding, in response order.

    Raises:
        PipelineStageError: When ``embeddings`` is missing, the count does not
            match ``expected_count``, or an entry lacks ``.values``.
    """
    embeddings = getattr(response, "embeddings", None)
    if not isinstance(embeddings, list) or len(embeddings) != expected_count:
        actual = len(embeddings) if isinstance(embeddings, list) else "missing"
        logger.error(
            "embed_response_shape_unexpected",
            batch_index=batch_index,
            expected_count=expected_count,
            actual_count=actual,
            fix_suggestion=(
                "Expected response.embeddings to be a list of length == batch size "
                "(google-genai EmbedContentResponse.embeddings[*].values). "
                "Inspect the installed SDK's embed_content response shape."
            ),
        )
        raise PipelineStageError(
            stage="clustering.embeddings",
            message=f"Unexpected embed_content response: embeddings count={actual}, expected={expected_count}",
            fix_suggestion="Inspect the installed google-genai embed_content response shape (expected .embeddings[*].values)",
        )

    parsed: list[list[float]] = []
    for position, embedding in enumerate(embeddings):
        values = getattr(embedding, "values", None)
        if not isinstance(values, (list, tuple)) or not values:
            logger.error(
                "embed_response_values_missing",
                batch_index=batch_index,
                position=position,
                fix_suggestion="Each embedding must expose a non-empty .values (list[float]); check the SDK version",
            )
            raise PipelineStageError(
                stage="clustering.embeddings",
                message=f"Embedding at batch {batch_index} position {position} has no .values",
                fix_suggestion="Each embedding must expose a non-empty .values (list[float])",
            )
        parsed.append(_l2_normalize([float(component) for component in values]))
    return parsed


def _l2_normalize(vector: list[float]) -> list[float]:
    """Return the L2-normalized copy of a vector (unit length).

    Args:
        vector: The raw embedding components.

    Returns:
        A vector scaled to unit L2 norm. A zero vector is returned unchanged
        (it has no meaningful direction to normalize).

    Example:
        >>> _l2_normalize([3.0, 4.0])
        [0.6, 0.8]
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # Reason: a zero vector cannot be normalized; return as-is rather than
        # dividing by zero. Cosine against it is defined as 0 by the caller.
        return vector
    return [component / norm for component in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embeddings as a dot product.

    Because ``embed_texts`` L2-normalizes every vector, cosine similarity equals
    the plain dot product. This helper is pure Python (no numpy) so it stays
    dependency-free and trivially testable.

    Args:
        a: The first embedding.
        b: The second embedding (must match ``a``'s length).

    Returns:
        The dot product of ``a`` and ``b`` — cosine similarity for normalized
        vectors (``~1.0`` identical, ``~0.0`` orthogonal).

    Raises:
        ValueError: When either vector is empty or the lengths differ.

    Example:
        >>> cosine_similarity([1.0, 0.0], [1.0, 0.0])
        1.0
        >>> cosine_similarity([1.0, 0.0], [0.0, 1.0])
        0.0
    """
    if not a or not b:
        raise ValueError("cosine_similarity requires two non-empty vectors")
    if len(a) != len(b):
        raise ValueError(f"cosine_similarity requires equal-length vectors, got {len(a)} and {len(b)}")
    return sum(component_a * component_b for component_a, component_b in zip(a, b, strict=True))
