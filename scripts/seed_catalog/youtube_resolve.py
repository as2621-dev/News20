"""Resolve a YouTube channel handle to canonical metadata — KEYLESS via yt-dlp.

Converts a curator-provided handle like ``AndrejKarpathy`` into the persistent
shape the News20 ``content_sources`` row stores: ``external_id`` (the stable
``UC…`` channel id), ``thumbnail_url``, ``subscriber_count``, plus title/handle/
description.

Why yt-dlp and not the YouTube Data API
---------------------------------------
The repo standard for YouTube is **yt-dlp** (already used for transcript pulls;
installed on the worker + locally). It needs **no API key** — it reads the public
channel page. The prior implementation hit ``channels.list?forHandle`` on the Data
API v3 and forced a ``YOUTUBE_API_KEY`` we do not have; this resolver drops that
dependency entirely. Field mapping (verified keyless 2026-07-04 on
``@AndrejKarpathy`` / ``@veritasium``):

  - ``channel_id``       ← yt-dlp ``channel_id`` (the ``UC…`` external_id)
  - ``title``            ← ``channel``
  - ``handle``           ← ``uploader_id`` (leading ``@`` stripped)
  - ``subscriber_count`` ← ``channel_follower_count``
  - ``description``      ← ``description``
  - ``thumbnail_url``    ← highest-resolution entry of ``thumbnails``

A dead / renamed handle 404s → yt-dlp raises ``DownloadError`` → this resolver
returns ``None`` (a clean MISS, never a guess). That IS the "excluded, not
guessed" anti-hallucination contract: a handle that will not resolve is dropped,
never fabricated.

The blocking yt-dlp extractor is INJECTED into ``resolve_channel`` /
``resolve_many`` (the ``extractor`` parameter) so the test suite mocks at that
boundary (CLAUDE.md) — no network, no key needed offline. The live path uses the
default real extractor, run in a thread pool with bounded concurrency so the
blocking calls do not stall the event loop.

Example:
    >>> import asyncio
    >>> async def demo() -> None:
    ...     meta = await resolve_channel(handle="AndrejKarpathy")  # doctest: +SKIP
    >>> asyncio.run(demo())  # doctest: +SKIP
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from agents.shared.logger import get_logger

logger = get_logger("seed_catalog.youtube_resolve")

# Public channel-page URL templates yt-dlp reads (metadata only — see _YTDLP_OPTS).
YOUTUBE_HANDLE_URL = "https://www.youtube.com/@{handle}"
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/channel/{channel_id}"
RESOLVE_CONCURRENCY = 4

# yt-dlp options for a METADATA-ONLY channel probe: quiet (no console spam), no
# download, flat extraction, and ``playlist_items="0"`` so yt-dlp fetches the
# channel's own metadata WITHOUT enumerating any uploads (fast + polite).
_YTDLP_OPTS: dict[str, Any] = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,
    "playlist_items": "0",
}

# The injectable extraction seam: a callable that takes a channel-page URL and
# returns yt-dlp's ``info`` dict, or None on a miss (dead handle / error). The
# default is the real yt-dlp extractor; tests inject a deterministic fake.
ChannelInfoExtractor = Callable[[str], dict[str, Any] | None]


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


def _pick_thumbnail(thumbnails: list[dict[str, Any]] | None) -> str | None:
    """Pick the highest-resolution thumbnail URL from a yt-dlp thumbnails list.

    yt-dlp channel thumbnails carry either explicit ``width``/``height`` or an
    ordinal ``preference`` (higher = better). Prefer the largest pixel area; fall
    back to ``preference`` when dimensions are absent; else keep the last usable
    URL (yt-dlp lists ascending, so the last is typically the best).

    Args:
        thumbnails: The ``thumbnails`` list from a yt-dlp info dict (may be None).

    Returns:
        The best thumbnail URL, or None when the list is empty / URL-less.
    """
    if not thumbnails:
        return None
    best_url: str | None = None
    best_score = float("-inf")
    for thumbnail in thumbnails:
        url = thumbnail.get("url")
        if not url:
            continue
        width = thumbnail.get("width") or 0
        height = thumbnail.get("height") or 0
        area = width * height
        # A real pixel area outranks any preference; preference breaks ties when
        # dimensions are missing; a URL with neither still beats nothing seen yet.
        score = float(area) if area > 0 else float(thumbnail.get("preference") or 0)
        if score >= best_score:
            best_score = score
            best_url = url
    return best_url


def _channel_meta_from_info(info: dict[str, Any]) -> ChannelMeta | None:
    """Map a yt-dlp channel ``info`` dict to a :class:`ChannelMeta`.

    Args:
        info: The dict yt-dlp returns from ``extract_info`` on a channel page.

    Returns:
        The validated channel metadata, or None when the mandatory ``channel_id``
        is absent (a page that resolved but is not a channel → treated as a miss).
    """
    channel_id = info.get("channel_id")
    if not channel_id:
        return None
    handle = info.get("uploader_id")
    if isinstance(handle, str):
        handle = handle.lstrip("@") or None
    subscriber_count = info.get("channel_follower_count")
    return ChannelMeta(
        channel_id=channel_id,
        handle=handle,
        title=info.get("channel") or info.get("title") or "",
        description=info.get("description") or None,
        thumbnail_url=_pick_thumbnail(info.get("thumbnails")),
        subscriber_count=(
            int(subscriber_count) if isinstance(subscriber_count, int) else None
        ),
    )


def _default_extractor(url: str) -> dict[str, Any] | None:
    """Extract channel metadata for a URL via real yt-dlp (the live path).

    A dead / renamed / private channel raises ``yt_dlp.utils.DownloadError``; this
    is caught and returned as None (a clean miss, never a guess). Any other
    unexpected error is likewise a None-miss (logged) so one bad handle can never
    abort a batch resolve.

    Args:
        url: The channel-page URL (``…/@handle`` or ``…/channel/UC…``).

    Returns:
        yt-dlp's info dict, or None on a dead handle / extraction error.
    """
    import yt_dlp  # noqa: PLC0415 — heavy import kept off the test path

    try:
        with yt_dlp.YoutubeDL(dict(_YTDLP_OPTS)) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        # 404 (dead/renamed) OR a transient 429 (IP throttle) both surface here.
        # Either way it is a miss for THIS attempt; the seeder decides whether a
        # high overall miss-rate smells like throttle and should halt (fail loud).
        logger.warning(
            "youtube_resolve_miss",
            url=url,
            error_message=str(exc),
            fix_suggestion="Dead/renamed handle → excluded (never guessed); if MANY miss at once, suspect IP throttle and pause the seed.",
        )
        return None
    except Exception as exc:  # noqa: BLE001 — a resolver miss must not abort the batch
        logger.warning(
            "youtube_resolve_error",
            url=url,
            error_message=str(exc),
            fix_suggestion="Inspect yt-dlp connectivity / version; a single miss is skipped.",
        )
        return None


async def resolve_channel(
    *,
    handle: str | None = None,
    channel_id: str | None = None,
    extractor: ChannelInfoExtractor | None = None,
) -> ChannelMeta | None:
    """Resolve a single channel by handle (preferred) or channel id, keyless.

    Tries the ``…/@handle`` page first when a handle is supplied; falls back to the
    ``…/channel/UC…`` page when a channel id is supplied. Returns None on any miss.
    The blocking extractor runs in a worker thread so it never stalls the loop.

    Args:
        handle: The channel handle (with or without a leading ``@``). Optional.
        channel_id: The ``UC…`` channel id. Optional (used when no handle resolves).
        extractor: The extraction seam (defaults to real yt-dlp; tests inject a fake).

    Returns:
        The resolved channel metadata, or None when neither input resolves.
    """
    extract = extractor or _default_extractor

    targets: list[str] = []
    cleaned_handle = (handle or "").lstrip("@").strip()
    if cleaned_handle:
        targets.append(YOUTUBE_HANDLE_URL.format(handle=cleaned_handle))
    cleaned_id = (channel_id or "").strip()
    if cleaned_id:
        targets.append(YOUTUBE_CHANNEL_URL.format(channel_id=cleaned_id))

    for url in targets:
        info = await asyncio.to_thread(extract, url)
        if not info:
            continue
        meta = _channel_meta_from_info(info)
        if meta is not None:
            return meta
    return None


async def resolve_many(
    entries: list[dict[str, Any]],
    *,
    extractor: ChannelInfoExtractor | None = None,
    concurrency: int = RESOLVE_CONCURRENCY,
) -> dict[str, ChannelMeta]:
    """Resolve a batch of channels concurrently, keyless.

    Args:
        entries: Dicts each carrying ``youtube_handle`` and/or ``channel_id``.
        extractor: The extraction seam (defaults to real yt-dlp; tests inject a fake).
        concurrency: Max simultaneous channel-page probes (bounds throttle risk).

    Returns:
        ``{key: ChannelMeta}`` where ``key`` is the lowercased handle when present,
        else the lowercased channel id. Misses are omitted. The key formula matches
        ``seed_catalog._dedup_key_fn('channels')`` so the caller can join by
        ``entry.dedup_key``.

    Example:
        >>> import asyncio
        >>> async def demo() -> dict:
        ...     return await resolve_many([{"youtube_handle": "AndrejKarpathy"}])  # doctest: +SKIP
        >>> asyncio.run(demo())  # doctest: +SKIP
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(entry: dict[str, Any]) -> tuple[str, ChannelMeta | None]:
        handle = entry.get("youtube_handle")
        channel_id = entry.get("channel_id")
        # Key formula MUST mirror _dedup_key_fn('channels'): the raw handle-or-id
        # lowercased, WITHOUT stripping '@' (so the join key equals entry.dedup_key).
        key = (handle or channel_id or "").lower()
        async with semaphore:
            meta = await resolve_channel(
                handle=handle, channel_id=channel_id, extractor=extractor
            )
        return key, meta

    results = await asyncio.gather(*(_one(entry) for entry in entries))
    return {key: meta for key, meta in results if meta is not None and key}
