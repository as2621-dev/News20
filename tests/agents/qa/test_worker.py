"""Tests for the Q&A worker endpoint boundary — HTTP-200 fallback (Phase 2b SP2).

WHY (the brief's boundary contract): the conversation must NEVER break — every
failure (missing Supabase config, corpus error, over-budget corpus, an
unexpected exception) returns HTTP 200 + the graceful refusal payload, never a
5xx. So these tests assert the status code is 200 AND the body is the refusal on
each failure path, and that the happy path returns the grounded answer.

Everything external is patched at the ``agents.worker.main`` module boundary —
the Supabase client builder, the corpus loader, and the answerer — so no network
/ no LLM / no env vars are needed.

    >>> pytest tests/agents/qa/test_worker.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agents.qa.models import AnswerCitation, QuestionAnswer
from agents.shared.exceptions import CorpusBudgetExceededError, GroundingCorpusError
from agents.worker import main as worker_main

_QUESTION_PATH = "/api/story/s1/question"
_BODY = {"question_text": "Why does Hormuz matter?"}


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient for the worker app."""
    return TestClient(worker_main.app)


@pytest.fixture(autouse=True)
def _stub_supabase_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the service-role client builder so no env vars / network are needed."""
    monkeypatch.setattr(
        worker_main, "_build_service_role_client", lambda: object()
    )


def _grounded_answer() -> QuestionAnswer:
    """A grounded answer payload for the happy-path stub."""
    return QuestionAnswer(
        answer_text="It carries a fifth of the world's oil.",
        answer_citations=[
            AnswerCitation(
                source_url="https://reuters.com/world/hormuz",
                source_outlet_name="Reuters",
                passage_id="detail_chunk:0",
            )
        ],
        answer_is_grounded=True,
    )


def test_happy_path_returns_grounded_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful request returns HTTP 200 + the grounded answer with a citation."""
    monkeypatch.setattr(
        worker_main, "get_or_load_corpus", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        worker_main, "answer_question", AsyncMock(return_value=_grounded_answer())
    )

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["answer_is_grounded"] is True
    assert len(body["answer_citations"]) == 1
    assert body["answer_citations"][0]["source_outlet_name"] == "Reuters"


def test_corpus_error_returns_http_200_refusal_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GroundingCorpusError → HTTP 200 refusal (the boundary fallback), NOT 500.

    THE boundary test: a corpus that cannot be assembled must degrade to a
    graceful refusal so the conversation never breaks — never a 5xx (Rule 12 +
    the brief). The body must be the refusal, never a fabricated answer.
    """

    def _raise(**_kwargs: Any) -> Any:
        raise GroundingCorpusError(
            story_id="s1",
            message="no grounding passages found",
            fix_suggestion="Seed detail_chunks",
        )

    monkeypatch.setattr(worker_main, "get_or_load_corpus", _raise)

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["answer_is_grounded"] is False
    assert body["answer_citations"] == []


def test_over_budget_corpus_returns_http_200_refusal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An over-budget corpus (CorpusBudgetExceededError) → HTTP 200 refusal."""

    def _raise(**_kwargs: Any) -> Any:
        raise CorpusBudgetExceededError(
            story_id="s1", total_char_count=99999, char_budget=24000
        )

    monkeypatch.setattr(worker_main, "get_or_load_corpus", _raise)

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    assert response.json()["answer_is_grounded"] is False


def test_missing_supabase_config_returns_http_200_refusal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing Supabase env config → HTTP 200 refusal, not a crash."""

    def _raise() -> Any:
        raise KeyError("SUPABASE_URL")

    monkeypatch.setattr(worker_main, "_build_service_role_client", _raise)

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    assert response.json()["answer_is_grounded"] is False


def test_unexpected_answer_error_returns_http_200_refusal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected error inside answering → HTTP 200 refusal (never a 5xx)."""
    monkeypatch.setattr(
        worker_main, "get_or_load_corpus", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        worker_main,
        "answer_question",
        AsyncMock(side_effect=RuntimeError("gemini exploded")),
    )

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    assert response.json()["answer_is_grounded"] is False
