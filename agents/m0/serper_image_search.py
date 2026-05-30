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


def _is_watermarked_stock(source_page_url: str) -> bool:
    """True if the result's source page is a known watermarked-stock domain."""
    lowered = source_page_url.lower()
    return any(domain in lowered for domain in WATERMARK_STOCK_DOMAINS)


def _passes_size_gate(width_px: int, height_px: int) -> bool:
    """Keep images whose shortest side >= floor; keep unknown dims (Serper omits some)."""
    if width_px <= 0 or height_px <= 0:
        return True
    return min(width_px, height_px) >= MIN_SHORTEST_SIDE_PX


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
