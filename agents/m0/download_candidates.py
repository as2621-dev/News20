"""Download candidate image bytes for scoring + seeding.

Fetches each candidate's full image (falling back to its thumbnail on failure),
validates it really is a decodable image meeting the size floor, and writes it
to ``assets/m0/<digest_id>/refs/``. Returns the bytes + mime so the scorer and
the generator can reuse them without re-fetching.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
from PIL import Image

from agents.m0.poster_models import MIN_SHORTEST_SIDE_PX, ImageCandidate
from agents.shared.logger import get_logger

logger = get_logger("m0.download_candidates")

_DOWNLOAD_TIMEOUT_SECONDS: float = 30.0
# Reason: the full image must meet the seed-quality floor, but the thumbnail is a
# last-resort fallback (when the full URL 403s) — a small Google-cached thumb is
# still a usable seed for image-conditioned generation, so accept it down to here.
_THUMB_MIN_SIDE_PX: int = 120
# Reason: many news CDNs reject default httpx UA / hotlinking; present a browser UA.
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_EXT_BY_FORMAT: dict[str, str] = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}


class DownloadedCandidate:
    """A candidate whose bytes were successfully fetched and validated."""

    def __init__(self, candidate: ImageCandidate, image_bytes: bytes, mime_type: str, local_path: Path):
        self.candidate = candidate
        self.image_bytes = image_bytes
        self.mime_type = mime_type
        self.local_path = local_path


def _fetch(url: str) -> bytes | None:
    """GET image bytes with a browser UA; return None on any failure."""
    try:
        response = httpx.get(
            url, headers=_BROWSER_HEADERS, timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
        )
        response.raise_for_status()
        return response.content
    except Exception:  # noqa: BLE001 — caller logs; a single bad URL must not kill the run.
        return None


def download_candidate(candidate: ImageCandidate, refs_dir: Path) -> DownloadedCandidate | None:
    """Download + validate one candidate's image, writing it under ``refs_dir``.

    Tries the full image first, then the thumbnail. Validates the bytes decode as
    an image whose shortest side meets the floor.

    Args:
        candidate: The candidate to fetch.
        refs_dir: Directory to write the reference image into.

    Returns:
        A DownloadedCandidate on success, else None (logged).
    """
    attempts = (
        (candidate.full_image_url, MIN_SHORTEST_SIDE_PX),
        (candidate.thumbnail_url, _THUMB_MIN_SIDE_PX),
    )
    for url, min_side_px in attempts:
        if not url:
            continue
        raw = _fetch(url)
        if not raw:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as img:
                img_format = (img.format or "").upper()
                width_px, height_px = img.size
        except Exception:  # noqa: BLE001 — not a decodable image; try the next url.
            continue
        if min(width_px, height_px) < min_side_px:
            continue

        extension = _EXT_BY_FORMAT.get(img_format, "img")
        mime_type = f"image/{'jpeg' if extension == 'jpg' else extension}"
        refs_dir.mkdir(parents=True, exist_ok=True)
        local_path = refs_dir / f"{candidate.candidate_id}.{extension}"
        local_path.write_bytes(raw)
        candidate.local_path = str(local_path)
        candidate.width_px = candidate.width_px or width_px
        candidate.height_px = candidate.height_px or height_px
        logger.info(
            "candidate_downloaded",
            candidate_id=candidate.candidate_id,
            local_path=str(local_path),
            width_px=width_px,
            height_px=height_px,
            byte_size=len(raw),
        )
        return DownloadedCandidate(candidate, raw, mime_type, local_path)

    logger.warning(
        "candidate_download_failed",
        candidate_id=candidate.candidate_id,
        full_image_url=candidate.full_image_url,
        fix_suggestion="Both full and thumbnail fetch failed or were too small; skipping this candidate.",
    )
    return None
