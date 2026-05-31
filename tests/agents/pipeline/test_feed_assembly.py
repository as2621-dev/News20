"""Unit tests for the SP4 per-user feed allocator + ``daily_feeds`` writer.

DoD (phase file SP4 / Rule 9):
  (a) ``assemble_daily_feeds`` writes ONE feed per active user with an ordered,
      non-empty ``feed_story_id`` sequence (``feed_position`` 1..N).
  (b) re-running the batch does NOT duplicate a feed (produce-once / idempotent) —
      this test FAILS if the produce-once pre-check in ``write_daily_feed`` is
      removed.
  (c) a user with NO eligible story is SKIPPED — no ``daily_feeds`` row at all
      (no empty-feed row).
Plus the §3 allocator invariants (ranking-spec §3): breaking-preempt, ~40% cap,
floor-1, and don't-repeat (§3.8) — asserted on 2 seeded users with DIFFERENT
interests so the feeds are demonstrably distinct.

Externals are mocked at the boundary: a fake supabase client captures inserts and
answers the existing-feed pre-check (no network, no writes). The ranking +
allocation math runs for REAL (pure functions) so the assertions test the
prioritization logic, not just the insert.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.feed_assembly import (
    SLOT_KIND_BREAKING,
    assemble_user_feed,
    write_daily_feed,
)
from agents.pipeline.orchestrator import (
    ActiveUserFeedInputs,
    assemble_daily_feeds,
)
from agents.pipeline.stages.ranking import UserProfileInterest

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
_TARGET_DATE = date(2026, 5, 31)

# Taxonomy: sport → soccer → arsenal ; markets → stocks → meta-stock.
_INTEREST_ARSENAL = "int-arsenal"
_INTEREST_SOCCER = "int-soccer"
_INTEREST_SPORT = "int-sport"
_INTEREST_META = "int-meta-stock"
_INTEREST_STOCKS = "int-stocks"
_INTEREST_MARKETS = "int-markets"

_INTEREST_NODES = {
    _INTEREST_SPORT: InterestNode(
        interest_id=_INTEREST_SPORT, interest_slug="sport", interest_label="Sport"
    ),
    _INTEREST_SOCCER: InterestNode(
        interest_id=_INTEREST_SOCCER,
        parent_interest_id=_INTEREST_SPORT,
        interest_slug="sport.soccer",
        interest_label="Soccer",
        depth_level=1,
    ),
    _INTEREST_ARSENAL: InterestNode(
        interest_id=_INTEREST_ARSENAL,
        parent_interest_id=_INTEREST_SOCCER,
        interest_slug="sport.soccer.arsenal",
        interest_label="Arsenal",
        depth_level=2,
    ),
    _INTEREST_MARKETS: InterestNode(
        interest_id=_INTEREST_MARKETS, interest_slug="markets", interest_label="Markets"
    ),
    _INTEREST_STOCKS: InterestNode(
        interest_id=_INTEREST_STOCKS,
        parent_interest_id=_INTEREST_MARKETS,
        interest_slug="markets.stocks",
        interest_label="Stocks",
        depth_level=1,
    ),
    _INTEREST_META: InterestNode(
        interest_id=_INTEREST_META,
        parent_interest_id=_INTEREST_STOCKS,
        interest_slug="markets.stocks.meta",
        interest_label="Meta Stock",
        depth_level=2,
    ),
}


def _story(
    story_id: str, outlet_count: int, published: datetime = _NOW
) -> CanonicalStory:
    """A fresh canonical story with a given coverage (Importance source)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Story {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain="bbc.com",
        covering_outlets=[f"outlet{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str, match_depth: int) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=match_depth,
    )


class FakeDailyFeedsQuery:
    """Captures ``daily_feeds`` inserts and answers the existing-feed pre-check.

    ``existing_rows`` simulates rows already in the table for ANY (user, date);
    keyed by ``(feed_user_id, feed_date)``. ``inserted`` collects every row the
    writer inserts this run so tests assert on the exact payloads.
    """

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
            # Reason: a real insert makes those rows "exist" — so a SECOND run
            # for the same (user, date) sees them via the pre-check. This is what
            # makes the idempotency test meaningful (it would duplicate without
            # the produce-once guard).
            for row in rows:
                key = (row["feed_user_id"], row["feed_date"])
                self.store.existing_rows.setdefault(key, []).append(row)
            return _Response([dict(row) for row in rows])
        # A select pre-check: return the existing rows for this (user, date).
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


# ── Allocator (assemble_user_feed) ─────────────────────────────────────────


def test_assemble_user_feed_orders_positions_and_excludes_prior() -> None:
    """A user's feed is ordered 1..N, non-empty, and never repeats a prior story.

    WHY: the reel reads ``daily_feeds`` by ``feed_position``; positions must be
    contiguous and 1-based, and §3.8 don't-repeat must drop a story already shown.
    """
    profile = [
        UserProfileInterest(profile_interest_id=_INTEREST_ARSENAL, profile_weight=3.0)
    ]
    stories = [_story("s-arsenal-1", 5), _story("s-arsenal-2", 3)]
    tags = [
        _tag("s-arsenal-1", _INTEREST_ARSENAL, 0),
        _tag("s-arsenal-2", _INTEREST_ARSENAL, 0),
    ]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        prior_feed_story_ids={"s-arsenal-1"},  # already shown yesterday
        now_utc=_NOW,
    )

    story_ids = [slot.feed_story_id for slot in slots]
    positions = [slot.feed_position for slot in slots]
    assert story_ids, "feed must be non-empty when eligible stories exist"
    assert "s-arsenal-1" not in story_ids, "§3.8 don't-repeat must exclude prior story"
    assert "s-arsenal-2" in story_ids
    assert positions == list(range(1, len(slots) + 1)), (
        "positions must be 1..N contiguous"
    )


