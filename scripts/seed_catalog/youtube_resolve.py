"""Resolve a YouTube channel handle (or channel id) to canonical metadata.

Ported from TL;DW (``scripts/seed_catalog/youtube_resolve.py``) per
reference/sources-reuse-map.md §2. Converts a curator-provided handle like
``AndrejKarpathy`` into the persistent shape the News20 ``content_sources`` row
stores: ``external_id`` (the stable ``UC…`` channel id), ``thumbnail_url``,
``subscriber_count``, plus title/description.

Quota cost: ``channels.list?forHandle`` is 1 unit per call. With a 10k unit
daily quota the whole curated catalog resolves in a single day even with retries.

The HTTP client is INJECTED into ``resolve_channel`` so the test suite mocks at
the httpx boundary (CLAUDE.md) — no network, no key needed offline.

Example:
    >>> import asyncio, httpx
    >>> async def demo() -> None:
    ...     async with httpx.AsyncClient() as client:
    ...         meta = await resolve_channel(  # doctest: +SKIP
    ...             handle="AndrejKarpathy", api_key="KEY", client=client
    ...         )
    >>> asyncio.run(demo())  # doctest: +SKIP
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agents.shared.logger import get_logger

logger = get_logger("seed_catalog.youtube_resolve")

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
REQUEST_TIMEOUT_SECONDS = 10.0
RESOLVE_CONCURRENCY = 4


class ChannelMeta(BaseModel):
    """Resolved metadata for a single YouTube channel.

    Attributes:
        channel_id: The stable ``UC…`` YouTube channel id (the ``external_id``).
        handle: The canonical handle without a leading ``@`` (None when absent).
        title: The channel display name.
        description: The channel about-page blurb (None when empty).
        thumbnail_url: The highest-resolution avatar URL available (None when none).
        subscriber_count: Live subscriber count, or None when the channel hides it.
    """

    channel_id: str = Field(
        ..., description="UC… stable YouTube channel id (external_id)."
    )
    handle: str | None = Field(
        default=None, description="Canonical handle without the @."
    )
    title: str = Field(..., description="Channel display name.")
    description: str | None = Field(
        default=None, description="Channel about-page blurb."
    )
    thumbnail_url: str | None = Field(
        default=None, description="Highest-resolution avatar URL."
    )
    subscriber_count: int | None = Field(
        default=None,
        description="Live subscriber count, or None when hidden by the channel.",
    )


def _pick_thumbnail(snippet: dict[str, Any] | None) -> str | None:
    """Pick the highest-resolution thumbnail URL from a channel snippet.

    Args:
        snippet: The ``snippet`` object from a ``channels.list`` item (may be None).

    Returns:
        The best (high > medium > default) thumbnail URL, or None when absent.
    """
    if not snippet:
        return None
    thumbs = snippet.get("thumbnails") or {}
    return (
        (thumbs.get("high") or {}).get("url")
        or (thumbs.get("medium") or {}).get("url")
        or (thumbs.get("default") or {}).get("url")
    )


def _parse_subscriber_count(stats: dict[str, Any] | None) -> int | None:
    """Parse the subscriber count from a channel statistics object.

    Args:
        stats: The ``statistics`` object from a ``channels.list`` item (may be None).

    Returns:
        The integer subscriber count, or None when hidden, missing, or unparseable.
    """
    if not stats:
        return None
    if stats.get("hiddenSubscriberCount"):
        return None
    raw = stats.get("subscriberCount")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _channel_meta_from_item(item: dict[str, Any]) -> ChannelMeta:
    """Map a single ``channels.list`` API item to a :class:`ChannelMeta`.

    Args:
        item: One element of the API ``items`` array.

    Returns:
        The validated channel metadata model.
    """
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    handle = snippet.get("customUrl")
    if isinstance(handle, str):
        handle = handle.lstrip("@") or None
    return ChannelMeta(
        channel_id=item["id"],
        handle=handle,
        title=snippet.get("title") or "",
        description=snippet.get("description") or None,
        thumbnail_url=_pick_thumbnail(snippet),
        subscriber_count=_parse_subscriber_count(stats),
    )


async def _channels_list(
    params: dict[str, Any], *, client: httpx.AsyncClient
) -> ChannelMeta | None:
    """Call ``channels.list`` once and map the first item to :class:`ChannelMeta`.

    Returns None on any HTTP error, non-200 status, or empty result (the caller
    logs + skips). Never raises on transport/status — a miss is a None, not an
    exception, so a single bad handle cannot abort a batch resolve.

    Args:
        params: Query params (without the URL) — ``forHandle``/``id`` + ``part`` + ``key``.
        client: An injected ``httpx.AsyncClient``.

    Returns:
        The resolved channel metadata, or None on miss/error.
    """
    url = f"{YOUTUBE_API_BASE}/channels"
    safe_params = {key: value for key, value in params.items() if key != "key"}
    try:
        response = await client.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning(
            "youtube_resolve_http_error",
            params=safe_params,
            error_message=str(exc),
            fix_suggestion="Inspect YouTube Data API v3 connectivity / DNS.",
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "youtube_resolve_non_ok",
            status_code=response.status_code,
            params=safe_params,
            fix_suggestion="Verify YOUTUBE_API_KEY has Data API v3 enabled and quota remaining.",
        )
        return None

    payload: dict[str, Any] = response.json()
    items = payload.get("items") or []
    if not items:
        return None
    return _channel_meta_from_item(items[0])


async def resolve_channel(
    *,
    api_key: str,
    client: httpx.AsyncClient,
    handle: str | None = None,
    channel_id: str | None = None,
) -> ChannelMeta | None:
    """Resolve a single channel by handle (preferred) or channel id.

    Tries ``forHandle`` first when a handle is supplied; falls back to ``id``
    when a channel id is supplied. Returns None on any miss/error.

    Args:
        api_key: The YouTube Data API v3 key (resolved at the call boundary, never logged).
        client: An injected ``httpx.AsyncClient``.
        handle: The channel handle (with or without a leading ``@``). Optional.
        channel_id: The ``UC…`` channel id. Optional (used when no handle resolves).

    Returns:
        The resolved channel metadata, or None when neither input resolves.
    """
    cleaned_handle = (handle or "").lstrip("@").strip()
    if cleaned_handle:
        meta = await _channels_list(
            {"forHandle": cleaned_handle, "part": "snippet,statistics", "key": api_key},
            client=client,
        )
        if meta is not None:
            return meta

    cleaned_id = (channel_id or "").strip()
    if cleaned_id:
        return await _channels_list(
            {"id": cleaned_id, "part": "snippet,statistics", "key": api_key},
            client=client,
        )
    return None


async def resolve_many(
    entries: list[dict[str, Any]],
    *,
    api_key: str,
    client: httpx.AsyncClient,
    concurrency: int = RESOLVE_CONCURRENCY,
) -> dict[str, ChannelMeta]:
    """Resolve a batch of channels concurrently.

    Args:
        entries: Dicts each carrying ``youtube_handle`` and/or ``channel_id``.
        api_key: The YouTube Data API v3 key.
        client: An injected ``httpx.AsyncClient`` (tests mock its ``.get``).
        concurrency: Max simultaneous API calls (default keeps us under quota).

    Returns:
        ``{key: ChannelMeta}`` where ``key`` is the lowercased handle when present,
        else the lowercased channel id. Misses are omitted.

    Example:
        >>> import asyncio, httpx
        >>> async def demo() -> dict:
        ...     async with httpx.AsyncClient() as client:
        ...         return await resolve_many(  # doctest: +SKIP
        ...             [{"youtube_handle": "AndrejKarpathy"}], api_key="K", client=client
        ...         )
        >>> asyncio.run(demo())  # doctest: +SKIP
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(entry: dict[str, Any]) -> tuple[str, ChannelMeta | None]:
        handle = entry.get("youtube_handle")
        channel_id = entry.get("channel_id")
        key = (handle or channel_id or "").lower()
        async with semaphore:
            meta = await resolve_channel(
                api_key=api_key, client=client, handle=handle, channel_id=channel_id
            )
        return key, meta

    results = await asyncio.gather(*(_one(entry) for entry in entries))
    return {key: meta for key, meta in results if meta is not None and key}
