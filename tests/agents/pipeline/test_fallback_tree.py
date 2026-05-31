"""Unit tests for the fallback tree candidate generator (Phase 1d SP3).

DoD (phase file SP3 / Rule 9): the fallback climbs leaf→parent ONLY when no leaf
story clears ``Score ≥ T``, and STOPS at a ``strict`` interest (no upward
broadening). These tests encode WHY the climb exists (a broad follower catches a
niche story for free) and WHY strict halts it (the owner's "just give me cricket"
directive), so they fail if the stop conditions regress.

reference/ranking-spec.md §2.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.stages.ranking import (
    DEFAULT_SCORE_THRESHOLD,
    UserProfileInterest,
    generate_fallback_candidates,
)

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

# A 3-level chain: leaf (arsenal) → parent (soccer) → grandparent (sport).
_NODES = {
    "int-arsenal": InterestNode(
        interest_id="int-arsenal",
        parent_interest_id="int-soccer",
        interest_slug="sport.soccer.arsenal",
        interest_label="Arsenal",
        depth_level=2,
    ),
    "int-soccer": InterestNode(
        interest_id="int-soccer",
        parent_interest_id="int-sport",
        interest_slug="sport.soccer",
        interest_label="Soccer",
        depth_level=1,
    ),
    "int-sport": InterestNode(
        interest_id="int-sport",
        parent_interest_id=None,
        interest_slug="sport",
        interest_label="Sport",
        depth_level=0,
    ),
}


def _story(story_id: str, outlet_count: int) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Headline {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="example.com",
        covering_outlets=[f"o{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str, depth: int) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=depth,
    )


class TestFallbackClimb:
    """The climb resolves at the LOWEST level that has a qualifying story."""

    def test_resolves_at_leaf_when_leaf_has_qualifier(self) -> None:
        """Happy path: a qualifying leaf story stops the climb at depth 0."""
        # Leaf story, strong importance+freshness → clears T at the leaf.
        leaf_story = _story("leaf", outlet_count=12)
        followed = UserProfileInterest(
            profile_interest_id="int-arsenal", profile_weight=1.0
        )

        candidates = generate_fallback_candidates(
            followed_interest=followed,
            affinity=1.0,
            stories=[leaf_story],
            tags_by_story={
                "leaf": {"int-arsenal": 0, "int-soccer": 1},
            },
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        assert candidates, "expected at least one leaf candidate"
        assert all(c.fallback_depth == 0 for c in candidates)
        assert all(c.matched_interest_id == "int-arsenal" for c in candidates)
        assert candidates[0].score >= DEFAULT_SCORE_THRESHOLD

    def test_climbs_to_parent_only_when_no_leaf_qualifies(self) -> None:
        """The DoD climb: no qualifying leaf → fall back to the parent node.

        The leaf node has NO tagged story at all; the parent (soccer) carries a
        story (depth-1 tag). With no leaf qualifier, the generator must climb and
        return the parent-level candidate at fallback_depth 1.
        """
        parent_story = _story("parent", outlet_count=12)
        # Story is tagged ONLY to the parent (soccer), not the arsenal leaf.
        tags_by_story = {"parent": {"int-soccer": 0}}
        followed = UserProfileInterest(
            profile_interest_id="int-arsenal", profile_weight=1.0
        )

        candidates = generate_fallback_candidates(
            followed_interest=followed,
            affinity=1.0,
            stories=[parent_story],
            tags_by_story=tags_by_story,
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        assert candidates, "expected the climb to find the parent-level story"
        assert candidates[0].matched_interest_id == "int-soccer"
        assert candidates[0].fallback_depth == 1

    def test_does_not_climb_when_leaf_qualifies_even_if_parent_richer(self) -> None:
        """The climb stops at the leaf the moment it qualifies (no broadening)."""
        leaf_story = _story("leaf", outlet_count=12)
        parent_story = _story("parent", outlet_count=12)
        tags_by_story = {
            "leaf": {"int-arsenal": 0, "int-soccer": 1},
            "parent": {"int-soccer": 0},
        }
        followed = UserProfileInterest(
            profile_interest_id="int-arsenal", profile_weight=1.0
        )
        candidates = generate_fallback_candidates(
            followed_interest=followed,
            affinity=1.0,
            stories=[leaf_story, parent_story],
            tags_by_story=tags_by_story,
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        # Resolved at the leaf — only the leaf-tagged story, never the parent-only one.
        assert {c.matched_interest_id for c in candidates} == {"int-arsenal"}
        assert all(c.fallback_depth == 0 for c in candidates)


class TestStrictHaltsClimb:
    """A strict interest halts the fallback at the leaf — no upward broadening."""

    def test_strict_does_not_climb_even_with_no_leaf_qualifier(self) -> None:
        """The DoD strict-stop: strict + no leaf qualifier → still leaf-only.

        The parent (soccer) has a rich qualifying story, but the followed leaf is
        ``strict`` and has only a weak/no leaf story. A non-strict interest would
        climb to the parent; strict must NOT — it returns only leaf-level
        candidates (here: none), never the parent story.
        """
        parent_story = _story("parent", outlet_count=12)
        tags_by_story = {"parent": {"int-soccer": 0}}
        followed_strict = UserProfileInterest(
            profile_interest_id="int-arsenal",
            profile_weight=1.0,
            profile_is_strict=True,
        )

        candidates = generate_fallback_candidates(
            followed_interest=followed_strict,
            affinity=1.0,
            stories=[parent_story],
            tags_by_story=tags_by_story,
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        # Strict: never broadened to the parent → no candidates from the parent.
        assert all(c.matched_interest_id == "int-arsenal" for c in candidates)
        assert all(c.fallback_depth == 0 for c in candidates)
        assert not any(c.matched_interest_id == "int-soccer" for c in candidates)

    def test_strict_still_returns_its_own_leaf_stories(self) -> None:
        """Strict scores its leaf normally — it just refuses to climb above it."""
        leaf_story = _story("leaf", outlet_count=12)
        tags_by_story = {"leaf": {"int-arsenal": 0}}
        followed_strict = UserProfileInterest(
            profile_interest_id="int-arsenal",
            profile_weight=1.0,
            profile_is_strict=True,
        )
        candidates = generate_fallback_candidates(
            followed_interest=followed_strict,
            affinity=1.0,
            stories=[leaf_story],
            tags_by_story=tags_by_story,
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        assert [c.story_id for c in candidates] == ["leaf"]
        assert candidates[0].fallback_depth == 0


class TestFallbackEdgeCases:
    """Empty/no-qualifier edges never crash and never silently drop the interest."""

    def test_no_tagged_stories_returns_empty(self) -> None:
        """Edge: an interest with no tagged story at any level returns []."""
        followed = UserProfileInterest(
            profile_interest_id="int-arsenal", profile_weight=1.0
        )
        candidates = generate_fallback_candidates(
            followed_interest=followed,
            affinity=1.0,
            stories=[_story("unrelated", outlet_count=5)],
            tags_by_story={"unrelated": {"int-other": 0}},
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        assert candidates == []

    def test_no_qualifier_anywhere_falls_back_to_leaf_scores(self) -> None:
        """Edge: nothing clears T → return leaf-level scores (allocator decides)."""
        # affinity 0 + 1 outlet + stale → well below T at every level.
        from datetime import timedelta

        weak_story = _story("weak", outlet_count=1)
        weak_story = weak_story.model_copy(
            update={"canonical_published_utc": _NOW - timedelta(days=10)}
        )
        followed = UserProfileInterest(
            profile_interest_id="int-arsenal", profile_weight=1.0
        )
        candidates = generate_fallback_candidates(
            followed_interest=followed,
            affinity=0.0,
            stories=[weak_story],
            tags_by_story={"weak": {"int-arsenal": 0}},
            interest_nodes=_NODES,
            now_utc=_NOW,
        )
        # No qualifier, but the leaf-level score is still returned for the allocator.
        assert [c.story_id for c in candidates] == ["weak"]
        assert candidates[0].score < DEFAULT_SCORE_THRESHOLD
