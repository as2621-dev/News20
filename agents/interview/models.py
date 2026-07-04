"""Pydantic models for the interview turn protocol — the public contract slice #4 consumes.

The request carries the whole conversation state (the worker is stateless, so state
travels with each request). The response is one of three shapes discriminated by
``response_kind``: ``question`` (next question + bubbles), ``terminal`` (the extracted
micro-interest list), or ``retry`` (a typed, HTTP-200 graceful-failure body the client
re-asks the same turn from). See ``reference/interview-onboarding-spec.md`` §2–§4.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# A single bubble label — bounded so a crafted body can't bloat the Gemini prompt.
BubbleLabel = Annotated[str, Field(max_length=200)]

# Bubble roles the client renders identically regardless of turn: real options plus
# the two always-present affordances (skip / type-your-own) the guardrails mandate.
BubbleKind = Literal["option", "skip", "type_your_own"]

# The one response discriminant. ``retry`` is the graceful-failure body (still HTTP 200).
ResponseKind = Literal["question", "terminal", "retry"]

# Which skippable question a deferred record came from (spec §5 skip semantics).
DeferralKind = Literal["subniche_skip", "category_skip", "who_drill_skip", "roots_skip"]


class DeferredQuestion(BaseModel):
    """A question the user skipped, recorded at terminal for later in-app resurfacing (spec §5/§6).

    The chat never blocks on a skip — it fast-forwards — but every skipped question is
    persisted as *deferred* so a fast-follow surface can re-ask it. The record is engine-owned
    and deterministic (never model judgment): the phase state machine emits one per skip.

    Attributes:
        deferral_kind: Which skippable turn produced this record.
        question_text: The exact question the user skipped (as shown that turn).
        root_slug: The category root the skip belongs to, when applicable.
        subniche_label: The sub-niche label the WHO drill was about, when applicable.
    """

    deferral_kind: DeferralKind = Field(
        ..., description="Which skippable turn produced this record."
    )
    question_text: str = Field(
        default="", max_length=1000, description="The question the user skipped."
    )
    root_slug: str | None = Field(
        default=None, description="The category root this skip belongs to, if any."
    )
    subniche_label: str | None = Field(
        default=None, description="The sub-niche the WHO drill was about, if any."
    )


class MuteTerm(BaseModel):
    """One thing the user asked to mute, from a SKIP TUNE tap/typed term (spec §6).

    Mutes are code-collected straight from the user's taps and typed text — never model
    judgment — so every term is traceable to an actual answer (Rule 5). Persisted to
    ``user_mute_terms`` and applied as a HARD FILTER at feed assembly (never at ingestion,
    never as downranking — spec §8): a story matching the term never enters the user's feed.

    Attributes:
        mute_category: The root category (slug) the mute belongs to (e.g. ``sport``).
        mute_term: The user-vocabulary term to mute (a tapped option or typed text).
    """

    mute_category: str = Field(..., description="Root category slug the mute belongs to.")
    mute_term: str = Field(
        ..., max_length=200, description="User-vocabulary term to mute (tap or typed text)."
    )


class AnglePreference(BaseModel):
    """One ANGLE TUNE answer: which lens the user reads a category through (spec §6).

    Code-collected from taps/typed text (traceable, never invented). Additive terminal
    output persisted with the profile; it does not gate assembly in this slice.

    Attributes:
        angle_category: The root category (slug) the angle belongs to.
        angle_label: The user-vocabulary lens label (a tapped option or typed text).
    """

    angle_category: str = Field(..., description="Root category slug the angle belongs to.")
    angle_label: str = Field(
        ..., max_length=200, description="User-vocabulary lens label (tap or typed text)."
    )


class InterviewExchange(BaseModel):
    """One completed prior turn, echoed back by the client (no server-side session).

    Attributes:
        question_text: The question the client showed the user this turn.
        bubbles_offered: The bubble labels shown (includes skip / type-your-own).
        bubbles_tapped: The bubble labels the user tapped (subset of offered; may be empty).
        free_text_entered: What the user typed via "something else", if anything.
    """

    question_text: str = Field(
        ..., max_length=1000, description="The question shown to the user this turn."
    )
    bubbles_offered: list[BubbleLabel] = Field(
        default_factory=list,
        max_length=30,
        description="Bubble labels shown this turn.",
    )
    bubbles_tapped: list[BubbleLabel] = Field(
        default_factory=list,
        max_length=30,
        description="Bubble labels the user tapped this turn.",
    )
    free_text_entered: str | None = Field(
        default=None,
        max_length=2000,
        description="Free text the user typed this turn, if any.",
    )


class InterviewTurnRequest(BaseModel):
    """The interview turn request body — the full conversation state so far.

    Attributes:
        conversation_state: Ordered prior exchanges (empty on the very first turn).
    """

    conversation_state: list[InterviewExchange] = Field(
        default_factory=list,
        max_length=40,
        description="Ordered prior exchanges; empty on the first turn.",
    )


class InterviewBubble(BaseModel):
    """One rendered bubble in a non-terminal turn.

    Attributes:
        bubble_label: The user-vocabulary label to render.
        bubble_kind: option / skip / type_your_own (drives client affordance).
    """

    bubble_label: str = Field(..., description="User-vocabulary label to render.")
    bubble_kind: BubbleKind = Field(default="option", description="Bubble role.")


class MicroInterest(BaseModel):
    """A validated terminal micro-interest (schema §4). Every field is server-verified.

    Attributes:
        display_label: The user's own vocabulary (drives feed section headers).
        canonical_slug: Root-anchored dotted slug (e.g. ``sport.cricket.ipl.auctions``).
        ladder: Ordered parent chain derived from the slug (the fallback ladder).
        search_anchor_terms: >=2 concrete anchor terms for the BigQuery batched query.
        strict: When true the feed never climbs the ladder for this interest.
    """

    display_label: str = Field(..., description="User-vocabulary label.")
    canonical_slug: str = Field(..., description="Root-anchored dotted canonical slug.")
    ladder: list[str] = Field(
        ..., description="Ordered parent chain from root to slug."
    )
    search_anchor_terms: list[str] = Field(
        ..., min_length=2, description="At least two concrete search anchor terms."
    )
    strict: bool = Field(default=False, description="No fallback climb when true.")


class InterviewTurnCost(BaseModel):
    """Per-turn LLM cost/latency, surfaced for observability (PRD stories #24/#25).

    Attributes:
        model_name: The Gemini model that served the turn (empty on a no-LLM turn).
        prompt_tokens: Prompt token count reported by Gemini (0 when no LLM ran).
        output_tokens: Output token count reported by Gemini (0 when no LLM ran).
        total_tokens: Total token count reported by Gemini (0 when no LLM ran).
        wall_time_ms: Wall time spent in the turn.
    """

    model_name: str = Field(
        default="", description="Gemini model that served the turn."
    )
    prompt_tokens: int = Field(
        default=0, description="Prompt tokens reported by Gemini."
    )
    output_tokens: int = Field(
        default=0, description="Output tokens reported by Gemini."
    )
    total_tokens: int = Field(default=0, description="Total tokens reported by Gemini.")
    wall_time_ms: int = Field(default=0, description="Wall time for the turn in ms.")


class InterviewTurnResponse(BaseModel):
    """The single interview turn response. Read ``response_kind`` first, then its fields.

    ``question``  -> ``question_text`` + ``bubbles``.
    ``terminal``  -> ``micro_interests`` (+ ``roots_only_fallback`` for the skip path).
    ``retry``     -> ``error_code`` / ``error_message`` / ``retry_hint`` (still HTTP 200).
    ``turn_cost`` is always present so the client can accumulate cost across turns.
    """

    response_kind: ResponseKind = Field(
        ..., description="Discriminant for the response shape."
    )
    turn_index: int = Field(
        ..., description="0-based index of the turn this response is for."
    )

    question_text: str | None = Field(
        default=None, description="Next question (question kind)."
    )
    bubbles: list[InterviewBubble] = Field(
        default_factory=list, description="Bubbles to render (question kind)."
    )

    micro_interests: list[MicroInterest] = Field(
        default_factory=list, description="Extracted interests (terminal kind)."
    )
    roots_only_fallback: bool = Field(
        default=False,
        description="Terminal kind: user skipped through, feed falls back to roots-only.",
    )
    deferred_questions: list[DeferredQuestion] = Field(
        default_factory=list,
        description="Terminal kind: every skipped question, recorded for later resurfacing.",
    )
    mute_terms: list[MuteTerm] = Field(
        default_factory=list,
        description="Terminal kind: SKIP TUNE answers, applied as hard filters at assembly.",
    )
    angle_preferences: list[AnglePreference] = Field(
        default_factory=list,
        description="Terminal kind: ANGLE TUNE answers (reading-lens per category).",
    )

    error_code: str | None = Field(
        default=None, description="Typed error code (retry kind)."
    )
    error_message: str | None = Field(
        default=None, description="Human message (retry kind)."
    )
    retry_hint: str | None = Field(
        default=None, description="What the client should do (retry kind)."
    )

    turn_cost: InterviewTurnCost = Field(
        default_factory=InterviewTurnCost, description="Per-turn LLM cost/latency."
    )


class MicroInterestDraft(BaseModel):
    """A model-proposed interest, BEFORE server validation (never trusted as-is).

    The engine re-derives the ladder from ``canonical_slug`` (the model does not propose
    it) and enforces root-anchoring, the >=2 anchor-term minimum, traceability, and dedup —
    invalid drafts are dropped loudly, never minted. See
    :func:`agents.interview.guards.validate_and_dedup`.
    """

    display_label: str = Field(
        default="", description="Proposed user-vocabulary label."
    )
    canonical_slug: str = Field(default="", description="Proposed root-anchored slug.")
    search_anchor_terms: list[str] = Field(
        default_factory=list, description="Proposed search anchor terms."
    )
    strict: bool = Field(default=False, description="Proposed strictness.")


class InterviewDecision(BaseModel):
    """The structured JSON the deeper-turn Gemini call returns (response_schema).

    ``action='ask'`` -> ``question_text`` + ``bubbles`` (server truncates to <=6 and
    appends skip / type-your-own). ``action='terminate'`` -> ``micro_interests`` drafts
    the server validates. The server can OVERRIDE the action to terminate when a hard
    guardrail (drill cap / tap budget) fires — the model never controls termination.
    """

    action: Literal["ask", "terminate"] = Field(
        default="ask", description="Whether to ask another question or terminate."
    )
    question_text: str = Field(
        default="", description="Next question text when asking."
    )
    bubbles: list[str] = Field(
        default_factory=list,
        description="Candidate option labels when asking (server caps at 6).",
    )
    micro_interests: list[MicroInterestDraft] = Field(
        default_factory=list, description="Proposed interests when terminating."
    )
