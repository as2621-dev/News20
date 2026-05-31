"""Unit tests for the produce-once gate (Phase 1d SP2).

DoD (phase file SP2):
  1. The gate SKIPS a story that already has a current digest.
  2. The gate SKIPS a story with ZERO story_interests (serves no active interest).

These tests encode WHY the skip matters (Rule 9): the produce-once economics
(one canonical asset per story, Decision #3) — a current-digest story must not be
re-generated, and a no-interest story reaches no user so generating it is wasted
cost. Each test asserts on the *verdict + reason*, so it fails if the skip-logic
regresses, not merely that a function was called.

    >>> pytest tests/agents/pipeline/test_produce_gate.py -v
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agents.pipeline.produce_gate import (
    SKIP_REASON_BELOW_FLOOR,
    SKIP_REASON_HAS_CURRENT_DIGEST,
    SKIP_REASON_NO_INTEREST,
    compute_freshness_score,
    compute_importance_score,
    evaluate_story_for_production,
    select_stories_to_produce,
)


class TestProduceGateSkips:
    """The two DoD skip conditions + the happy path that proves they're real gates."""

    def test_passes_when_all_checks_clear(
        self, canonical_story, story_interest_tags, fixed_now
    ) -> None:
        """A fresh, multi-outlet, interest-serving story with no current digest is produced."""
        decision = evaluate_story_for_production(
            story=canonical_story,
            story_interest_tags=story_interest_tags,
            has_current_digest=False,
            now_utc=fixed_now,
        )
        assert decision.should_produce is True
        assert decision.skip_reason == ""
        # Two distinct interests (Arsenal leaf + Soccer parent) serve this story.
        assert decision.serves_interest_count == 2

    def test_skips_story_with_current_digest(
        self, canonical_story, story_interest_tags, fixed_now
    ) -> None:
        """DoD 1: a story that already has a current digest is NOT re-produced.

        Same story, same tags, same floor-clearing scores as the happy-path test
        — the ONLY difference is ``has_current_digest=True``. So a regression that
        ignored the existing-digest check would flip this to should_produce=True
        and fail here.
        """
        decision = evaluate_story_for_production(
            story=canonical_story,
            story_interest_tags=story_interest_tags,
            has_current_digest=True,
            now_utc=fixed_now,
        )
        assert decision.should_produce is False
        assert decision.skip_reason == SKIP_REASON_HAS_CURRENT_DIGEST

    def test_skips_story_with_zero_interests(self, canonical_story, fixed_now) -> None:
        """DoD 2: a story serving zero active interests is skipped (reaches no user).

        Passing an empty tag list means the story serves no followed interest;
        a regression that dropped the interest check would produce it anyway.
        """
        decision = evaluate_story_for_production(
            story=canonical_story,
            story_interest_tags=[],
            has_current_digest=False,
            now_utc=fixed_now,
        )
        assert decision.should_produce is False
        assert decision.skip_reason == SKIP_REASON_NO_INTEREST
        assert decision.serves_interest_count == 0

    def test_interest_check_ignores_other_stories_tags(
        self, canonical_story, fixed_now
    ) -> None:
        """Edge: tags belonging to a *different* story do not count as serving this one.

        Guards the produce-once economics — only THIS story's tags qualify it, so
        a clusterer that leaked another story's tags can't trick the gate.
        """
        from agents.ingestion.models import StoryInterestTag

        foreign_tags = [
            StoryInterestTag(
                story_interest_story_id="some-other-story",
                story_interest_interest_id="int-arsenal",
                story_interest_match_depth=0,
            )
        ]
        decision = evaluate_story_for_production(
            story=canonical_story,
            story_interest_tags=foreign_tags,
            has_current_digest=False,
            now_utc=fixed_now,
        )
        assert decision.should_produce is False
        assert decision.skip_reason == SKIP_REASON_NO_INTEREST


