"""M0 driver: generate 5 concept-first news posters via Gemini Nano Banana Pro.

Run as a module::

    .venv/bin/python -m agents.m0.generate_posters            # all 5
    .venv/bin/python -m agents.m0.generate_posters --only 3   # just digest-3

For each prompt in ``agents.m0.poster_prompts.POSTER_PROMPTS`` it calls
``gemini-3-pro-image-preview`` (Nano Banana Pro) with
``response_modalities=["TEXT", "IMAGE"]`` and a 9:16 ``image_config``, then
writes the returned image bytes to ``assets/m0/digest-<n>/poster.png``.

This is a REAL render that makes live Gemini image-generation calls and so also
probes whether the configured ``GEMINI_API_KEY`` has image-gen access/quota:

  - **digest-1 is generated FIRST.** If it errors (no image access, quota,
    billing, model-not-found), the run STOPS immediately and reports — it does
    not burn calls on the remaining four.
  - On a safety-filter empty-parts response for a specific story, the prompt is
    retried ONCE with a more abstract/symbolic rephrase, then recorded as a
    refusal if still empty (Rule 12 — never silently skip).

The API key is read from ``.env`` via ``Settings`` and is NEVER logged.

Ported from the working Canvas TS request pattern
(``canvas/src/lib/services/gemini-service.ts``):
``client.models.generate_content(model, contents=[{role,parts:[{text}]}],
config=GenerateContentConfig(response_modalities=["TEXT","IMAGE"],
image_config=ImageConfig(aspect_ratio="9:16")))`` then iterate
``response.candidates[0].content.parts`` for ``part.inline_data.data``.
"""

from __future__ import annotations

import argparse
import base64
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from agents.m0.poster_prompts import POSTER_PROMPTS, PosterPrompt
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("m0.generate_posters")

# Nano Banana Pro = Gemini 3 Pro Image (poster-pipeline §9 primary generator).
# Do NOT silently downgrade to gemini-3.1-flash-image-preview — if this id is
# unavailable on the key, STOP and report so the orchestrator decides.
GEMINI_IMAGE_MODEL: str = "gemini-3-pro-image-preview"
POSTER_ASPECT_RATIO: str = "9:16"

# Reason: resolve output dir relative to the repo root (this module lives at
# agents/m0/), so the driver works regardless of the current working directory.
ASSETS_M0_DIR: Path = Path(__file__).resolve().parents[2] / "assets" / "m0"

# Reason: be quota-polite between live image calls in a 5-item spike.
INTER_CALL_DELAY_SECONDS: float = 2.0


@dataclass
class PosterResult:
    """Outcome of attempting to generate one poster.

    Attributes:
        digest_id: Which digest this result is for.
        status: "success" | "refused" | "error".
        output_path: Where the PNG was written (success only).
        width_px: Decoded image width in pixels (success only).
        height_px: Decoded image height in pixels (success only).
        byte_size: Number of image bytes written (success only).
        used_fallback: True if the abstract fallback prompt produced the image.
        detail: Human-readable note (refusal reason / verbatim error message).
    """

    digest_id: str
    status: str
    output_path: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    byte_size: int | None = None
    used_fallback: bool = False
    detail: str = ""


@dataclass
class RunSummary:
    """Aggregate outcome across all attempted posters."""

    results: list[PosterResult] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str = ""


def _read_png_dimensions(image_bytes: bytes) -> tuple[int | None, int | None]:
    """Parse width/height from a PNG byte string without external deps.

    Reads the IHDR chunk (bytes 16-24) of the PNG header. Returns ``(None, None)``
    if the bytes are not a recognizable PNG (the caller still treats the bytes as
    a written image and reports the unknown dimensions).

    Args:
        image_bytes: The raw image bytes returned by Gemini.

    Returns:
        ``(width_px, height_px)`` or ``(None, None)`` if not a parseable PNG.

    Example:
        >>> _read_png_dimensions(open("poster.png", "rb").read())
        (1080, 1920)
    """
    png_signature = b"\x89PNG\r\n\x1a\n"
    if len(image_bytes) < 24 or image_bytes[:8] != png_signature:
        return (None, None)
    # IHDR width/height are big-endian uint32 at offsets 16 and 20.
    width_px, height_px = struct.unpack(">II", image_bytes[16:24])
    return (int(width_px), int(height_px))


def _coerce_image_bytes(inline_data_value: bytes | str) -> bytes:
    """Coerce an inline_data.data value to raw bytes.

    The google-genai SDK usually returns already-decoded ``bytes`` for
    ``inline_data.data``, but the port note requires handling a base64 ``str``
    defensively too.

    Args:
        inline_data_value: Either raw bytes or a base64-encoded string.

    Returns:
        The decoded raw image bytes.
    """
    if isinstance(inline_data_value, bytes):
        return inline_data_value
    return base64.b64decode(inline_data_value)


