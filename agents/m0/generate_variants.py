"""Experimental A/B/C register bake-off for a single digest's poster.

Generates the SAME story in three registers so we can eyeball which look wins
before committing it across all 5 digests (poster-pipeline.md §5 caricature
spike):

  - **a-caricature** — stylized editorial caricature (Rodriguez / Blitt / Fairey).
    The §5 LOCKED default; strongest legal footing; on-brand hybrid register.
  - **b-photoreal**  — recognizable photoreal likeness. The riskiest path: Nano
    Banana Pro most often REFUSES named living political figures here. An empty
    response is recorded loudly as a refusal (Rule 12), NOT silently skipped.
  - **c-metaphor**   — metaphor-led, with the recognizable figure(s) as a
    secondary caricatured element inside the conceptual object.

Run::

    .venv/bin/python -m agents.m0.generate_variants            # digest-1 ×3

Reuses the proven live call path from ``generate_posters`` (``_generate_one_call``
+ ``_extract_image_bytes``) so this experiment can't drift from the real driver.
Writes ``assets/m0/<digest_id>/variant-<register>.<ext>`` using the REAL mime
type for the extension (so a JPEG is named ``.jpg``, fixing the mislabel the
main driver still has). The API key is read via ``Settings`` and NEVER logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from google import genai

from agents.m0.generate_posters import (
    ASSETS_M0_DIR,
    GEMINI_IMAGE_MODEL,
    INTER_CALL_DELAY_SECONDS,
    _extract_image_bytes,
    _generate_one_call,
)
from agents.m0.poster_prompts import HOUSE_GRADE_SUFFIX
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("m0.generate_variants")

# Reason: the shared house suffix bakes in "editorial-illustration / conceptual
# register", which fights a PHOTOREAL intent. For the photoreal variant only,
# swap that one clause for a photoreal one while keeping every other house
# constant (near-black, lower-40% reserve, grain, vignette, 9:16, negatives).
_PHOTOREAL_SUFFIX: str = HOUSE_GRADE_SUFFIX.replace(
    "Editorial-illustration / conceptual-poster register fused with cinematic lighting "
    "(hybrid).",
    "Photorealistic cinematic editorial photography, deep chiaroscuro, fused with a "
    "conceptual-poster composition.",
)

# Mime type -> file extension for correctly-typed variant files.
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@dataclass
class Variant:
    """One register variant of a single digest's poster.

    Attributes:
        register: Short slug used in the output filename (a-caricature, ...).
        label: Human-readable register name for logging.
        prompt_text: The fully-resolved image-gen prompt (concept + suffix).
    """

    register: str
    label: str
    prompt_text: str


# Reason: digest-1 = US strikes Iran (Versus / Split, ember red #EF4444). The
# two named figures are US President Donald Trump and Iran's Supreme Leader
# Ayatollah Ali Khamenei. Three registers of the SAME standoff concept.
DIGEST_1_VARIANTS: tuple[Variant, ...] = (
    Variant(
        register="a-caricature",
        label="stylized editorial caricature",
        prompt_text=(
            "Versus / Split composition rendered as a bold editorial political caricature "
            "in the flat-graphic, heavy-ink register of Edel Rodriguez and Barry Blitt. "
            "Two posterized caricatures face off in left and right profile: on the LEFT a "
            "caricature of US President Donald Trump (exaggerated swept-back blond hair, "
            "jutting jaw, the red tie reduced to one graphic shape); on the RIGHT a "
            "caricature of Iran's Supreme Leader Ayatollah Ali Khamenei (black turban, "
            "white beard, large glasses, dark robe). Minimal flat shapes, no fine detail. "
            "A single jagged glowing ember-red seam tears straight down the center between "
            "them, and the empty negative space of that seam reads as the silhouette of an "
            "upright missile / warhead. Anchored in the upper and center thirds. Ember red "
            "#EF4444 is the only accent, glowing along the seam." + HOUSE_GRADE_SUFFIX
        ),
    ),
    Variant(
        register="b-photoreal",
        label="photoreal likeness",
        prompt_text=(
            "Versus / Split composition as a photorealistic cinematic editorial portrait. "
            "Two real political figures face off in dramatic left and right profile across "
            "the center of the frame: on the LEFT, US President Donald Trump; on the RIGHT, "
            "Iran's Supreme Leader Ayatollah Ali Khamenei. Realistic faces held mostly in "
            "deep shadow, each carved out by a single hard directional rim light. A jagged "
            "glowing ember-red seam tears straight down the center between them, and the gap "
            "at the seam reads as the silhouette of an upright missile / warhead. Anchored "
            "in the upper and center thirds. Ember red #EF4444 is the only accent, glowing "
            "along the seam." + _PHOTOREAL_SUFFIX
        ),
    ),
    Variant(
        register="c-metaphor",
        label="metaphor-led with caricatured figures",
        prompt_text=(
            "Metaphor-led Versus composition. The HERO object is a single dominant upright "
            "missile / warhead standing in the center of the frame, a jagged ember-red seam "
            "of molten light running up its length. At its base, dwarfed by the weapon, two "
            "small stylized caricature figures stand in profile facing the missile and each "
            "other: a caricature of US President Donald Trump on the LEFT and a caricature of "
            "Iran's Supreme Leader Ayatollah Ali Khamenei on the RIGHT. The metaphor (the "
            "weapon between them) carries the whole idea; the recognizable figures are small "
            "secondary accents, not the subject. Anchored in the upper and center thirds; "
            "quiet dark lower band. Ember red #EF4444 is the only accent, glowing on the "
            "seam of the missile." + HOUSE_GRADE_SUFFIX
        ),
    ),
)


def _write_variant(image_bytes: bytes, digest_id: str, register: str, mime_type: str) -> Path:
    """Write variant bytes to ``assets/m0/<digest_id>/variant-<register>.<ext>``.

    Args:
        image_bytes: The raw image bytes returned by Gemini.
        digest_id: The digest identifier (e.g. ``digest-1``).
        register: The variant register slug (e.g. ``a-caricature``).
        mime_type: The mime type reported by Gemini, used to pick the extension.

    Returns:
        The path the image was written to.
    """
    extension = _MIME_TO_EXT.get(mime_type, "png")
    output_dir = ASSETS_M0_DIR / digest_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"variant-{register}.{extension}"
    output_path.write_bytes(image_bytes)
    return output_path


def generate_variants(client: genai.Client, digest_id: str, variants: tuple[Variant, ...]) -> None:
    """Generate every register variant for one digest, recording refusals loudly.

    Args:
        client: Initialized google-genai client.
        digest_id: The digest these variants belong to (for output pathing).
        variants: The register variants to generate.
    """
    for index, variant in enumerate(variants):
        logger.info(
            "variant_generation_started",
            digest_id=digest_id,
            register=variant.register,
            label=variant.label,
            model=GEMINI_IMAGE_MODEL,
            prompt_length=len(variant.prompt_text),
        )
        try:
            response = _generate_one_call(client, variant.prompt_text)
        except Exception as exc:  # noqa: BLE001 — record any live API error verbatim and continue.
            logger.error(
                "variant_generation_failed",
                digest_id=digest_id,
                register=variant.register,
                error_type=type(exc).__name__,
                error_message=str(exc),
                fix_suggestion=(
                    "Live Gemini error on this variant (NOT a content refusal). Check key "
                    "image-gen access/quota; the other variants still attempt."
                ),
            )
            continue

        image_bytes, mime_type = _extract_image_bytes(response)
        if not image_bytes:
            # Reason: empty parts = safety filter. For the photoreal-of-named-figures
            # spike this is the EXPECTED failure mode and a real §5 finding — surface
            # it loudly (Rule 12), never silently skip.
            logger.error(
                "variant_generation_refused",
                digest_id=digest_id,
                register=variant.register,
                label=variant.label,
                fix_suggestion=(
                    "Nano Banana Pro returned no image part for this register (likely a "
                    "named-living-person safety filter). This is the §5 refusal finding: "
                    "prefer the caricature register, or rephrase to descriptive-not-named."
                ),
            )
            continue

        output_path = _write_variant(image_bytes, digest_id, variant.register, mime_type)
        logger.info(
            "variant_generation_completed",
            digest_id=digest_id,
            register=variant.register,
            output_path=str(output_path),
            mime_type=mime_type,
            byte_size=len(image_bytes),
        )

        if index < len(variants) - 1:
            time.sleep(INTER_CALL_DELAY_SECONDS)


def main() -> None:
    """Module entry point: generate the 3 register variants for digest-1."""
    settings = Settings()
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        logger.error(
            "gemini_api_key_missing",
            fix_suggestion="Set GEMINI_API_KEY in .env (project root).",
        )
        return

    client = genai.Client(api_key=api_key)
    generate_variants(client, "digest-1", DIGEST_1_VARIANTS)


if __name__ == "__main__":
    main()
