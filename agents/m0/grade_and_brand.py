"""Deterministic house grade + brand pass (Pillow) — poster-pipeline §6/§7.

Takes the raw generated poster and makes it read as "from this app": cover-fit to
1080x1920, a SUBTLE duotone pull toward near-black #020617, a single-accent edge
vignette, a lower-~45% scrim for the caption-safe zone, and faint film grain.

Kept intentionally light (over-grading reads as a cheap filter). Any sub-step
that fails degrades gracefully so grading never breaks a run (Rule 12 — but it
logs what it skipped).
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageOps

from agents.m0.poster_models import NEAR_BLACK_HEX
from agents.shared.logger import get_logger

logger = get_logger("m0.grade_and_brand")

TARGET_WIDTH_PX: int = 1080
TARGET_HEIGHT_PX: int = 1920


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert '#RRGGBB' to an (r, g, b) tuple."""
    cleaned = hex_color.lstrip("#")
    return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))


def _cover_resize(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale to cover the target box, then center-crop to exactly width×height."""
    src_w, src_h = image.size
    scale = max(width / src_w, height / src_h)
    resized = image.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _shadow_pull(image: Image.Image, navy_rgb: tuple[int, int, int]) -> Image.Image:
    """Pull the darkest areas toward near-black navy (subtle duotone shadow)."""
    luminance = image.convert("L")
    # Mask is strong where the image is dark (inverted luminance), capped low.
    shadow_mask = ImageOps.invert(luminance).point(lambda p: int(p * 0.22))
    navy = Image.new("RGB", image.size, navy_rgb)
    return Image.composite(navy, image, shadow_mask)


def _accent_vignette(image: Image.Image, accent_rgb: tuple[int, int, int]) -> Image.Image:
    """Darken the edges with a faint accent-tinted radial vignette."""
    radial = Image.radial_gradient("L").resize(image.size, Image.LANCZOS)
    edge_mask = radial.point(lambda p: int(p * 0.45))  # brighter at edges → more tint there
    accent = Image.new("RGB", image.size, accent_rgb)
    # Blend a very low-opacity accent at the edges, then darken edges slightly.
    tinted = Image.composite(accent, image, edge_mask.point(lambda p: int(p * 0.18)))
    navy = Image.new("RGB", image.size, (2, 6, 23))
    return Image.composite(navy, tinted, edge_mask.point(lambda p: int(p * 0.5)))


def _bottom_scrim(image: Image.Image, navy_rgb: tuple[int, int, int]) -> Image.Image:
    """Composite a transparent→navy gradient over the lower ~45% (text-safe zone)."""
    width, height = image.size
    scrim_start = int(height * 0.55)
    column = []
    for y in range(height):
        if y < scrim_start:
            column.append(0)
        else:
            ramp = (y - scrim_start) / max(1, height - scrim_start)
            column.append(int(min(1.0, ramp) * 216))  # up to ~0.85 opacity navy
    scrim_mask = Image.new("L", (1, height))
    scrim_mask.putdata(column)
    scrim_mask = scrim_mask.resize((width, height))
    navy = Image.new("RGB", image.size, navy_rgb)
    return Image.composite(navy, image, scrim_mask)


def _film_grain(image: Image.Image) -> Image.Image:
    """Overlay faint film grain."""
    noise = Image.effect_noise(image.size, 16).convert("RGB")
    return Image.blend(image, ImageChops.overlay(image, noise), 0.05)


def grade_and_brand(raw_image_bytes: bytes, accent_hex: str) -> bytes:
    """Apply the house grade to a raw poster and return PNG bytes at 1080×1920.

    Args:
        raw_image_bytes: The raw bytes from the image generator.
        accent_hex: The single segment accent for this poster.

    Returns:
        Graded PNG bytes (1080×1920).
    """
    navy_rgb = _hex_to_rgb(NEAR_BLACK_HEX)
    accent_rgb = _hex_to_rgb(accent_hex)

    image = Image.open(io.BytesIO(raw_image_bytes)).convert("RGB")
    image = _cover_resize(image, TARGET_WIDTH_PX, TARGET_HEIGHT_PX)

    # Reason: each step is optional — a failure must not lose the (still useful) poster.
    for step_name, step in (
        ("desaturate", lambda im: Image.blend(im, ImageOps.grayscale(im).convert("RGB"), 0.18)),
        ("shadow_pull", lambda im: _shadow_pull(im, navy_rgb)),
        ("accent_vignette", lambda im: _accent_vignette(im, accent_rgb)),
        ("bottom_scrim", lambda im: _bottom_scrim(im, navy_rgb)),
        ("film_grain", _film_grain),
    ):
        try:
            image = step(image)
        except Exception as exc:  # noqa: BLE001 — log + skip this grade step only.
            logger.warning(
                "grade_step_skipped",
                step=step_name,
                error_message=str(exc),
                fix_suggestion="Grade step failed; continuing with the un-stepped image.",
            )

    out = io.BytesIO()
    image.save(out, format="PNG")
    logger.info("poster_graded", accent_hex=accent_hex, width_px=TARGET_WIDTH_PX, height_px=TARGET_HEIGHT_PX)
    return out.getvalue()
