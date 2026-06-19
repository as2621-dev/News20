"""Lock test: the reel feed's category order == the Build-your-30 order.

Phase SP4 sub-phase 3. The "Build your 30" screen lets a user arrange their
category blocks; that manual sequence is persisted as ``allocation_sort_order`` on
their ``user_feed_allocation`` rows. The promise to the user is that **the order
they arrange blocks in is the order categories appear in their reel feed** — their
#1 block leads the feed.

This test pins that promise (Rule 9 — it encodes WHY, and is built to FAIL if the
ordering is regressed): :func:`_ordered_categories_from_allocation` must drive the
emit order by ``allocation_sort_order`` — NOT alphabetically and NOT by the order
the rows happen to arrive in. The fixtures are deliberately chosen so a regression
to either of those wrong orders is caught:

  - Chosen emit order (by ``allocation_sort_order``): ``sport → tech → business``.
  - Alphabetical order would be ``business → sport → tech`` (DIFFERENT).
  - Insertion order of the allocation list is ``tech → business → sport`` (DIFFERENT).

So if the code sorted by category name, or ignored ``allocation_sort_order`` and
kept insertion order, the assertion below would fail.

Externals are mocked at the boundary the same way the sibling
``test_feed_assembly.py`` does: the ranking + allocation math runs for REAL (pure
functions over in-memory stories); no supabase, no network. The 8-root taxonomy
roots are used (``sport``/``tech``/``business`` here).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import CategoryAllocation
from agents.pipeline.feed_assembly import (
    SLOT_KIND_INTEREST,
    _ordered_categories_from_allocation,
    assemble_user_feed,
)
from agents.pipeline.stages.ranking import UserProfileInterest

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
_TARGET_DATE = date(2026, 5, 31)

# ── Taxonomy: one depth-0 interest per topic root (slug → category map). ──
_INTEREST_SPORT = "int-sport"
_INTEREST_TECH = "int-tech"
_INTEREST_BUSINESS = "int-business"

_INTEREST_NODES = {
    _INTEREST_SPORT: InterestNode(
        interest_id=_INTEREST_SPORT, interest_slug="sport", interest_label="Sport"
    ),
    _INTEREST_TECH: InterestNode(
        interest_id=_INTEREST_TECH, interest_slug="tech", interest_label="Tech"
    ),
    _INTEREST_BUSINESS: InterestNode(
        interest_id=_INTEREST_BUSINESS,
        interest_slug="business",
        interest_label="Business",
    ),
}

# Category root → its depth-0 interest id (these slugs map 1:1 to the roots).
_CATEGORY_INTEREST = {
    "sport": _INTEREST_SPORT,
    "tech": _INTEREST_TECH,
    "business": _INTEREST_BUSINESS,
}

_PROFILE = [
    UserProfileInterest(profile_interest_id=iid, profile_weight=3.0)
    for iid in _CATEGORY_INTEREST.values()
]


def _story(story_id: str, outlet_count: int = 4) -> CanonicalStory:
    """A fresh equal-coverage canonical story (so Score is uniform within a category)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Story {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        covering_outlets=[f"outlet{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=0,
    )


def _category_of(story_id: str) -> str:
    """The category prefix encoded in a pool story id (``sport-2`` → ``sport``)."""
    return story_id.rsplit("-", 1)[0]


def test_ordered_categories_from_allocation_sorts_by_sort_order_not_name() -> None:
    """The allocation rows come back in ``allocation_sort_order`` order, not by name.

    WHY: this is the single function that turns the user's persisted Build-your-30
    sequence into the feed's category order. It is fed rows whose INSERTION order
    (``tech, business, sport``) differs from both their ``allocation_sort_order``
    (``sport, tech, business``) and the alphabetical order (``business, sport,
    tech``). The returned order must be the sort-order one — proving the function
    keys on ``allocation_sort_order`` and nothing else. Fails the instant the sort
    key drops ``allocation_sort_order`` (e.g. sorts by category name or by insertion).
    """
    rows = [
        CategoryAllocation(
            allocation_category="tech", allocation_slot_count=5, allocation_sort_order=1
        ),
        CategoryAllocation(
            allocation_category="business",
            allocation_slot_count=4,
            allocation_sort_order=2,
        ),
        CategoryAllocation(
            allocation_category="sport",
            allocation_slot_count=5,
            allocation_sort_order=0,
        ),
    ]

    ordered = _ordered_categories_from_allocation(rows)

    assert [row.allocation_category for row in ordered] == ["sport", "tech", "business"], (
        "rows must come back in allocation_sort_order, not by name or insertion"
    )


def test_assembled_feed_category_order_follows_allocation_sort_order() -> None:
    """The assembled feed emits categories in the user's Build-your-30 sequence.

    WHY: this is the user-facing promise — "the order I arrange my blocks is the
    order they appear in my reel feed." For a user whose allocation orders the roots
    ``sport (0) → tech (1) → business (2)`` by ``allocation_sort_order``, the
    assembled feed must lead with all the Sport reels, then all the Tech reels, then
    all the Business reels.

    The order is deliberately NON-alphabetical and NON-insertion so this is a real
    lock (Rule 9):
      - by ``allocation_sort_order``  → ``sport, tech, business``  (EXPECTED)
      - alphabetical                  → ``business, sport, tech``  (would fail)
      - insertion order of the rows   → ``tech, business, sport``  (would fail)

    Each category holds EXACTLY its budgeted count of equal-coverage stories, so
    there is no shortfall and no leftover capacity to redistribute — the only thing
    that can determine the cross-category order is ``allocation_sort_order``.
    """
    budget_by_category = {"sport": 5, "tech": 5, "business": 4}
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    for category, count in budget_by_category.items():
        interest_id = _CATEGORY_INTEREST[category]
        for index in range(count):
            story_id = f"{category}-{index}"
            stories.append(_story(story_id, outlet_count=4))
            tags.append(_tag(story_id, interest_id))

    # Rows are supplied in an insertion order (tech, business, sport) that differs
    # from BOTH their allocation_sort_order AND alphabetical order, so the assertion
    # below can only pass if the assembler honors allocation_sort_order.
    allocation = [
        CategoryAllocation(
            allocation_category="tech", allocation_slot_count=5, allocation_sort_order=1
        ),
        CategoryAllocation(
            allocation_category="business",
            allocation_slot_count=4,
            allocation_sort_order=2,
        ),
        CategoryAllocation(
            allocation_category="sport",
            allocation_slot_count=5,
            allocation_sort_order=0,
        ),
    ]

    slots = assemble_user_feed(
        profile_interests=_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        now_utc=_NOW,
    )

    assert len(slots) == 14, "5 sport + 5 tech + 4 business topic slots"
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots)
    assert [s.feed_position for s in slots] == list(range(1, 15)), "positions 1..14"

    # The cross-category emit order: every Sport slot precedes every Tech slot, and
    # every Tech slot precedes every Business slot. The contiguous run lengths must
    # match each category's budget exactly.
    category_run = [_category_of(s.feed_story_id) for s in slots]
    assert category_run == (
        ["sport"] * 5 + ["tech"] * 5 + ["business"] * 4
    ), (
        "feed category order must be the Build-your-30 sequence "
        "(sport, tech, business) — not alphabetical, not insertion order"
    )
