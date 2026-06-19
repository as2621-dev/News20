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
    "that a viewer can grasp the ENTIRE story from the image alone, with no text. Be concrete "
    "and specific.\n"
    "- entity_kind: what the story is fundamentally ABOUT — 'company' (a business/brand is the "
    "subject), 'person' (a specific named individual), 'country' (a nation/state is the subject), "
    "or 'other'. Pick the single dominant one.\n"
    "- entity_name: the ONE named entity to depict for that kind — the company (e.g. 'Nvidia'), the "
    "person (e.g. 'Jensen Huang'), or the country (e.g. 'France'). Empty string if 'other'.\n"
    "- IDENTITY RESOLUTION (critical): if the story references a ROLE or TITLE rather than a name "
    "(e.g. 'the Fed chair', 'the US president', 'the PM', 'the CEO'), resolve entity_name to the "
    "SPECIFIC person NAMED IN THIS STORY'S TEXT as of {story_date}. The story text is the ONLY "
    "source of truth: NEVER use your own knowledge of who currently or previously holds that office, "
    "and NEVER return the role/title itself as entity_name. If several leaders appear (e.g. a summit "
    "or a confrontation), pick the SINGLE PRIMARY person the story centers on.\n"
    "- image_search_query: 3-8 words to surface a strong, real reference photo of what to SHOW. "
    "Seed it by entity_kind: company -> '<company> logo'/'<company> sign'; country -> '<country> "
    "flag'; person -> the named person shown WITH the defining object/action.\n"
    "- key_subject: the single most important thing to SHOW. For 'company' it is the company's LOGO "
    "/ branding (e.g. 'the Nvidia logo'); for 'country' it is the national FLAG (e.g. 'the French "
    "flag'); for 'person' the named person (e.g. 'Nvidia CEO Jensen Huang').\n"
    "- defining_object_or_action: the ONE object/action/visual METAPHOR that makes the story "
    "instantly legible. Encode the story's TENSION in it, e.g. an internal company rift -> 'the "
    "company logo with a crack splitting through it'; two parties clashing -> 'the two subjects in "
    "side profile facing off'; a country in crisis -> 'the flag torn or storm-lit'; a rise/fall -> "
    "'a climbing/falling chart line'.\n"
    "- emotional_valence: the TRUE mood, INCLUDING irony — if results are great but the market "
    "punished them, the mood is subdued/deflated despite success, NOT triumphant.\n"
    "- gist: one sentence a viewer should understand from the image alone.\n"
    "- is_person_driven: true if a specific person is the natural hero of the image.\n"
    "- central_subject_count: how many subjects are CENTRAL — 1 normally; 2 ONLY for a genuine "
    "confrontation or partnership (e.g. two named leaders, or two clashing companies). Co-authors, "
    "aides, crowds and bystanders do NOT count; a single hero unless two are truly essential.\n"
    "- directional_sentiment: 'down_loss' if the story is fundamentally about a fall/decline/loss "
    "(stock down, defeat), 'up_gain' if a rise/gain/win, else 'none'.\n"
    "Return JSON only."
)


def _normalize_entity_key(entity_name: str) -> str:
    """Normalize a resolved entity name into a stable store-lookup key.

    Lowercases and collapses surrounding/inner whitespace so the same person
    maps to one key regardless of casing or stray spacing. Returns an empty
    string for an empty/whitespace name (no person to key on).

    Args:
        entity_name: The resolved entity name (may be empty).

    Returns:
        The normalized key, e.g. ``"Donald  Trump"`` -> ``"donald trump"``;
        ``""`` when there is no named entity.

    Example:
        >>> _normalize_entity_key("  Kevin   Warsh ")
        'kevin warsh'
        >>> _normalize_entity_key("")
        ''
    """
    # Reason: this key is used BOTH as the DB unique key AND a storage object-path
    # segment ("{entity_key}/reference.jpg"), and entity_name is LLM-derived — drop
    # path separators / non-printable chars and '..' so a resolved name can never
    # escape its bucket sub-path or form a traversal segment.
    cleaned = entity_name.replace("/", " ").replace("\\", " ").replace("..", " ")
    cleaned = "".join(character for character in cleaned if character.isprintable())
    return " ".join(cleaned.split()).lower()


def extract_story_concept(
    headline: str,
    summary: str,
    client: genai.Client,
    *,
    story_body: str | None = None,
    story_date: str | None = None,
) -> StoryConcept:
    """Extract the visual ``StoryConcept`` for a story, resolving the real named person.

    The model is fed the FULL story body and the story's date so it can resolve a
    role/title ("Fed chair", "US president") to the specific individual the story
    NAMES as of that date — never the model's own (often stale) prior of who holds
    the office. ``entity_key`` is computed deterministically from the resolved
    ``entity_name`` (the LLM is not trusted to normalize) and ``entity_as_of`` is
    set to ``story_date``.

    Args:
        headline: The story headline.
        summary: A short summary (e.g. the joined narration).
        client: Initialized google-genai client.
        story_body: The full story body text. Defaults to ``summary`` when not
            supplied, so existing headline+summary callers keep working.
        story_date: ISO date (YYYY-MM-DD) the story is anchored to, used to resolve
            office-holders as of that date. ``None`` when unknown.

    Returns:
        A StoryConcept; falls back to a headline-derived concept on failure.

    Example:
        >>> concept = extract_story_concept(  # doctest: +SKIP
        ...     headline="Trump names Kevin Warsh as Fed chair",
        ...     summary="...",
        ...     client=client,
        ...     story_body="President Trump named Kevin Warsh ...",
        ...     story_date="2026-02-01",
        ... )
        >>> concept.entity_name  # doctest: +SKIP
        'Kevin Warsh'
    """
    body_text = story_body if story_body is not None else summary
    date_text = (
        story_date
        if story_date
        else "the story's publication date (unknown — rely only on the text)"
    )
    instruction = _CONCEPT_INSTRUCTION.replace("{story_date}", date_text)
    user_text = f"Story date: {date_text}\nHeadline: {headline}\nSummary: {summary}\nFull story body:\n{body_text}"
    try:
        response = client.models.generate_content(
            model=GEMINI_LLM_MODEL,
            contents=[
                genai.types.Content(
                    role="user",
                    parts=[genai.types.Part(text=f"{instruction}\n\n{user_text}")],
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
        # Reason: own the normalization + date anchor in code, not the LLM, so the
        # store key is stable and entity_as_of is exactly the story's date.
        concept.entity_key = _normalize_entity_key(concept.entity_name)
        concept.entity_as_of = story_date
        logger.info(
            "story_concept_extracted",
            headline=headline,
            story_date=story_date,
            image_search_query=concept.image_search_query,
            key_subject=concept.key_subject,
            entity_name=concept.entity_name,
            entity_key=concept.entity_key,
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
            entity_kind="other",
            entity_name="",
            entity_key="",
            entity_as_of=story_date,
        )
