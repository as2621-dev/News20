"""Unit tests for the per-(user, story) heuristic scorer (Phase 1d SP3).

DoD (phase file SP3 / Rule 9): assert AFFINITY-DOMINANT ordering — a small
niche-followed story OUTSCORES a generic broad one on the Score *math*, not a
stub. These tests encode WHY the formula is affinity-dominant (α=0.5 × the
leaf-vs-grandparent DepthMatch gap) so they fail if the weights or DepthMatch
ladder change. All inputs are pure data — no DB, no clock, no network.

Score = (Affinity × DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2
        (reference/ranking-spec.md §1)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.stages.ranking import (
    AFFINITY_WEIGHT,
    DEPTH_MATCH_BY_DEPTH,
    FRESHNESS_WEIGHT,
    IMPORTANCE_WEIGHT,
    UserProfileInterest,
    compute_story_score,
    normalize_affinities,
    score_candidates_for_user,
)

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _story(
    story_id: str,
    outlet_count: int,
    published: datetime = _NOW,
) -> CanonicalStory:
    """Build a minimal canonical story with a given importance/freshness."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Headline {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain="example.com",
        covering_outlets=[f"o{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


class TestNormalizeAffinities:
    """Affinity normalization — the 0–1 weight per interest (ranking-spec §1)."""

    def test_max_normalization_scales_top_interest_to_one(self) -> None:
        """The most-followed interest reaches affinity 1.0; others scale to it."""
        affinities = normalize_affinities(
            [
                UserProfileInterest(profile_interest_id="a", profile_weight=4.0),
                UserProfileInterest(profile_interest_id="b", profile_weight=1.0),
            ]
        )
        assert affinities["a"] == 1.0
        assert affinities["b"] == 0.25

    def test_empty_profile_returns_empty(self) -> None:
        """An empty profile yields no affinities (sparse-profile safe)."""
        assert normalize_affinities([]) == {}

    def test_all_zero_weights_do_not_divide_by_zero(self) -> None:
        """Edge: all-zero weights resolve to 0.0 affinity, not a ZeroDivisionError."""
        affinities = normalize_affinities(
            [UserProfileInterest(profile_interest_id="a", profile_weight=0.0)]
        )
        assert affinities == {"a": 0.0}


class TestComputeStoryScore:
    """The Score formula — exact weight application (ranking-spec §1)."""

    def test_score_equals_weighted_sum_of_terms(self) -> None:
        """Score is exactly (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2."""
        # 6 outlets / 12 saturation = 0.5 importance; reported now = 1.0 freshness.
        story = _story("s1", outlet_count=6, published=_NOW)
        score, depth_match, importance, freshness = compute_story_score(
            affinity=1.0, match_depth=0, story=story, now_utc=_NOW
        )
        assert depth_match == DEPTH_MATCH_BY_DEPTH[0] == 1.0
        assert importance == pytest.approx(0.5)
        assert freshness == pytest.approx(1.0)
        expected = (
            (1.0 * 1.0) * AFFINITY_WEIGHT
            + 0.5 * IMPORTANCE_WEIGHT
            + 1.0 * FRESHNESS_WEIGHT
        )
        assert score == pytest.approx(expected)

    def test_depth_match_ladder_penalizes_ancestor_matches(self) -> None:
        """Failure-mode guard: a parent/grandparent match scores below a leaf match."""
        story = _story("s1", outlet_count=6)
        leaf_score, _, _, _ = compute_story_score(1.0, 0, story, _NOW)
        parent_score, _, _, _ = compute_story_score(1.0, 1, story, _NOW)
        grandparent_score, _, _, _ = compute_story_score(1.0, 2, story, _NOW)
        assert leaf_score > parent_score > grandparent_score

    def test_stale_story_loses_freshness(self) -> None:
        """Edge: a 24h-old story halves its freshness term vs a now-dated one."""
        fresh = _story("fresh", outlet_count=6, published=_NOW)
        stale = _story("stale", outlet_count=6, published=_NOW - timedelta(hours=24))
        fresh_score, _, _, fresh_freshness = compute_story_score(1.0, 0, fresh, _NOW)
        _, _, _, stale_freshness = compute_story_score(1.0, 0, stale, _NOW)
        assert stale_freshness == pytest.approx(0.5, abs=0.01)
        assert fresh_freshness > stale_freshness


class TestAffinityDominantOrdering:
    """THE DoD test — a niche-followed small story beats a generic big one."""

    def test_niche_small_story_outscores_generic_big_story(self) -> None:
        """A small niche-followed story outscores a generic big one (Rule 9).

        Mirrors the ranking-spec rationale: a Mumbai-Indians fan should see a
        small Mumbai-Indians item above a generic blockbuster they only catch
        via a low-affinity grandparent. Tests the *math*, not a stub:
          - niche story: 1 outlet (importance ~0.083), leaf-matched (depth 0)
            on the user's TOP interest (affinity 1.0).
          - generic story: 12 outlets (importance 1.0), but only matched via the
            user's grandparent node (depth 2) on a LOW-affinity interest.
        """
        user = [
            UserProfileInterest(profile_interest_id="int-mumbai", profile_weight=5.0),
            UserProfileInterest(profile_interest_id="int-world", profile_weight=1.0),
        ]
        niche = _story("niche", outlet_count=1, published=_NOW)
        generic = _story("generic", outlet_count=12, published=_NOW)
        tags = [
            # niche: leaf-matched on the top interest.
            StoryInterestTag(
                story_interest_story_id="niche",
                story_interest_interest_id="int-mumbai",
                story_interest_match_depth=0,
            ),
            # generic: grandparent-matched on the low-affinity interest.
            StoryInterestTag(
                story_interest_story_id="generic",
                story_interest_interest_id="int-world",
                story_interest_match_depth=2,
            ),
        ]
        nodes = {
            "int-mumbai": InterestNode(
                interest_id="int-mumbai",
                parent_interest_id=None,
                interest_slug="sport.cricket.mumbai",
                interest_label="Mumbai Indians",
            ),
            "int-world": InterestNode(
                interest_id="int-world",
                parent_interest_id=None,
                interest_slug="world",
                interest_label="World",
            ),
        }

        candidates = score_candidates_for_user(
            profile_interests=user,
            stories=[niche, generic],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )

        niche_score = candidates["int-mumbai"][0].score
        generic_score = candidates["int-world"][0].score
        assert niche_score > generic_score, (
            f"niche {niche_score} should beat generic {generic_score} — "
            "affinity×depth dominance failed"
        )

    def test_bucket_keyed_by_followed_leaf_and_sorted_desc(self) -> None:
        """Candidates come back per followed leaf, sorted descending by score."""
        user = [
            UserProfileInterest(profile_interest_id="int-a", profile_weight=2.0),
        ]
        story_hi = _story("hi", outlet_count=12, published=_NOW)
        story_lo = _story("lo", outlet_count=1, published=_NOW - timedelta(hours=48))
        tags = [
            StoryInterestTag(
                story_interest_story_id="hi",
                story_interest_interest_id="int-a",
                story_interest_match_depth=0,
            ),
            StoryInterestTag(
                story_interest_story_id="lo",
                story_interest_interest_id="int-a",
                story_interest_match_depth=0,
            ),
        ]
        nodes = {
            "int-a": InterestNode(
                interest_id="int-a",
                parent_interest_id=None,
                interest_slug="a",
                interest_label="A",
            )
        }
        candidates = score_candidates_for_user(
            profile_interests=user,
            stories=[story_lo, story_hi],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        bucket = candidates["int-a"]
        assert [c.story_id for c in bucket] == ["hi", "lo"]
        assert bucket[0].score >= bucket[1].score
