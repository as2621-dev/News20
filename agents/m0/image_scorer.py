"""Score candidate images against the 6-criteria rubric (Gemini Flash, multimodal).

Each candidate image + the story is sent to the flash model, which returns a
0-10 score per criterion as structured JSON. We compute the weighted total
(``SELECTION_CRITERIA`` weights), apply the relevance soft-gate, and pick the
winner. The rubric and weights live in ``poster_models.py`` so they're tunable
in one place.
"""

from __future__ import annotations

from google import genai
from pydantic import BaseModel, Field

from agents.m0.download_candidates import DownloadedCandidate
from agents.m0.poster_models import (
    GEMINI_LLM_MODEL,
    RELEVANCE_GATE_MIN,
    SELECTION_CRITERIA,
    CriterionScore,
    ScoredCandidate,
    StoryConcept,
)
from agents.shared.logger import get_logger

logger = get_logger("m0.image_scorer")


class _ScoreResponse(BaseModel):
    """Structured score the flash model must return (0-10 each)."""

    headline_aptness: float = Field(ge=0, le=10)
    metaphor_potential: float = Field(ge=0, le=10)
    vertical_fit: float = Field(ge=0, le=10)
    single_subject: float = Field(ge=0, le=10)
    iconic: float = Field(ge=0, le=10)
    emotional_tone: float = Field(ge=0, le=10)
    rationale: str = Field(default="", description="One sentence justifying the scores")


_WEIGHT_BY_KEY: dict[str, float] = {c.key: c.weight for c in SELECTION_CRITERIA}


def _score_instruction(concept: StoryConcept) -> str:
    """Concept-aware scoring rubric: reward story-legibility + the TRUE emotion."""
    return (
        "You are an art director selecting a SEED photo to transform into a PHOTOREALISTIC, "
        "graphically-styled editorial news poster (the photo will be recast, not used as-is). "
        "The #1 goal: a viewer should grasp the story FROM THE IMAGE ALONE.\n"
        f"Story gist: {concept.gist}\n"
        f"Key subject to show: {concept.key_subject}\n"
        f"Defining object/action that tells the story: {concept.defining_object_or_action}\n"
        f"Target emotional tone: {concept.emotional_valence}\n\n"
        "Score the attached image 0-10 on each:\n"
        "- headline_aptness: does it depict the key subject AND, ideally, the defining "
        "object/action — i.e. is the story legible at a glance? Reward images that TELL THE STORY "
        "(subject together with the object/action); penalize pretty-but-generic shots.\n"
        "- metaphor_potential: if recast, could it become a strong conceptual poster?\n"
        "- vertical_fit: does the focal arrangement survive a 9:16 vertical recompose?\n"
        "- single_subject: one clear, recognizable hero (ideally the key subject), not clutter?\n"
        "- iconic: striking and memorable vs. a generic forgettable wire/stock photo?\n"
        f"- emotional_tone: does its mood match the TARGET tone '{concept.emotional_valence}'?\n"
        "Be discerning; spread the scores. Return JSON only."
    )


def _score_one(downloaded: DownloadedCandidate, concept: StoryConcept, client: genai.Client) -> ScoredCandidate:
    """Score a single downloaded candidate; returns zeros on a failed call."""
    candidate = downloaded.candidate
    prompt = _score_instruction(concept)
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
                        genai.types.Part(text=prompt),
                    ],
                )
            ],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ScoreResponse,
            ),
        )
        parsed: _ScoreResponse = response.parsed  # type: ignore[assignment]  # SDK validates against the schema
    except Exception as exc:  # noqa: BLE001 — a single scoring failure scores 0, never kills the batch.
        logger.error(
            "candidate_scoring_failed",
            candidate_id=candidate.candidate_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            fix_suggestion="Candidate scored 0 across criteria so the batch continues.",
        )
        parsed = _ScoreResponse(
            headline_aptness=0, metaphor_potential=0, vertical_fit=0,
            single_subject=0, iconic=0, emotional_tone=0, rationale="scoring call failed",
        )

    scores = [
        CriterionScore(key=criterion.key, score=getattr(parsed, criterion.key), rationale=parsed.rationale)
        for criterion in SELECTION_CRITERIA
    ]
    weighted_total = sum(_WEIGHT_BY_KEY[s.key] * s.score for s in scores)
    disqualified = parsed.headline_aptness < RELEVANCE_GATE_MIN

    logger.info(
        "candidate_scored",
        candidate_id=candidate.candidate_id,
        weighted_total=round(weighted_total, 2),
        disqualified=disqualified,
        **{s.key: s.score for s in scores},
    )
    return ScoredCandidate(
        candidate=candidate, scores=scores, weighted_total=weighted_total, disqualified=disqualified
    )


def score_candidates(
    downloaded: list[DownloadedCandidate], concept: StoryConcept, client: genai.Client
) -> list[ScoredCandidate]:
    """Score every downloaded candidate against the concept-aware rubric."""
    return [_score_one(d, concept, client) for d in downloaded]


def select_winner(scored: list[ScoredCandidate]) -> ScoredCandidate | None:
    """Pick the highest weighted total, honoring the relevance gate + tie-breaks.

    Eligible = candidates that pass the relevance gate (unless ALL fail, in which
    case every candidate is eligible so we still return something). Tie-break:
    headline_aptness, then iconic.

    Args:
        scored: All scored candidates.

    Returns:
        The winning ScoredCandidate, or None if the list is empty.
    """
    if not scored:
        return None
    eligible = [s for s in scored if not s.disqualified] or scored
    eligible.sort(
        key=lambda s: (s.weighted_total, s.score_for("headline_aptness"), s.score_for("iconic")),
        reverse=True,
    )
    winner = eligible[0]
    logger.info(
        "winner_selected",
        winner_candidate_id=winner.candidate.candidate_id,
        weighted_total=round(winner.weighted_total, 2),
        used_relevance_gate=any(not s.disqualified for s in scored),
    )
    return winner
