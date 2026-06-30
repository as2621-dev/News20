"""Unit tests for the phase-5a "Build your 30" category-budget allocator + writer.

The allocator was REWRITTEN in phase-5a SP3 from the old affinity-proportional
model (proportional split / floor-1 / ~40% cap / exploration) to **user-set
per-category slot budgets + manual sequence**. These tests pin the new invariants
(Rule 9 — each encodes WHY the behavior matters):

  - **Exact per-category budgets** honored (subject to story availability), in the
    user's manual **sequence** order. (phase-SP1 removed the breaking tier — every
    slot is now ``interest`` or ``source``.)
  - **Source soft-roll**: ``youtube``/``x`` are budgeted-but-empty (phase-5d); their
    slots roll into the topic categories so ``len(feed) == 30``.
  - **Entity bonus** lifts a Nvidia-followed story above its non-followed twin
    WITHIN its category (Layer-2 scoring feeding Layer-1 allocation).
  - **No-allocation default**: a pre-screen user gets the balanced fallback
    (an even split of the full 30 across non-empty topic categories).
  - **Sparse category yields forward**: a category with no eligible stories gives
    its slots to the next sequence category (no gap), feed still fills toward 30.
  - **§3.8 don't-repeat** (prior-feed exclusion) + **within-feed dedup** preserved.
  - **Produce-once** writer idempotency preserved.

Externals are mocked at the boundary: a fake supabase client captures inserts and
answers the existing-feed pre-check (no network, no writes). The ranking +
allocation math runs for REAL (pure functions) so the assertions test the
prioritization logic, not just the insert.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import CategoryAllocation
from agents.pipeline.feed_assembly import (
    SLOT_KIND_INTEREST,
    SLOT_KIND_SOURCE,
    AllocatedSlot,
    assemble_user_feed,
    write_daily_feed,
)
from agents.pipeline.stages.ranking import FollowedEntity, UserProfileInterest

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
_TARGET_DATE = date(2026, 5, 31)

# ── Taxonomy: one depth-0 interest per topic category (slug → category map). ──
# world → geopolitics ; tech → tech ; business → business ;
# sport → sport ; entertainment → arts (per agents/pipeline/categories.py, SP3).
_INTEREST_WORLD = "int-world"
_INTEREST_TECH = "int-tech"
_INTEREST_BUSINESS = "int-business"
_INTEREST_SPORT = "int-sport"
_INTEREST_ENT = "int-ent"

_INTEREST_NODES = {
    _INTEREST_WORLD: InterestNode(
        interest_id=_INTEREST_WORLD, interest_slug="world", interest_label="World"
    ),
    _INTEREST_TECH: InterestNode(
        interest_id=_INTEREST_TECH, interest_slug="tech", interest_label="Tech"
    ),
    _INTEREST_BUSINESS: InterestNode(
        interest_id=_INTEREST_BUSINESS,
        interest_slug="business",
        interest_label="Business",
    ),
    _INTEREST_SPORT: InterestNode(
        interest_id=_INTEREST_SPORT, interest_slug="sport", interest_label="Sport"
    ),
    _INTEREST_ENT: InterestNode(
        interest_id=_INTEREST_ENT,
        interest_slug="entertainment",
        interest_label="Entertainment",
    ),
}

_CATEGORY_INTEREST = {
    "geopolitics": _INTEREST_WORLD,
    "tech": _INTEREST_TECH,
    "business": _INTEREST_BUSINESS,
    "sport": _INTEREST_SPORT,
    "arts": _INTEREST_ENT,
}

_ALL_TOPIC_PROFILE = [
    UserProfileInterest(profile_interest_id=iid, profile_weight=3.0)
    for iid in _CATEGORY_INTEREST.values()
]


def _story(
    story_id: str,
    outlet_count: int = 4,
    title: str | None = None,
    published: datetime = _NOW,
) -> CanonicalStory:
    """A fresh canonical story with a given coverage (Importance source)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title or f"Story {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain="bbc.com",
        covering_outlets=[f"outlet{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str, match_depth: int = 0) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=match_depth,
    )


def _pool_per_category(
    per_category_count: int,
) -> tuple[list[CanonicalStory], list[StoryInterestTag]]:
    """Build a story pool with ``per_category_count`` stories in each topic category.

    Story ids are prefixed with the category key (``markets-3``) so a test can count
    how many of each category landed in the feed.
    """
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    for category, interest_id in _CATEGORY_INTEREST.items():
        for index in range(per_category_count):
            story_id = f"{category}-{index}"
            stories.append(_story(story_id, outlet_count=4))
            tags.append(_tag(story_id, interest_id))
    return stories, tags


def _category_of(story_id: str) -> str:
    """The category prefix encoded in a pool story id (``markets-3`` → ``markets``)."""
    return story_id.rsplit("-", 1)[0]


def _dod_allocation() -> list[CategoryAllocation]:
    """The DoD allocation: 5/5/4/3/4 topics, 6+3 source (sums to 30).

    SP3 unified the taxonomy onto the picker roots (geopolitics/tech/business/sport/
    arts); the per-category counts are kept identical so the assertions below stay
    meaningful — the 5-category topic budgets (21) + the 9 source slots total 30.
    """
    spec = [
        ("geopolitics", 5, 0),
        ("tech", 5, 1),
        ("business", 4, 2),
        ("sport", 3, 3),
        ("arts", 4, 4),
        ("youtube", 6, 5),
        ("x", 3, 6),
    ]
    return [
        CategoryAllocation(
            allocation_category=category,
            allocation_slot_count=count,
            allocation_sort_order=order,
        )
        for category, count, order in spec
    ]