class TestImportanceFreshnessFloor:
    """The importance/freshness floor (ranking-spec.md §1) is a real gate, not decoration."""

    def test_skips_stale_single_outlet_story(
        self, canonical_story, story_interest_tags, fixed_now
    ) -> None:
        """A story old enough AND thin enough to fail BOTH terms is skipped.

        A 10-day-old single-outlet story: freshness ≈ 0.5**10 ≈ 0.001 (below the
        0.10 floor) and importance = 1/12 ≈ 0.083. Freshness fails → skip. This
        proves the floor blocks trivial stale stories from burning generation cost.
        """
        stale_story = canonical_story.model_copy(
            update={
                "story_outlet_count": 1,
                "canonical_published_utc": fixed_now - timedelta(days=10),
            }
        )
        decision = evaluate_story_for_production(
            story=stale_story,
            story_interest_tags=story_interest_tags,
            has_current_digest=False,
            now_utc=fixed_now,
        )
        assert decision.should_produce is False
        assert decision.skip_reason == SKIP_REASON_BELOW_FLOOR

    def test_fresh_single_outlet_breaking_story_passes(
        self, canonical_story, story_interest_tags, fixed_now
    ) -> None:
        """A just-broke single-outlet story passes on freshness (breaking-tier intent).

        Importance is low (1 outlet → 0.083) but freshness ≈ 1.0 clears the floor,
        so a genuine fresh break is still produced — the floor must not starve
        breaking news that only one outlet has so far.
        """
        breaking_story = canonical_story.model_copy(
            update={"story_outlet_count": 1, "canonical_published_utc": fixed_now}
        )
        decision = evaluate_story_for_production(
            story=breaking_story,
            story_interest_tags=story_interest_tags,
            has_current_digest=False,
            now_utc=fixed_now,
        )
        assert decision.should_produce is True

    def test_importance_score_scales_and_saturates(self) -> None:
        """Importance is linear up to saturation, then clamps at 1.0."""
        assert compute_importance_score(0) == 0.0
        assert compute_importance_score(6, saturation_outlet_count=12) == pytest.approx(
            0.5
        )
        assert compute_importance_score(20, saturation_outlet_count=12) == 1.0

    def test_freshness_halves_each_half_life(self, fixed_now) -> None:
        """Freshness halves every half-life; 'now' and future clamp at 1.0."""
        assert compute_freshness_score(fixed_now, fixed_now) == pytest.approx(1.0)
        one_day_old = fixed_now - timedelta(hours=24)
        assert compute_freshness_score(one_day_old, fixed_now) == pytest.approx(0.5)
        future = fixed_now + timedelta(hours=5)
        assert compute_freshness_score(future, fixed_now) == pytest.approx(1.0)


class TestSelectStoriesToProduce:
    """The batch wrapper partitions the pool correctly."""

    def test_batch_partitions_produced_and_skipped(
        self, canonical_story, story_interest_tags, fixed_now
    ) -> None:
        """Three stories: one passes, one has a current digest, one has no interest."""
        good = canonical_story
        already_done = canonical_story.model_copy(
            update={"canonical_story_id": "cand-already-done"}
        )
        no_interest = canonical_story.model_copy(
            update={"canonical_story_id": "cand-no-interest"}
        )
        # Tags only for the good story and the already-done story.
        from agents.ingestion.models import StoryInterestTag

        tags = list(story_interest_tags) + [
            StoryInterestTag(
                story_interest_story_id="cand-already-done",
                story_interest_interest_id="int-arsenal",
                story_interest_match_depth=0,
            )
        ]
        to_produce, decisions = select_stories_to_produce(
            stories=[good, already_done, no_interest],
            story_interest_tags=tags,
            has_current_digest_lookup={"cand-already-done": True},
            now_utc=fixed_now,
        )
        produced_ids = {s.canonical_story_id for s in to_produce}
        assert produced_ids == {good.canonical_story_id}
        assert len(decisions) == 3
        reason_by_id = {d.story_id: d.skip_reason for d in decisions}
        assert reason_by_id["cand-already-done"] == SKIP_REASON_HAS_CURRENT_DIGEST
        assert reason_by_id["cand-no-interest"] == SKIP_REASON_NO_INTEREST
