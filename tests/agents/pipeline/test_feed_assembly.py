"""Unit tests for the phase-5a "Build your 30" category-budget allocator + writer.

The allocator was REWRITTEN in phase-5a SP3 from the old affinity-proportional
model (proportional split / floor-1 / ~40% cap / exploration) to **user-set
per-category slot budgets + manual sequence**. These tests pin the new invariants
(Rule 9 — each encodes WHY the behavior matters):

  - **Exact per-category budgets** honored (subject to story availability), in the
    user's manual **sequence** order; the breaking tier filled by top-Importance.
  - **Source soft-roll**: ``youtube``/``x`` are budgeted-but-empty (phase-5d); their
    slots roll into the topic categories so ``len(feed) == 30``.
  - **Entity bonus** lifts a Nvidia-followed story above its non-followed twin
    WITHIN its category (Layer-2 scoring feeding Layer-1 allocation).
  - **No-allocation default**: a pre-screen user gets the balanced fallback
    (``breaking 4`` + even split across non-empty topic categories).
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
    SLOT_KIND_BREAKING,
    SLOT_KIND_INTEREST,
    AllocatedSlot,
    assemble_user_feed,
    write_daily_feed,
)
from agents.pipeline.stages.ranking import FollowedEntity, UserProfileInterest

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
_TARGET_DATE = date(2026, 5, 31)

# ── Taxonomy: one depth-0 interest per topic category (slug → category map). ──
# world → world_politics ; tech → tech_science ; business → markets ;
# sport → sport ; entertainment → culture (per agents/pipeline/categories.py).
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
    "world_politics": _INTEREST_WORLD,
    "tech_science": _INTEREST_TECH,
    "markets": _INTEREST_BUSINESS,
    "sport": _INTEREST_SPORT,
    "culture": _INTEREST_ENT,
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
    """The DoD allocation: 2/4/5/4/3/3 topics+breaking, 6+3 source (sums to 30)."""
    spec = [
        ("breaking", 2, 0),
        ("world_politics", 4, 1),
        ("tech_science", 5, 2),
        ("markets", 4, 3),
        ("sport", 3, 4),
        ("culture", 3, 5),
        ("youtube", 6, 6),
        ("x", 3, 7),
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
    user dialed. With each topic holding exactly its budget of stories, two dedicated
    high-Importance breaking stories, and zero source budget, the feed must be EXACTLY
    2 breaking + 4/5/4/3/3 topic slots = 21, ordered breaking-first then by
    ``allocation_sort_order``. This fails the moment a per-category budget is
    mis-counted or the sequence order is dropped.
    """
    # Each topic holds EXACTLY its budgeted count of equal-coverage stories, so the
    # topic-slot count per category is unambiguous (no surplus to roll, no shortfall).
    budget_by_category = {
        "world_politics": 4,
        "tech_science": 5,
        "markets": 4,
        "sport": 3,
        "culture": 3,
    }
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    for category, count in budget_by_category.items():
        interest_id = _CATEGORY_INTEREST[category]
        for index in range(count):
            story_id = f"{category}-{index}"
            stories.append(_story(story_id, outlet_count=4))
            tags.append(_tag(story_id, interest_id))
    # Two dedicated high-Importance breaking stories (distinct ids) so the breaking
    # tier pulls THESE, leaving every topic's budgeted stories intact for its slots.
    stories += [
        _story("world_politics-break", outlet_count=40),
        _story("tech_science-break", outlet_count=38),
    ]
    tags += [
        _tag("world_politics-break", _INTEREST_WORLD),
        _tag("tech_science-break", _INTEREST_TECH),
    ]

    allocation = [
        CategoryAllocation(
            allocation_category="breaking",
            allocation_slot_count=2,
            allocation_sort_order=0,
        ),
        CategoryAllocation(
            allocation_category="world_politics",
            allocation_slot_count=4,
            allocation_sort_order=1,
        ),
        CategoryAllocation(
            allocation_category="tech_science",
            allocation_slot_count=5,
            allocation_sort_order=2,
        ),
        CategoryAllocation(
            allocation_category="markets",
            allocation_slot_count=4,
            allocation_sort_order=3,
        ),
        CategoryAllocation(
            allocation_category="sport",
            allocation_slot_count=3,
            allocation_sort_order=4,
        ),
        CategoryAllocation(
            allocation_category="culture",
            allocation_slot_count=3,
            allocation_sort_order=5,
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

    assert len(slots) == 21, "2 breaking + 4+5+4+3+3 topic slots"
    # Breaking is the first 2 positions, kind=breaking, and exactly the 2 spikes.
    assert [s.feed_slot_kind for s in slots[:2]] == [SLOT_KIND_BREAKING] * 2
    assert {s.feed_story_id for s in slots[:2]} == {
        "world_politics-break",
        "tech_science-break",
    }
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots[2:])

    # Each category's TOPIC slots (kind=interest) match its budget EXACTLY — the
    # breaking tier is counted separately and does not inflate a topic budget.
    topic_by_category = Counter(
        _category_of(s.feed_story_id)
        for s in slots
        if s.feed_slot_kind == SLOT_KIND_INTEREST
    )
    assert topic_by_category["world_politics"] == 4
    assert topic_by_category["tech_science"] == 5
    assert topic_by_category["markets"] == 4
    assert topic_by_category["sport"] == 3
    assert topic_by_category["culture"] == 3

    # Topic slots appear in the user's sequence: all world_politics before any
    # tech_science (among the non-breaking slots), etc.
    topic_order = [_category_of(s.feed_story_id) for s in slots[2:]]
    sequence = ["world_politics", "tech_science", "markets", "sport", "culture"]
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
    # Breaking tier is exactly the budgeted 2; the rest are category slots.
    assert sum(1 for s in slots if s.feed_slot_kind == SLOT_KIND_BREAKING) == 2
    # No source-category story ever appears (youtube/x have no stories to place).
    assert not any(_category_of(s.feed_story_id) in ("youtube", "x") for s in slots), (
        "source categories contribute zero items"
    )


def test_breaking_tier_filled_by_importance_and_not_double_placed() -> None:
    """Breaking pulls the top-Importance stories and removes them from their topic
    bucket (no double-placement).

    WHY: ``breaking`` is a tier (top-Importance across topics), and a story promoted
    to breaking must NOT also occupy a topic slot — that would duplicate it and
    miscount the budget. We make two stories overwhelmingly high-Importance and
    assert they take the 2 breaking slots and appear exactly once.
    """
    stories, tags = _pool_per_category(6)
    # Two very-high-coverage stories (Importance spike) in distinct categories.
    big_a = _story("world_politics-big", outlet_count=40)
    big_b = _story("tech_science-big", outlet_count=38)
    stories += [big_a, big_b]
    tags += [
        _tag("world_politics-big", _INTEREST_WORLD),
        _tag("tech_science-big", _INTEREST_TECH),
    ]

    slots = assemble_user_feed(
        profile_interests=_ALL_TOPIC_PROFILE,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=_dod_allocation(),
        now_utc=_NOW,
    )

    breaking_ids = {
        s.feed_story_id for s in slots if s.feed_slot_kind == SLOT_KIND_BREAKING
    }
    assert breaking_ids == {"world_politics-big", "tech_science-big"}, (
        "the two Importance spikes take the breaking slots"
    )
    # Each breaking story appears exactly once (not also as a topic slot).
    all_ids = [s.feed_story_id for s in slots]
    for breaking_id in breaking_ids:
        assert all_ids.count(breaking_id) == 1, f"{breaking_id} double-placed"
    # Breaking slots carry no matched-interest attribution.
    for slot in slots:
        if slot.feed_slot_kind == SLOT_KIND_BREAKING:
            assert slot.feed_matched_interest_id is None


def test_followed_entity_lifts_story_within_its_category() -> None:
    """A Nvidia-followed story outranks an equivalent non-followed story WITHIN its
    category (markets/tech_science).

    WHY: the entity follow is a Layer-2 score bonus that must change the ORDER
    within a category's slots — a Nvidia follower should see the Nvidia story above
    its twin. This fails if the allocator does not feed ``followed_entities`` into
    the entity-aware scorer, or sorts a category by something other than Score.
    """
    # Two equivalent markets stories (same coverage), one mentioning Nvidia.
    nvidia_story = _story(
        "markets-nvidia",
        outlet_count=5,
        title="Nvidia Q3 earnings beat expectations",
    )
    twin_story = _story(
        "markets-twin",
        outlet_count=5,
        title="Chipmaker quarterly earnings beat expectations",
    )
    stories = [nvidia_story, twin_story]
    tags = [
        _tag("markets-nvidia", _INTEREST_BUSINESS),
        _tag("markets-twin", _INTEREST_BUSINESS),
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
            allocation_category="markets",
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
    assert order.index("markets-nvidia") < order.index("markets-twin"), (
        "the Nvidia-followed story must rank above its non-followed twin"
    )
    nvidia_slot = next(s for s in slots if s.feed_story_id == "markets-nvidia")
    twin_slot = next(s for s in slots if s.feed_story_id == "markets-twin")
    assert nvidia_slot.feed_score > twin_slot.feed_score, (
        "the entity bonus must lift the Nvidia story's Score above the twin's"
    )


def test_no_allocation_user_gets_balanced_default() -> None:
    """A user with NO ``user_feed_allocation`` rows gets the balanced default
    (breaking 4 + an even split across non-empty topic categories).

    WHY: pre-screen users must still receive a feed. The default replaces the old
    affinity-proportional behavior. With 5 non-empty topic categories and a deep
    pool, the default is breaking 4 + 26 split 6/5/5/5/5 across the 5 topics → 30
    slots. This fails if the empty ``category_allocation`` path does not synthesize
    a default.
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
    assert sum(1 for s in slots if s.feed_slot_kind == SLOT_KIND_BREAKING) == 4, (
        "default breaking budget is 4"
    )
    # All 5 topic categories are represented (even split, none starved).
    represented = {
        _category_of(s.feed_story_id)
        for s in slots
        if s.feed_slot_kind == SLOT_KIND_INTEREST
    }
    assert represented == set(_CATEGORY_INTEREST.keys()), (
        "the even split must touch every non-empty topic category"
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
            allocation_category="breaking",
            allocation_slot_count=2,
            allocation_sort_order=0,
        ),
        CategoryAllocation(
            allocation_category="world_politics",
            allocation_slot_count=5,
            allocation_sort_order=1,
        ),
        CategoryAllocation(
            allocation_category="tech_science",
            allocation_slot_count=5,
            allocation_sort_order=2,
        ),
        CategoryAllocation(
            allocation_category="markets",
            allocation_slot_count=5,
            allocation_sort_order=3,
        ),
        CategoryAllocation(
            allocation_category="sport",
            allocation_slot_count=8,
            allocation_sort_order=4,
        ),
        CategoryAllocation(
            allocation_category="culture",
            allocation_slot_count=5,
            allocation_sort_order=5,
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
    # The 8 sport slots yielded forward; the feed still fills to its 30 target given
    # the deep pool in the other four topic categories.
    assert len(slots) == 30, "sport's 8 budgeted slots rolled forward to fill 30"
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
    excluded_id = "markets-0"
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