# ── Fake supabase client (writer idempotency) ───────────────────────────────


class FakeDailyFeedsQuery:
    """Captures ``daily_feeds`` inserts and answers the existing-feed pre-check."""

    def __init__(self, store: "FakeSupabaseClient") -> None:
        self.store = store
        self._select_filters: dict[str, str] = {}

    def select(self, _columns: str) -> "FakeDailyFeedsQuery":
        self._select_filters = {}
        return self

    def eq(self, column: str, value: str) -> "FakeDailyFeedsQuery":
        self._select_filters[column] = value
        return self

    def insert(self, rows: list[dict]) -> "FakeDailyFeedsQuery":
        self._pending_insert = rows
        return self

    def execute(self):
        if getattr(self, "_pending_insert", None) is not None:
            rows = self._pending_insert
            self._pending_insert = None
            self.store.inserted.extend(rows)
            for row in rows:
                key = (row["feed_user_id"], row["feed_date"])
                self.store.existing_rows.setdefault(key, []).append(row)
            return _Response([dict(row) for row in rows])
        key = (
            self._select_filters.get("feed_user_id"),
            self._select_filters.get("feed_date"),
        )
        return _Response(list(self.store.existing_rows.get(key, [])))


class _Response:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class FakeSupabaseClient:
    """Minimal supabase stub: only the ``daily_feeds`` table is exercised."""

    def __init__(self, seeded_existing: dict | None = None) -> None:
        self.inserted: list[dict] = []
        self.existing_rows: dict = dict(seeded_existing or {})

    def table(self, name: str) -> FakeDailyFeedsQuery:
        assert name == "daily_feeds", f"unexpected table {name}"
        return FakeDailyFeedsQuery(self)


# ── Allocator: per-category budgets + sequence + source soft-roll ────────────


def test_allocation_honors_per_category_budgets_in_sequence() -> None:
    """Exact per-category budgets are filled, in the user's sequence, when each
    category has EXACTLY its budgeted stories and there is NO source budget.

    WHY: the whole point of "Build your 30" is that the feed honors the counts the
    user dialed. With each topic holding exactly its budget of stories and zero
    source budget, the feed must be EXACTLY 5/5/4/3/4 topic slots = 21, ordered by
    ``allocation_sort_order``. This fails the moment a per-category budget is
    mis-counted or the sequence order is dropped. (phase-SP1: no breaking tier — all
    slots are ``interest``.)
    """
    # Each topic holds EXACTLY its budgeted count of equal-coverage stories, so the
    # topic-slot count per category is unambiguous (no surplus to roll, no shortfall).
    budget_by_category = {
        "geopolitics": 5,
        "tech": 5,
        "business": 4,
        "sport": 3,
        "arts": 4,
    }
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    for category, count in budget_by_category.items():
        interest_id = _CATEGORY_INTEREST[category]
        for index in range(count):
            story_id = f"{category}-{index}"
            stories.append(_story(story_id, outlet_count=4))
            tags.append(_tag(story_id, interest_id))

    allocation = [
        CategoryAllocation(
            allocation_category="geopolitics",
            allocation_slot_count=5,
            allocation_sort_order=0,
        ),
        CategoryAllocation(
            allocation_category="tech",
            allocation_slot_count=5,
            allocation_sort_order=1,
        ),
        CategoryAllocation(
            allocation_category="business",
            allocation_slot_count=4,
            allocation_sort_order=2,
        ),
        CategoryAllocation(
            allocation_category="sport",
            allocation_slot_count=3,
            allocation_sort_order=3,
        ),
        CategoryAllocation(
            allocation_category="arts",
            allocation_slot_count=4,
            allocation_sort_order=4,
        ),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        now_utc=_NOW,
    )

    assert len(slots) == 21, "5+5+4+3+4 topic slots"
    # Every slot is an interest slot (no breaking tier, no source budget here).
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots)

    # Each category's TOPIC slots match its budget EXACTLY.
    topic_by_category = Counter(_category_of(s.feed_story_id) for s in slots)
    assert topic_by_category["geopolitics"] == 5
    assert topic_by_category["tech"] == 5
    assert topic_by_category["business"] == 4
    assert topic_by_category["sport"] == 3
    assert topic_by_category["arts"] == 4

    # Topic slots appear in the user's sequence: all geopolitics before any
    # tech, etc.
    topic_order = [_category_of(s.feed_story_id) for s in slots]
    sequence = ["geopolitics", "tech", "business", "sport", "arts"]
    last_rank = -1
    for category in topic_order:
        rank = sequence.index(category)
        assert rank >= last_rank, (
            f"out-of-sequence category {category} in {topic_order}"
        )
        last_rank = rank


