"""Selection-level coverage census — the ≥60% hit-rate read on a HALTED run (slice #10).

``agents.pipeline.coverage_census`` measures the hit rate from persisted
``story_interests`` rows, which only exist for PRODUCED stories
(``docs/solutions/architecture-patterns/story-interests-holds-produced-not-ingested.md``).
Under the founder's selection-first / produce-once spend contract (2026-07-26) a
validation run halts at the shortlist rung, produces nothing, and therefore writes
no tags at all — the stored-tag census reads ``0/0 (not computable)`` and the
go/no-go clock cannot advance without paying for reels.

This module answers the SAME question one stage earlier, off the ``#67``/``#74``
shortlist artifact: *did each followed micro-interest draw at least one directly
matched story into today's selection pool?* The artifact's
``shortlist_matched_interest_slugs`` are the depth-0 (leaf) matches — the identical
signal a ``story_interests`` direct tag records — so the metric definition is
unchanged (hit cells / total cells over one pull day); only the observation point
moves from post-production to pre-production.

Two numbers, never blended (the day-2 report's Rule-12 separation):

* **hit rate** — fraction of a persona's followed interests drawing ≥1 direct
  match in the pool. This is the ≥60% go/no-go metric.
* **direct-leaf slot share** — fraction of a persona's SELECTED 30 carrying a
  direct match on an interest they follow. Always the lower number, because a
  section honestly climbs its ladder when leaf supply runs short.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.pipeline.coverage_census import HIT_RATE_TARGET
from agents.shared.logger import get_logger

logger = get_logger("pipeline.selection_census")


class CensusFollowedInterest(BaseModel):
    """One micro-interest a persona follows, as the selection census scores it.

    Attributes:
        interest_slug: Dotted taxonomy slug (e.g. ``sport.cricket.ipl``).
        interest_label: The persona's own display label for the niche.
    """

    interest_slug: str = Field(..., description="Dotted taxonomy slug")
    interest_label: str = Field(default="", description="Persona's display label")


class CensusPersonaInput(BaseModel):
    """A persona and the interests + selected stories to score them against.

    Attributes:
        persona_key: Stable short key (``founder``/``cricket``/``chip``).
        persona_email: The persona's auth email.
        persona_user_id: The persona's auth user id, or None when unresolved.
        followed_interests: Every micro-interest on the persona's profile.
        selected_story_ids: The persona's cut from
            ``ProductionSelectionPlan.selection_by_user``; empty when the run
            recorded no per-user selection (pre-#74 artifact).
    """

    persona_key: str = Field(..., description="Stable short key")
    persona_email: str = Field(..., description="Auth user email")
    persona_user_id: str | None = Field(default=None, description="Auth user id")
    followed_interests: list[CensusFollowedInterest] = Field(default_factory=list)
    selected_story_ids: list[str] = Field(default_factory=list)


class InterestSelectionCoverage(BaseModel):
    """Per-interest coverage in the selection pool.

    Attributes:
        interest_slug: The niche scored.
        interest_label: Its display label.
        pool_direct_story_count: Shortlist entries carrying this slug as a direct
            (depth-0) match.
        selected_direct_story_count: How many of those are in the persona's own
            selected cut.
        is_dry: True when the niche drew no direct match at all (a miss cell).
    """

    interest_slug: str = Field(..., description="Dotted taxonomy slug")
    interest_label: str = Field(default="", description="Display label")
    pool_direct_story_count: int = Field(default=0, ge=0)
    selected_direct_story_count: int = Field(default=0, ge=0)
    is_dry: bool = Field(default=True, description="No direct match anywhere")


class PersonaSelectionCoverage(BaseModel):
    """One persona's selection-level coverage and its two rates."""

    persona_key: str = Field(..., description="Stable short key")
    persona_email: str = Field(..., description="Auth user email")
    interests: list[InterestSelectionCoverage] = Field(default_factory=list)
    hit_interest_count: int = Field(default=0, ge=0)
    total_interest_count: int = Field(default=0, ge=0)
    hit_rate: float = Field(default=0.0, description="hit/total interests, 0..1")
    meets_target: bool = Field(default=False, description="hit_rate ≥ target")
    selected_story_count: int = Field(default=0, ge=0)
    selected_direct_leaf_count: int = Field(
        default=0, ge=0, description="Selected stories with ≥1 followed direct match"
    )
    selected_direct_leaf_rate: float = Field(
        default=0.0, description="Reported BESIDE the hit rate, never blended into it"
    )


class SelectionCensusReport(BaseModel):
    """The whole-run selection-level census (one pull day)."""

    feed_date: str = Field(..., description="ISO feed date of the shortlist run")
    hit_rate_target: float = Field(default=HIT_RATE_TARGET)
    shortlist_entry_count: int = Field(default=0, ge=0)
    has_selection_block: bool = Field(
        default=False, description="False for a pre-#74 artifact (slot share is N/A)"
    )
    personas: list[PersonaSelectionCoverage] = Field(default_factory=list)
    overall_hit_interest_count: int = Field(default=0, ge=0)
    overall_total_interest_count: int = Field(default=0, ge=0)
    overall_hit_rate: float = Field(default=0.0)
    overall_meets_target: bool = Field(default=False)


def build_selection_census(
    personas: list[CensusPersonaInput],
    shortlist_entries: list[dict],
    feed_date: str,
    has_selection_block: bool = False,
    hit_rate_target: float = HIT_RATE_TARGET,
) -> SelectionCensusReport:
    """Score each persona's niches against one shortlist run's direct matches.

    Pure over its inputs — no I/O, no clock, no Supabase (the CLI fetches).

    Args:
        personas: The personas to score, each with their followed interests and
            their selected cut.
        shortlist_entries: Raw ``shortlist_entries`` dicts from the artifact; only
            ``shortlist_story_id`` and ``shortlist_matched_interest_slugs`` are read.
        feed_date: The artifact's ``run_feed_date``, stamped onto the report.
        has_selection_block: Whether the artifact carried ``shortlist_selection``.
            When False the direct-leaf slot share is structurally unavailable and is
            reported as 0 alongside this flag rather than as a real measurement.
        hit_rate_target: The ≥ bar (default the shared 60% constant).

    Returns:
        The :class:`SelectionCensusReport`. A persona following no interests
        contributes no cells (rate 0.0, and it cannot silently inflate the overall
        rate because its denominator is 0 too).

    Example:
        >>> report = build_selection_census(
        ...     personas=[CensusPersonaInput(
        ...         persona_key="chip", persona_email="c@news20.seed",
        ...         followed_interests=[CensusFollowedInterest(interest_slug="tech.chips")],
        ...         selected_story_ids=["s1"])],
        ...     shortlist_entries=[{"shortlist_story_id": "s1",
        ...                         "shortlist_matched_interest_slugs": ["tech.chips"]}],
        ...     feed_date="2026-07-26", has_selection_block=True)
        >>> report.overall_hit_rate
        1.0
    """
    stories_by_slug: dict[str, set[str]] = {}
    for entry in shortlist_entries:
        story_id = str(entry.get("shortlist_story_id", ""))
        for slug in entry.get("shortlist_matched_interest_slugs") or []:
            stories_by_slug.setdefault(str(slug), set()).add(story_id)

    persona_coverages: list[PersonaSelectionCoverage] = []
    overall_hits = 0
    overall_total = 0

    for persona in personas:
        selected_ids = set(persona.selected_story_ids)
        interest_coverages: list[InterestSelectionCoverage] = []
        hits = 0
        directly_matched_selected: set[str] = set()

        for interest in persona.followed_interests:
            pool_stories = stories_by_slug.get(interest.interest_slug, set())
            selected_hits = pool_stories & selected_ids
            directly_matched_selected |= selected_hits
            if pool_stories:
                hits += 1
            interest_coverages.append(
                InterestSelectionCoverage(
                    interest_slug=interest.interest_slug,
                    interest_label=interest.interest_label,
                    pool_direct_story_count=len(pool_stories),
                    selected_direct_story_count=len(selected_hits),
                    is_dry=not pool_stories,
                )
            )

        total = len(persona.followed_interests)
        rate = hits / total if total else 0.0
        selected_count = len(persona.selected_story_ids)
        persona_coverages.append(
            PersonaSelectionCoverage(
                persona_key=persona.persona_key,
                persona_email=persona.persona_email,
                interests=interest_coverages,
                hit_interest_count=hits,
                total_interest_count=total,
                hit_rate=round(rate, 4),
                meets_target=rate >= hit_rate_target,
                selected_story_count=selected_count,
                selected_direct_leaf_count=len(directly_matched_selected),
                selected_direct_leaf_rate=round(
                    len(directly_matched_selected) / selected_count, 4
                )
                if selected_count
                else 0.0,
            )
        )
        overall_hits += hits
        overall_total += total

    overall_rate = overall_hits / overall_total if overall_total else 0.0
    logger.info(
        "selection_census_built",
        feed_date=feed_date,
        persona_count=len(personas),
        shortlist_entry_count=len(shortlist_entries),
        overall_hit_rate=round(overall_rate, 4),
        meets_target=overall_rate >= hit_rate_target,
    )
    return SelectionCensusReport(
        feed_date=feed_date,
        hit_rate_target=hit_rate_target,
        shortlist_entry_count=len(shortlist_entries),
        has_selection_block=has_selection_block,
        personas=persona_coverages,
        overall_hit_interest_count=overall_hits,
        overall_total_interest_count=overall_total,
        overall_hit_rate=round(overall_rate, 4),
        overall_meets_target=overall_rate >= hit_rate_target,
    )


def render_selection_census_text(report: SelectionCensusReport) -> str:
    """Render the census as the plain-text ops block pasted into the report."""
    target_pct = f"{report.hit_rate_target * 100:.0f}%"
    lines = [
        f"SELECTION-LEVEL COVERAGE CENSUS — feed_date {report.feed_date}",
        f"  shortlist entries: {report.shortlist_entry_count}"
        f"   selection block: {'yes' if report.has_selection_block else 'NO (pre-#74 artifact)'}",
        "",
    ]
    for persona in report.personas:
        lines.append(
            f"{persona.persona_key} ({persona.persona_email}) — "
            f"hit rate {persona.hit_rate * 100:5.1f}% "
            f"[{persona.hit_interest_count}/{persona.total_interest_count}] "
            f"{'PASS' if persona.meets_target else 'MISS'} vs {target_pct}"
        )
        for interest in persona.interests:
            mark = "DRY " if interest.is_dry else "hit "
            lines.append(
                f"    {mark}{interest.interest_slug:45s} "
                f"pool={interest.pool_direct_story_count:3d} "
                f"selected={interest.selected_direct_story_count:3d}"
            )
        lines.append(
            f"    direct-leaf slot share (reported separately): "
            f"{persona.selected_direct_leaf_count}/{persona.selected_story_count} "
            f"= {persona.selected_direct_leaf_rate * 100:.1f}%"
        )
        lines.append("")
    lines.append(
        f"OVERALL hit rate {report.overall_hit_rate * 100:5.1f}% "
        f"[{report.overall_hit_interest_count}/{report.overall_total_interest_count}] "
        f"{'PASS' if report.overall_meets_target else 'MISS'} vs {target_pct}"
    )
    return "\n".join(lines)
