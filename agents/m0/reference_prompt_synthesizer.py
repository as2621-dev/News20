"""Write a recast image-gen prompt from the winning reference (Gemini Flash, multimodal).

Target register (locked by the user from digest-1 variant-b): PHOTOREALISTIC base
with bold graphic/brand treatment layered on top and ONE subtle visual metaphor —
NOT a flat cartoon/caricature. The non-negotiable: a viewer who can't read any
text should grasp the story at a glance, so the poster keeps the recognizable
``key_subject`` together with the ``defining_object_or_action``, in the story's
TRUE ``emotional_valence``. The model sees the chosen seed photo and recasts it.
"""

from __future__ import annotations

from google import genai

from agents.m0.download_candidates import DownloadedCandidate
from agents.m0.poster_models import GEMINI_LLM_MODEL, StoryConcept
from agents.m0.poster_prompts import house_render_suffix
from agents.shared.logger import get_logger

logger = get_logger("m0.reference_prompt_synthesizer")


def _cast_rule(concept: StoryConcept) -> str:
    """One-dominant-subject rule: pin the exact people for person stories; for a
    company/country story keep a single dominant logo/flag with no stray figures."""
    person_driven = concept.is_person_driven or concept.entity_kind == "person"
    if not person_driven:
        return (
            "- ONE DOMINANT SUBJECT: keep a single clear focal subject (the logo / flag / object). "
            "Do NOT add people, bystanders or background figures unless the story is genuinely about "
            "a person.\n"
        )
    if concept.central_subject_count <= 1:
        return (
            f"- ONE MAIN SUBJECT: depict EXACTLY ONE person — only {concept.key_subject}. Remove any "
            "other people, co-workers, aides, bystanders or background figures that appear in the "
            "seed photo; the frame has a single human focal character.\n"
        )
    return (
        f"- CAST: depict EXACTLY {concept.central_subject_count} people — {concept.key_subject} — "
        "because they are both central to the story. Include NO other bystanders or background "
        "figures.\n"
    )


def _entity_rule(concept: StoryConcept) -> str:
    """Make the subject instantly recognizable: show the brand logo / flag / person."""
    name = concept.entity_name.strip()
    if concept.entity_kind == "company" and name:
        return (
            f"- IDENTITY: this is a story ABOUT {name}. The recognizable {name} LOGO / wordmark / "
            "brand mark must be the clear focal subject so a viewer instantly knows the company. "
            "Render the logo accurately. If the story is about trouble, a rift, a fall or a scandal, "
            "express it ON the brand itself (e.g. a crack splitting the logo, the mark dimmed, "
            "fracturing or under storm light) — the logo plus that treatment IS the story.\n"
        )
    if concept.entity_kind == "country" and name:
        return (
            f"- IDENTITY: this is a story ABOUT {name}. The {name} national FLAG must be clearly "
            "present and recognizable as the identifying element (waving, draped, or lit), carrying "
            "the story's mood — proud, tense, or storm-lit per the emotional tone.\n"
        )
    if concept.entity_kind == "person" and name:
        return (
            f"- IDENTITY: keep a believable, recognizable likeness of {name} as the hero of the "
            "frame; do not generalize them into an anonymous figure.\n"
        )
    return ""


def _color_rule(concept: StoryConcept, accent_hex: str) -> str:
    """Colour-serves-meaning rule: directional elements use semantic red/green."""
    base = (
        f"- COLOUR: the segment accent is {accent_hex}; use it as the dominant accent on the "
        "subject and overall grade.\n"
    )
    if concept.directional_sentiment == "down_loss":
        return base + (
            "  BUT any element that encodes the DECLINE/LOSS (a stock chart line, a down-arrow, a "
            "falling trend) MUST be RED #EF4444 so it reads as 'down' — colour serves meaning first, "
            "so this overrides the segment accent for that element specifically.\n"
        )
    if concept.directional_sentiment == "up_gain":
        return base + (
            "  AND any element that encodes the RISE/GAIN (a chart line, an up-arrow, a climbing "
            "trend) MUST be GREEN #22C55E so it reads as 'up' — colour serves meaning first.\n"
        )
    return base


