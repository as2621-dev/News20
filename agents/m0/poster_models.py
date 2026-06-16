"""Shared types + constants for the SERP-seeded poster pipeline.

The pipeline turns a news headline into a brand-graded 9:16 poster by:
search (Serper) -> gate -> download -> score (6 criteria) -> select -> recast
prompt (Gemini) -> image-conditioned generate (Nano Banana Pro) -> grade.

This module is the single typed boundary every stage shares (Pydantic v2, no
raw dicts at the seams). See ``plans/phase-0b-serp-seeded-poster-pipeline.md``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Cheap flash model for the 3 LLM judgment steps (query build, scoring, synth);
# the image generator stays Nano Banana Pro (see generate_posters.GEMINI_IMAGE_MODEL).
GEMINI_LLM_MODEL: str = "gemini-2.5-flash"

# Serper / gating knobs (poster-pipeline plan §gates).
SERPER_NUM_RESULTS: int = 10
CANDIDATE_LIMIT: int = 5
MIN_SHORTEST_SIDE_PX: int = 320

# Reason: heavily-watermarked stock previews are poor seeds — drop by domain.
WATERMARK_STOCK_DOMAINS: tuple[str, ...] = (
    "gettyimages.",
    "shutterstock.",
    "alamy.",
    "istockphoto.",
    "dreamstime.",
    "123rf.",
)

# Segment accent per digest (poster-pipeline §8; diversified kept cyan to match
# the prior digest-5 look). Exactly one accent per poster.
SEGMENT_ACCENT_BY_DIGEST: dict[str, str] = {
    "digest-1": "#EF4444",  # Geopolitics / conflict — ember red
    "digest-2": "#F59E0B",  # Sport — amber
    "digest-3": "#22D3EE",  # Tech & science — cyan
    "digest-4": "#22C55E",  # Markets / company — green
    "digest-5": "#22D3EE",  # Diversified / wildcard (AI) — cyan
}
DEFAULT_ACCENT_HEX: str = "#22D3EE"

NEAR_BLACK_HEX: str = "#020617"


class SelectionCriterion(BaseModel):
    """One scored selection criterion and its weight."""

    key: str = Field(..., description="Stable snake_case criterion key")
    label: str = Field(..., description="Human-readable criterion name")
    weight: float = Field(..., description="Multiplier applied to the 0-10 score")
    description: str = Field(..., description="What the scorer judges for this criterion")


# The 6 criteria + weights, LOCKED by the user 2026-05-29 (emotional tone ×3 is
# heaviest; relevance ×2 and also a soft gate so a striking-but-off image can't win).
SELECTION_CRITERIA: tuple[SelectionCriterion, ...] = (
    SelectionCriterion(
        key="headline_aptness",
        label="Headline aptness",
        weight=2.0,
        description="Does the image actually depict THIS story's subject/event/person?",
    ),
    SelectionCriterion(
        key="metaphor_potential",
        label="Metaphor / transformation potential",
        weight=1.5,
        description="If recast, how likely to become a strong conceptual/pun poster (not just a restyle)?",
    ),
    SelectionCriterion(
        key="vertical_fit",
        label="9:16 vertical fit",
        weight=1.0,
        description="Does the focal arrangement survive a vertical recompose (not a wide panorama)?",
    ),
    SelectionCriterion(
        key="single_subject",
        label="Single dominant + recognizable subject",
        weight=1.0,
        description="One clear, identifiable hero (person/object), not a cluttered crowd/podium.",
    ),
    SelectionCriterion(
        key="iconic",
        label="Iconic distinctiveness",
        weight=1.0,
        description="Striking and memorable vs. a generic forgettable wire/stock photo.",
    ),
    SelectionCriterion(
        key="emotional_tone",
        label="Emotional tone match",
        weight=3.0,
        description="Does the image's mood (tension/awe/triumph/grief) match the story's valence?",
    ),
)

# Below this relevance score a candidate is disqualified before ranking.
RELEVANCE_GATE_MIN: float = 5.0


class StoryConcept(BaseModel):
    """The concept-first distillation of a story (poster-pipeline §4).

    One cheap Gemini-flash call yields this; it then drives the search query, the
    scoring emphasis, and the synthesis prompt so the poster tells the story at a
    glance with the correct emotion.
    """

    image_search_query: str = Field(
        ..., description="3-8 word Google-Images query to surface a strong real photo of the subject"
    )
    key_subject: str = Field(
        ..., description="The single most important person/entity to SHOW (prefer the named person)"
    )
    defining_object_or_action: str = Field(
        ..., description="The one object or action that makes the story instantly legible"
    )
    emotional_valence: str = Field(
        ..., description="The TRUE mood, including irony (e.g. 'record results but stock fell — subdued')"
    )
    gist: str = Field(..., description="One sentence a viewer should grasp from the image alone")
    is_person_driven: bool = Field(
        default=True, description="True if a specific person is the natural hero of the image"
    )
    central_subject_count: int = Field(
        default=1,
        ge=1,
        le=3,
        description="How many people are CENTRAL to the story (1 normally; 2 for a confrontation/"
        "partnership). Bystanders/background people do NOT count and must be excluded.",
    )
    directional_sentiment: str = Field(
        default="none",
        description="'up_gain' | 'down_loss' | 'none'. If the story is a financial/quantitative "
        "rise or fall, the trend element must be coloured semantically (green up / red down).",
    )
    entity_kind: str = Field(
        default="other",
        description="What the story is fundamentally ABOUT, for image seeding: 'company' (show its "
        "logo/branding), 'person' (show the person), 'country' (show its flag), or 'other'.",
    )
    entity_name: str = Field(
        default="",
        description="The single named entity to depict — the company (e.g. 'Nvidia'), person "
        "(e.g. 'Jensen Huang'), or country (e.g. 'France'). Empty when entity_kind is 'other'.",
    )


class ImageCandidate(BaseModel):
    """One image result from the SERP search (post-gate)."""

    candidate_id: str = Field(..., description="Stable id, e.g. 'digest-1-cand-2'")
    title: str = Field(default="", description="Result title / caption")
    source_page_url: str = Field(..., description="Page the image was found on (Serper 'link')")
    thumbnail_url: str = Field(default="", description="Low-res thumbnail (Serper 'thumbnailUrl')")
    full_image_url: str = Field(..., description="Full-res image URL (Serper 'imageUrl')")
    width_px: int = Field(default=0, description="Reported image width")
    height_px: int = Field(default=0, description="Reported image height")
    local_path: str | None = Field(default=None, description="Where the downloaded bytes were written")


class CriterionScore(BaseModel):
    """A single 0-10 criterion score with a one-line rationale."""

    key: str = Field(..., description="Matches a SELECTION_CRITERIA key")
    score: float = Field(..., ge=0.0, le=10.0, description="0-10 score for this criterion")
    rationale: str = Field(default="", description="One-line why")


class ScoredCandidate(BaseModel):
    """A candidate plus its per-criterion scores and weighted total."""

    candidate: ImageCandidate
    scores: list[CriterionScore] = Field(default_factory=list)
    weighted_total: float = Field(default=0.0, description="Sum of weight·score across criteria")
    disqualified: bool = Field(default=False, description="True if it failed the relevance gate")

    def score_for(self, key: str) -> float:
        """Return the 0-10 score for a criterion key (0.0 if absent)."""
        for criterion_score in self.scores:
            if criterion_score.key == key:
                return criterion_score.score
        return 0.0


class SelectionReport(BaseModel):
    """The auditable per-story record (Rule 12 — never silently pick)."""

    digest_id: str
    headline: str
    accent_hex: str
    refined_query: str
    candidate_count: int
    story_concept: dict = Field(default_factory=dict, description="The extracted StoryConcept (audit)")
    scored: list[ScoredCandidate] = Field(default_factory=list)
    winner_candidate_id: str | None = None
    synthesized_prompt: str = ""
    poster_path: str | None = None
    notes: str = ""
