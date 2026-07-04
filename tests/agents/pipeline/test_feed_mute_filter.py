"""Assembly-level HARD MUTE filter tests (FSR interview slice #17, PRD stories #10/#11).

Each test encodes WHY the rule matters (Rule 9): a mute means GONE, not demoted — a story
matching a user's mute term must NEVER appear in that user's assembled feed at any position
(AC #2). The filter runs at ASSEMBLY ONLY (the story pool is shared across users, so the
shared input is never mutated — AC #6), and word-boundary matching keeps a short mute term
from collateral-erasing unrelated stories (AC #4 safety). Pure functions run for real.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.feed_assembly import assemble_niche_feed, assemble_user_feed
from agents.pipeline.niche_allocation import NicheAllocationRow
from agents.pipeline.stages.ranking import UserProfileInterest

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

_SPORT = "int-sport"
_CRICKET = "int-cricket"
_NODES: dict[str, InterestNode] = {
    _SPORT: InterestNode(
        interest_id=_SPORT, interest_slug="sport", interest_label="Sport", depth_level=0
    ),
    _CRICKET: InterestNode(
        interest_id=_CRICKET,
        parent_interest_id=_SPORT,
        interest_slug="sport.cricket",
        interest_label="Cricket",
        depth_level=1,
    ),
}


def _story(story_id: str, title: str, body: str = "") -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title,
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        canonical_body_text=body or None,
        covering_outlets=[f"outlet{i}.com" for i in range(4)],
        story_outlet_count=4,
    )


def _chain_tags(story_id: str) -> list[StoryInterestTag]:
    """Tag a story at cricket (0) and sport (1) — the direct + one-level-up chain."""
    return [
        StoryInterestTag(
            story_interest_story_id=story_id,
            story_interest_interest_id=_CRICKET,
            story_interest_match_depth=0,
        ),
        StoryInterestTag(
            story_interest_story_id=story_id,
            story_interest_interest_id=_SPORT,
            story_interest_match_depth=1,
        ),
    ]


def _cricket_pool() -> tuple[list[CanonicalStory], list[StoryInterestTag]]:
    """Four cricket stories; ``ipl-mute`` alone carries the mute term 'transfer rumours'."""
    stories = [
        _story("ipl-clean-0", "IPL final thriller in Mumbai"),
        _story("ipl-clean-1", "Cricket board announces new season"),
        _story("ipl-mute", "IPL transfer rumours swirl before auction"),
        _story("ipl-clean-2", "Test match century for the ages"),
    ]
    tags: list[StoryInterestTag] = []
    for story in stories:
        tags += _chain_tags(story.canonical_story_id)
    return stories, tags


# ── AC #2 / B3.5 — a muted story never enters the assembled feed (hard filter) ──


def test_muted_story_never_appears_in_niche_assembled_feed() -> None:
    """A story matching a SKIP mute term is absent from the niche feed at every position.

    WHY: "mute" is the founder's promise that the topic is GONE — if the muted story still
    surfaced anywhere in the 30, the promise is broken and the filter is a lie.
    """
    profile = [UserProfileInterest(profile_interest_id=_CRICKET, profile_weight=3.0)]
    alloc = [
        NicheAllocationRow(
            allocation_category="sport",
            allocation_interest_id=_CRICKET,
            allocation_section_label="Cricket",
            allocation_slot_count=4,
            allocation_sort_order=0,
        )
    ]
    stories, tags = _cricket_pool()

    slots = assemble_niche_feed(
        profile_interests=profile,
        niche_allocation=alloc,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_NODES,
        mute_terms=["transfer rumours"],
        feed_slot_budget=4,
        now_utc=_NOW,
    )

    placed_ids = {slot.feed_story_id for slot in slots}
    assert "ipl-mute" not in placed_ids  # muted → gone, not demoted
    # The clean stories still fill the section (the feed is not starved by the mute).
    assert "ipl-clean-0" in placed_ids


def test_mute_filter_is_assembly_only_and_never_mutates_the_shared_pool() -> None:
    """The shared candidate pool is untouched — filtering happens on a copy at assembly.

    WHY: the pool is SHARED across all users (spec §8). Muting for one user must never
    remove a story from the pool another user (who didn't mute it) will assemble from.
    """
    profile = [UserProfileInterest(profile_interest_id=_CRICKET, profile_weight=3.0)]
    alloc = [
        NicheAllocationRow(
            allocation_category="sport",
            allocation_interest_id=_CRICKET,
            allocation_section_label="Cricket",
            allocation_slot_count=4,
            allocation_sort_order=0,
        )
    ]
    stories, tags = _cricket_pool()
    pool_ids_before = [s.canonical_story_id for s in stories]

    assemble_niche_feed(
        profile_interests=profile,
        niche_allocation=alloc,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_NODES,
        mute_terms=["transfer rumours"],
        feed_slot_budget=4,
        now_utc=_NOW,
    )

    # The input list is unchanged: same length, the muted story still present in the pool.
    assert [s.canonical_story_id for s in stories] == pool_ids_before
    assert "ipl-mute" in pool_ids_before


def test_short_mute_term_matches_whole_words_only() -> None:
    """A short mute term ('india') never collateral-erases a story that merely contains it.

    WHY: a naive substring match would let 'india' drop 'Indiana' or 'ai' drop 'Spain' —
    silently erasing stories the user never asked to mute. Word boundaries keep mutes honest.
    """
    profile = [UserProfileInterest(profile_interest_id=_CRICKET, profile_weight=3.0)]
    alloc = [
        NicheAllocationRow(
            allocation_category="sport",
            allocation_interest_id=_CRICKET,
            allocation_section_label="Cricket",
            allocation_slot_count=2,
            allocation_sort_order=0,
        )
    ]
    stories = [
        _story("indiana", "Indiana league cricket grows"),  # contains 'india' as substring
        _story("india-real", "India win the cricket series"),  # whole word 'India'
    ]
    tags = _chain_tags("indiana") + _chain_tags("india-real")

    slots = assemble_niche_feed(
        profile_interests=profile,
        niche_allocation=alloc,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_NODES,
        mute_terms=["india"],
        feed_slot_budget=2,
        now_utc=_NOW,
    )

    placed_ids = {slot.feed_story_id for slot in slots}
    assert "india-real" not in placed_ids  # whole-word match → muted
    assert "indiana" in placed_ids  # substring-only → survives


def test_mute_filter_applies_on_the_coarse_roots_only_path_too() -> None:
    """A roots-only (coarse) profile still hard-filters mutes via assemble_user_feed.

    WHY: mutes must work for EVERY profile shape, including the roots-only baseline that
    delegates to the coarse allocator — a mute can't leak through the legacy path.
    """
    profile = [UserProfileInterest(profile_interest_id=_SPORT, profile_weight=3.0)]
    stories, tags = _cricket_pool()

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_NODES,
        mute_terms=["transfer rumours"],
        feed_slot_budget=4,
        now_utc=_NOW,
    )

    assert "ipl-mute" not in {slot.feed_story_id for slot in slots}