def test_source_budget_rolls_into_topics_so_feed_totals_30() -> None:
    """The 9 source slots (youtube 6 + x 3) roll into topics so ``len(feed) == 30``.

    WHY: ``youtube``/``x`` are source-axis categories with zero candidates today
    (phase-5d). The user budgeted 9 slots there; the feed would be short 9 (only 21)
    unless those slots soft-roll into the topic categories. This is the load-bearing
    "feed still totals 30" invariant. We give a surplus pool so the roll-over has
    somewhere to land, then assert the count, no-dupes, and contiguity.
    """
    # Surplus pool: 10 per category so the 9 rolled slots can be absorbed.
    stories, tags = _pool_per_category(10)

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        now_utc=_NOW,
    )

    assert len(slots) == 30, "9 source slots rolled into topics → exactly 30"
    story_ids = [s.feed_story_id for s in slots]
    assert len(story_ids) == len(set(story_ids)), "no duplicate story in one feed"
    assert [s.feed_position for s in slots] == list(range(1, 31)), "positions 1..30"
    # Every placed slot is an interest slot (source budgets rolled into topics, no
    # breaking tier exists post phase-SP1).
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots)
    # No source-category story ever appears (youtube/x have no stories to place).
    assert not any(_category_of(s.feed_story_id) in ("youtube", "x") for s in slots), (
        "source categories contribute zero items"
    )


