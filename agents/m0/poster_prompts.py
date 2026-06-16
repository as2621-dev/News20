"""Concept-first poster prompts for the 5 M0 digests (poster-pipeline.md SSOT).

Each prompt is built from the ``reference/poster-pipeline.md`` §4 image-gen
skeleton (archetype + single metaphor object + witty twist + reserved lower-40%
negative space + near-black ``#020617`` + ONE accent), graded per §6 (duotone +
single-source rim light + film grain + soft vignette), and terminated with the
§9 negative prompt.

Concept-first means the metaphor is decided in WORDS here, before any image is
generated. Every concept is SYMBOLIC — no named or recognizable real people —
to dodge the named-living-person refusal risk flagged in poster-pipeline §5.

These are typed string constants consumed by ``generate_posters.py``. The
digest numbering matches ``documents/m0-digests.md`` (1=Iran, 2=Kelce/Guardians,
3=superconductivity, 4=Nvidia, 5=Pope-on-AI).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Reason: §6 grade + §9 negative are the constant "house" suffix appended to
# every concept so the 5 posters read as one system regardless of subject.
HOUSE_GRADE_SUFFIX: str = (
    " Near-black background #020617, single accent hue only, duotone grade toward "
    "#020617 plus the accent, cinematic single-source rim light, deep shadow falloff "
    "into black, fine film grain, soft radial vignette toward the focal point. "
    "Editorial-illustration / conceptual-poster register fused with cinematic lighting "
    "(hybrid). High contrast, one idea and one object only, legible at 120px thumbnail. "
    "Reserve the lower 40% of the frame as quiet dark low-detail negative space for a "
    "caption overlay; 9:16 vertical. "
    "--no text, no logos, no watermark, no border, no UI, no words."
)

# Register-NEUTRAL house constants for the SERP-seeded pipeline. Same grade/safe-zone/
# negatives as HOUSE_GRADE_SUFFIX, but WITHOUT the "editorial-illustration" register
# clause — because the reference-seeded synth targets a PHOTOREAL + graphic register
# (the look the user locked from digest-1 variant-b), set in the concept body itself.
_HOUSE_RENDER_GRADE: str = (
    " Near-black background #020617, restrained palette built around the segment accent (with "
    "semantic red/green only on any up/down element), duotone grade toward #020617 plus the accent, "
    "cinematic single-source rim light, deep shadow falloff into black, fine film grain, soft radial "
    "vignette toward the focal point. High contrast, one clear idea, one main subject, legible at "
    "120px thumbnail. Reserve the lower 40% of the frame as quiet dark low-detail negative space for "
    "a caption overlay; 9:16 vertical. "
)


def house_render_suffix(entity_kind: str = "other") -> str:
    """House grade + a negative-prompt tail tuned to the story's entity kind.

    Company and country posters MUST be allowed to show the identifying brand LOGO or
    national FLAG — that is the whole point: a viewer should recognize the subject at a
    glance. So the blanket "no logos / no words" negative is relaxed for those kinds;
    'person' and 'other' keep the strict no-logo/no-text negative.

    Args:
        entity_kind: The ``StoryConcept.entity_kind`` ('company'|'person'|'country'|'other').

    Returns:
        The grade clause followed by the entity-appropriate negative prompt.

    Example:
        >>> "no logos" in house_render_suffix("company")
        False
        >>> "no logos" in house_render_suffix("person")
        True
    """
    if entity_kind == "company":
        # Allow the brand logo/wordmark (it identifies the subject); still no stock junk.
        negative = "--no watermark, no border, no UI, no stock-site text, no background people, no bystanders, no crowd."
    elif entity_kind == "country":
        # Allow the national flag; flags carry no words, so keep "no text".
        negative = "--no text, no watermark, no border, no UI, no background people, no bystanders, no crowd."
    else:
        negative = (
            "--no text, no logos, no watermark, no border, no UI, no words, no background people, "
            "no bystanders, no crowd."
        )
    return _HOUSE_RENDER_GRADE + negative


# Back-compat constant: the strict (person/other) suffix for any legacy caller.
HOUSE_RENDER_SUFFIX: str = house_render_suffix("other")


class PosterPrompt(BaseModel):
    """A single concept-first poster prompt for one M0 digest.

    Attributes:
        digest_id: Digest identifier, e.g. ``digest-1`` (matches m0-digests.md).
        digest_headline: Short human-readable headline for logging/QA only.
        archetype: One of the 6 poster-pipeline §3 archetypes.
        accent_hex: The single accent hue for this poster (§8).
        prompt_text: The fully-resolved image-gen prompt (concept + house grade).
        fallback_prompt_text: A more abstract/symbolic rephrase retried ONCE if
            the primary prompt returns an empty (safety-filtered) response.
    """

    digest_id: str = Field(..., description="Digest identifier matching m0-digests.md")
    digest_headline: str = Field(..., description="Short headline, for logging/QA only")
    archetype: str = Field(..., description="One of the 6 poster-pipeline archetypes")
    accent_hex: str = Field(..., description="The single accent hue for this poster")
    prompt_text: str = Field(..., description="Fully-resolved primary image-gen prompt")
    fallback_prompt_text: str = Field(
        ...,
        description="More abstract rephrase, retried once on a safety-filter empty response",
    )


# Reason: concepts are authored as the bare metaphor; HOUSE_GRADE_SUFFIX is
# appended at construction so the grade/negative stay identical across all five.
POSTER_PROMPTS: tuple[PosterPrompt, ...] = (
    PosterPrompt(
        digest_id="digest-1",
        digest_headline="US strikes Iran again; Strait of Hormuz tensions",
        archetype="Versus / Split",
        accent_hex="#EF4444",
        prompt_text=(
            "Versus / Split composition. Two anonymous opposed dark human profiles "
            "facing each other from the left and right edges, posterized as flat black "
            "silhouettes; a single jagged glowing ember-red seam tears straight down the "
            "center between them. The empty negative space between the two profiles is "
            "shaped so it reads as the silhouette of an upright missile / warhead. The "
            "standoff is anchored in the upper and center thirds. Ember red #EF4444 is the "
            "only accent, glowing along the seam." + HOUSE_GRADE_SUFFIX
        ),
        fallback_prompt_text=(
            "Versus / Split composition. A single jagged glowing ember-red fracture seam "
            "running vertically down the center of a near-black field, splitting it into "
            "two opposed dark halves; the bright gap at the seam is shaped like an upright "
            "missile silhouette. Purely abstract, no human figures. Ember red #EF4444 is "
            "the only accent." + HOUSE_GRADE_SUFFIX
        ),
    ),
    PosterPrompt(
        digest_id="digest-2",
        digest_headline="Travis Kelce buys minority stake in the Guardians",
        archetype="Metaphor / Pun",
        accent_hex="#F59E0B",
        prompt_text=(
            "Metaphor / Pun composition. A single baseball, hero macro, anchored in the "
            "upper-center third; its curved red-and-amber stitching unspools and morphs "
            "upward into a rising stock-ticker equity line / climbing market chart — an "
            "athlete buying the game. One concrete object, the pun is the whole idea. Warm "
            "amber #F59E0B is the only accent, glowing along the ascending line."
            + HOUSE_GRADE_SUFFIX
        ),
        fallback_prompt_text=(
            "Metaphor / Pun composition. A worn leather baseball glove, hero macro, "
            "cradling a single glowing minted gold coin where the ball would sit — sport "
            "becoming ownership and equity. One object only. Warm amber #F59E0B is the "
            "only accent, glowing from the coin." + HOUSE_GRADE_SUFFIX
        ),
    ),
    PosterPrompt(
        digest_id="digest-3",
        digest_headline="University of Houston breaks 30-year superconductivity record (151 K)",
        archetype="Single Icon",
        accent_hex="#22D3EE",
        prompt_text=(
            "Single Icon composition. A single glowing crystalline superconductor sample "
            "levitating in mid-air above a frosted ceramic plate (the Meissner effect), hero "
            "macro, anchored in the upper-center third; cold vapor curls off it; a thin "
            "magnetic glow separates the floating crystal from the plate below. One object, "
            "calm and authoritative. Cyan #22D3EE is the only accent, lit from within the "
            "crystal." + HOUSE_GRADE_SUFFIX
        ),
        fallback_prompt_text=(
            "Single Icon composition. A single glowing cyan crystalline shard floating above "
            "a frosted surface with cold vapor drifting off it, hero macro, anchored high in "
            "the frame. Abstract laboratory still. Cyan #22D3EE is the only accent, lit from "
            "within." + HOUSE_GRADE_SUFFIX
        ),
    ),
    PosterPrompt(
        digest_id="digest-4",
        digest_headline="Nvidia blowout quarter, but the stock slips",
        archetype="Metaphor / Pun",
        accent_hex="#22C55E",
        prompt_text=(
            "Metaphor / Pun composition. A single giant bold green up-arrow standing upright "
            "on a dark floor, anchored in the upper-center third; the long cast shadow it "
            "throws across the floor is, contradictorily, a red down-arrow — the contradiction "
            "between blowout earnings and a slipping stock is the entire story. One object. "
            "Primary accent green #22C55E on the arrow; the contested color red #EF4444 "
            "appears ONLY in the cast shadow (story-driven two-tone exception)."
            + HOUSE_GRADE_SUFFIX
        ),
        fallback_prompt_text=(
            "Metaphor / Pun composition. A single bold green up-arrow whose cast shadow on the "
            "dark floor below stretches downward as a red down-arrow — triumph casting a "
            "decline. Minimal, one object on a near-black field. Green #22C55E on the arrow, "
            "red #EF4444 only in the shadow." + HOUSE_GRADE_SUFFIX
        ),
    ),
    PosterPrompt(
        digest_id="digest-5",
        digest_headline="Pope Leo XIV's strongest-yet moral warning on AI",
        archetype="Single Icon",
        accent_hex="#22D3EE",
        prompt_text=(
            "Single Icon composition. A single shaft of pale light falls through a tall dark "
            "stone cathedral arch onto a glowing circuit-board floor below — sacred meets "
            "technology. The arch is anchored in the upper and center thirds; the lit circuit "
            "floor sits just above the reserved dark lower band. Awe and stillness, one idea. "
            "Cyan #22D3EE is the only accent, glowing in the circuit traces of the floor."
            + HOUSE_GRADE_SUFFIX
        ),
        fallback_prompt_text=(
            "Single Icon composition. A single shaft of light descending through a dark stone "
            "arch onto a faintly glowing circuit-board floor, sacred-meets-tech, anchored high "
            "in the frame. No figures. Cyan #22D3EE is the only accent, in the circuit traces."
            + HOUSE_GRADE_SUFFIX
        ),
    ),
)
