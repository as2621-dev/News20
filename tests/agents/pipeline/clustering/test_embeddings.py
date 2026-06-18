"""Unit tests for the Gemini embedding adapter (Milestone M3a, Sub-phase 1).

The genai client is mocked at its boundary (``client.aio.models.embed_content``)
— NO network, NO key, NO cost (CLAUDE.md §6). The real ``LLMClient`` retry
wrapper runs, so these tests exercise ``embed_texts`` through the same
``_retry_with_backoff`` the pipeline uses.

These tests encode WHY (Rule 9):
    - L2-norm ≈ 1.0 is the load-bearing invariant: the whole clusterer treats
      cosine as a dot product, which is only valid for unit vectors. A
      regression that dropped normalization fails (a).
    - The 3-call assertion proves we batch (one call per 100), not one call per
      text (cost) nor one giant call (API limit). Fails (b).
    - cosine identical→1 / orthogonal→0 anchors the similarity contract the
      assign-or-spawn engine depends on. Fails (c).

    >>> pytest tests/agents/pipeline/clustering/test_embeddings.py -q
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.pipeline.clustering.embeddings import cosine_similarity, embed_texts
from agents.pipeline.llm_clients import LLMClient
from agents.shared.exceptions import PipelineStageError

_EMBED_DIM = 768


def _fake_embedding_values(seed: float) -> list[float]:
    """A deterministic, non-zero 768-d raw (un-normalized) vector for one text.

    The components are not all equal so L2 normalization is a real transform
    (not a no-op), making the norm≈1.0 assertion meaningful.
    """
    return [seed + (index % 7) + 1.0 for index in range(_EMBED_DIM)]


def _embed_response(texts: list[str]) -> SimpleNamespace:
    """Mimic a google-genai EmbedContentResponse for ``texts``.

    Shape matches the installed SDK: ``.embeddings`` is a list of objects each
    exposing ``.values`` (list[float]), one per input text.
    """
    embeddings = [
        SimpleNamespace(values=_fake_embedding_values(seed=float(index)))
        for index, _ in enumerate(texts)
    ]
    return SimpleNamespace(embeddings=embeddings)


def _make_llm_client_with_embed(embed_mock: AsyncMock) -> LLMClient:
    """An ``LLMClient`` whose genai client's ``aio.models.embed_content`` is mocked.

    Skips ``__init__`` (no Settings/key needed) and patches
    ``_get_gemini_client`` to hand back a fake client wired to ``embed_mock``.
    The real ``_retry_with_backoff`` is preserved so the call path is exercised.
    """
    client = LLMClient.__new__(LLMClient)
    client.max_retries = 3
    client.backoff_base_seconds = 0.0  # Reason: no real sleep between retries in tests
    fake_genai_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(embed_content=embed_mock)))
    client._get_gemini_client = lambda: fake_genai_client  # type: ignore[method-assign]
    return client


class TestEmbedTextsHappyPath:
    """N texts → N L2-normalized 768-d vectors."""

    @pytest.mark.asyncio
    async def test_returns_one_unit_vector_per_text(self) -> None:
        """(a) N in → N out, each len 768, each L2-norm ≈ 1.0."""
        texts = ["headline one", "headline two", "headline three"]
        embed_mock = AsyncMock(side_effect=lambda model, contents: _embed_response(contents))
        client = _make_llm_client_with_embed(embed_mock)

        vectors = await embed_texts(texts, llm_client=client)

        assert len(vectors) == len(texts)
        for vector in vectors:
            assert len(vector) == _EMBED_DIM
            norm = math.sqrt(sum(component * component for component in vector))
            assert norm == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_without_calling_api(self) -> None:
        """Edge case: empty input short-circuits to [] and never calls embed."""
        embed_mock = AsyncMock(side_effect=lambda model, contents: _embed_response(contents))
        client = _make_llm_client_with_embed(embed_mock)

        vectors = await embed_texts([], llm_client=client)

        assert vectors == []
        embed_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_unexpected_response_shape_raises(self) -> None:
        """Failure case: a count mismatch in the response fails loud (Rule 12)."""
        # Return a single embedding for a two-text batch → shape mismatch.
        embed_mock = AsyncMock(return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * _EMBED_DIM)]))
        client = _make_llm_client_with_embed(embed_mock)

        with pytest.raises(PipelineStageError):
            await embed_texts(["a", "b"], llm_client=client)


class TestEmbedTextsBatching:
    """Texts are chunked by batch_size — one embed call per chunk."""

    @pytest.mark.asyncio
    async def test_250_texts_batch_100_triggers_three_calls(self) -> None:
        """(b) 250 texts / batch_size 100 → exactly 3 underlying embed calls."""
        texts = [f"story-{index}" for index in range(250)]
        # side_effect returns the right-sized slice per call (100, 100, 50).
        embed_mock = AsyncMock(side_effect=lambda model, contents: _embed_response(contents))
        client = _make_llm_client_with_embed(embed_mock)

        vectors = await embed_texts(texts, llm_client=client, batch_size=100)

        assert embed_mock.call_count == 3
        assert len(vectors) == 250
        # Per-call batch sizes prove the chunking boundaries, not just the total.
        batch_sizes = [len(call.kwargs["contents"]) for call in embed_mock.call_args_list]
        assert batch_sizes == [100, 100, 50]


class TestCosineSimilarity:
    """Dot-product cosine on normalized vectors."""

    def test_identical_vectors_return_one(self) -> None:
        """(c) identical normalized vectors → ~1.0."""
        vector = _normalize([0.2, 0.5, 0.1, 0.9, 0.3])
        assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-9)

    def test_orthogonal_vectors_return_zero(self) -> None:
        """(c) orthogonal normalized vectors → ~0.0."""
        a = _normalize([1.0, 0.0, 0.0])
        b = _normalize([0.0, 1.0, 0.0])
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-9)

    def test_mismatched_lengths_raise(self) -> None:
        """Failure case: differing lengths raise rather than truncate silently."""
        with pytest.raises(ValueError):
            cosine_similarity([1.0, 0.0], [1.0])

    def test_empty_vector_raises(self) -> None:
        """Edge case: an empty vector is an error, not a 0.0."""
        with pytest.raises(ValueError):
            cosine_similarity([], [])


def _normalize(vector: list[float]) -> list[float]:
    """Local L2-normalize helper for cosine tests (kept independent of the SUT)."""
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]
