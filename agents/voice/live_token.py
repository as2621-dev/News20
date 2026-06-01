"""Gemini Live ephemeral-token mint helper (Phase 3 SP3).

The Gemini Live realtime voice transport runs over a **raw WebSocket opened from
the browser**. To keep the long-lived ``GEMINI_API_KEY`` off the device, the
client never sees it: instead the worker mints a short-lived, single-use
**ephemeral token** server-side and hands only that token to the frontend, which
passes it to the constrained Bidi endpoint via ``?access_token=`` (see
``src/lib/voice/useGeminiLive.ts`` gotcha 2).

Hard-won contract (memory ``news20-gemini-live-tts-contract.md`` gotcha 1 — port
VERBATIM):

* ``POST https://generativelanguage.googleapis.com/v1alpha/auth_tokens``
* header ``x-goog-api-key: <GEMINI_API_KEY>`` (the key stays here, never logged,
  never returned to the client)
* body is ``{"uses": 1, "expireTime": ..., "newSessionExpireTime": ...}`` **ONLY**
  — do NOT lock the setup via ``bidiGenerateContentSetup`` (that causes the WS to
  drop without ever sending ``setupComplete``)
* the minted token is returned in the response ``.name`` and starts with
  ``auth_tokens/``.

This module is intentionally a single pure-ish async function plus its typed
request/response models so it is trivially unit-testable with the Gemini HTTP
call mocked (Rule 9 / the SP3 DoD).

Example:
    >>> from agents.voice.live_token import mint_ephemeral_token
    >>> token = await mint_ephemeral_token()
    >>> token.ephemeral_token_name.startswith("auth_tokens/")
    True
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from pydantic import BaseModel, Field

from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("voice.live_token")


# ---------------------------------------------------------------------------
# Constants — the verbatim Gemini Live auth-token contract
# ---------------------------------------------------------------------------

GEMINI_AUTH_TOKENS_URL = "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"

# Reason: a Live session is short — the token is single-use (``uses=1``) and only
# needs to live long enough for the client to open the WSS. These windows match
# the working contract: ~30 min token validity, ~1 min to start a new session.
DEFAULT_TOKEN_TTL_SECONDS = 30 * 60
DEFAULT_NEW_SESSION_TTL_SECONDS = 60

# Reason: the minted token name MUST start with this prefix to be usable on the
# ``BidiGenerateContentConstrained`` endpoint (gotcha 2). We assert it so a
# malformed response fails loud here (Rule 12) instead of silently dropping the
# WS later on the client.
AUTH_TOKEN_NAME_PREFIX = "auth_tokens/"

DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0


class EphemeralTokenRequestBody(BaseModel):
    """The EXACT body sent to ``v1alpha/auth_tokens`` (gotcha 1 — these keys ONLY).

    Deliberately carries nothing else — adding ``bidiGenerateContentSetup`` here
    locks the setup server-side and makes the WS drop without ``setupComplete``.

    Attributes:
        uses: How many times the token may open a session. Always ``1`` (the
            client guards against React-StrictMode double-connect).
        expireTime: RFC-3339 UTC instant after which the token is invalid.
        newSessionExpireTime: RFC-3339 UTC instant after which the token can no
            longer *start* a new session (shorter than ``expireTime``).
    """

    uses: int = Field(
        default=1, ge=1, description="Single-use session token (always 1)."
    )
    expireTime: str = Field(..., description="RFC-3339 UTC instant the token expires.")
    newSessionExpireTime: str = Field(
        ..., description="RFC-3339 UTC instant after which no new session may start."
    )


class EphemeralTokenResponse(BaseModel):
    """The typed result handed back to the worker route (and on to the client).

    Carries ONLY the opaque token name — never the API key, never any other field
    from the Gemini response. The client passes ``ephemeral_token_name`` to the
    WSS via ``?access_token=<name>``.

    Attributes:
        ephemeral_token_name: The minted token id, e.g. ``auth_tokens/abc123`` —
            always starts with :data:`AUTH_TOKEN_NAME_PREFIX`.
        expire_time_iso: When the token expires (RFC-3339 UTC), echoed so the
            client can avoid using a stale token.
    """

    ephemeral_token_name: str = Field(
        ...,
        description="The minted Gemini Live ephemeral token name (auth_tokens/...).",
    )
    expire_time_iso: str = Field(
        ..., description="RFC-3339 UTC instant the token expires."
    )


def _rfc3339_utc(instant: datetime) -> str:
    """Format an aware UTC datetime as an RFC-3339 string Gemini accepts.

    Args:
        instant: A timezone-aware UTC datetime.

    Returns:
        An RFC-3339 string with a trailing ``Z`` (e.g. ``2026-05-31T12:00:00Z``).
    """
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_token_request_body(
    *,
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    new_session_ttl_seconds: int = DEFAULT_NEW_SESSION_TTL_SECONDS,
) -> EphemeralTokenRequestBody:
    """Build the single-use auth-token request body with computed expiry windows.

    Pure (clock aside) and exported so the exact body shape is unit-testable
    without making an HTTP call.

    Args:
        token_ttl_seconds: Seconds until the token expires.
        new_session_ttl_seconds: Seconds until the token can no longer start a
            new session (kept shorter than ``token_ttl_seconds``).

    Returns:
        A validated :class:`EphemeralTokenRequestBody`.

    Example:
        >>> body = build_token_request_body()
        >>> body.uses
        1
    """
    now_utc = datetime.now(timezone.utc)
    return EphemeralTokenRequestBody(
        uses=1,
        expireTime=_rfc3339_utc(now_utc + timedelta(seconds=token_ttl_seconds)),
        newSessionExpireTime=_rfc3339_utc(
            now_utc + timedelta(seconds=new_session_ttl_seconds)
        ),
    )


async def mint_ephemeral_token(
    *,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    new_session_ttl_seconds: int = DEFAULT_NEW_SESSION_TTL_SECONDS,
) -> EphemeralTokenResponse:
    """Mint a single-use Gemini Live ephemeral token (the key stays server-side).

    Calls ``POST v1alpha/auth_tokens`` with the ``x-goog-api-key`` header and the
    minimal ``{uses, expireTime, newSessionExpireTime}`` body, then returns the
    token name from the response ``.name``. The ``GEMINI_API_KEY`` is read from
    :class:`~agents.shared.settings.Settings` and is NEVER logged or returned to
    the caller (env-var-safety mandate).

    Args:
        settings: Application settings carrying ``GEMINI_API_KEY``. Defaults to a
            freshly-loaded :class:`Settings` (reads ``.env`` / the environment).
        http_client: An optional injected ``httpx.AsyncClient`` (the unit-test
            seam — pass a mock transport here). When ``None`` a client is created
            and closed for the single call.
        token_ttl_seconds: Seconds until the token expires.
        new_session_ttl_seconds: Seconds until no new session may start.

    Returns:
        A typed :class:`EphemeralTokenResponse` whose ``ephemeral_token_name``
        starts with ``auth_tokens/``.

    Raises:
        RuntimeError: If ``GEMINI_API_KEY`` is unset, the HTTP call fails, or the
            response is missing/malformed a ``name`` (fails loud — Rule 12). The
            worker route catches this and returns a typed 5xx-free error payload.

    Example:
        >>> token = await mint_ephemeral_token()
        >>> token.ephemeral_token_name.startswith("auth_tokens/")
        True
    """
    resolved_settings = settings or Settings()
    # Reason: the Live transport authenticates with the *main* Gemini key (Live
    # API), not the TTS-permissioned key. Read it only here, at the call boundary.
    api_key = resolved_settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        # Reason: fail loud rather than POST an empty key and get an opaque 401.
        logger.error(
            "live_token_missing_api_key",
            error_message="GEMINI_API_KEY is not configured",
            fix_suggestion="Set GEMINI_API_KEY in the worker .env before minting Live tokens",
        )
        raise RuntimeError(
            "GEMINI_API_KEY is not configured for Gemini Live token minting"
        )

    request_body = build_token_request_body(
        token_ttl_seconds=token_ttl_seconds,
        new_session_ttl_seconds=new_session_ttl_seconds,
    )

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS)
    try:
        # Reason: the key travels ONLY in this header — never in a log field, never
        # in the response we return to the client.
        response = await client.post(
            GEMINI_AUTH_TOKENS_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=request_body.model_dump(),
        )
    except httpx.HTTPError as exc:
        logger.error(
            "live_token_http_error",
            error_message=str(exc)[:200],
            fix_suggestion="Check network reachability to generativelanguage.googleapis.com",
        )
        raise RuntimeError("Gemini Live token mint HTTP call failed") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        logger.error(
            "live_token_non_200",
            status_code=response.status_code,
            # Reason: the response body may echo request context but never our key.
            error_message=response.text[:200],
            fix_suggestion="Verify GEMINI_API_KEY validity + Live API access for this key",
        )
        raise RuntimeError(
            f"Gemini Live token mint returned HTTP {response.status_code}"
        )

    payload = response.json()
    token_name = payload.get("name", "")
    if not isinstance(token_name, str) or not token_name.startswith(
        AUTH_TOKEN_NAME_PREFIX
    ):
        logger.error(
            "live_token_malformed_response",
            error_message=f"response .name did not start with {AUTH_TOKEN_NAME_PREFIX!r}",
            fix_suggestion="Inspect the v1alpha/auth_tokens response shape; expected a name like auth_tokens/...",
        )
        raise RuntimeError(
            "Gemini Live token mint response is missing a valid auth_tokens/ name"
        )

    logger.info(
        "live_token_minted",
        # Reason: log the PREFIX only, never the full token (treat it as a secret).
        token_name_prefix=AUTH_TOKEN_NAME_PREFIX,
        expire_time_iso=request_body.expireTime,
    )
    return EphemeralTokenResponse(
        ephemeral_token_name=token_name,
        expire_time_iso=request_body.expireTime,
    )
