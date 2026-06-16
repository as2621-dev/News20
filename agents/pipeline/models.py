"""Pydantic models for the News20 produce-gate + single-source script pipeline (Phase 1d SP2).

ADAPTED from the TLDW donor (`agents/pipeline/models.py`). The donor models a
multi-source, multi-story, 12-minute audio *briefing* (RankedStory with N
source_content_items, BriefingScript at ~2050 words). News20 is the opposite
unit: **one canonical story → one ~55-second single-source digest**
(Decision #4) produced once and fanned out across every user whose interest it
serves. So these models are News20-native and intentionally smaller than the
donor's:

    - ``DialogueTurn``       -- one ALEX/JORDAN line (ported shape).
    - ``DigestScript``       -- the single-source digest script (News20's slim
                                analog of the donor's BriefingScript).
    - ``ClaimVerification``  -- one fact-checked claim (ported shape).
    - ``VerificationReport`` -- the hallucination-guardrail result.
    - ``ProduceDecision``    -- the produce-once gate's typed verdict (NEW).

The donor's ranking / quality / pipeline-state models are NOT ported here:
ranking is SP3 (and is a different, per-user heuristic — `reference/ranking-spec.md`),
the quality gate is out of scope, and pipeline state is the SP3 orchestrator's
concern. Porting them now would be dead code (Rule 2), mirroring how SP1
deferred ``feed_utils``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Speaker labels are the locked News20 anchor duo (reference/reuse-map.md:
# ALEX → Leda, JORDAN → Sadaltager). Unlike the donor, there is no CLIP speaker
# — News20 single-source digests have no embedded YouTube clip audio.
SpeakerLabel = Literal["ALEX", "JORDAN"]

# A claim's grounding status against the single source article. News20 drops the
# donor's NEEDS_HEDGE/Google-Search path: grounding is **in-context only** against
# the one source's body text (memory: news20-qa-incontext-grounding) — a claim is
# either SUPPORTED by the source or it is not (UNSUPPORTED / CONTRADICTED).
ClaimStatus = Literal["SUPPORTED", "UNSUPPORTED", "CONTRADICTED"]

# ── Detail analytics (Phase 2c + category-specific panels) ───────────────────
# One Detail "analytic panel" kind. Chosen DETERMINISTICALLY from the story's
# detail CATEGORY (never by the LLM) — ``detail_templates.py`` maps a category to
# its ordered list of panels, each with one of these kinds. Mirrors the
# analytic_kind Postgres enum (migrations 0004 + 0011 + 0014).
#
# The original Phase-2c kinds (market_impact / ripple / impact / stakes /
# why_it_matters / subject_profile) plus the category-specific additions:
#   what_we_know   — Breaking slot 2 (confirmed vs unconfirmed)
#   by_the_numbers — Markets slot 3 (key figures as rows)
#   the_concept    — Tech slot 3 (explain the underlying principle)
#   stat_line      — Sport slot 2 (the event's box-score numbers)
#   recent_form    — Sport slot 3 (team/player recent record; background allowed)
#   source_context — Sources slot 1 (the video/episode/gist)   [used phase-5d+]
#   key_points     — Sources slot 2                            [used phase-5d+]
#   implications   — Sources slot 3                            [used phase-5d+]
# ``ripple`` / ``impact`` remain for rows enriched before the 2026-06-12 remap.
AnalyticKind = Literal[
    "market_impact",
    "ripple",
    "impact",
    "stakes",
    "why_it_matters",
    "subject_profile",
    "what_we_know",
    "by_the_numbers",
    "the_concept",
    "stat_line",
    "recent_form",
    "source_context",
    "key_points",
    "implications",
]

# How the Detail "Coverage" tab is framed. Mirrors the coverage_mode Postgres enum
# (migrations 0004 + 0014). partisan = L·C·R + blindspot (contested / world);
# reach = covered-by-N + momentum + who-broke-it; reach_lite = covered-by-N +
# notable outlet names ONLY (Breaking — no momentum / who-broke-it). Chosen
# deterministically by detail category.
CoverageMode = Literal["partisan", "reach", "reach_lite"]

# Bias lean for outlets / coverage blindspot. Mirrors the bias_lean Postgres enum
# (supabase-schema.md §1) and the TS BiasLean type (src/types/detail.ts).
BiasLean = Literal["left", "center", "right"]

# Optional directional glyph for a single analytic row. Numeric values that the
# source does not support are dropped to a bare direction (Decision #5), so a row
# may carry a direction with no value.
AnalyticRowDirection = Literal["up", "down", "flat"]

# Reach-mode coverage momentum derived from the GDELT seendate volume/spread (SP2).
CoverageMomentum = Literal["breaking", "developing", "settled"]


class DialogueTurn(BaseModel):
    """A single turn of dialogue spoken by either ALEX or JORDAN.

    Ported shape from the donor. ALEX is the witty, playful host; JORDAN the
    sincere anchor. The text is rendered verbatim by Gemini multi-speaker TTS
    downstream (SP3), so it must be plain speakable English — no bracket tags.

    Attributes:
        speaker: Which anchor speaks this turn ("ALEX" or "JORDAN").
        text: The dialogue text (plain, speakable, non-empty).

    Example:
        >>> turn = DialogueTurn(speaker="ALEX", text="Wait, so they just announced it?")
        >>> turn.speaker
        'ALEX'
    """

    speaker: SpeakerLabel = Field(
        ...,
        description="Which anchor speaks this turn: ALEX (curious) or JORDAN (analyst)",
    )
    text: str = Field(
        ..., min_length=1, description="Plain speakable dialogue text for this turn"
    )


class DigestScript(BaseModel):
    """A complete single-source digest script for one canonical story.

    News20's slim analog of the donor's ``BriefingScript`` — but bounded to a
    single source article and a ~55-second runtime (Decision #4), not a
    multi-story 12-minute briefing.

    Attributes:
        digest_story_id: The canonical story this script narrates (FK to
            ``stories.story_id`` at persist time, SP3).
        turns: Ordered ALEX/JORDAN dialogue turns.
        word_count: Total spoken word count across all turns.
        estimated_duration_seconds: Estimated spoken duration at the calibrated WPM.
        source_url: The single source article URL the script is constrained to
            (carried through for the verification grounding step + UI attribution).

    Example:
        >>> script = DigestScript(
        ...     digest_story_id="cand-abc123",
        ...     turns=[DialogueTurn(speaker="ALEX", text="What just happened?")],
        ...     word_count=4,
        ...     estimated_duration_seconds=2,
        ... )
        >>> script.digest_story_id
        'cand-abc123'
    """

    digest_story_id: str = Field(
        ...,
        description="Canonical story id this script narrates (FK to stories.story_id)",
    )
    turns: list[DialogueTurn] = Field(
        ..., min_length=1, description="Ordered ALEX/JORDAN dialogue turns"
    )
    word_count: int = Field(
        default=0, ge=0, description="Total spoken word count across all turns"
    )
    estimated_duration_seconds: int = Field(
        default=0,
        ge=0,
        description="Estimated spoken duration in seconds (at the calibrated WPM)",
    )
    source_url: str = Field(
        default="",
        description="The single source article URL the script is constrained to",
    )


class ClaimVerification(BaseModel):
    """The grounding verdict for one factual claim extracted from the script.

    Ported shape from the donor, trimmed to News20's in-context grounding:
    each claim is checked against the single source's body text and classified
    SUPPORTED / UNSUPPORTED / CONTRADICTED (no NEEDS_HEDGE, no web search).

    Attributes:
        claim_text: The factual claim extracted from the digest script.
        status: Whether the source supports, fails to support, or contradicts it.
        source_evidence: A short supporting snippet/locator from the source
            (empty for UNSUPPORTED).

    Example:
        >>> claim = ClaimVerification(
        ...     claim_text="The central bank cut rates by 50 basis points",
        ...     status="SUPPORTED",
        ...     source_evidence="...cut its benchmark rate by half a point...",
        ... )
        >>> claim.status
        'SUPPORTED'
    """

    claim_text: str = Field(
        ..., description="The factual claim extracted from the digest script"
    )
    status: ClaimStatus = Field(
        ..., description="Grounding status against the single source article"
    )
    source_evidence: str = Field(
        default="",
        description="Short supporting snippet/locator from the source (empty if unsupported)",
    )


class VerificationReport(BaseModel):
    """The hallucination-guardrail result for one digest script.

    The guardrail (Decision #5) blocks any digest whose script makes claims the
    single source does not support. ``is_grounded`` is False whenever any claim
    is UNSUPPORTED or CONTRADICTED — the orchestrator (SP3) must not publish a
    non-grounded digest.

    Attributes:
        digest_story_id: The story whose script was verified.
        claims: Every extracted claim with its grounding verdict.
        is_grounded: True only when no claim is UNSUPPORTED or CONTRADICTED.
        ungrounded_claim_count: Count of UNSUPPORTED + CONTRADICTED claims.

    Example:
        >>> report = VerificationReport(
        ...     digest_story_id="cand-abc123",
        ...     claims=[ClaimVerification(claim_text="x", status="SUPPORTED")],
        ...     is_grounded=True,
        ...     ungrounded_claim_count=0,
        ... )
        >>> report.is_grounded
        True
    """

    digest_story_id: str = Field(..., description="The story whose script was verified")
    claims: list[ClaimVerification] = Field(
        default_factory=list,
        description="Every extracted claim with its grounding verdict",
    )
    is_grounded: bool = Field(
        ...,
        description="True only when no claim is UNSUPPORTED or CONTRADICTED",
    )
    ungrounded_claim_count: int = Field(
        default=0, ge=0, description="Count of UNSUPPORTED + CONTRADICTED claims"
    )


class ProduceDecision(BaseModel):
    """The produce-once gate's typed verdict for one canonical story (NEW).

    A story is produced only when it (a) serves at least one active interest,
    (b) clears the importance/freshness floor, and (c) lacks a current digest
    (``digests.digest_is_current``). When any check fails, ``should_produce`` is
    False and ``skip_reason`` names the failing check — keeping generation cost
    down (one canonical asset per story, Decision #3).

    Attributes:
        story_id: The canonical story this verdict is for.
        should_produce: True only when every gate check passes.
        skip_reason: Machine-readable reason when skipped, else "".
        serves_interest_count: How many active interests the story serves.
        importance_score: The 0–1 importance score evaluated against the floor.
        freshness_score: The 0–1 freshness score evaluated against the floor.

    Example:
        >>> decision = ProduceDecision(
        ...     story_id="cand-abc123",
        ...     should_produce=False,
        ...     skip_reason="has_current_digest",
        ...     serves_interest_count=2,
        ... )
        >>> decision.should_produce
        False
    """

    story_id: str = Field(..., description="The canonical story this verdict is for")
    should_produce: bool = Field(
        ..., description="True only when every produce-gate check passes"
    )
    skip_reason: str = Field(
        default="",
        description="Machine-readable skip reason when not produced, else empty",
    )
    serves_interest_count: int = Field(
        default=0, ge=0, description="How many active interests the story serves"
    )
    importance_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="0–1 importance score vs the floor"
    )
    freshness_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="0–1 freshness score vs the floor"
    )


# ── Detail analytics models (Phase 2c) ───────────────────────────────────────
# These back the richer Story Detail payload: the segment-skinned "second
# analytic" tab (story_analytics), the GDELT-fed Coverage report (story_trust
# reach columns), the hero key figure (stories.story_key_figure_*), the "HOW IT
# DEVELOPED" timeline (story_timeline), and the 5 at-a-glance bullets
# (detail_key_points). Every story_analytics.analytic_rows[] element is validated
# through ``AnalyticRow`` before insert — never a raw dict at the DB boundary
# (CLAUDE.md §0 / supabase-schema.md §0 types).


class AnalyticRow(BaseModel):
    """One labeled row inside a ``story_analytics.analytic_rows`` JSONB array.

    The variable-length array is consumed whole by the client renderer, but each
    element MUST round-trip through this model before insert so a fabricated /
    malformed row can never reach Postgres (Rule 9 — drop this validation and the
    test fails). Numeric ``analytic_row_value``s are grounded-or-omitted by SP3:
    an unsupported number is dropped and the row falls back to ``direction``-only.

    Attributes:
        analytic_row_label: The row's left-column label ("Brent crude", "EUR/USD").
        analytic_row_value: The grounded figure ("+4%", "$81.6B"), or None when no
            source-supported number exists (render direction-only instead).
        analytic_row_direction: Optional directional glyph ("up"|"down"|"flat").
        analytic_row_note: Optional one-line qualifier, or None.

    Example:
        >>> row = AnalyticRow(
        ...     analytic_row_label="Brent crude",
        ...     analytic_row_value="+4%",
        ...     analytic_row_direction="up",
        ... )
        >>> row.analytic_row_direction
        'up'
    """

    model_config = {"extra": "forbid"}

    analytic_row_label: str = Field(
        ...,
        min_length=1,
        description="Left-column label for the row (e.g. 'Brent crude')",
    )
    analytic_row_value: str | None = Field(
        default=None,
        description="Grounded figure (e.g. '+4%'); None when no source-supported number exists",
    )
    analytic_row_direction: AnalyticRowDirection | None = Field(
        default=None, description="Optional directional glyph: 'up' | 'down' | 'flat'"
    )
    analytic_row_note: str | None = Field(
        default=None, description="Optional one-line qualifier for the row"
    )


class SecondAnalytic(BaseModel):
    """One Detail "analytic panel" — a ``story_analytics`` row.

    Was strictly 1:1 per story (Phase 2c); now 1:N (migration 0013), so a story
    carries up to THREE ordered panels — one per ``analytic`` slot in its detail
    CATEGORY template (``detail_templates.py``). ``analytic_slot_index`` fixes the
    panel's position on the Detail page. ``analytic_kind`` is chosen
    deterministically from the template (never by the LLM), which also fixes
    ``analytic_tab_label``. The narrative (``analytic_headline`` /
    ``analytic_summary_text``) is LLM-drafted; the rows carry the (grounded)
    figures. ``analytic_is_grounded`` is the per-panel verdict that gates whether
    numeric values publish as facts (Decision #5).

    Attributes:
        analytic_story_id: The story this analytic belongs to (FK stories.story_id).
        analytic_slot_index: 0-based panel order on the Detail page (0 = second tab).
        analytic_kind: The template-derived analytic kind (drives tab label + accent).
        analytic_tab_label: The display label ("MARKET IMPACT", "STAKES", ...).
        analytic_headline: One-liner under the tab.
        analytic_summary_text: LLM 1–2 sentence so-what.
        analytic_rows: 2–4 validated ``AnalyticRow``s.
        analytic_is_grounded: True only when numeric values were verified vs source.

    Example:
        >>> analytic = SecondAnalytic(
        ...     analytic_story_id="s1",
        ...     analytic_slot_index=0,
        ...     analytic_kind="market_impact",
        ...     analytic_tab_label="MARKET IMPACT",
        ...     analytic_headline="Oil markets twitch on the Hormuz threat",
        ...     analytic_summary_text="A closure would choke ~20% of seaborne crude.",
        ...     analytic_rows=[AnalyticRow(analytic_row_label="Brent crude", analytic_row_value="+4%")],
        ...     analytic_is_grounded=True,
        ... )
        >>> analytic.analytic_kind
        'market_impact'
    """

    analytic_story_id: str = Field(
        ..., description="Story this analytic belongs to (FK stories.story_id)"
    )
    analytic_slot_index: int = Field(
        default=0, ge=0, description="0-based panel order on the Detail page (0 = second tab)"
    )
    analytic_kind: AnalyticKind = Field(
        ..., description="Template-derived analytic kind (drives tab label + accent)"
    )
    analytic_tab_label: str = Field(
        ..., min_length=1, description="Display label ('MARKET IMPACT', 'STAKES', ...)"
    )
    analytic_headline: str = Field(
        ..., min_length=1, description="One-liner shown under the tab"
    )
    analytic_summary_text: str = Field(
        ..., min_length=1, description="LLM 1–2 sentence so-what"
    )
    analytic_rows: list[AnalyticRow] = Field(
        default_factory=list,
        description="2–4 labeled rows; each validated as an AnalyticRow before insert",
    )
    analytic_is_grounded: bool = Field(
        default=False,
        description="True only when numeric row values were verified vs source",
    )


class CoverageReport(BaseModel):
    """The GDELT-fed Coverage summary backing ``story_trust``'s reach/partisan tab.

    Mode is chosen deterministically by segment (Decision #3): ``partisan`` for
    geopolitics (+ contested), else ``reach``. In ``partisan`` mode the L/C/R
    counts + ``blindspot_lean`` are meaningful; in ``reach`` mode the count is the
    distinct-outlet total and the momentum / originating / notable fields describe
    spread (SP2 fills these from the GDELT seendate distribution). Both shapes
    share ``coverage_outlet_count`` so the "COVERED BY N OUTLETS" line always renders.

    Attributes:
        coverage_mode: How this story's coverage is framed ("partisan" | "reach").
        coverage_left_count: partisan: outlets leaning left.
        coverage_center_count: partisan: outlets leaning center.
        coverage_right_count: partisan: outlets leaning right.
        coverage_outlet_count: total distinct outlets covering the story.
        blindspot_lean: partisan: the materially under-covered lean, or None.
        coverage_momentum: reach: 'breaking' | 'developing' | 'settled', or None.
        coverage_originating_outlet_name: reach: who broke it (earliest seendate), or None.
        coverage_notable_outlet_names: reach: up to 5 notable outlet names.
        coverage_is_breaking: whether the GDELT seendate spread reads as a fresh,
            tight "breaking" burst. Transport-only (NOT a ``story_trust`` column) —
            it selects the story's Detail panel template (the Breaking template).
            Computed for ALL modes, so a partisan/geopolitics story can still be
            flagged breaking even though partisan reports carry no ``coverage_momentum``.

    Example:
        >>> report = CoverageReport(
        ...     coverage_mode="reach",
        ...     coverage_outlet_count=23,
        ...     coverage_momentum="developing",
        ...     coverage_originating_outlet_name="Reuters",
        ...     coverage_notable_outlet_names=["Reuters", "BBC News"],
        ... )
        >>> report.coverage_mode
        'reach'
    """

    coverage_mode: CoverageMode = Field(
        ...,
        description="How coverage is framed: 'partisan' (L·C·R) | 'reach' (covered-by-N)",
    )
    coverage_left_count: int = Field(
        default=0, ge=0, description="partisan: outlets leaning left"
    )
    coverage_center_count: int = Field(
        default=0, ge=0, description="partisan: outlets leaning center"
    )
    coverage_right_count: int = Field(
        default=0, ge=0, description="partisan: outlets leaning right"
    )
    coverage_outlet_count: int = Field(
        default=0, ge=0, description="Total distinct outlets covering the story"
    )
    blindspot_lean: BiasLean | None = Field(
        default=None, description="partisan: the materially under-covered lean, or None"
    )
    coverage_momentum: CoverageMomentum | None = Field(
        default=None,
        description="reach: 'breaking' | 'developing' | 'settled', or None",
    )
    coverage_originating_outlet_name: str | None = Field(
        default=None,
        description="reach: who broke it (earliest GDELT seendate), or None",
    )
    coverage_notable_outlet_names: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="reach: up to 5 notable outlet names",
    )
    coverage_is_breaking: bool = Field(
        default=False,
        description="Transport-only: GDELT spread reads breaking → use Breaking template",
    )


class DetailTimelineEvent(BaseModel):
    """One "HOW IT DEVELOPED" event — a ``story_timeline`` row.

    Events are ordered by ``timeline_event_index``. ``timeline_when_label`` is the
    mono display string ("08:10", "Mon", "1993"); ``timeline_event_at`` is the real
    sortable timestamp (ISO) that drives the follows "what's new" query.

    Attributes:
        timeline_event_index: 0-based order within the story.
        timeline_when_label: Mono display label ("08:10", "Mon", "1993").
        timeline_what_text: The development sentence.
        timeline_event_at: ISO sortable timestamp, or None to default at persist.

    Example:
        >>> event = DetailTimelineEvent(
        ...     timeline_event_index=0,
        ...     timeline_when_label="08:10",
        ...     timeline_what_text="Strikes reported near the strait.",
        ... )
        >>> event.timeline_event_index
        0
    """

    timeline_event_index: int = Field(
        ..., ge=0, description="0-based order within the story"
    )
    timeline_when_label: str = Field(
        ..., min_length=1, description="Mono display label ('08:10', 'Mon', '1993')"
    )
    timeline_what_text: str = Field(
        ..., min_length=1, description="The development sentence"
    )
    timeline_event_at: str | None = Field(
        default=None,
        description="ISO sortable timestamp; None defaults to now() at persist",
    )


class KeyFigure(BaseModel):
    """The Detail hero key-figure card — the ``stories.story_key_figure_*`` fields.

    Both fields are nullable (a story may have no key figure). When present, they
    render as the big number + its caption ("~20%" / "of global oil transits Hormuz").

    Attributes:
        key_figure_value: The headline figure ("~20%", "$81.6B"), or None.
        key_figure_label: What the figure measures, or None.

    Example:
        >>> figure = KeyFigure(key_figure_value="~20%", key_figure_label="of global oil transits Hormuz")
        >>> figure.key_figure_value
        '~20%'
    """

    key_figure_value: str | None = Field(
        default=None, description="The headline figure ('~20%', '$81.6B'), or None"
    )
    key_figure_label: str | None = Field(
        default=None, description="What the figure measures, or None"
    )


class DetailKeyPoint(BaseModel):
    """One at-a-glance bullet — a ``detail_key_points`` row.

    Exactly 5 per story (Decision #6), shown above "Read the full article" and
    distinct from the long-form ``detail_chunks`` body. Ordered by
    ``key_point_index`` (0-based).

    Attributes:
        key_point_index: 0-based display order (0..4).
        key_point_text: One bullet sentence.

    Example:
        >>> point = DetailKeyPoint(key_point_index=0, key_point_text="Iran threatened to close Hormuz.")
        >>> point.key_point_index
        0
    """

    key_point_index: int = Field(..., ge=0, description="0-based display order (0..4)")
    key_point_text: str = Field(..., min_length=1, description="One bullet sentence")