def _source_story(story_id: str, outlet_domain: str) -> CanonicalStory:
    """A produced source-origin story (youtube.com / x.com marks its source slot)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Source {story_id}",
        canonical_url=f"https://{outlet_domain}/{story_id}",
        canonical_normalized_url=f"https://{outlet_domain}/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain=outlet_domain,
        covering_outlets=[outlet_domain],
        story_outlet_count=1,
        canonical_social_image_url=f"https://{outlet_domain}/{story_id}/thumb.jpg",
    )


def test_source_stories_fill_source_slots_instead_of_rolling_into_topics() -> None:
    """Produced YouTube/X stories fill the youtube/x slots as ``source`` kind.

    WHY: phase-5d wires followed sources into the feed. With source stories supplied,
    the youtube(6)+x(3) budgets must be filled by those reels — NOT soft-rolled into
    topics — so the user actually sees the creators they follow. The feed still
    totals 30, the source slots carry the ``source`` slot kind with NO matched
    interest, and they sit at the youtube/x sequence positions. This is the
    load-bearing "source slots are real now" invariant.
    """
    stories, tags = _pool_per_category(10)
    youtube_stories = [_source_story(f"yt-{i}", "youtube.com") for i in range(6)]
    x_stories = [_source_story(f"x-{i}", "x.com") for i in range(3)]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        source_stories=youtube_stories + x_stories,
        now_utc=_NOW,
    )

    assert len(slots) == 30, "source slots filled + topics → still exactly 30"
    source_slots = [s for s in slots if s.feed_slot_kind == SLOT_KIND_SOURCE]
    source_ids = {s.feed_story_id for s in source_slots}
    assert source_ids == {f"yt-{i}" for i in range(6)} | {f"x-{i}" for i in range(3)}, (
        "all 6 youtube + 3 x produced reels fill their budgeted source slots"
    )
    assert all(s.feed_matched_interest_id is None for s in source_slots), (
        "a source slot carries no matched interest (placed by source axis, not a slug)"
    )
    story_ids = [s.feed_story_id for s in slots]
    assert len(story_ids) == len(set(story_ids)), "no duplicate story in one feed"
    assert [s.feed_position for s in slots] == list(range(1, 31)), "positions 1..30"


def test_unfilled_source_budget_still_rolls_into_topics() -> None:
    """A source category with NO produced story soft-rolls its budget into topics.

    WHY: ingestion is best-effort (a YouTube channel may be throttled, an X account
    may have nothing fresh). When only some source slots fill, the REMAINDER must
    still roll into topics so the feed never comes up short of 30 — the graceful
    degradation that keeps the source wiring safe to ship.
    """
    stories, tags = _pool_per_category(10)
    # Only 2 youtube reels produced (budget 6); zero x reels (budget 3).
    youtube_stories = [_source_story(f"yt-{i}", "youtube.com") for i in range(2)]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        source_stories=youtube_stories,
        now_utc=_NOW,
    )

    assert len(slots) == 30, "2 filled source slots + 7 rolled into topics → 30"
    assert sum(1 for s in slots if s.feed_slot_kind == SLOT_KIND_SOURCE) == 2, (
        "only the 2 produced youtube reels occupy source slots"
    )
    story_ids = [s.feed_story_id for s in slots]
    assert len(story_ids) == len(set(story_ids)), "no duplicate story in one feed"


def test_no_slot_is_ever_breaking_kind() -> None:
    """No assembled slot is ever a ``breaking`` slot kind (phase-SP1 invariant).

    WHY (Rule 9): the breaking tier was removed. This guards the regression — even
    when the pool contains overwhelmingly high-Importance stories that the OLD
    allocator would have promoted into a breaking tier, every emitted slot must now
    be ``interest`` or ``source``. It fails the moment any breaking-tier code is
    reintroduced.
    """
    stories, tags = _pool_per_category(6)
    # Two very-high-coverage stories (would have been Importance "spikes").
    stories += [
        _story("geopolitics-big", outlet_count=40),
        _story("tech-big", outlet_count=38),
    ]
    tags += [
        _tag("geopolitics-big", _INTEREST_WORLD),
        _tag("tech-big", _INTEREST_TECH),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        now_utc=_NOW,
    )

    assert slots, "the feed is non-empty"
    assert all(
        s.feed_slot_kind in {SLOT_KIND_INTEREST, SLOT_KIND_SOURCE} for s in slots
    ), "only {interest, source} slot kinds — no breaking tier"
    # No duplicate placement.
    all_ids = [s.feed_story_id for s in slots]
    assert len(all_ids) == len(set(all_ids))


def test_followed_entity_lifts_story_within_its_category() -> None:
    """A Nvidia-followed story outranks an equivalent non-followed story WITHIN its
    category (business).

    WHY: the entity follow is a Layer-2 score bonus that must change the ORDER
    within a category's slots — a Nvidia follower should see the Nvidia story above
    its twin. This fails if the allocator does not feed ``followed_entities`` into
    the entity-aware scorer, or sorts a category by something other than Score.
    """
    # Two equivalent business stories (same coverage), one mentioning Nvidia.
    nvidia_story = _story(
        "business-nvidia",
        outlet_count=5,
        title="Nvidia Q3 earnings beat expectations",
    )
    twin_story = _story(
        "business-twin",
        outlet_count=5,
        title="Chipmaker quarterly earnings beat expectations",
    )
    stories = [nvidia_story, twin_story]
    tags = [
        _tag("business-nvidia", _INTEREST_BUSINESS),
        _tag("business-twin", _INTEREST_BUSINESS),
    ]
    profile = [
        UserProfileInterest(profile_interest_id=_INTEREST_BUSINESS, profile_weight=3.0)
    ]
    followed = [
        FollowedEntity(
            entity_id="tech/semis/companies/nvidia",
            entity_label="Nvidia",
            entity_ticker="NVDA",
            entity_kind="company",
            follow_weight=3.0,
        )
    ]
    allocation = [
        CategoryAllocation(
            allocation_category="business",
            allocation_slot_count=2,
            allocation_sort_order=0,
        ),
    ]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        followed_entities=followed,
        category_allocation=allocation,
        now_utc=_NOW,
    )

    order = [s.feed_story_id for s in slots]
    assert order.index("business-nvidia") < order.index("business-twin"), (
        "the Nvidia-followed story must rank above its non-followed twin"
    )
    nvidia_slot = next(s for s in slots if s.feed_story_id == "business-nvidia")
    twin_slot = next(s for s in slots if s.feed_story_id == "business-twin")
    assert nvidia_slot.feed_score > twin_slot.feed_score, (
        "the entity bonus must lift the Nvidia story's Score above the twin's"
    )


def test_no_allocation_user_gets_balanced_default() -> None:
    """A user with NO ``user_feed_allocation`` rows gets the balanced default
    (an even split of the full 30 across non-empty topic categories).

    WHY: pre-screen users must still receive a feed. The default replaces the old
    affinity-proportional behavior. phase-SP1 removed the breaking tier, so the
    default now evenly splits the FULL 30 across non-empty topics — with 5 non-empty
    topic categories and a deep pool that is 6/6/6/6/6 → 30 slots, all ``interest``
    kind. This fails if the empty ``category_allocation`` path does not synthesize a
    default, or if a breaking slot reappears.
    """
    stories, tags = _pool_per_category(12)

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=[],  # pre-screen user — no rows
        now_utc=_NOW,
    )

    assert len(slots) == 30, "balanced default fills the full 30-slot feed"
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots), (
        "the default feed is all interest slots — no breaking tier post phase-SP1"
    )
    # All 5 topic categories are represented (even split, none starved).
    represented = {_category_of(s.feed_story_id) for s in slots}
    assert represented == set(_CATEGORY_INTEREST.keys()), (
        "the even split must touch every non-empty topic category"
    )
    # Even split of 30 across 5 topics → 6 each.
    per_category = Counter(_category_of(s.feed_story_id) for s in slots)
    assert all(count == 6 for count in per_category.values()), (
        "30 split evenly across 5 non-empty topics is 6 per category"
    )
    story_ids = [s.feed_story_id for s in slots]
    assert len(story_ids) == len(set(story_ids)), "no duplicate story"


def test_empty_category_yields_slots_to_next_in_sequence() -> None:
    """A budgeted category with NO eligible stories yields its slots to the next
    sequence category — the feed fills forward, not a gap.

    WHY: if ``sport`` is budgeted 3 but the pool has no sport story, those 3 slots
    must NOT become dead air — they roll to the next category that has stories. We
    omit sport entirely from the pool and assert the feed still reaches its target
    and contains zero sport stories.
    """
    # Pool has all topics EXCEPT sport.
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    for category, interest_id in _CATEGORY_INTEREST.items():
        if category == "sport":
            continue  # no sport stories at all
        for index in range(10):
            story_id = f"{category}-{index}"
            stories.append(_story(story_id))
            tags.append(_tag(story_id, interest_id))

    allocation = [
        CategoryAllocation(
            allocation_category="geopolitics",
            allocation_slot_count=5,
            allocation_sort_order=0,
        ),
        CategoryAllocation(
            allocation_category="tech",
            allocation_slot_count=5,
            allocation_sort_order=1,
        ),
        CategoryAllocation(
            allocation_category="business",
            allocation_slot_count=5,
            allocation_sort_order=2,
        ),
        CategoryAllocation(
            allocation_category="sport",
            allocation_slot_count=10,
            allocation_sort_order=3,
        ),
        CategoryAllocation(
            allocation_category="arts",
            allocation_slot_count=5,
            allocation_sort_order=4,
        ),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        now_utc=_NOW,
    )

    assert not any(_category_of(s.feed_story_id) == "sport" for s in slots), (
        "no sport story exists, so none can appear"
    )
    # The 10 sport slots yielded forward; the feed still fills to its 30 target given
    # the deep pool in the other four topic categories.
    assert len(slots) == 30, "sport's 10 budgeted slots rolled forward to fill 30"
    story_ids = [s.feed_story_id for s in slots]
    assert len(story_ids) == len(set(story_ids)), "no duplicate story"
    assert [s.feed_position for s in slots] == list(range(1, 31))


def test_dont_repeat_excludes_prior_feed_stories() -> None:
    """§3.8 don't-repeat: a story already in the user's prior feed never reappears.

    WHY: the reel must not re-show yesterday's stories. The prior-feed exclusion is
    a load-bearing invariant carried over from the old allocator. We exclude a
    specific story and assert it is absent even though it would otherwise qualify.
    """
    stories, tags = _pool_per_category(6)
    excluded_id = "business-0"
    allocation = _dod_allocation()

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        prior_feed_story_ids={excluded_id},
        now_utc=_NOW,
    )

    story_ids = {s.feed_story_id for s in slots}
    assert excluded_id not in story_ids, "§3.8 don't-repeat must drop the prior story"
    # And there are still no duplicates.
    all_ids = [s.feed_story_id for s in slots]
    assert len(all_ids) == len(set(all_ids))


def test_empty_profile_returns_no_slots() -> None:
    """A user with NO followed interests gets an empty allocation (caller skips them).

    WHY: a pre-screen user with zero interests has nothing to score; the allocator
    must return ``[]`` so the writer writes no empty-feed row.
    """
    stories, tags = _pool_per_category(6)
    slots = assemble_user_feed(
        profile_interests=[],
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        now_utc=_NOW,
    )
    assert slots == []


def test_backward_compat_call_without_allocation_or_entities() -> None:
    """The old call shape (no ``followed_entities`` / ``category_allocation``, with
    an ``exploration_candidates_by_interest`` kwarg) still produces a feed.

    WHY: ``sim/ranking_sim.simulate_profile`` and the orchestrator's
    ``assemble_daily_feeds`` still call the allocator without the new kwargs (and the
    sim passes ``exploration_candidates_by_interest``). The rewrite must not break
    those callers — the new params default safely and the legacy exploration kwarg is
    accepted-and-ignored.
    """
    stories, tags = _pool_per_category(6)
    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        prior_feed_story_ids=None,
        exploration_candidates_by_interest={"int-world": []},
        now_utc=_NOW,
    )
    assert slots, "the legacy call shape still yields a feed via the balanced default"
    assert [s.feed_position for s in slots] == list(range(1, len(slots) + 1))


# ── FSR-M6b SP1 — guaranteed source-priority assembly ───────────────────────


def test_fresh_follows_lead_and_topics_fill_to_30_under_budget() -> None:
    """K fresh followed-source items (K < 30) all occupy priority slots; topics fill
    the remaining 30−K — even when the user's source BUDGET is smaller than K.

    WHY (Rule 9 — the load-bearing M6 thesis): follows are the personalization (PRD
    Decision #8), so a fresh follow must NEVER be displaced by a topic story. The user
    here budgeted only youtube=2 but has K=8 fresh follows; under guaranteed priority
    ALL 8 must appear (capped at the feed), and the topics fill exactly 30−8=22. This
    FAILS the moment a topic story displaces a fresh follow (i.e. if source items were
    capped at their source budget instead of getting guaranteed priority).
    """
    stories, tags = _pool_per_category(10)  # deep topic pool (50 stories)
    k = 8
    youtube_stories = [_source_story(f"yt-{i}", "youtube.com") for i in range(k)]
    # User budgeted youtube only 2 (+ topic budgets summing to 28) → feed target 30,
    # but has 8 fresh follows. The guarantee must still surface all 8.
    allocation = [
        CategoryAllocation(allocation_category="youtube", allocation_slot_count=2, allocation_sort_order=0),
        CategoryAllocation(allocation_category="geopolitics", allocation_slot_count=7, allocation_sort_order=1),
        CategoryAllocation(allocation_category="tech", allocation_slot_count=7, allocation_sort_order=2),
        CategoryAllocation(allocation_category="business", allocation_slot_count=7, allocation_sort_order=3),
        CategoryAllocation(allocation_category="sport", allocation_slot_count=7, allocation_sort_order=4),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        source_stories=youtube_stories,
        now_utc=_NOW,
    )

    assert len(slots) == 30, "feed totals 30"
    source_slots = [s for s in slots if s.feed_slot_kind == SLOT_KIND_SOURCE]
    source_ids = {s.feed_story_id for s in source_slots}
    assert source_ids == {f"yt-{i}" for i in range(k)}, (
        "ALL 8 fresh follows occupy priority slots despite a youtube budget of only 2 — "
        "a fresh follow is never displaced by a topic story"
    )
    assert len(slots) - len(source_slots) == 30 - k, "topics fill exactly 30 − K"
    ids = [s.feed_story_id for s in slots]
    assert len(ids) == len(set(ids)), "no story appears twice"


def test_zero_followed_sources_fills_full_30_of_news() -> None:
    """Zero followed sources → a full 30 of category news, never empty (User Story 21)."""
    stories, tags = _pool_per_category(10)
    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        source_stories=[],  # follows nothing
        now_utc=_NOW,
    )
    assert len(slots) == 30, "no follows → full 30 of shared-backbone news"
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots), (
        "every slot is category news when the user follows no sources"
    )


def test_dual_eligible_story_placed_once_as_source_slot() -> None:
    """A story eligible for BOTH a source slot and a topic slot is placed ONCE, with
    the SOURCE slot winning.

    WHY: PRD edge case — a single story id reachable as a followed-source item and as a
    topic candidate must not occupy two slots, and the follow (source) takes it. We tag
    the SAME id as both a youtube source story and a geopolitics topic candidate.
    """
    stories, tags = _pool_per_category(6)
    dual_id = "geopolitics-dual"
    # Topic-eligible: tagged into geopolitics.
    stories.append(_story(dual_id))
    tags.append(_tag(dual_id, _INTEREST_WORLD))
    # ALSO source-eligible: same id, youtube.com origin.
    dual_source = _source_story(dual_id, "youtube.com")

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        source_stories=[dual_source],
        now_utc=_NOW,
    )

    appearances = [s for s in slots if s.feed_story_id == dual_id]
    assert len(appearances) == 1, "the dual-eligible story appears exactly once"
    assert appearances[0].feed_slot_kind == SLOT_KIND_SOURCE, (
        "source wins on a dual-eligible story (the follow takes precedence)"
    )


# ── FSR-M6b SP2 — over-budget source spill rule (recency + importance) ───────


def _dated_source_story(
    story_id: str, outlet_domain: str, published: datetime, outlet_count: int = 1
) -> CanonicalStory:
    """A source story with an explicit publish time + coverage (for the spill rule)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Source {story_id}",
        canonical_url=f"https://{outlet_domain}/{story_id}",
        canonical_normalized_url=f"https://{outlet_domain}/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain=outlet_domain,
        covering_outlets=[f"o{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def test_over_budget_sources_capped_to_top_30_by_recency_then_importance() -> None:
    """>30 fresh source items → exactly 30 kept, by the documented recency+importance
    rule; a known stale low-coverage item is DROPPED and a known fresh item is KEPT.

    WHY (Rule 9): the over-budget cap must be deterministic by RULE, not insertion
    order. We build 35 youtube follows; 33 are fresh (recent), and we plant one very
    STALE item (``yt-stale``, published 10 days ago) plus one very FRESH item
    (``yt-fresh``, published now). With only 30 feed slots, the stale item must be the
    one dropped and the fresh item must survive. This FAILS if the cap is arbitrary /
    keeps insertion order.
    """
    base = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta

    source_stories: list[CanonicalStory] = []
    # 33 "middle" items, each a few hours old, in scrambled insertion order.
    for i in range(33):
        published = base - timedelta(hours=2 + i)  # 2h..34h old
        source_stories.append(_dated_source_story(f"yt-mid-{i:02d}", "youtube.com", published, outlet_count=1))
    # The clear winner (freshest) and the clear loser (stale, 10 days old).
    fresh = _dated_source_story("yt-fresh", "youtube.com", base, outlet_count=1)
    stale = _dated_source_story("yt-stale", "youtube.com", base - timedelta(days=10), outlet_count=5)
    # Insert the loser FIRST and the winner LAST to prove order-independence.
    source_stories = [stale] + source_stories + [fresh]
    assert len(source_stories) == 35

    # Single source category budgeted the whole feed; topics present but pre-empted.
    allocation = [
        CategoryAllocation(allocation_category="youtube", allocation_slot_count=30, allocation_sort_order=0),
    ]
    stories, tags = _pool_per_category(10)

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        source_stories=source_stories,
        now_utc=base,
    )

    assert len(slots) == 30, "exactly 30 kept from 35 over-budget source items"
    kept_ids = {s.feed_story_id for s in slots}
    assert "yt-fresh" in kept_ids, "the freshest item is kept (recency primary)"
    assert "yt-stale" not in kept_ids, (
        "the 10-day-stale item is dropped despite higher coverage — recency leads, "
        "not insertion order and not importance alone"
    )
    assert all(s.feed_slot_kind == SLOT_KIND_SOURCE for s in slots), (
        "all 30 slots are source slots — fresh follows pre-empt the whole feed"
    )


def test_source_spill_importance_breaks_recency_ties_then_id() -> None:
    """Two same-instant source items tie on recency → higher importance kept; a further
    importance tie breaks deterministically by ascending story id.

    WHY: the spill rule's secondary (importance) + tertiary (id) keys must be exercised
    so the ordering is provably deterministic on ties (Rule 9), not luck.
    """
    base = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    # Three items all published at the SAME instant (recency tie). Budget = 2 keeps 2.
    high = _dated_source_story("yt-b-high", "youtube.com", base, outlet_count=9)
    low = _dated_source_story("yt-a-low", "youtube.com", base, outlet_count=1)
    mid = _dated_source_story("yt-c-mid", "youtube.com", base, outlet_count=9)  # ties high on importance
    allocation = [
        CategoryAllocation(allocation_category="youtube", allocation_slot_count=2, allocation_sort_order=0),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=[],
        story_interest_tags=[],
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        source_stories=[high, low, mid],
        now_utc=base,
    )

    kept = [s.feed_story_id for s in slots]
    # importance: high(9) == mid(9) > low(1). The two outlet-9 items win; the id
    # tiebreak (ascending) orders yt-b-high before yt-c-mid. low(1) is dropped.
    assert kept == ["yt-b-high", "yt-c-mid"], (
        f"importance keeps the two outlet-9 items, id tiebreak orders them: {kept}"
    )
    assert "yt-a-low" not in kept, "the low-importance item is dropped on the recency tie"


# ── FSR-M6b SP4 — source-led mix integration test + regression guard ─────────


def test_source_led_mix_integration_follows_lead_news_fills_no_dupes_no_repeats() -> None:
    """ONE end-to-end scenario asserting the composed M6 invariant: fresh follows lead,
    category top-stories fill to 30, no dupes, no prior-feed repeats, a both-eligible
    story is a single source slot.

    WHY (Rule 9 — the regression guard for the load-bearing product thesis): this is
    the single place a future change can't silently regress "follows are the
    personalization." It combines: under-budget source follows (youtube), a dual
    source+topic story, a deep topic pool, and a prior feed (don't-repeat). The test
    FAILS if source items stop leading, if the feed isn't 30, if any story repeats, or
    if the dual story doubles up.
    """
    stories, tags = _pool_per_category(10)  # 50-story topic pool
    # A prior-feed story to exclude (§3.8 don't-repeat).
    prior_id = "tech-0"
    # A dual-eligible story: topic candidate AND a followed youtube source.
    dual_id = "business-dual"
    stories.append(_story(dual_id))
    tags.append(_tag(dual_id, _INTEREST_BUSINESS))
    dual_source = _source_story(dual_id, "youtube.com")
    # Five fresh youtube follows (one is the dual) + two x follows.
    youtube_stories = [_source_story(f"yt-{i}", "youtube.com") for i in range(4)] + [dual_source]
    x_stories = [_source_story(f"x-{i}", "x.com") for i in range(2)]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        source_stories=youtube_stories + x_stories,
        prior_feed_story_ids={prior_id},
        now_utc=_NOW,
    )

    # 1) Fresh follows LEAD — every source follow is present as a source slot.
    source_slots = [s for s in slots if s.feed_slot_kind == SLOT_KIND_SOURCE]
    source_ids = {s.feed_story_id for s in source_slots}
    expected_sources = {f"yt-{i}" for i in range(4)} | {dual_id} | {f"x-{i}" for i in range(2)}
    assert source_ids == expected_sources, "all fresh follows occupy source slots"
    # 2) News fills the rest to 30.
    assert len(slots) == 30, "category top-stories fill the remainder to exactly 30"
    # 3) No story appears twice.
    ids = [s.feed_story_id for s in slots]
    assert len(ids) == len(set(ids)), "no story appears twice in one feed"
    # 4) No prior-feed story repeats.
    assert prior_id not in set(ids), "§3.8 don't-repeat excludes the prior-feed story"
    # 5) The both-eligible story is a SINGLE source slot.
    dual_appearances = [s for s in slots if s.feed_story_id == dual_id]
    assert len(dual_appearances) == 1 and dual_appearances[0].feed_slot_kind == SLOT_KIND_SOURCE, (
        "the dual source+topic story is placed once, as a source slot"
    )
    # 6) Positions are 1..30 contiguous.
    assert [s.feed_position for s in slots] == list(range(1, 31))


