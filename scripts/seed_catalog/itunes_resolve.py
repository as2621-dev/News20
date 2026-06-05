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
RESOLVE_CONCURRENCY = 2
RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.0, 1.5, 4.0)


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


async def _itunes_search_one(
    cleaned: str, *, client: httpx.AsyncClient
) -> PodcastMeta | None:
    """One iTunes Search attempt. Returns None on miss / any error.

    iTunes signals a soft rate-limit with an empty 200 body (``.json()`` raises),
    which is treated as a miss so the caller can retry with backoff.

    Args:
        cleaned: The (already-stripped) search term.
        client: An injected ``httpx.AsyncClient``.

    Returns:
        The top podcast match, or None on miss / transport error / empty body.
    """
    params = {"term": cleaned, "media": "podcast", "entity": "podcast", "limit": 1}
    try:
        response = await client.get(
            ITUNES_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        return None
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
        if result is not None:
            return result
        if attempt == len(RETRY_DELAYS_SECONDS) - 1:
            logger.warning(
                "itunes_resolve_giving_up",
                name=cleaned,
                attempts=attempt + 1,
                fix_suggestion="Try a less specific search term, or skip — iTunes may lack this title.",
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
