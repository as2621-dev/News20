"""Tests for the SP4 verified-answer cache around the Q&A endpoint (Phase 2b SP4).

WHY (the SP4 contract): a verified turn is persisted to ``story_qa`` once, then a
repeat of the IDENTICAL ``(story_id, question)`` is served from that row WITHOUT
re-running the LLM + verification. These tests assert, at the boundary:

  - one ``story_qa`` row is written with the correct ``qa_is_grounded`` flag +
    citation outlet names (the write payload shape);
  - the identical question hits the cache and the answerer is NOT called a second
    time (``answer_question`` call-count stays 1) — and the cached grounded flag
    MATCHES the live answer (Rule 9: the cache cannot flip a grounded turn to a
    refusal or vice-versa);
  - a verified REFUSAL is cached and re-served (off-source repeats are cheap);
  - a cache-WRITE failure still returns the live answer (the HTTP-200/graceful
    fallback contract — the cache is best-effort).

Everything external is mocked at the ``agents.worker.main`` boundary: a
:class:`FakeStoryQaClient` stands in for Supabase ``story_qa`` reads/writes (no
network, no env vars) and ``answer_question`` is patched (no LLM, no cost).

    >>> pytest tests/agents/qa/test_qa_cache.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agents.qa.models import AnswerCitation, QuestionAnswer
from agents.worker import main as worker_main

_QUESTION_PATH = "/api/story/s1/question"
_BODY = {"question_text": "Why does Hormuz matter?"}


class _FakeStoryQaQuery:
    """A recording fluent ``story_qa`` query (read OR write) for one ``.table()`` call.

    Mirrors the ``supabase-py`` chains the worker uses:
    read  → ``.select(...).eq(col, v).eq(col, v).limit(1).execute()``
    write → ``.insert(payload).execute()``.
    Reads filter the client's stored rows by every ``.eq()``; writes append the
    payload (so a later read sees it — the round-trip the cache relies on).
    """

    def __init__(self, client: FakeStoryQaClient) -> None:
        self._client = client
        self._eq_filters: dict[str, Any] = {}
        self._insert_payload: dict[str, Any] | None = None

    def select(self, *_columns: str) -> _FakeStoryQaQuery:
        return self

    def eq(self, column: str, value: Any) -> _FakeStoryQaQuery:
        self._eq_filters[column] = value
        return self

    def limit(self, _count: int) -> _FakeStoryQaQuery:
        return self

    def insert(self, payload: dict[str, Any]) -> _FakeStoryQaQuery:
        self._insert_payload = payload
        return self

    def execute(self) -> Any:
        if self._insert_payload is not None:
            if self._client.raise_on_write:
                raise RuntimeError("story_qa insert exploded")
            self._client.written_rows.append(dict(self._insert_payload))
            return type("FakeResponse", (), {"data": [dict(self._insert_payload)]})()

        self._client.read_count += 1
        if self._client.raise_on_read:
            raise RuntimeError("story_qa read exploded")
        matched = [
            row
            for row in self._client.written_rows
            if all(row.get(col) == val for col, val in self._eq_filters.items())
        ]
        return type("FakeResponse", (), {"data": matched})()


class FakeStoryQaClient:
    """A boundary mock of the service-role client for ``story_qa`` read/write.

    Starts empty; an INSERT appends to ``written_rows`` and a later SELECT filtered
    by the same ``(qa_story_id, qa_question_text)`` finds it — reproducing the
    cache round-trip. ``raise_on_read`` / ``raise_on_write`` force the boundary
    failure paths. ``read_count`` proves whether the cache was consulted.
    """

    def __init__(self) -> None:
        self.written_rows: list[dict[str, Any]] = []
        self.read_count: int = 0
        self.raise_on_read: bool = False
        self.raise_on_write: bool = False

    def table(self, _table_name: str) -> _FakeStoryQaQuery:
        return _FakeStoryQaQuery(self)


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient for the worker app."""
    return TestClient(worker_main.app)


@pytest.fixture
def fake_qa_client() -> FakeStoryQaClient:
    """A fresh ``story_qa`` boundary client (empty cache)."""
    return FakeStoryQaClient()


@pytest.fixture(autouse=True)
def _wire_boundary(
    monkeypatch: pytest.MonkeyPatch, fake_qa_client: FakeStoryQaClient
) -> None:
    """Wire the worker boundary: the service-role client + a no-op corpus loader.

    The service-role builder returns the SAME ``fake_qa_client`` every request (so
    a write in request 1 is visible to the read in request 2 — the cache
    round-trip). ``get_or_load_corpus`` returns a sentinel (the answerer is mocked
    per-test, so the corpus object is never inspected).
    """
    monkeypatch.setattr(
        worker_main, "_build_service_role_client", lambda: fake_qa_client
    )
    monkeypatch.setattr(worker_main, "get_or_load_corpus", lambda **_kwargs: object())


def _grounded_answer() -> QuestionAnswer:
    """A grounded answer with one Reuters citation (the live-answer stub)."""
    return QuestionAnswer(
        answer_text="It carries a fifth of the world's oil.",
        answer_citations=[
            AnswerCitation(
                source_url="https://reuters.com/world/hormuz",
                source_quote="a fifth of the world's oil",
                source_outlet_name="Reuters",
                passage_id="detail_chunk:0",
            )
        ],
        answer_is_grounded=True,
    )