def _synthesis_instruction(concept: StoryConcept, accent_hex: str) -> str:
    """Build the recast instruction targeting the photoreal-graphical house register."""
    return (
        "You are an editorial art director. The attached photo is the chosen SEED for a "
        "stylized 9:16 news poster. Write ONE image-generation prompt (3-5 sentences) that "
        "RECASTS this photo into our house style:\n"
        "- PHOTOREALISTIC base: keep a believable, recognizable real likeness of the subject "
        "(do NOT turn it into a flat cartoon or caricature), then layer bold GRAPHIC poster "
        "treatment on top (cinematic lighting, strong duotone grade, subtle graphic shapes / "
        "texture). Think 'photoreal movie-poster key art', not 'illustration'.\n"
        "- TELL THE STORY AT A GLANCE: a viewer who cannot read any text must understand the "
        f"story. Show {concept.key_subject} together with {concept.defining_object_or_action}. "
        f"Story gist: {concept.gist}.\n"
        + _entity_rule(concept)
        + _cast_rule(concept)
        + "- ADD ONE restrained visual metaphor only if it strengthens the idea.\n"
        f"- EMOTIONAL TONE must be exactly: {concept.emotional_valence}. The subject's "
        "expression and the whole mood must match this (e.g. if the mood is subdued/ironic, do "
        "NOT make it look triumphant).\n"
        + _color_rule(concept, accent_hex)
        + "- Anchor the subject in the upper/center third; keep the lower 40% quiet and dark.\n"
        "Write the prompt as direct generation instructions (no preamble, no 'the image shows', no "
        "marketing words like 'seamlessly'). Do NOT describe a literal copy of the seed photo."
    )


def synthesize_prompt(
    downloaded: DownloadedCandidate, concept: StoryConcept, accent_hex: str, client: genai.Client
) -> str:
    """Synthesize a recast poster prompt from the winning reference image + concept.

    Args:
        downloaded: The winning downloaded candidate (image bytes + mime).
        concept: The story concept (subject, defining object, valence, gist).
        accent_hex: The single segment accent for this poster.
        client: Initialized google-genai client.

    Returns:
        A full poster prompt = recast concept + the register-neutral HOUSE_RENDER_SUFFIX.
        Falls back to a concept-only prompt if the LLM call fails.
    """
    instruction = _synthesis_instruction(concept, accent_hex)
    try:
        response = client.models.generate_content(
            model=GEMINI_LLM_MODEL,
            contents=[
                genai.types.Content(
                    role="user",
                    parts=[
                        genai.types.Part.from_bytes(
                            data=downloaded.image_bytes, mime_type=downloaded.mime_type
                        ),
                        genai.types.Part(text=instruction),
                    ],
                )
            ],
        )
        body = (response.text or "").strip()
        if not body:
            raise ValueError("empty concept from model")
    except Exception as exc:  # noqa: BLE001 — fall back to a safe concept body, logged.
        logger.error(
            "prompt_synthesis_failed",
            key_subject=concept.key_subject,
            error_type=type(exc).__name__,
            error_message=str(exc),
            fix_suggestion="Using a concept-only fallback prompt for this story.",
        )
        body = (
            f"A photorealistic editorial poster: {concept.key_subject} shown with "
            f"{concept.defining_object_or_action}, recast as cinematic photoreal key art with bold "
            f"graphic treatment. Emotional tone: {concept.emotional_valence}. {accent_hex} is the "
            "only accent. The story reads at a glance without text."
        )

    full_prompt = body + house_render_suffix(concept.entity_kind)
    logger.info(
        "prompt_synthesized",
        key_subject=concept.key_subject,
        entity_kind=concept.entity_kind,
        entity_name=concept.entity_name,
        accent_hex=accent_hex,
        body_length=len(body),
        prompt_length=len(full_prompt),
    )
    return full_prompt