def test_regression_source_items_must_lead_not_be_displaced() -> None:
    """REGRESSION GUARD: with the source budget set BELOW the fresh-follow count, the
    follows must still all lead — proving the SP1 guarantee, not the old budget cap.

    WHY: the old allocator capped source items at their source-category budget. This
    test pins the M6 change: even with youtube budget 1, all 5 fresh youtube follows
    must surface. It FAILS (reverts red) if anyone re-introduces the per-source-budget
    cap on fresh follows.
    """
    # Deep pool so the topic budgets can fill the 25 non-source slots.
    stories, tags = _pool_per_category(10)
    youtube_stories = [_source_story(f"yt-{i}", "youtube.com") for i in range(5)]
    # youtube budget 1 (below the 5 follows); topic budgets sum to 29 across the five
    # topic categories (each with a 10-deep pool) so the 25 non-source slots fill.
    allocation = [
        CategoryAllocation(allocation_category="youtube", allocation_slot_count=1, allocation_sort_order=0),
        CategoryAllocation(allocation_category="geopolitics", allocation_slot_count=6, allocation_sort_order=1),
        CategoryAllocation(allocation_category="tech", allocation_slot_count=6, allocation_sort_order=2),
        CategoryAllocation(allocation_category="business", allocation_slot_count=6, allocation_sort_order=3),
        CategoryAllocation(allocation_category="sport", allocation_slot_count=6, allocation_sort_order=4),
        CategoryAllocation(allocation_category="arts", allocation_slot_count=5, allocation_sort_order=5),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        source_stories=youtube_stories,
        now_utc=_NOW,
    )

    source_ids = {s.feed_story_id for s in slots if s.feed_slot_kind == SLOT_KIND_SOURCE}
    assert source_ids == {f"yt-{i}" for i in range(5)}, (
        "all 5 fresh follows lead despite a youtube budget of 1 — the M6 guarantee"
    )
    assert len(slots) == 30