def test_assemble_user_feed_breaking_preempts_top_slot() -> None:
    """The highest-Importance story takes the breaking slot (position 1).

    WHY (§3.1): a big multi-outlet spike must preempt the proportional split — a
    24-outlet story outranks a 2-outlet one in the first slot even within one
    interest.
    """
    profile = [
        UserProfileInterest(profile_interest_id=_INTEREST_ARSENAL, profile_weight=3.0)
    ]
    stories = [_story("s-small", 2), _story("s-breaking", 24)]
    tags = [
        _tag("s-small", _INTEREST_ARSENAL, 0),
        _tag("s-breaking", _INTEREST_ARSENAL, 0),
    ]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        now_utc=_NOW,
    )

    assert slots[0].feed_story_id == "s-breaking"
    assert slots[0].feed_slot_kind == SLOT_KIND_BREAKING


def test_assemble_user_feed_caps_single_interest_share_for_multi_interest_user() -> (
    None
):
    """No single interest exceeds ~40% of the feed for a multi-interest user.

    WHY (§3.4): the cap stops the feed collapsing onto one topic even if one
    interest has a flood of stories — diversity protection. We give one interest
    a glut and the other a couple, then assert the glut's interest-bucket share
    is capped at ~40% of N and the starved interest still appears.
    """
    profile = [
        UserProfileInterest(profile_interest_id=_INTEREST_ARSENAL, profile_weight=3.0),
        UserProfileInterest(profile_interest_id=_INTEREST_META, profile_weight=3.0),
    ]
    # 25 arsenal stories (the glut) vs 2 meta-stock stories.
    arsenal_stories = [_story(f"s-ars-{i}", 4) for i in range(25)]
    meta_stories = [_story("s-meta-1", 4), _story("s-meta-2", 4)]
    stories = arsenal_stories + meta_stories
    tags = [_tag(f"s-ars-{i}", _INTEREST_ARSENAL, 0) for i in range(25)] + [
        _tag("s-meta-1", _INTEREST_META, 0),
        _tag("s-meta-2", _INTEREST_META, 0),
    ]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        now_utc=_NOW,
    )

    # The §3.4 cap bites on the INTEREST-bucket slots: ~40% of N(30) = 12.
    # The breaking tier (§3.1) is a separate preempt tier that may legitimately
    # add more high-Importance stories, so the cap is asserted on interest-kind
    # slots specifically — that is what the diversity guard protects.
    arsenal_interest_slots = sum(
        1
        for s in slots
        if s.feed_story_id.startswith("s-ars-") and s.feed_slot_kind == "interest"
    )
    cap = round(30 * 0.40)
    assert arsenal_interest_slots <= cap, (
        f"arsenal over-filled the interest buckets: {arsenal_interest_slots} > {cap}"
    )
    assert any(s.feed_story_id.startswith("s-meta-") for s in slots), (
        "the under-supplied interest must still appear (floor-1, §3.3)"
    )


