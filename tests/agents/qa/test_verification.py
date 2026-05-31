"""Tests for the answer-vs-corpus verifier — the second guardrail (Phase 2b SP2).

WHY (Rule 9): this gate exists to catch a plausible-but-ungrounded answer the
answerer slipped through. So we assert it returns True ONLY on an explicit
'supported' verdict and fails SAFE (returns False) on every other outcome — an
empty answer, an 'unsupported' verdict, a garbled response, or an LLM error. A
regression that defaulted to True would let hallucinations through.

The LLM is mocked at the ``LLMClient.call_gemini`` boundary.

    >>> pytest tests/agents/qa/test_verification.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agents.pipeline.llm_clients import LLMClient
from agents.qa.verification import verify_answer_against_corpus

_CONTEXT = "[detail_chunk:0] The strait carries about a fifth of the world's oil."


def _client_returning(response: str) -> LLMClient:
    """An ``LLMClient`` whose ``call_gemini`` returns one canned response."""
    client = LLMClient()
    client.call_gemini = AsyncMock(return_value=response)  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_supported_verdict_returns_true() -> None:
    """An explicit 'supported' verdict → grounded (the only True path)."""
    client = _client_returning(json.dumps({"verdict": "supported", "evidence": "x"}))
    result = await verify_answer_against_corpus(
        "It carries a fifth of the world's oil.", _CONTEXT, client
    )
    assert result is True


@pytest.mark.asyncio
async def test_unsupported_verdict_returns_false() -> None:
    """An 'unsupported' verdict → not grounded (fail the answer)."""
    client = _client_returning(json.dumps({"verdict": "unsupported", "evidence": ""}))
    result = await verify_answer_against_corpus(
        "The strait was permanently closed.", _CONTEXT, client
    )
    assert result is False


@pytest.mark.asyncio
async def test_empty_answer_skips_llm_and_returns_false() -> None:
    """An empty answer is trivially ungrounded — no LLM call, fail safe."""
    client = LLMClient()
    client.call_gemini = AsyncMock()  # type: ignore[method-assign]
    result = await verify_answer_against_corpus("   ", _CONTEXT, client)
    assert result is False
    client.call_gemini.assert_not_awaited()


@pytest.mark.asyncio
async def test_garbled_response_fails_safe_to_false() -> None:
    """A non-JSON verifier response → not grounded (fail safe, Rule 9)."""
    client = _client_returning("the model rambled and returned no json")
    result = await verify_answer_against_corpus("Some answer.", _CONTEXT, client)
    assert result is False


@pytest.mark.asyncio
async def test_llm_error_fails_safe_to_false() -> None:
    """The verifier LLM raising → not grounded (fail safe), never True by default."""
    client = LLMClient()
    client.call_gemini = AsyncMock(side_effect=RuntimeError("gemini 503"))  # type: ignore[method-assign]
    result = await verify_answer_against_corpus("Some answer.", _CONTEXT, client)
    assert result is False
