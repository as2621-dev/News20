"""Resolve an X (Twitter) handle to a real avatar URL via unavatar.io.

There is no live X API in Phase 5f (taxonomy Q2), so the X axis cannot fetch
follower counts or canonical profile metadata. What it CAN do — and must, to hit
the ≥90% thumbnail-coverage DoD and to verify a handle is real (anti-hallucination
guard, mirroring how the YouTube/iTunes/Wikipedia resolvers independently verify
every LLM proposal) — is probe **unavatar.io**:

    https://unavatar.io/x/<handle>?fallback=false

``fallback=false`` makes unavatar return ``404`` when it has NO real avatar for
the handle (instead of silently serving a generated placeholder). So:

  - ``200`` ⇒ a real avatar exists ⇒ the handle is real ⇒ we store the avatar URL
    WITHOUT ``fallback=false`` (``https://unavatar.io/x/<handle>``) so the app
    always renders something even if unavatar's upstream briefly changes.
  - ``404`` ⇒ no real avatar ⇒ a soft miss: the row keeps ``thumbnail_url=None``
    (honest) and is logged. The handle may still be a real account unavatar just
    can't mirror; the seeder keeps the row (count) but it does not count toward
    avatar coverage.

The HTTP client is INJECTED into ``resolve_avatar`` so the test suite mocks at
the httpx boundary (CLAUDE.md) — no network needed offline. Probes are gated by a
small semaphore to stay polite to unavatar (it is lenient but should not be
hammered).

Example:
    >>> import asyncio, httpx
    >>> async def demo() -> None:
    ...     async with httpx.AsyncClient() as client:
    ...         url = await resolve_avatar(handle="karpathy", client=client)  # doctest: +SKIP
    >>> asyncio.run(demo())  # doctest: +SKIP
"""

from __future__ import annotations

import asyncio

import httpx

from agents.shared.logger import get_logger

logger = get_logger("seed_catalog.x_resolve")

UNAVATAR_BASE = "https://unavatar.io/x"
REQUEST_TIMEOUT_SECONDS = 10.0
# unavatar's free anonymous tier caps at ~25 req/day/IP (see https://unavatar.io/erate),
# so a full run will exhaust verification quickly; keep concurrency low to stay
# polite — the hot-link is stored regardless of the probe outcome.
RESOLVE_CONCURRENCY = 4


def _avatar_url(handle: str) -> str:
    """Return the stored (renderable) unavatar URL for a handle, sans probe flag.

    The stored URL omits ``fallback=false`` so the app always renders SOMETHING
    even if unavatar's upstream changes after the seed; the ``fallback=false``
    flag is only used during the probe to detect a genuine no-avatar miss.

    Args:
        handle: The bare X handle (no leading ``@``).

    Returns:
        ``https://unavatar.io/x/<handle>``.
    """
    return f"{UNAVATAR_BASE}/{handle}"


async def resolve_avatar(*, handle: str, client: httpx.AsyncClient) -> str | None:
    """Resolve the hot-linked unavatar avatar URL for one X handle.

    Taxonomy Q2/Q3 (reference/source-catalog-taxonomy.md) locks the X avatar as a
    HOT-LINK to ``unavatar.io/x/<handle>`` — unavatar serves the real X avatar when
    it can fetch/cache it and a generated initials-avatar otherwise, so the URL
    always renders SOMETHING in the app. This resolver therefore returns that
    hot-link URL for any non-blank handle (100% renderable coverage by design).

    It additionally PROBES unavatar (plain ``GET`` of the same URL, following
    redirects) purely as a best-effort realness signal for logging — a
    ``200 image/*`` confirms unavatar has a real cached avatar; a ``404`` is a
    confirmed no-such-avatar (still hot-linked — the app falls back to its own
    initials avatar); a ``429`` is unavatar's per-IP daily anonymous rate limit
    (``25 req/day`` on the free tier — see https://unavatar.io/erate), which means
    "could not verify", NOT "no avatar". A bare/blank handle yields ``None`` (no
    real account to hot-link).

    Args:
        handle: The X handle (with or without a leading ``@``).
        client: An injected ``httpx.AsyncClient``.

    Returns:
        The hot-link avatar URL for any non-blank handle, except a confirmed
        ``404`` (returns ``None`` so the caller stores a null thumbnail). A blank
        handle also returns ``None``.
    """
    cleaned = (handle or "").strip().lstrip("@").strip().lower()
    if not cleaned:
        return None

    hot_link = _avatar_url(cleaned)
    try:
        response = await client.get(
            hot_link,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        # Could not reach unavatar to verify; still hot-link (it renders when
        # reachable). Honest log so a connectivity issue is visible.
        logger.warning(
            "x_resolve_http_error",
            handle=cleaned,
            error_message=str(exc),
            fix_suggestion="Could not reach unavatar to verify; hot-link kept (renders when reachable).",
        )
        return hot_link

    content_type = response.headers.get("content-type", "")
    if response.status_code == 200 and content_type.startswith("image/"):
        return hot_link
    if response.status_code == 404:
        logger.info(
            "x_resolve_no_avatar",
            handle=cleaned,
            fix_suggestion="unavatar has no avatar for this handle; stored with null thumbnail.",
        )
        return None
    if response.status_code == 429:
        # Per-IP daily anonymous cap (25/day free) — verification budget spent.
        # NOT a no-avatar signal, so keep the hot-link (renders on a fresh day /
        # with an API key). Logged loudly so the coverage caveat is never silent.
        logger.warning(
            "x_resolve_rate_limited",
            handle=cleaned,
            retry_after_seconds=response.headers.get("retry-after"),
            fix_suggestion="unavatar free tier = 25 req/day/IP; hot-link kept (unverified). Register a key to verify at scale.",
        )
        return hot_link
    logger.warning(
        "x_resolve_unexpected_status",
        handle=cleaned,
        status_code=response.status_code,
        fix_suggestion="Unexpected unavatar status; hot-link kept (best effort).",
    )
    return hot_link


async def resolve_many(
    handles: list[str],
    *,
    client: httpx.AsyncClient,
    concurrency: int = RESOLVE_CONCURRENCY,
) -> dict[str, str]:
    """Resolve avatars for a batch of X handles concurrently.

    Args:
        handles: The X handles (with or without a leading ``@``).
        client: An injected ``httpx.AsyncClient`` (tests mock its ``.get``).
        concurrency: Max simultaneous unavatar probes (default polite).

    Returns:
        ``{handle_key: avatar_url}`` keyed by the lowercased bare handle, carrying
        the hot-link URL for every handle except a confirmed ``404`` / blank handle
        (those are omitted → the caller stores a null thumbnail). The caller
        compares keys to the input to find the omitted ones.

    Example:
        >>> import asyncio, httpx
        >>> async def demo() -> dict:
        ...     async with httpx.AsyncClient() as client:
        ...         return await resolve_many(["karpathy"], client=client)  # doctest: +SKIP
        >>> asyncio.run(demo())  # doctest: +SKIP
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(handle: str) -> tuple[str, str | None]:
        key = (handle or "").strip().lstrip("@").strip().lower()
        async with semaphore:
            avatar = await resolve_avatar(handle=handle, client=client)
        return key, avatar

    results = await asyncio.gather(*(_one(handle) for handle in handles))
    return {key: avatar for key, avatar in results if avatar is not None and key}