def _extract_image_bytes(
    response: types.GenerateContentResponse,
) -> tuple[bytes | None, str]:
    """Pull the first inline image part out of a Gemini response.

    Mirrors the Canvas TS parser: iterate ``candidates[0].content.parts`` and
    return the first part carrying ``inline_data``. Empty/no parts means the
    prompt was filtered by safety settings (the caller retries once).

    Args:
        response: The raw Gemini generate_content response.

    Returns:
        ``(image_bytes, mime_type)``. ``image_bytes`` is None when no image part
        was present (empty response / safety filter / text-only).
    """
    candidates = response.candidates or []
    if not candidates:
        return (None, "")
    content = candidates[0].content
    parts = (content.parts if content else None) or []
    for part in parts:
        inline_data = part.inline_data
        if inline_data is not None and inline_data.data:
            mime_type = inline_data.mime_type or "image/png"
            return (_coerce_image_bytes(inline_data.data), mime_type)
    return (None, "")


def _generate_one_call(
    client: genai.Client, prompt_text: str
) -> types.GenerateContentResponse:
    """Make a single Nano Banana Pro generate_content call for a textless poster.

    Args:
        client: Initialized google-genai client.
        prompt_text: The fully-resolved poster prompt.

    Returns:
        The raw Gemini response (caller extracts the image part).
    """
    return client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=prompt_text)])],
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=POSTER_ASPECT_RATIO),
        ),
    )


def generate_from_reference(
    client: genai.Client,
    prompt_text: str,
    reference_image_bytes: bytes,
    reference_mime_type: str = "image/jpeg",
) -> types.GenerateContentResponse:
    """Image-conditioned Nano Banana Pro call: a reference image + a recast prompt.

    Passes BOTH an input image part (the SERP-selected seed) and the synthesized
    text prompt so the model transforms the reference rather than inventing from
    scratch. Used by the SERP-seeded poster pipeline; the text-only
    ``_generate_one_call`` remains the concept-first fallback.

    Args:
        client: Initialized google-genai client.
        prompt_text: The recast poster prompt (concept + house grade).
        reference_image_bytes: Raw bytes of the chosen seed image.
        reference_mime_type: Mime type of the seed image.

    Returns:
        The raw Gemini response (caller extracts the image part via _extract_image_bytes).
    """
    return client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=reference_image_bytes, mime_type=reference_mime_type),
                    types.Part(text=prompt_text),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=POSTER_ASPECT_RATIO),
        ),
    )


def _write_poster(image_bytes: bytes, digest_id: str) -> Path:
    """Write poster bytes to ``assets/m0/<digest_id>/poster.png``.

    Args:
        image_bytes: The raw image bytes to persist.
        digest_id: The digest identifier (e.g. ``digest-1``).

    Returns:
        The path the PNG was written to.
    """
    output_dir = ASSETS_M0_DIR / digest_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "poster.png"
    output_path.write_bytes(image_bytes)
    return output_path


def generate_poster(client: genai.Client, poster: PosterPrompt) -> PosterResult:
    """Generate, persist, and verify one poster, retrying once on a safety filter.

    Attempts the primary prompt first. If Gemini returns no image part (empty
    response = safety filter), retries ONCE with the abstract fallback prompt.
    Live API errors (model-not-found, quota, billing, etc.) propagate to the
    caller so it can decide whether to stop the run.

    Args:
        client: Initialized google-genai client.
        poster: The poster prompt definition.

    Returns:
        A PosterResult describing the outcome (success / refused).

    Raises:
        Exception: Any live Gemini API error — propagated verbatim so the
            orchestrator can stop early (especially on digest-1).
    """
    logger.info(
        "poster_generation_started",
        digest_id=poster.digest_id,
        archetype=poster.archetype,
        accent_hex=poster.accent_hex,
        model=GEMINI_IMAGE_MODEL,
        prompt_length=len(poster.prompt_text),
    )

    attempts = ((poster.prompt_text, False), (poster.fallback_prompt_text, True))
    for prompt_text, is_fallback in attempts:
        response = _generate_one_call(client, prompt_text)
        image_bytes, mime_type = _extract_image_bytes(response)

        if image_bytes:
            output_path = _write_poster(image_bytes, poster.digest_id)
            width_px, height_px = _read_png_dimensions(image_bytes)
            logger.info(
                "poster_generation_completed",
                digest_id=poster.digest_id,
                output_path=str(output_path),
                used_fallback=is_fallback,
                mime_type=mime_type,
                byte_size=len(image_bytes),
                width_px=width_px,
                height_px=height_px,
            )
            return PosterResult(
                digest_id=poster.digest_id,
                status="success",
                output_path=str(output_path),
                width_px=width_px,
                height_px=height_px,
                byte_size=len(image_bytes),
                used_fallback=is_fallback,
                detail="generated via fallback prompt"
                if is_fallback
                else "generated via primary prompt",
            )

        if not is_fallback:
            logger.warning(
                "poster_generation_empty_response_retrying",
                digest_id=poster.digest_id,
                fix_suggestion=(
                    "Gemini returned no image part (likely safety filter). Retrying once "
                    "with a more abstract, fully symbolic rephrase of the same concept."
                ),
            )

    # Reason: both primary and fallback returned empty parts — record the refusal
    # loudly (Rule 12) rather than silently skipping.
    logger.error(
        "poster_generation_refused",
        digest_id=poster.digest_id,
        fix_suggestion=(
            "Both the primary and abstract-fallback prompts returned no image part. "
            "Rephrase the concept to be even more abstract/symbolic, or hand to manual review."
        ),
    )
    return PosterResult(
        digest_id=poster.digest_id,
        status="refused",
        detail="empty response (safety filter) on both primary and fallback prompts",
    )


