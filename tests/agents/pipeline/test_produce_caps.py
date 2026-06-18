"""Unit tests for the per-category produce caps (agents/pipeline/produce_caps.py).

These encode WHY the cap exists (Rule 9): the live batch once produced 39 reels
all in markets/semiconductor because nothing bounded per-category production. The
cap must hold the cross-user max per category, drop categories nobody picked, and
trim an overall ceiling without re-skewing. (phase-SP1 removed the breaking tier,
so there is no breaking-headroom union any more — caps are returned alone.)

Pure functions — no DB, no LLM, no clock. Inputs are mocked Pydantic models.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import CategoryAllocation
from agents.pipeline.models import ProduceDecision
from agents.pipeline.produce_caps import (
    cap_stories_per_category,
    compute_category_produce_caps,
    enforce_overall_ceiling,
)

# Reason: two interest nodes whose roots map to distinct screen categories so
# assign_category buckets stories deterministically (business→markets, sport→sport).
_INTEREST_NODES: dict[str, InterestNode] = {
    "i-markets": InterestNode(
        interest_id="i-markets",
        interest_slug="business.semis",
        interest_label="Semiconductors",
    ),
    "i-sport": InterestNode(
        interest_id="i-sport",
        interest_slug="sport.cricket",
        interest_label="Cricket",
    ),
    "i-tech": InterestNode(
        interest_id="i-tech",
        interest_slug="tech.ai",
        interest_label="AI",
    ),
}


def _story(story_id: str) -> CanonicalStory:
    """Build a minimal CanonicalStory with the given id (other fields are stubs)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"title {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=datetime(2026, 6, 16, tzinfo=timezone.utc),
        canonical_primary_outlet_domain="example.com",
    )


def _tag(story_id: str, interest_id: str) -> StoryInterestTag:
    """Tag a story to one interest at leaf depth (drives assign_category)."""
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=0,
    )


def _decision(story_id: str, importance: float) -> ProduceDecision:
    """A produce decision carrying the importance score used to rank within a cap."""
    return ProduceDecision(
        story_id=story_id,
        should_produce=True,
        importance_score=importance,
        freshness_score=0.5,
    )


def _alloc(category: str, slot_count: int) -> CategoryAllocation:
    return CategoryAllocation(
        allocation_category=category,
        allocation_slot_count=slot_count,
        allocation_sort_order=0,
    )


# ── compute_category_produce_caps ──────────────────────────────────────────────

# The universal default a no-row user inherits (mirrors DEFAULT_FEED_ALLOCATION).
_DEFAULT = {"markets": 4, "sport": 3, "culture": 2}


def test_compute_caps_takes_cross_user_max_per_category():
    """Happy path: cap = the highest slot_count any single user requested."""
    allocation_by_user = {
        "u1": [_alloc("markets", 5), _alloc("sport", 3)],
        "u2": [_alloc("markets", 7), _alloc("sport", 6)],
    }
    caps = compute_category_produce_caps(allocation_by_user, ["u1", "u2"], _DEFAULT)
    assert caps == {"markets": 7, "sport": 6}


def test_compute_caps_no_active_users_returns_empty():
    """Edge: no active users → no caps."""
    caps = compute_category_produce_caps({}, [], _DEFAULT)
    assert caps == {}


def test_compute_caps_no_row_user_inherits_default():
    """A user who never built their 30 counts as the universal default allocation."""
    caps = compute_category_produce_caps({}, ["u1"], _DEFAULT)
    assert caps == {"markets": 4, "sport": 3, "culture": 2}


def test_compute_caps_explicit_rows_override_default_per_user():
    """A user WITH rows uses exactly those rows — a left-out category is 0, not default.

    u1 explicitly wants only sport (no markets) → u1 contributes 0 to markets; u2
    has no rows → inherits the default (markets 4). Cross-user max markets = 4.
    """
    allocation_by_user = {"u1": [_alloc("sport", 6)]}
    caps = compute_category_produce_caps(allocation_by_user, ["u1", "u2"], _DEFAULT)
    assert caps["sport"] == 6  # u1's explicit 6 beats the default 3
    assert caps["markets"] == 4  # only u2 (default) wants markets


def test_compute_caps_zero_slot_user_does_not_lower_the_max():
    """Edge: a user asking for 0 of a category must not pull the cap below others."""
    allocation_by_user = {
        "u1": [_alloc("sport", 0)],
        "u2": [_alloc("sport", 4)],
    }
    caps = compute_category_produce_caps(allocation_by_user, ["u1", "u2"], _DEFAULT)
    assert caps["sport"] == 4


# ── cap_stories_per_category ───────────────────────────────────────────────────


