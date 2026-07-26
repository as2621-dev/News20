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
        production_story_ids={"s1", "s2", "s3", "s-direct", "s-other"},
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
        production_story_ids={"s1", "s2", "s3", "s-direct", "s-other"},
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
        production_story_ids={"s1", "s2", "s3", "s-direct", "s-other"},
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
        production_story_ids={"s1", "s2", "s3", "s-direct", "s-other"},
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
        production_story_ids={"s1", "s2", "s3", "s-direct", "s-other"},
    )
    text = render_selection_census_text(report)
    assert "DRY sport.cricket.wc" in text
    assert "OVERALL hit rate  50.0%" in text
    assert "MISS" in text


def test_a_standby_only_match_is_a_miss_not_a_hit() -> None:
    """The #74 shortlist carries STANDBYS, which are never produced.

    ``story_interests`` — the stored-tag instrument this census stands in for — is
    written per PRODUCED story, so a niche whose only match sits below the cap line
    must read DRY. Counting it would make this census disagree with the older one on
    exactly the cells nearest the 60% bar.
    """
    report = build_selection_census(
        personas=[
            _persona("founder", ["business.venture-capital", "ai.llms"], ["s-prod"])
        ],
        shortlist_entries=[
            _entry("s-standby", ["business.venture-capital"]),
            _entry("s-prod", ["ai.llms"]),
        ],
        feed_date="2026-07-26",
        has_selection_block=True,
        production_story_ids={"s-prod"},
    )
    persona = report.personas[0]
    vc = next(
        i for i in persona.interests if i.interest_slug == "business.venture-capital"
    )
    assert vc.pool_direct_story_count == 1
    assert vc.produced_direct_story_count == 0
    assert vc.is_dry is True
    assert persona.hit_rate == 0.5


def test_zero_followed_interests_is_not_computable_rather_than_a_zero_percent_no_go() -> (
    None
):
    """A run that measured NOTHING must never render as a 0% no-go verdict.

    Directly reachable: the 2026-07-26 seed-exclusion directive deleted every
    persona profile row, and a census run in that window has an empty denominator.
    Reporting it as "0.0% MISS" would manufacture a no-go out of missing data.
    """
    report = build_selection_census(
        personas=[_persona("founder", []), _persona("chip", [])],
        shortlist_entries=[_entry("s1", ["tech.chips"])],
        feed_date="2026-07-26",
        has_selection_block=True,
        production_story_ids={"s1"},
    )
    assert report.is_computable is False
    assert report.overall_meets_target is False
    text = render_selection_census_text(report)
    assert "NOT COMPUTABLE" in text
    assert "NOT a no-go" in text


def test_census_reads_the_real_shortlist_artifact_field_names() -> None:
    """Round-trip against the ACTUAL writer, so a field rename cannot pass silently.

    Every other test hand-builds entry dicts. If ``ShortlistEntry`` renamed a field,
    those would all still pass while the real reader saw None, every niche read DRY,
    and the census reported a plausible-looking structural no-go.
    """
    from agents.pipeline.production_selection import (
        ProductionSelectionPlan,
        UserSelection,
    )
    from agents.pipeline.shortlist import (
        ShortlistArtifact,
        ShortlistEntry,
        ShortlistRunHeader,
    )

    artifact = ShortlistArtifact(
        shortlist_run=ShortlistRunHeader(
            run_feed_date="2026-07-26",
            run_semantic_relevance_mode="semantic",
            run_fell_back_to_lexical=False,
        ),
        shortlist_entries=[
            ShortlistEntry(
                shortlist_story_id="story-1",
                shortlist_headline="TSMC lifts capex",
                shortlist_category="business",
                shortlist_matched_interest_slugs=["tech.semiconductors.tsmc"],
            )
        ],
        shortlist_selection=ProductionSelectionPlan(
            selection_production_story_ids=["story-1"],
            selection_by_user=[
                UserSelection(
                    selection_user_id="uid-chip", selection_story_ids=["story-1"]
                )
            ],
        ),
    )
    dumped = artifact.model_dump()
    selection = dumped["shortlist_selection"]

    report = build_selection_census(
        personas=[
            CensusPersonaInput(
                persona_key="chip",
                persona_email="persona.chip@news20.seed",
                persona_user_id="uid-chip",
                followed_interests=[
                    CensusFollowedInterest(interest_slug="tech.semiconductors.tsmc")
                ],
                selected_story_ids=selection["selection_by_user"][0][
                    "selection_story_ids"
                ],
            )
        ],
        shortlist_entries=dumped["shortlist_entries"],
        feed_date=dumped["shortlist_run"]["run_feed_date"],
        has_selection_block=True,
        production_story_ids=set(selection["selection_production_story_ids"]),
    )
    assert report.overall_hit_rate == 1.0
    assert report.personas[0].interests[0].produced_direct_story_count == 1
    assert report.personas[0].selected_direct_leaf_rate == 1.0