# ── FSR-M3 residual #2 — cluster_importance reaches the scorer ───────────────


def test_cluster_importance_threads_into_feed_scoring() -> None:
    """A clustered story's E1 ``cluster_importance`` lifts its Score above an
    un-clustered twin in the same category (FSR-M3 residual #2 — the assembly seam).

    WHY: M3 built E1 importance but the feed scorer defaulted to the raw outlet count.
    This proves ``cluster_importance_by_story`` reaches ``compute_story_score`` through
    the whole assembly path. Two equal-coverage business stories: the clustered one
    (importance 1.0) must outrank the un-clustered one (which falls back to its low
    raw outlet importance). Removing the threading reverts this red.
    """
    clustered = _story("business-clustered", outlet_count=4)
    twin = _story("business-twin", outlet_count=4)
    stories = [clustered, twin]
    tags = [
        _tag("business-clustered", _INTEREST_BUSINESS),
        _tag("business-twin", _INTEREST_BUSINESS),
    ]
    profile = [UserProfileInterest(profile_interest_id=_INTEREST_BUSINESS, profile_weight=3.0)]
    allocation = [
        CategoryAllocation(allocation_category="business", allocation_slot_count=2, allocation_sort_order=0),
    ]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=allocation,
        # Only the clustered story carries an E1 importance; the twin is un-clustered.
        cluster_importance_by_story={"business-clustered": 1.0},
        now_utc=_NOW,
    )

    order = [s.feed_story_id for s in slots]
    assert order.index("business-clustered") < order.index("business-twin"), (
        "the clustered story's E1 importance must lift it above its un-clustered twin"
    )
    clustered_slot = next(s for s in slots if s.feed_story_id == "business-clustered")
    twin_slot = next(s for s in slots if s.feed_story_id == "business-twin")
    assert clustered_slot.feed_score > twin_slot.feed_score, (
        "cluster_importance must reach compute_story_score and raise the Score"
    )


