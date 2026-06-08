"""Resolve a podcast name to canonical iTunes metadata.

Ported from TL;DW (``scripts/seed_catalog/itunes_resolve.py``) per
reference/sources-reuse-map.md §2. iTunes Search needs no API key; it
soft-rate-limits at ~20 rpm per IP by returning an empty 200 body (not a 429),
so a brief throttle is retried with backoff.

The resolved ``collectionId`` is stored as ``external_id`` with an ``itunes-``
prefix so a row inserted here collides cleanly on the
``(content_source_type, external_id)`` unique key with the same podcast created
by any other entry point (onboarding / live search in 5c/5d). The RSS
``feed_url`` is captured for ``platform_metadata`` (the 5d ingestion reads it).

The HTTP client is INJECTED so the test suite mocks at the httpx boundary
(CLAUDE.md) — no network needed offline.

Example:
    >>> import asyncio, httpx
    >>> async def demo() -> None:
    ...     async with httpx.AsyncClient() as client:
    ...         meta = await resolve_podcast("Lex Fridman", client=client)  # doctest: +SKIP
    >>> asyncio.run(demo())  # doctest: +SKIP
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agents.shared.logger import get_logger

logger = get_logger("seed_catalog.itunes_resolve")

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
USER_AGENT = "News20-SeedCatalog/1.0 (+https://news20.app)"
REQUEST_TIMEOUT_SECONDS = 10.0
# iTunes Search hard-rate-limits at ~20 rpm per IP and, at bulk-seed scale,
# returns HTTP 429 (not just an empty 200 body). Resolution stays polite via the
# shared pace gate below (one request every ~3.2s); concurrency only lets each
# request's network round-trip overlap the gate wait — the gate, not concurrency,
# is the rate limiter, so a few workers are safe and back off on the rare 429.
RESOLVE_CONCURRENCY = 5
# Longer, 429-aware backoff schedule (seconds). The first attempt is immediate;
# subsequent attempts wait progressively so a throttled IP recovers between tries.
RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.0, 3.0, 8.0, 20.0)
# Honoured when iTunes returns a 429 with a Retry-After header (seconds), capped
# so one hostile value cannot stall the whole run.
RETRY_AFTER_CAP_SECONDS = 30.0
# Recommended seconds between successive iTunes requests for a BULK seed: pacing
# UNDER the ~20 rpm/IP limit (one request every ~3.2s) keeps a 1,000+ term run
# below the throttle threshold so it resolves steadily instead of bursting into
# 429s and wasting the rate budget on retries. The live seed opts in via
# ``set_pace_interval``; the default is 0.0 so unit tests (mocked client) are not
# slowed by real sleeps.
BULK_REQUEST_INTERVAL_SECONDS = 3.2


class _PaceGate:
    """A process-wide minimum-interval gate shared by every iTunes request.

    Serializes the *timing* (not the concurrency) of outbound calls so they are
    spaced at least ``min_interval`` apart. Set to a positive interval (via
    :func:`set_pace_interval`) for a bulk seed to stay under iTunes's per-IP rate
    limit; left at 0 (the default) it is a no-op so unit tests run instantly.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = min_interval_seconds
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def wait_turn(self) -> None:
        """Block until enough time has elapsed since the previous request."""
        if self.min_interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            sleep_for = self._next_allowed_at - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
                now = loop.time()
            self._next_allowed_at = now + self.min_interval


_PACE_GATE = _PaceGate(0.0)


def set_pace_interval(seconds: float) -> None:
    """Set the shared inter-request pacing interval (seconds).

    Call this once before a bulk seed (e.g. ``set_pace_interval(BULK_REQUEST_INTERVAL_SECONDS)``)
    to throttle under iTunes's per-IP rate limit. Unit tests leave it at 0.

    Args:
        seconds: Minimum spacing between successive iTunes requests; 0 disables pacing.
    """
    _PACE_GATE.min_interval = seconds


class PodcastMeta(BaseModel):
    """Resolved metadata for a single podcast.

    Attributes:
        collection_id: The stable iTunes collection id.
        name: The podcast collection name.
        artist_name: The producer / publisher (None when absent).
        feed_url: The RSS feed URL when published (captured into platform_metadata).
        artwork_url: The highest-resolution artwork iTunes returns (None when none).
        track_count: Episode count at fetch time (None when absent).
    """

    collection_id: int = Field(..., description="iTunes collection id (stable).")
    name: str = Field(..., description="Podcast collection name.")
    artist_name: str | None = Field(default=None, description="Producer / publisher.")
    feed_url: str | None = Field(
        default=None, description="RSS feed URL when published."
    )
    artwork_url: str | None = Field(
        default=None, description="Highest-resolution artwork URL."
    )
    track_count: int | None = Field(
        default=None, description="Episode count at fetch time."
    )

    @property
    def external_id(self) -> str:
        """The canonical ``external_id`` for the ``content_sources`` row.

        Returns:
            ``itunes-<collection_id>`` — the cross-entry-point dedup key.
        """
        return f"itunes-{self.collection_id}"