def _refusal_answer() -> QuestionAnswer:
    """A verified refusal (off-source) — the canonical refusal payload shape."""
    return QuestionAnswer(
        answer_text="⌀ CAN'T ANSWER FROM SOURCE",
        answer_citations=[],
        answer_is_grounded=False,
    )


def test_asking_writes_one_story_qa_row_with_flag_and_outlets(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_qa_client: FakeStoryQaClient,
) -> None:
    """A grounded answer persists ONE story_qa row with the correct flag + outlets.

    Asserts the write PAYLOAD SHAPE (Rule 9): qa_is_grounded is preserved from the
    live answer, the citation outlet name is flattened into
    qa_citation_outlet_names, qa_source_kind is the legacy 'rag_cached' label, and
    the cache key columns match the request.
    """
    monkeypatch.setattr(
        worker_main, "answer_question", AsyncMock(return_value=_grounded_answer())
    )

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    assert response.json()["answer_is_grounded"] is True
    assert len(fake_qa_client.written_rows) == 1
    row = fake_qa_client.written_rows[0]
    assert row["qa_story_id"] == "s1"
    assert row["qa_question_text"] == _BODY["question_text"]
    assert row["qa_answer_text"] == "It carries a fifth of the world's oil."
    assert row["qa_is_grounded"] is True
    assert row["qa_source_kind"] == "rag_cached"
    assert row["qa_citation_outlet_names"] == ["Reuters"]


def test_identical_question_hits_cache_without_second_llm_call(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_qa_client: FakeStoryQaClient,
) -> None:
    """The IDENTICAL (story, question) is served from cache — the answerer runs ONCE.

    THE SP4 cache-hit proof (Rule 9): two identical requests, but
    ``answer_question`` is called exactly once (the second request takes the
    cache-hit path and skips the whole LLM + verification round-trip), AND the
    cached grounded flag + answer text MATCH the live answer (the cache cannot
    silently change the verified verdict).
    """
    answer_mock = AsyncMock(return_value=_grounded_answer())
    monkeypatch.setattr(worker_main, "answer_question", answer_mock)

    first = client.post(_QUESTION_PATH, json=_BODY)
    second = client.post(_QUESTION_PATH, json=_BODY)

    # The answerer (LLM + verification) ran only on the MISS, never on the hit.
    assert answer_mock.call_count == 1
    assert len(fake_qa_client.written_rows) == 1  # only the miss persisted

    live = first.json()
    cached = second.json()
    assert cached["answer_is_grounded"] == live["answer_is_grounded"] is True
    assert cached["answer_text"] == live["answer_text"]
    # The cached chip re-serves the persisted outlet name.
    assert [c["source_outlet_name"] for c in cached["answer_citations"]] == ["Reuters"]


def test_verified_refusal_is_cached_and_reserved(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_qa_client: FakeStoryQaClient,
) -> None:
    """A verified refusal is persisted (qa_is_grounded=False) and re-served from cache.

    An off-source question is a cacheable result too — the second identical ask
    must NOT re-run the answerer and must re-serve the refusal contract (no
    citations, not grounded).
    """
    answer_mock = AsyncMock(return_value=_refusal_answer())
    monkeypatch.setattr(worker_main, "answer_question", answer_mock)

    first = client.post(_QUESTION_PATH, json=_BODY)
    second = client.post(_QUESTION_PATH, json=_BODY)

    assert answer_mock.call_count == 1
    assert len(fake_qa_client.written_rows) == 1
    row = fake_qa_client.written_rows[0]
    assert row["qa_is_grounded"] is False
    assert row["qa_citation_outlet_names"] == []

    cached = second.json()
    assert first.json()["answer_is_grounded"] is False
    assert cached["answer_is_grounded"] is False
    assert cached["answer_citations"] == []


def test_cache_write_failure_still_returns_the_answer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_qa_client: FakeStoryQaClient,
) -> None:
    """A story_qa WRITE failure must NOT break the response — the live answer returns.

    The cache is best-effort: an insert that raises (network / UNIQUE race) logs a
    typed ErrorResponse and falls through, so the user still gets HTTP 200 + the
    grounded answer (the boundary/graceful-fallback contract).
    """
    fake_qa_client.raise_on_write = True
    monkeypatch.setattr(
        worker_main, "answer_question", AsyncMock(return_value=_grounded_answer())
    )

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["answer_is_grounded"] is True
    assert body["answer_text"] == "It carries a fifth of the world's oil."
    assert len(fake_qa_client.written_rows) == 0  # the write raised → nothing cached


def test_cache_read_failure_falls_through_to_live_answer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    fake_qa_client: FakeStoryQaClient,
) -> None:
    """A story_qa READ failure falls through to the live answerer (HTTP 200 answer).

    A cache lookup that raises must not break the response: log + return None so
    the endpoint produces the live answer (graceful fallback).
    """
    fake_qa_client.raise_on_read = True
    answer_mock = AsyncMock(return_value=_grounded_answer())
    monkeypatch.setattr(worker_main, "answer_question", answer_mock)

    response = client.post(_QUESTION_PATH, json=_BODY)

    assert response.status_code == 200
    assert response.json()["answer_is_grounded"] is True
    # The read raised → the live answerer was consulted (fell through).
    assert answer_mock.call_count == 1