def test_unclustered_story_scores_identically_without_cluster_importance() -> None:
    """A story absent from ``cluster_importance_by_story`` scores exactly as it did
    before the M3 seam (graceful fallback to raw outlet count) — additive change.

    WHY (Rule 3/12): the residual-#2 threading must be purely additive. Passing an
    EMPTY cluster-importance map must yield byte-identical Scores to passing None.
    """
    stories, tags = _pool_per_category(4)
    none_slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        cluster_importance_by_story=None,
        now_utc=_NOW,
    )
    empty_slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        cluster_importance_by_story={},
        now_utc=_NOW,
    )
    assert [(s.feed_story_id, s.feed_score) for s in none_slots] == [
        (s.feed_story_id, s.feed_score) for s in empty_slots
    ], "an empty cluster-importance map is byte-identical to None (un-clustered fallback)"


# ── Writer (write_daily_feed) — idempotency preserved ───────────────────────


def test_write_daily_feed_skips_empty_slots() -> None:
    """An empty slot list writes NO ``daily_feeds`` row.

    WHY: skipping a zero-eligible user must not create a phantom empty feed.
    """
    client = FakeSupabaseClient()
    result = write_daily_feed(client, "u-empty", _TARGET_DATE, slots=[])
    assert result.slots_written == 0
    assert result.already_present is False
    assert client.inserted == []


