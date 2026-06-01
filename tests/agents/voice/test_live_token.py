"""Tests for the Gemini Live ephemeral-token mint helper + worker route (SP3).

WHY (the SP3 Definition of Done + the env-var-safety mandate): the worker mints a
single-use token so ``GEMINI_API_KEY`` never reaches the device. These tests
encode the contract that matters (Rule 9), with the Gemini HTTP call mocked so no
network / no real key is needed:

  * the request body is EXACTLY ``{uses, expireTime, newSessionExpireTime}`` —
    adding ``bidiGenerateContentSetup`` would silently drop the WS (gotcha 1);
  * the key travels ONLY in the ``x-goog-api-key`` header, never in the returned
    payload (the client must never see it);
  * the minted name MUST start with ``auth_tokens/`` (gotcha 2) — a malformed
    response fails LOUD (Rule 12), it does not return a bad token;
  * the worker route returns HTTP 200 with that ``.name`` on success, and a 502
    (never a leaked key, never a silent 200) when the mint fails.

    >>> pytest tests/agents/voice/test_live_token.py -v
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agents.shared.settings import Settings
from agents.voice import live_token
from agents.voice.live_token import (
    AUTH_TOKEN_NAME_PREFIX,
    build_token_request_body,
    mint_ephemeral_token,
)
from agents.worker import main as worker_main

_VALID_TOKEN_NAME = "auth_tokens/abc123xyz"


def _settings_with_key(api_key: str = "test-gemini-key") -> Settings:
    """Build a Settings instance carrying a fake Gemini key (never a real one)."""
    return Settings(gemini_api_key=SecretStr(api_key))


def _mock_transport(
    *,
    status_code: int = 200,
    json_body: dict | None = None,
    text_body: str | None = None,
) -> httpx.MockTransport:
    """Build a MockTransport returning a canned auth_tokens response.

    Captures the inbound request on the transport instance so tests can assert the
    header + body shape (the key-handling + gotcha-1 contract).
    """

    captured: dict[str, httpx.Request] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        if text_body is not None:
            return httpx.Response(status_code, text=text_body)
        return httpx.Response(status_code, json=json_body or {})

    transport = httpx.MockTransport(_handler)
    # Reason: stash the capture dict on the transport so the test can read it back.
    transport.captured = captured  # type: ignore[attr-defined]
    return transport


# ---------------------------------------------------------------------------
# build_token_request_body — the EXACT body shape (gotcha 1)
# ---------------------------------------------------------------------------


def test_request_body_has_only_the_three_contract_keys() -> None:
    """The body is uses/expireTime/newSessionExpireTime ONLY (gotcha 1).

    WHY: locking the setup via an extra ``bidiGenerateContentSetup`` key makes the
    WS connect (HTTP 101) then silently drop without ever emitting setupComplete.
    The single most expensive mistake to regress, so it is asserted explicitly.
    """
    body = build_token_request_body()
    assert set(body.model_dump().keys()) == {
        "uses",
        "expireTime",
        "newSessionExpireTime",
    }
    assert body.uses == 1


def test_request_body_new_session_expiry_is_before_token_expiry() -> None:
    """newSessionExpireTime must be <= expireTime (a new session can't start late)."""
    body = build_token_request_body(token_ttl_seconds=1800, new_session_ttl_seconds=60)
    assert body.newSessionExpireTime <= body.expireTime


# ---------------------------------------------------------------------------
# mint_ephemeral_token — happy path, key handling, failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_returns_auth_tokens_name_on_success() -> None:
    """A 200 with a ``name`` → a typed response whose name starts auth_tokens/."""
    transport = _mock_transport(json_body={"name": _VALID_TOKEN_NAME})
    async with httpx.AsyncClient(transport=transport) as client:
        result = await mint_ephemeral_token(
            settings=_settings_with_key(), http_client=client
        )
    assert result.ephemeral_token_name == _VALID_TOKEN_NAME
    assert result.ephemeral_token_name.startswith(AUTH_TOKEN_NAME_PREFIX)


@pytest.mark.asyncio
async def test_mint_sends_key_only_in_header_never_in_body() -> None:
    """The API key travels in x-goog-api-key ONLY — never in the POST body.

    WHY (env-var-safety): the key must stay server-side. We assert it is in the
    header and that the JSON body carries none of it (only the three contract keys).
    """
    transport = _mock_transport(json_body={"name": _VALID_TOKEN_NAME})
    async with httpx.AsyncClient(transport=transport) as client:
        await mint_ephemeral_token(
            settings=_settings_with_key("secret-key-value"), http_client=client
        )
    request = transport.captured["request"]  # type: ignore[attr-defined]
    assert request.headers["x-goog-api-key"] == "secret-key-value"
    body_text = request.content.decode("utf-8")
    assert "secret-key-value" not in body_text
    assert "bidiGenerateContentSetup" not in body_text


@pytest.mark.asyncio
async def test_mint_raises_when_api_key_missing() -> None:
    """An empty GEMINI_API_KEY fails loud (RuntimeError), not an opaque 401.

    WHY (Rule 12): minting with no key would POST an empty key and get a confusing
    upstream error; failing here with a clear message is the honest behaviour.
    """
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await mint_ephemeral_token(settings=_settings_with_key(""))


@pytest.mark.asyncio
async def test_mint_raises_on_non_200() -> None:
    """A non-200 Gemini response fails loud rather than returning a bad token."""
    transport = _mock_transport(status_code=403, text_body="permission denied")
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="HTTP 403"):
            await mint_ephemeral_token(
                settings=_settings_with_key(), http_client=client
            )


@pytest.mark.asyncio
async def test_mint_raises_on_malformed_name() -> None:
    """A 200 whose name lacks the auth_tokens/ prefix fails loud (gotcha 2).

    WHY: a name without the prefix is unusable on the Constrained endpoint and
    would cause a silent WS drop on the client — catch it at the mint boundary.
    """
    transport = _mock_transport(json_body={"name": "not-a-valid-token"})
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="auth_tokens/"):
            await mint_ephemeral_token(
                settings=_settings_with_key(), http_client=client
            )


# ---------------------------------------------------------------------------
# Worker route — POST /api/voice/live-token
# ---------------------------------------------------------------------------


def test_route_returns_200_with_auth_tokens_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route returns 200 + a body whose ephemeral_token_name starts auth_tokens/.

    WHY (the SP3 DoD): this is the contract the client depends on. The mint helper
    is patched so no network / key is needed.
    """

    async def _fake_mint() -> live_token.EphemeralTokenResponse:
        return live_token.EphemeralTokenResponse(
            ephemeral_token_name=_VALID_TOKEN_NAME,
            expire_time_iso="2026-05-31T12:00:00Z",
        )

    monkeypatch.setattr(worker_main, "mint_ephemeral_token", _fake_mint)
    client = TestClient(worker_main.app)

    response = client.post("/api/voice/live-token")

    assert response.status_code == 200
    body = response.json()
    assert body["ephemeral_token_name"].startswith("auth_tokens/")


def test_route_returns_502_on_mint_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mint failure → HTTP 502 (fail loud), not a 200 with a fake token.

    WHY (Rule 12): unlike the Q&A endpoint, a missing token has no graceful
    in-conversation fallback — the client can't open the WSS at all — so the
    honest signal is an explicit error, never a silently-empty success.
    """

    async def _fail_mint() -> live_token.EphemeralTokenResponse:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    monkeypatch.setattr(worker_main, "mint_ephemeral_token", _fail_mint)
    client = TestClient(worker_main.app)

    response = client.post("/api/voice/live-token")

    assert response.status_code == 502