def test_assemble_user_feed_empty_when_no_eligible_story() -> None:
    """No eligible story anywhere → empty allocation (caller will skip the user).

    WHY (DoD-c): a user whose interests have no fresh stories must not get an
    empty feed — the allocator returns ``[]`` so the writer writes no row.
    """
    profile = [
        UserProfileInterest(profile_interest_id=_INTEREST_ARSENAL, profile_weight=3.0)
    ]
    # Stories exist but none is tagged to anything the user follows.
    stories = [_story("s-unrelated", 5)]
    tags = [_tag("s-unrelated", "int-cricket", 0)]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        now_utc=_NOW,
    )

    assert slots == []


# ── Writer (write_daily_feed) — idempotency ─────────────────────────────────


def test_write_daily_feed_skips_empty_slots() -> None:
    """An empty slot list writes NO ``daily_feeds`` row (DoD-c).

    WHY: skipping a zero-eligible user must not create a phantom empty feed.
    """
    client = FakeSupabaseClient()
    result = write_daily_feed(client, "u-empty", _TARGET_DATE, slots=[])
    assert result.slots_written == 0
    assert result.already_present is False
    assert client.inserted == []


def test_write_daily_feed_is_idempotent_on_rerun() -> None:
    """Re-running the writer for the same (user, date) does NOT duplicate (DoD-b).

    WHY: the daily batch may re-run; produce-once must hold. This test FAILS if
    the existing-feed pre-check in ``write_daily_feed`` is removed (the second
    run would re-insert all rows).
    """
    profile = [
        UserProfileInterest(profile_interest_id=_INTEREST_ARSENAL, profile_weight=3.0)
    ]
    stories = [_story("s-a", 5), _story("s-b", 4)]
    tags = [_tag("s-a", _INTEREST_ARSENAL, 0), _tag("s-b", _INTEREST_ARSENAL, 0)]
    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        now_utc=_NOW,
    )
    assert slots, "precondition: the user has eligible stories"

    client = FakeSupabaseClient()

    first = write_daily_feed(client, "u1", _TARGET_DATE, slots)
    assert first.slots_written == len(slots)
    assert first.already_present is False
    rows_after_first = len(client.inserted)

    second = write_daily_feed(client, "u1", _TARGET_DATE, slots)
    assert second.already_present is True, "re-run must detect the existing feed"
    assert second.slots_written == 0
    assert len(client.inserted) == rows_after_first, (
        "re-run must NOT insert any new rows (produce-once)"
    )


# ── Batch (assemble_daily_feeds) — DoD a/b/c end to end ─────────────────────


def _two_user_inputs() -> list[ActiveUserFeedInputs]:
    """Two active users with DIFFERENT interests + one zero-eligible user."""
    return [
        ActiveUserFeedInputs(
            active_user_id="u-soccer",
            profile_interests=[
                UserProfileInterest(
                    profile_interest_id=_INTEREST_ARSENAL, profile_weight=3.0
                )
            ],
        ),
        ActiveUserFeedInputs(
            active_user_id="u-markets",
            profile_interests=[
                UserProfileInterest(
                    profile_interest_id=_INTEREST_META, profile_weight=3.0
                )
            ],
        ),
        ActiveUserFeedInputs(
            active_user_id="u-nothing",
            profile_interests=[
                UserProfileInterest(
                    profile_interest_id="int-cricket", profile_weight=3.0
                )
            ],
        ),
    ]


