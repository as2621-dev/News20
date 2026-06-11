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
    monkeypatch.setattr(worker_main, "_build_service_role_client", lambda: object())


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Clear the in-memory per-IP rate-limit state so tests don't pollute each other."""
    worker_main._request_times_by_ip.clear()


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
    monkeypatch.setattr(worker_main, "get_or_load_corpus", lambda **_kwargs: object())
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


class TestDeployHardening:
    """CSO follow-ups: CORS scoped to the app origin + per-IP rate limiting."""

    def test_cors_allows_the_configured_app_origin(self, client: TestClient) -> None:
        """A preflight from the allowed origin gets it echoed (NOT `*`)."""
        response = client.options(
            _QUESTION_PATH,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("access-control-allow-origin") == (
            "http://localhost:3000"
        )

    def test_cors_does_not_allow_an_unlisted_origin(self, client: TestClient) -> None:
        """A preflight from an unlisted origin is NOT granted access (no wildcard)."""
        response = client.options(
            _QUESTION_PATH,
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "*"
        assert response.headers.get("access-control-allow-origin") != (
            "https://evil.example"
        )

    def test_paid_endpoint_is_rate_limited_per_ip(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Nth+1 request in the window is throttled with HTTP 429 — the interim
        cost-abuse ceiling on the paid Q&A endpoint (CSO MEDIUM-1)."""
        monkeypatch.setattr(worker_main, "get_or_load_corpus", lambda **_k: object())
        monkeypatch.setattr(
            worker_main, "answer_question", AsyncMock(return_value=_grounded_answer())
        )
        limit = worker_main._RATE_LIMIT_PER_MINUTE

        ok_statuses = [
            client.post(_QUESTION_PATH, json=_BODY).status_code for _ in range(limit)
        ]
        throttled = client.post(_QUESTION_PATH, json=_BODY)

        assert all(status == 200 for status in ok_statuses)
        assert throttled.status_code == 429
        assert throttled.headers.get("Retry-After") is not None


def test_unexpected_answer_error_returns_http_200_refusal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected error inside answering → HTTP 200 refusal (never a 5xx)."""
    monkeypatch.setattr(worker_main, "get_or_load_corpus", lambda **_kwargs: object())
    monkeypatch.setattr(
        worker_main,
        "answer_question",
        AsyncMock(side_effect=RuntimeError("gemini exploded")),
    )

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    assert response.json()["answer_is_grounded"] is False


class TestConversationTurnsCacheBypass:
    """Bug 3: conversation turns bypass the answer cache (read AND write)."""

    _TURNS_BODY = {
        "question_text": "What about its margins?",
        "conversation_turns": [
            {"role": "user", "text": "What is the current PE of TSMC?"},
            {"role": "model", "text": "TSMC trades at approximately 24x forward earnings."},
        ],
    }

    def test_turns_present_skips_cache_read_and_write_and_passes_turns(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With turns: no cache read/write; answer_question receives the turns."""
        read_spy = AsyncMock()  # would explode if awaited; we assert not-called
        monkeypatch.setattr(worker_main, "_read_cached_answer", read_spy)
        write_spy = AsyncMock()
        monkeypatch.setattr(worker_main, "_write_cached_answer", write_spy)
        monkeypatch.setattr(
            worker_main, "get_or_load_corpus", lambda **_kwargs: object()
        )
        answer_mock = AsyncMock(return_value=_grounded_answer())
        monkeypatch.setattr(worker_main, "answer_question", answer_mock)

        response = client.post(_QUESTION_PATH, json=self._TURNS_BODY)

        assert response.status_code == 200
        read_spy.assert_not_called()
        write_spy.assert_not_called()
        passed_turns = answer_mock.await_args.kwargs["conversation_turns"]
        assert [turn.role for turn in passed_turns] == ["user", "model"]
        assert passed_turns[0].text == "What is the current PE of TSMC?"

    def test_no_turns_keeps_cache_read_and_write_path(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without turns: cache is read and (on a live answer) written, as before."""
        read_calls: list[str] = []

        def _read_miss(_client: Any, _story_id: str, question_text: str) -> None:
            read_calls.append(question_text)
            return None

        write_calls: list[str] = []

        def _write(_client: Any, _story_id: str, question_text: str, _answer: Any) -> None:
            write_calls.append(question_text)

        monkeypatch.setattr(worker_main, "_read_cached_answer", _read_miss)
        monkeypatch.setattr(worker_main, "_write_cached_answer", _write)
        monkeypatch.setattr(
            worker_main, "get_or_load_corpus", lambda **_kwargs: object()
        )
        monkeypatch.setattr(
            worker_main, "answer_question", AsyncMock(return_value=_grounded_answer())
        )

        response = client.post(_QUESTION_PATH, json=_BODY)

        assert response.status_code == 200
        assert read_calls == [_BODY["question_text"]]
        assert write_calls == [_BODY["question_text"]]

    def test_invalid_turn_role_is_rejected_by_validation(
        self, client: TestClient
    ) -> None:
        """A malformed turn role fails pydantic validation (422 — contract breach)."""
        response = client.post(
            _QUESTION_PATH,
            json={
                "question_text": "q",
                "conversation_turns": [{"role": "narrator", "text": "x"}],
            },
        )
        assert response.status_code == 422