def generate_all_posters(only_digest_number: int | None = None) -> RunSummary:
    """Generate posters for all 5 digests (or just one with ``only_digest_number``).

    digest-1 is generated FIRST as the image-access/quota probe. If it raises,
    the run STOPS immediately and the error is recorded verbatim — the remaining
    posters are not attempted.

    Args:
        only_digest_number: If set (1-5), generate only that digest; otherwise all.

    Returns:
        A RunSummary with per-poster results and any early-stop reason.
    """
    settings = Settings()
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        logger.error(
            "gemini_api_key_missing",
            fix_suggestion="Set GEMINI_API_KEY in .env (project root). Get a key from Google AI Studio.",
        )
        return RunSummary(
            stopped_early=True, stop_reason="GEMINI_API_KEY is empty in .env"
        )

    client = genai.Client(api_key=api_key)

    if only_digest_number is not None:
        target_id = f"digest-{only_digest_number}"
        posters = tuple(p for p in POSTER_PROMPTS if p.digest_id == target_id)
        if not posters:
            return RunSummary(
                stopped_early=True, stop_reason=f"No poster prompt for {target_id}"
            )
    else:
        posters = POSTER_PROMPTS

    summary = RunSummary()
    for index, poster in enumerate(posters):
        try:
            result = generate_poster(client, poster)
        except Exception as exc:  # noqa: BLE001 — we record any live API error verbatim and stop.
            error_message = str(exc)
            logger.error(
                "poster_generation_failed",
                digest_id=poster.digest_id,
                error_type=type(exc).__name__,
                error_message=error_message,
                fix_suggestion=(
                    "Check that GEMINI_API_KEY has image-generation access and quota, that the "
                    f"model '{GEMINI_IMAGE_MODEL}' is available on this key (NOT_FOUND / "
                    "PERMISSION_DENIED => model unavailable; 429 => quota; billing => enable billing). "
                    "Do NOT downgrade the model automatically — report to the orchestrator."
                ),
            )
            summary.results.append(
                PosterResult(
                    digest_id=poster.digest_id,
                    status="error",
                    detail=f"{type(exc).__name__}: {error_message}",
                )
            )
            # Reason: the first attempted poster is the access/quota probe — a failure there
            # means the key/model is unusable, so stop before burning the rest.
            if index == 0:
                summary.stopped_early = True
                summary.stop_reason = (
                    f"First poster ({poster.digest_id}) failed; stopping before burning the rest. "
                    f"Verbatim error: {type(exc).__name__}: {error_message}"
                )
                return summary
            # A later poster failing is recorded; continue with the remaining ones.
            continue

        summary.results.append(result)
        if index < len(posters) - 1:
            time.sleep(INTER_CALL_DELAY_SECONDS)

    return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the module entry point."""
    parser = argparse.ArgumentParser(
        description="Generate M0 concept-first posters via Gemini Nano Banana Pro."
    )
    parser.add_argument(
        "--only",
        type=int,
        default=None,
        metavar="N",
        help="Generate only digest-N (1-5) instead of all five.",
    )
    return parser


def main() -> None:
    """Module entry point: generate the M0 posters and emit a summary log."""
    args = _build_arg_parser().parse_args()
    summary = generate_all_posters(only_digest_number=args.only)

    success_ids = [r.digest_id for r in summary.results if r.status == "success"]
    refused_ids = [r.digest_id for r in summary.results if r.status == "refused"]
    error_ids = [r.digest_id for r in summary.results if r.status == "error"]

    logger.info(
        "poster_run_summary",
        stopped_early=summary.stopped_early,
        stop_reason=summary.stop_reason,
        success_count=len(success_ids),
        success_ids=success_ids,
        refused_ids=refused_ids,
        error_ids=error_ids,
        dimensions={
            r.digest_id: f"{r.width_px}x{r.height_px}"
            for r in summary.results
            if r.status == "success"
        },
    )

    # Reason: non-zero exit only when nothing was produced AND something failed,
    # so the orchestrator can detect a hard FAILURE vs a PARTIAL success.
    if not success_ids and (summary.stopped_early or error_ids or refused_ids):
        sys.exit(1)


if __name__ == "__main__":
    main()