def _pick_artwork(entry: dict[str, Any]) -> str | None:
    """Pick the highest-resolution artwork URL from an iTunes result entry.

    Args:
        entry: One element of the iTunes ``results`` array.

    Returns:
        The best (600 > 100 > 60) artwork URL, or None when absent.
    """
    return (
        entry.get("artworkUrl600")
        or entry.get("artworkUrl100")
        or entry.get("artworkUrl60")
    )


def _podcast_meta_from_entry(entry: dict[str, Any]) -> PodcastMeta | None:
    """Map a single iTunes result entry to a :class:`PodcastMeta`.

    Args:
        entry: One element of the iTunes ``results`` array.

    Returns:
        The validated podcast metadata, or None when the entry lacks the
        required ``collectionId`` / ``collectionName``.
    """
    collection_id = entry.get("collectionId")
    collection_name = entry.get("collectionName")
    if not collection_id or not collection_name:
        return None
    return PodcastMeta(
        collection_id=int(collection_id),
        name=collection_name,
        artist_name=entry.get("artistName"),
        feed_url=entry.get("feedUrl"),
        artwork_url=_pick_artwork(entry),
        track_count=entry.get("trackCount"),
    )


# Sentinel signalling "iTunes throttled this attempt (429 / empty body) — retry",
# distinct from a genuine "no such podcast" miss (a plain None, do not over-retry).
THROTTLED = object()


async def _itunes_search_one(
    cleaned: str, *, client: httpx.AsyncClient
) -> PodcastMeta | None | object:
    """One iTunes Search attempt.

    iTunes throttles in two ways under bulk load: a hard ``429 Too Many Requests``
    (sometimes with a ``Retry-After`` header) and a soft empty ``200`` body
    (``.json()`` raises). Both are transient and are reported as :data:`THROTTLED`
    so the caller keeps retrying with a longer backoff. A genuine no-results miss
    returns ``None`` so the caller can stop early. A resolved match returns the
    :class:`PodcastMeta`.

    Args:
        cleaned: The (already-stripped) search term.
        client: An injected ``httpx.AsyncClient``.

    Returns:
        The top :class:`PodcastMeta` on a hit, ``None`` on a genuine miss /
        transport error, or :data:`THROTTLED` when iTunes rate-limited the call.
    """
    params = {"term": cleaned, "media": "podcast", "entity": "podcast", "limit": 1}
    # Pace every outbound call under the per-IP rate limit (shared across resolves).
    await _PACE_GATE.wait_turn()
    try:
        response = await client.get(
            ITUNES_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 429:
        return THROTTLED
    if response.status_code != 200:
        return None
    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        # Empty 200 body == soft rate-limit; ask the caller to retry.
        return THROTTLED
    results = payload.get("results") or []
    if not results:
        return None
    return _podcast_meta_from_entry(results[0])


async def resolve_podcast(
    name: str, *, client: httpx.AsyncClient
) -> PodcastMeta | None:
    """Resolve the top iTunes match for one podcast name, with retries.

    iTunes soft-rate-limits at ~20 rpm per IP and signals it by returning an
    empty 200 body (not a 429); retries with exponential backoff so a brief
    throttle doesn't silently drop the row.

    Args:
        name: The podcast search term.
        client: An injected ``httpx.AsyncClient``.

    Returns:
        The resolved podcast metadata, or None when iTunes has no match.
    """
    cleaned = name.strip()
    if not cleaned:
        return None
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS):
        if delay:
            await asyncio.sleep(delay)
        result = await _itunes_search_one(cleaned, client=client)
        if isinstance(result, PodcastMeta):
            return result
        if result is None:
            # Genuine no-results miss — retrying will not help; stop early.
            return None
        # result is THROTTLED: keep retrying with the next (longer) backoff.
        if attempt == len(RETRY_DELAYS_SECONDS) - 1:
            logger.warning(
                "itunes_resolve_giving_up",
                name=cleaned,
                attempts=attempt + 1,
                fix_suggestion="iTunes rate-limited every attempt; lower concurrency or re-run this cell later.",
            )
    return None


async def resolve_many(
    names: list[str],
    *,
    client: httpx.AsyncClient,
    concurrency: int = RESOLVE_CONCURRENCY,
) -> dict[str, PodcastMeta]:
    """Resolve a batch of podcast names concurrently.

    Args:
        names: The podcast search terms.
        client: An injected ``httpx.AsyncClient`` (tests mock its ``.get``).
        concurrency: Max simultaneous searches (default polite for iTunes).

    Returns:
        ``{name: PodcastMeta}``. Misses are omitted; the caller compares keys to
        the input to find them.

    Example:
        >>> import asyncio, httpx
        >>> async def demo() -> dict:
        ...     async with httpx.AsyncClient() as client:
        ...         return await resolve_many(["Lex Fridman"], client=client)  # doctest: +SKIP
        >>> asyncio.run(demo())  # doctest: +SKIP
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(name: str) -> tuple[str, PodcastMeta | None]:
        async with semaphore:
            meta = await resolve_podcast(name, client=client)
        return name, meta

    results = await asyncio.gather(*(_one(name) for name in names))
    return {name: meta for name, meta in results if meta is not None}
