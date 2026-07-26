"""Tests for the shortlist ARTIFACT envelope — the run header that names the mode (#67).

Encodes WHY (Rule 9): ``.agents/shortlists/<date>-shortlist.json`` is the evidence a
later audit reads, often weeks after the run and with no log access. Before this
header, a fully-semantic shortlist and an embedding-outage shortlist were byte-shape
identical, so no audit of a bad tag could distinguish a quality miss from an outage
artifact. The header makes the FILE ALONE answer "which mode produced this?".
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.ingestion.models import (
    SEMANTIC_RELEVANCE_MODE_DEGRADED,
    SEMANTIC_RELEVANCE_MODE_DISABLED,
    SEMANTIC_RELEVANCE_MODE_SEMANTIC,
    SemanticRelevanceRunStamp,
)
from agents.pipeline.shortlist import ShortlistEntry, build_shortlist_artifact


def _entry(story_id: str = "s1") -> ShortlistEntry:
    return ShortlistEntry(
        shortlist_story_id=story_id,
        shortlist_headline="Arsenal win at the Emirates",
        shortlist_primary_outlet="BBC",
        shortlist_outlet_count=4,
        shortlist_category="sport",
        shortlist_matched_interest_slugs=["sport.soccer.arsenal"],
    )


def _result(mode: str, *, stories_checked: int = 0, interests_checked: int = 0):
    """A DailyPipelineResult stand-in — the artifact builder only reads these attrs."""
    return SimpleNamespace(
        feed_date="2026-07-25",
        shortlist=[_entry()],
        semantic_relevance=SemanticRelevanceRunStamp(
            semantic_relevance_mode=mode,
            semantic_relevance_stories_checked=stories_checked,
            semantic_relevance_interests_checked=interests_checked,
        ),
    )


class TestShortlistArtifactHeader:
    """Three distinguishable states, and the entries survive the envelope."""

    def test_degraded_run_artifact_declares_the_lexical_fallback(self) -> None:
        """WHY: the outage case is the one an audit MUST catch — a shortlist built on
        strict-lexical admission cannot be read as evidence about the semantic gate."""
        artifact = build_shortlist_artifact(
            _result(SEMANTIC_RELEVANCE_MODE_DEGRADED, stories_checked=19)
        )
        assert (
            artifact.shortlist_run.run_semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_DEGRADED
        )
        assert artifact.shortlist_run.run_fell_back_to_lexical is True

    def test_healthy_run_artifact_does_not_set_the_fallback_flag(self) -> None:
        """WHY: the flag has to be quiet on a clean run, or it means nothing when it
        fires. A clean run is stamped positively (mode == semantic), never by silence."""
        artifact = build_shortlist_artifact(
            _result(
                SEMANTIC_RELEVANCE_MODE_SEMANTIC,
                stories_checked=19,
                interests_checked=7,
            )
        )
        assert (
            artifact.shortlist_run.run_semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_SEMANTIC
        )
        assert artifact.shortlist_run.run_fell_back_to_lexical is False
        assert artifact.shortlist_run.run_semantic_interests_checked == 7

    def test_disabled_run_is_distinguishable_from_degraded(self) -> None:
        """WHY: an intentionally lexical-only run is cheap-by-choice, not broken. Both
        skip the semantic gate, so only the MODE separates them — the boolean cannot."""
        artifact = build_shortlist_artifact(_result(SEMANTIC_RELEVANCE_MODE_DISABLED))
        assert (
            artifact.shortlist_run.run_semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_DISABLED
        )
        assert artifact.shortlist_run.run_fell_back_to_lexical is False

    def test_envelope_keeps_every_entry_and_the_run_date(self) -> None:
        """WHY: the header is additive — the reviewable list itself must be unchanged,
        and the serialized shape is the on-disk contract audits parse."""
        artifact = build_shortlist_artifact(_result(SEMANTIC_RELEVANCE_MODE_SEMANTIC))
        dumped = artifact.model_dump()
        assert dumped["shortlist_run"]["run_feed_date"] == "2026-07-25"
        assert dumped["shortlist_run"]["run_shortlist_story_count"] == 1
        assert [e["shortlist_story_id"] for e in dumped["shortlist_entries"]] == ["s1"]
