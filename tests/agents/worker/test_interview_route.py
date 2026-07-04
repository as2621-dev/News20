"""Auth + integration tests for POST /api/interview/turn (FSR interview slice #1).

The route is exercised through the REAL request-handling chain (a FastAPI app mounting
the real interview router), so these prove the contract end-to-end: the JWT guard rejects
anonymous/bad callers, and an authenticated call reaches the engine and returns a typed
turn. The Supabase-auth client is faked at its builder boundary (per the assemble-mine
tests) and Gemini is faked at ``LLMClient.call_gemini_json`` — no live services.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.interview.models import InterviewDecision, MicroInterestDraft
from agents.pipeline.llm_clients import GeminiJsonResult
from agents.worker import interview_routes, pipeline_routes

_PATH = "/api/interview/turn"
_VERIFIED_USER_ID = "user-abc-123"


@pytest.fixture()
def client() -> TestClient:
    """A TestClient over a minimal app that mounts ONLY the interview router."""
    app = FastAPI()
    app.include_router(interview_routes.router)
    return TestClient(app)


@pytest.fixture()
def fake_auth(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Fake the Supabase-auth client so a valid bearer token resolves to a user id."""
    get_user = MagicMock(
        return_value=SimpleNamespace(user=SimpleNamespace(id=_VERIFIED_USER_ID))
    )
    fake_client = SimpleNamespace(auth=SimpleNamespace(get_user=get_user))
    monkeypatch.setattr(
        pipeline_routes, "_build_supabase_for_auth", lambda: fake_client
    )
    return get_user


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_no_authorization_header_returns_401(client: TestClient) -> None:
    """No token → 401: the interview is never open to anonymous callers."""
    response = client.post(_PATH, json={"conversation_state": []})
    assert response.status_code == 401


def test_invalid_token_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token Supabase rejects → 401 (auth.get_user raising is an auth failure, not a 500)."""
    failing = MagicMock(side_effect=Exception("invalid jwt"))
    monkeypatch.setattr(
        pipeline_routes,
        "_build_supabase_for_auth",
        lambda: SimpleNamespace(auth=SimpleNamespace(get_user=failing)),
    )
    response = client.post(
        _PATH, json={"conversation_state": []}, headers=_bearer("bad")
    )
    assert response.status_code == 401


def test_turn_one_reaches_engine_and_returns_roots_without_gemini(
    client: TestClient, fake_auth: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid token + empty state → HTTP 200 question with the 8 roots and NO Gemini call.

    Proves the full chain (guard → route → engine) and the turn-1 no-LLM guarantee.
    """
    call_recorder = MagicMock(
        side_effect=AssertionError("Gemini must not be called on turn 1")
    )
    monkeypatch.setattr(
        "agents.pipeline.llm_clients.LLMClient.call_gemini_json", call_recorder
    )

    response = client.post(
        _PATH, json={"conversation_state": []}, headers=_bearer("good-jwt")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["response_kind"] == "question"
    option_labels = [
        b["bubble_label"] for b in body["bubbles"] if b["bubble_kind"] == "option"
    ]
    assert option_labels == [
        "AI",
        "Geopolitics",
        "Business",
        "Environment",
        "Politics",
        "Tech",
        "Sport",
        "Arts",
    ]
    fake_auth.assert_called_once_with("good-jwt")


def test_deeper_turn_returns_terminal_with_gemini_mocked(
    client: TestClient, fake_auth: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drilled conversation returns a terminal micro-interest via the mocked Gemini call."""

    async def _fake_call(self, *args: object, **kwargs: object) -> GeminiJsonResult:
        decision = InterviewDecision(
            action="terminate",
            micro_interests=[
                MicroInterestDraft(
                    display_label="IPL auctions",
                    canonical_slug="sport.cricket.ipl.auctions",
                    search_anchor_terms=["IPL auction", "player transfers"],
                )
            ],
        )
        return GeminiJsonResult(
            parsed=decision,
            prompt_tokens=1,
            output_tokens=2,
            total_tokens=3,
            elapsed_ms=1,
            model="m",
        )

    monkeypatch.setattr(
        "agents.pipeline.llm_clients.LLMClient.call_gemini_json", _fake_call
    )

    body = {
        "conversation_state": [
            {
                "question_text": "What news?",
                "bubbles_offered": [],
                "bubbles_tapped": ["Sport"],
            },
            {
                "question_text": "Which sport?",
                "bubbles_offered": [],
                "bubbles_tapped": ["Cricket"],
            },
            # The one open WHO drill for the selected sub-niche; answering it reaches terminal.
            {
                "question_text": "Cricket — name one, or skip.",
                "bubbles_offered": [],
                "bubbles_tapped": [],
                "free_text_entered": "IPL auctions",
            },
        ]
    }
    response = client.post(_PATH, json=body, headers=_bearer("good-jwt"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["response_kind"] == "terminal"
    assert (
        payload["micro_interests"][0]["canonical_slug"] == "sport.cricket.ipl.auctions"
    )
