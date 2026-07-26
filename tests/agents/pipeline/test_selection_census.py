"""Tests for the selection-level coverage census (slice #10, day 3).

WHY these assertions matter (Rule 9): this census is the ONLY way the ≥60% go/no-go
clock can advance while the founder's produce-once spend contract keeps runs halted
at the shortlist rung. If it counted a niche as covered when nothing directly matched
it, a no-go run would read as a go and the PRD's coarser-niche pivot would never fire.
"""

from __future__ import annotations

from agents.pipeline.selection_census import (
    CensusFollowedInterest,
    CensusPersonaInput,
    build_selection_census,
    render_selection_census_text,
)


def _persona(
    key: str, slugs: list[str], selected: list[str] | None = None
) -> CensusPersonaInput:
    return CensusPersonaInput(
        persona_key=key,
        persona_email=f"persona.{key}@news20.seed",
        persona_user_id=f"uid-{key}",
        followed_interests=[
            CensusFollowedInterest(interest_slug=slug, interest_label=slug)
            for slug in slugs
        ],
        selected_story_ids=selected or [],
    )


def _entry(story_id: str, slugs: list[str]) -> dict:
    return {"shortlist_story_id": story_id, "shortlist_matched_interest_slugs": slugs}


def test_persona_with_every_niche_directly_matched_passes_the_target() -> None:
    """Happy path: 3 followed niches, each drawing a direct match → 100% → PASS.

    Encodes the go decision: a persona whose every section can fill from its OWN
    niche is exactly what the ≥60% bet claims is achievable.
    """
    report = build_selection_census(
        personas=[
            _persona(
                "chip", ["tech.chips", "biz.semis", "geo.export"], ["s1", "s2", "s3"]
            )
        ],
        shortlist_entries=[
            _entry("s1", ["tech.chips"]),
            _entry("s2", ["biz.semis"]),
            _entry("s3", ["geo.export"]),
        ],
        feed_date="2026-07-26",
        has_selection_block=True,
    )
    assert report.overall_hit_rate == 1.0
    assert report.overall_meets_target is True
    assert report.personas[0].hit_interest_count == 3
    assert all(not i.is_dry for i in report.personas[0].interests)


def test_niches_with_no_direct_match_are_dry_and_drag_the_rate_below_target() -> None:
    """Failure case: only 1 of 3 niches draws a direct match → 33.3% → MISS.

    A dry niche MUST be charged a miss even though the section will still render
    (the ladder climbs and fills it from an ancestor). Counting a climbed section as
    covered is the precise way this instrument would hide a structural no-go.
    """
    report = build_selection_census(
        personas=[
            _persona(
                "cricket",
                ["sport.cricket.ipl", "sport.cricket.wc", "sport.cricket.india"],
                ["s1"],
            )
        ],
        shortlist_entries=[
            _entry("s1", ["sport.cricket.ipl"]),
            _entry("s2", ["sport.football"]),
        ],
        feed_date="2026-07-26",
        has_selection_block=True,
    )
    persona = report.personas[0]
    assert persona.hit_interest_count == 1
    assert persona.meets_target is False
    assert round(persona.hit_rate, 3) == 0.333
    dry = {i.interest_slug for i in persona.interests if i.is_dry}
    assert dry == {"sport.cricket.wc", "sport.cricket.india"}


def test_pool_match_outside_the_selected_cut_still_counts_as_a_hit() -> None:
    """Edge: a niche matched in the POOL but not in the persona's selected 30.

    The ≥60% metric asks "did the niche draw supply today?", not "did it win a
    slot" — those are different questions and the report keeps them apart. The
    slot share must drop while the hit rate stays up, or the two numbers have been
    silently blended.
    """
    report = build_selection_census(
        personas=[_persona("founder", ["ai.foundation-models"], ["s-other"])],
        shortlist_entries=[
            _entry("s-direct", ["ai.foundation-models"]),
            _entry("s-other", []),
        ],
        feed_date="2026-07-26",
        has_selection_block=True,
    )
    persona = report.personas[0]
    assert persona.hit_rate == 1.0
    assert persona.interests[0].pool_direct_story_count == 1
    assert persona.interests[0].selected_direct_story_count == 0
    assert persona.selected_direct_leaf_rate == 0.0


def test_persona_with_no_followed_interests_contributes_no_cells() -> None:
    """Edge: an unprofiled persona must not silently move the overall rate.

    Its denominator is 0, so it can neither inflate nor deflate the go/no-go read —
    it is reported as an explicit 0/0 instead.
    """
    report = build_selection_census(
        personas=[_persona("empty", []), _persona("chip", ["tech.chips"], ["s1"])],
        shortlist_entries=[_entry("s1", ["tech.chips"])],
        feed_date="2026-07-26",
        has_selection_block=True,
    )
    assert report.overall_total_interest_count == 1
    assert report.overall_hit_rate == 1.0
    assert report.personas[0].total_interest_count == 0
    assert report.personas[0].hit_rate == 0.0


def test_empty_shortlist_reads_zero_not_a_pass() -> None:
    """Failure case: a run that selected nothing must read 0%, never a vacuous PASS."""
    report = build_selection_census(
        personas=[_persona("chip", ["tech.chips", "biz.semis"])],
        shortlist_entries=[],
        feed_date="2026-07-26",
        has_selection_block=False,
    )
    assert report.overall_hit_rate == 0.0
    assert report.overall_meets_target is False
    assert report.has_selection_block is False


def test_rendered_text_names_dry_niches_and_the_overall_verdict() -> None:
    """The ops block must show the MISS verdict and each dry slug (Rule 12)."""
    report = build_selection_census(
        personas=[
            _persona("cricket", ["sport.cricket.ipl", "sport.cricket.wc"], ["s1"])
        ],
        shortlist_entries=[_entry("s1", ["sport.cricket.ipl"])],
        feed_date="2026-07-26",
        has_selection_block=True,
    )
    text = render_selection_census_text(report)
    assert "DRY sport.cricket.wc" in text
    assert "OVERALL hit rate  50.0%" in text
    assert "MISS" in text