def _shared_pool() -> tuple[list[CanonicalStory], list[StoryInterestTag]]:
    stories = [
        _story("s-arsenal-1", 5),
        _story("s-arsenal-2", 3),
        _story("s-meta-1", 6),
        _story("s-meta-2", 4),
    ]
    tags = [
        _tag("s-arsenal-1", _INTEREST_ARSENAL, 0),
        _tag("s-arsenal-2", _INTEREST_ARSENAL, 0),
        _tag("s-meta-1", _INTEREST_META, 0),
        _tag("s-meta-2", _INTEREST_META, 0),
    ]
    return stories, tags


def test_assemble_daily_feeds_one_distinct_feed_per_active_user() -> None:
    """DoD-a + distinctness: each eligible user gets ONE ordered, non-empty feed;
    the two users' feeds are demonstrably DIFFERENT; the zero-eligible user is
    SKIPPED (DoD-c).

    WHY: this is the SP4 phase floor — ≥2 distinct per-user feeds, no empty-feed
    rows, ordered story_id arrays.
    """
    client = FakeSupabaseClient()
    stories, tags = _shared_pool()

    result = assemble_daily_feeds(
        target_date=_TARGET_DATE,
        active_user_inputs=_two_user_inputs(),
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        supabase_client=client,
        now_utc=_NOW,
    )

    assert result.active_user_count == 3
    assert result.feeds_written == 2, "two eligible users → two feeds"
    assert result.users_skipped_empty == 1, "the zero-eligible user is skipped"

    # Group inserted rows by user.
    by_user: dict[str, list[dict]] = {}
    for row in client.inserted:
        by_user.setdefault(row["feed_user_id"], []).append(row)

    assert set(by_user.keys()) == {"u-soccer", "u-markets"}
    assert "u-nothing" not in by_user, "no empty-feed row for the skipped user"

    for user_id, rows in by_user.items():
        rows.sort(key=lambda r: r["feed_position"])
        positions = [r["feed_position"] for r in rows]
        story_ids = [r["feed_story_id"] for r in rows]
        assert positions == list(range(1, len(rows) + 1)), (
            f"{user_id} positions not 1..N"
        )
        assert story_ids, f"{user_id} feed is empty"

    soccer_ids = {r["feed_story_id"] for r in by_user["u-soccer"]}
    markets_ids = {r["feed_story_id"] for r in by_user["u-markets"]}
    assert soccer_ids != markets_ids, "the two feeds must be demonstrably different"
    assert soccer_ids == {"s-arsenal-1", "s-arsenal-2"}
    assert markets_ids == {"s-meta-1", "s-meta-2"}


def test_assemble_daily_feeds_rerun_does_not_duplicate() -> None:
    """DoD-b end to end: re-running the whole batch does not duplicate feeds.

    WHY: the daily cron may fire twice / be retried; produce-once must hold for
    the batch, not just a single ``write_daily_feed`` call.
    """
    client = FakeSupabaseClient()
    stories, tags = _shared_pool()
    inputs = _two_user_inputs()

    first = assemble_daily_feeds(
        target_date=_TARGET_DATE,
        active_user_inputs=inputs,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        supabase_client=client,
        now_utc=_NOW,
    )
    rows_after_first = len(client.inserted)
    assert first.feeds_written == 2

    second = assemble_daily_feeds(
        target_date=_TARGET_DATE,
        active_user_inputs=inputs,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        supabase_client=client,
        now_utc=_NOW,
    )

    assert second.feeds_written == 0, "re-run writes no new feeds"
    assert second.users_skipped_idempotent == 2, "both feeds already present"
    assert len(client.inserted) == rows_after_first, "re-run inserted no new rows"
