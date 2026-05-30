"""Concept-first distillation of a story (poster-pipeline §4), via Gemini Flash.

One cheap call turns a headline + summary into a ``StoryConcept``: the search
query, the key subject to show, the defining object/action that makes the story
legible at a glance, the TRUE emotional valence (including irony), and a one-line
gist. This concept then drives the search, the scoring emphasis, and the
synthesis prompt — so every downstream step optimizes for "a viewer can almost
tell the story from the image alone."

Falls back to a headline-derived concept if the LLM call fails (Rule 12).
"""

from __future__ import annotations

from google import genai

from agents.m0.poster_models import GEMINI_LLM_MODEL, StoryConcept
from agents.shared.logger import get_logger

logger = get_logger("m0.story_concept")

_CONCEPT_INSTRUCTION: str = (
    "You distill a news story into a VISUAL concept for a single news poster whose goal is "
    "that a viewer can grasp the story at a glance, with no text. Be concrete and specific.\n"
    "- image_search_query: 3-8 words that will surface a strong, real EDITORIAL photo of the "
    "key subject, ideally shown WITH the defining object/action (prefer named people/places).\n"
    "- key_subject: the single most important person or entity to SHOW (prefer the named "
    "person, e.g. 'Nvidia CEO Jensen Huang', 'Pope Leo XIV', 'the University of Houston "
    "physicist').\n"
    "- defining_object_or_action: the ONE object or action that makes the story instantly "
    "legible (e.g. 'holding up the levitating superconductor sample', 'a falling stock chart').\n"
    "- emotional_valence: the TRUE mood, INCLUDING irony — if results are great but the market "
    "punished them, the mood is subdued/deflated despite success, NOT triumphant.\n"
    "- gist: one sentence a viewer should understand from the image alone.\n"
    "- is_person_driven: true if a specific person is the natural hero of the image.\n"
    "- central_subject_count: how many people are CENTRAL to the story — 1 normally; 2 ONLY for a "
    "genuine confrontation or partnership (e.g. two named leaders). Co-authors, aides, crowds and "
    "bystanders do NOT count; the image should have a single hero unless two are truly essential.\n"
    "- directional_sentiment: 'down_loss' if the story is fundamentally about a fall/decline/loss "
    "(stock down, defeat), 'up_gain' if a rise/gain/win, else 'none'.\n"
    "Return JSON only."
)


def extract_story_concept(headline: str, summary: str, client: genai.Client) -> StoryConcept:
    """Extract the visual ``StoryConcept`` for a story.

    Args:
        headline: The story headline.
        summary: A short summary (e.g. the joined narration).
        client: Initialized google-genai client.

    Returns:
        A StoryConcept; falls back to a headline-derived concept on failure.
    """
    user_text = f"Headline: {headline}\nSummary: {summary}"
    try:
        response = client.models.generate_content(
            model=GEMINI_LLM_MODEL,
            contents=[
                genai.types.Content(
                    role="user",
                    parts=[genai.types.Part(text=f"{_CONCEPT_INSTRUCTION}\n\n{user_text}")],
                )
            ],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=StoryConcept,
            ),
        )
        concept: StoryConcept = response.parsed  # type: ignore[assignment]  # SDK validates the schema
        if not concept.image_search_query.strip():
            raise ValueError("empty search query in concept")
        logger.info(
            "story_concept_extracted",
            headline=headline,
            image_search_query=concept.image_search_query,
            key_subject=concept.key_subject,
            emotional_valence=concept.emotional_valence,
            is_person_driven=concept.is_person_driven,
        )
        return concept
    except Exception as exc:  # noqa: BLE001 — fall back to a headline concept, logged loudly.
        logger.error(
            "story_concept_failed",
            headline=headline,
            error_type=type(exc).__name__,
            error_message=str(exc),
            fix_suggestion="Using a headline-derived fallback concept.",
        )
        return StoryConcept(
            image_search_query=headline,
            key_subject=headline,
            defining_object_or_action="the central subject of the story",
            emotional_valence="serious news tone",
            gist=headline,
            is_person_driven=True,
            central_subject_count=1,
            directional_sentiment="none",
        )