def test_cap_keeps_top_n_by_importance_no_skew():
    """Happy path: 39 markets stories, markets cap 7 → exactly 7, highest first.

    This is the regression guard for the 39-reel single-category skew.
    """
    stories = [_story(f"m{i}") for i in range(39)]
    tags = [_tag(f"m{i}", "i-markets") for i in range(39)]
    # Ascending importance so the top 7 are deterministically m32..m38.
    decisions = [_decision(f"m{i}", importance=i / 100.0) for i in range(39)]
    caps = {"markets": 7}

    kept = cap_stories_per_category(
        stories, decisions, tags, _INTEREST_NODES, caps, default_cap=8
    )

    assert len(kept) == 7
    kept_ids = {s.canonical_story_id for s in kept}
    assert kept_ids == {f"m{i}" for i in range(32, 39)}  # the 7 most important


def test_cap_drops_category_nobody_picked():
    """Edge: a category absent from caps is dropped entirely (explicit-rows-only)."""
    stories = [_story("s1"), _story("m1")]
    tags = [_tag("s1", "i-sport"), _tag("m1", "i-markets")]
    decisions = [_decision("s1", 0.9), _decision("m1", 0.9)]
    caps = {"markets": 5}  # nobody picked sport

    kept = cap_stories_per_category(
        stories, decisions, tags, _INTEREST_NODES, caps, default_cap=8
    )

    assert {s.canonical_story_id for s in kept} == {"m1"}


def test_cap_keeps_only_the_category_cap_no_headroom_union():
    """Edge: the cap keeps EXACTLY the top-N per category — no headroom over-keep.

    WHY (Rule 9): phase-SP1 removed the breaking-headroom union that used to keep
    extra top-importance stories beyond a category's cap. This pins the new
    contract: with markets cap 1, ONLY the single most-important markets story (m4)
    survives — nothing extra leaks through.
    """
    stories = [_story(f"m{i}") for i in range(5)]
    tags = [_tag(f"m{i}", "i-markets") for i in range(5)]
    decisions = [_decision(f"m{i}", importance=i / 10.0) for i in range(5)]
    caps = {"markets": 1}  # cap keeps only m4 (highest)

    kept = cap_stories_per_category(
        stories, decisions, tags, _INTEREST_NODES, caps, default_cap=8
    )

    assert {s.canonical_story_id for s in kept} == {"m4"}


def test_cap_empty_caps_falls_back_to_default_cap():
    """Edge: no user allocations at all → default_cap applied per category."""
    stories = [_story(f"m{i}") for i in range(5)]
    tags = [_tag(f"m{i}", "i-markets") for i in range(5)]
    decisions = [_decision(f"m{i}", importance=i / 10.0) for i in range(5)]

    kept = cap_stories_per_category(
        stories, decisions, tags, _INTEREST_NODES, {}, default_cap=2
    )

    assert len(kept) == 2
    assert {s.canonical_story_id for s in kept} == {"m3", "m4"}


def test_cap_empty_pool_returns_empty():
    """Edge: nothing to cap."""
    assert (
        cap_stories_per_category(
            [], [], [], _INTEREST_NODES, {"markets": 5}, default_cap=8
        )
        == []
    )


# ── enforce_overall_ceiling ────────────────────────────────────────────────────


def test_ceiling_trims_round_robin_balanced():
    """Happy path: 20 stories across 4 categories, ceiling 8 → 8 kept, balanced."""
    # 5 stories in each of markets / sport / tech_science / culture.
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    decisions: list[ProduceDecision] = []
    cat_to_interest = {
        "m": "i-markets",
        "s": "i-sport",
        "t": "i-tech",
    }
    for prefix, interest_id in cat_to_interest.items():
        for i in range(5):
            sid = f"{prefix}{i}"
            stories.append(_story(sid))
            tags.append(_tag(sid, interest_id))
            decisions.append(_decision(sid, importance=i / 10.0))

    kept = enforce_overall_ceiling(
        stories, decisions, tags, _INTEREST_NODES, max_total=6
    )

    assert len(kept) == 6
    # Round-robin across the 3 categories → 2 from each (balanced, no skew).
    by_prefix: dict[str, int] = {}
    for s in kept:
        by_prefix[s.canonical_story_id[0]] = (
            by_prefix.get(s.canonical_story_id[0], 0) + 1
        )
    assert by_prefix == {"m": 2, "s": 2, "t": 2}


def test_ceiling_noop_when_under_limit():
    """Edge: pool already within the ceiling → returned unchanged."""
    stories = [_story("m0"), _story("m1")]
    tags = [_tag("m0", "i-markets"), _tag("m1", "i-markets")]
    decisions = [_decision("m0", 0.5), _decision("m1", 0.5)]

    kept = enforce_overall_ceiling(
        stories, decisions, tags, _INTEREST_NODES, max_total=10
    )
    assert len(kept) == 2


def test_ceiling_zero_is_noop():
    """Edge: max_total<=0 disables the ceiling."""
    stories = [_story(f"m{i}") for i in range(3)]
    tags = [_tag(f"m{i}", "i-markets") for i in range(3)]
    decisions = [_decision(f"m{i}", 0.5) for i in range(3)]

    kept = enforce_overall_ceiling(
        stories, decisions, tags, _INTEREST_NODES, max_total=0
    )
    assert len(kept) == 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