def test_write_daily_feed_is_idempotent_on_rerun() -> None:
    """Re-running the writer for the same (user, date) does NOT duplicate (produce-once).

    WHY: the daily batch may re-run; produce-once must hold. This test FAILS if the
    existing-feed pre-check in ``write_daily_feed`` is removed (the second run would
    re-insert all rows).
    """
    slots = [
        AllocatedSlot(
            feed_story_id="s-a",
            feed_position=1,
            feed_score=0.7,
            feed_matched_interest_id="int-world",
            feed_slot_kind=SLOT_KIND_INTEREST,
        ),
        AllocatedSlot(
            feed_story_id="s-b",
            feed_position=2,
            feed_score=0.6,
            feed_matched_interest_id="int-tech",
            feed_slot_kind=SLOT_KIND_INTEREST,
        ),
    ]
    client = FakeSupabaseClient()

    first = write_daily_feed(client, "u1", _TARGET_DATE, slots)
    assert first.slots_written == 2
    assert first.already_present is False
    rows_after_first = len(client.inserted)

    second = write_daily_feed(client, "u1", _TARGET_DATE, slots)
    assert second.already_present is True, "re-run must detect the existing feed"
    assert second.slots_written == 0
    assert len(client.inserted) == rows_after_first, (
        "re-run must NOT insert any new rows (produce-once)"
    )
