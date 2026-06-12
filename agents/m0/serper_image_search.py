"""Serper.dev Google-Images search + gating (pattern-ported from Canvas).

Canvas source: ``creator-studio/src/lib/services/search-service.ts`` (TypeScript).
Same contract, rewritten in Python/httpx:
``POST https://google.serper.dev/images`` with header ``X-API-KEY`` and body
``{q, num, page}`` -> ``{images:[{title, link, thumbnailUrl, imageUrl,
imageWidth, imageHeight}]}``. We then gate (dedup by URL, drop tiny images and
watermarked-stock domains) and return typed ``ImageCandidate``s.

The API key is read via ``Settings`` and NEVER logged.
"""

from __future__ import annotations

import re

import httpx

from agents.m0.poster_models import (
    MIN_SHORTEST_SIDE_PX,
    SERPER_NUM_RESULTS,
    WATERMARK_STOCK_DOMAINS,
    ImageCandidate,
)
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("m0.serper_image_search")

SERPER_IMAGES_ENDPOINT: str = "https://google.serper.dev/images"
_REQUEST_TIMEOUT_SECONDS: float = 30.0

# Reason: one pattern per YouTube URL shape that carries the video id —
# watch?v=, youtu.be/, and the /shorts|live|embed/ path forms. Ids are the
# canonical 11-char [A-Za-z0-9_-] token; extra query params may follow.
_YOUTUBE_VIDEO_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:www\.|m\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:www\.|m\.)?youtube\.com/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})"),
)


def _is_watermarked_stock(source_page_url: str) -> bool:
    """True if the result's source page is a known watermarked-stock domain."""
    lowered = source_page_url.lower()
    return any(domain in lowered for domain in WATERMARK_STOCK_DOMAINS)


def _passes_size_gate(width_px: int, height_px: int) -> bool:
    """Keep images whose shortest side >= floor; keep unknown dims (Serper omits some)."""
    if width_px <= 0 or height_px <= 0:
        return True
    return min(width_px, height_px) >= MIN_SHORTEST_SIDE_PX


def youtube_video_id_from_url(source_url: str) -> str | None:
    """Extract the 11-char YouTube video id from a video/podcast URL (PURE).

    Recognizes ``youtube.com/watch?v=``, ``youtu.be/``, and the
    ``youtube.com/shorts|live|embed/`` path forms (with or without extra query
    params). Anything else returns None.

    Args:
        source_url: The story's source URL.

    Returns:
        The video id, or None when the URL is not a YouTube video link.

    Example:
        >>> youtube_video_id_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        'dQw4w9WgXcQ'
        >>> youtube_video_id_from_url("https://example.com/article") is None
        True
    """
    if not source_url:
        return None
    for pattern in _YOUTUBE_VIDEO_ID_PATTERNS:
        match = pattern.search(source_url)
        if match:
            return match.group(1)
    return None


def youtube_thumbnail_candidate(
    source_url: str, digest_id: str
) -> ImageCandidate | None:
    """Build the YouTube-thumbnail seed candidate for a video-sourced story (PURE).

    When a story's source is a YouTube video/podcast, the video's own thumbnail
    is the most on-subject image seed available — prepend it ahead of the SERP
    results. ``full_image_url`` is the maxres frame; ``thumbnail_url`` is the
    always-present hqdefault, which ``download_candidate`` falls back to when
    maxres 404s (older uploads). Exempt from the watermark gate (it IS the
    source's image) but still subject to the post-download size gate.

    Args:
        source_url: The story's source URL (any shape; non-YouTube → None).
        digest_id: Owning digest id, used to mint the candidate id.

    Returns:
        The thumbnail :class:`ImageCandidate`, or None for non-YouTube URLs.

    Example:
        >>> cand = youtube_thumbnail_candidate(
        ...     "https://youtu.be/dQw4w9WgXcQ", "digest-1"
        ... )
        >>> cand.full_image_url
        'https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg'
    """
    video_id = youtube_video_id_from_url(source_url)
    if video_id is None:
        return None
    logger.info(
        "youtube_thumbnail_candidate_built",
        digest_id=digest_id,
        video_id=video_id,
    )
    return ImageCandidate(
        candidate_id=f"{digest_id}-cand-youtube",
        title="YouTube video thumbnail",
        source_page_url=source_url,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        full_image_url=f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        # Reason: maxresdefault is 1280×720 when present; the real dims are
        # validated after download by the size gate.
        width_px=1280,
        height_px=720,
    )


def search_images(
    query: str, digest_id: str, num: int = SERPER_NUM_RESULTS
) -> list[ImageCandidate]:
    """Search Google Images via Serper and return gated, deduped candidates.

    Args:
        query: The (already-refined) image search query.
        digest_id: Owning digest id, used to mint stable candidate ids.
        num: How many raw results to request from Serper.

    Returns:
        Gated, deduplicated candidates (dropped: dup URLs, tiny images,
        watermarked-stock domains). May be fewer than ``num``.

    Raises:
        httpx.HTTPStatusError / RuntimeError: on a failed Serper call (caller decides).
    """
    api_key = Settings().serper_api_key.get_secret_value().strip()
    if not api_key:
        logger.error(
            "serper_api_key_missing",
            fix_suggestion="Set SERPER_API_KEY in .env (get a key at https://serper.dev).",
        )
        raise RuntimeError("SERPER_API_KEY is empty in .env")

    logger.info("serper_search_started", digest_id=digest_id, query=query, num=num)
    response = httpx.post(
        SERPER_IMAGES_ENDPOINT,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num, "page": 1},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw_images = response.json().get("images", []) or []

    candidates: list[ImageCandidate] = []
    seen_urls: set[str] = set()
    dropped_size = 0
    dropped_stock = 0
    for item in raw_images:
        full_image_url = (item.get("imageUrl") or "").strip()
        if not full_image_url or full_image_url in seen_urls:
            continue
        source_page_url = (item.get("link") or "").strip()
        width_px = int(item.get("imageWidth") or 0)
        height_px = int(item.get("imageHeight") or 0)

        if _is_watermarked_stock(source_page_url):
            dropped_stock += 1
            continue
        if not _passes_size_gate(width_px, height_px):
            dropped_size += 1
            continue

        seen_urls.add(full_image_url)
        candidates.append(
            ImageCandidate(
                candidate_id=f"{digest_id}-cand-{len(candidates) + 1}",
                title=(item.get("title") or "").strip(),
                source_page_url=source_page_url,
                thumbnail_url=(item.get("thumbnailUrl") or "").strip(),
                full_image_url=full_image_url,
                width_px=width_px,
                height_px=height_px,
            )
        )

    logger.info(
        "serper_search_completed",
        digest_id=digest_id,
        raw_count=len(raw_images),
        kept=len(candidates),
        dropped_size=dropped_size,
        dropped_stock=dropped_stock,
    )
    return candidates
