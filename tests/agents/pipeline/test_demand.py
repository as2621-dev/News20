"""Unit tests for per-user (category, subcategory) demand (phase-m2 SP2).

These assert the *intent* of ``derive_user_subcategory_demand`` (Rule 9):

  - the budget is *split* across followed subcategories, not duplicated (a) — so a
    user wanting 6 markets slots across two subcats sizes the pool to 3+3, not 6+6;
  - a root-only follow yields the ``"_all"`` sentinel (b) — there is no subcategory
    to size against, so the whole budget is "any subcategory in this category";
  - a no-row user inherits the flat default summing to 30 (c) — the pool must still
    size the new user, who has not customized their 30;
  - the remainder is deterministic (d) — an odd budget across an even number of
    subcats must split the same way every run (lexicographic remainder), or the
    daily pool target would be non-reproducible.
"""

from __future__ import annotations

import math

from agents.ingestion.models import InterestNode
from agents.pipeline.categories import (
    CATEGORY_FLOOR,
    DEFAULT_FEED_ALLOCATION,
    CategoryAllocation,
)
from agents.pipeline.demand import (
    compute_pool_target,
    derive_user_subcategory_demand,
)


def _allocation(category: str, slot_count: int, sort_order: int = 0) -> CategoryAllocation:
    """Build one ``CategoryAllocation`` row with the real required fields."""
    return CategoryAllocation(
        allocation_category=category,
        allocation_slot_count=slot_count,
        allocation_sort_order=sort_order,
    )


def _node(interest_slug: str, depth_level: int) -> InterestNode:
    """Build one followed ``InterestNode`` with the real required fields."""
    return InterestNode(
        interest_id=f"int-{interest_slug}",
        interest_slug=interest_slug,
        interest_label=interest_slug.split(".")[-1].title(),
        depth_level=depth_level,
    )


def test_budget_splits_evenly_across_two_followed_subcategories() -> None:
    """(a) markets=6 following crypto+stocks → 3 each (split, not duplicated)."""
    allocation = [_allocation("markets", 6)]
    follows = [
        _node("markets.crypto.btc", depth_level=2),
        _node("markets.stocks", depth_level=1),
    ]

    demand = derive_user_subcategory_demand(
        allocation, follows, DEFAULT_FEED_ALLOCATION
    )

    assert demand == {
        ("markets", "markets.crypto"): 3,
        ("markets", "markets.stocks"): 3,
    }
    # WHY: the pool is sized to demand, so two subcats share the 6 — never 6+6.
    assert sum(demand.values()) == 6


def test_root_only_follow_yields_all_sentinel() -> None:
    """(b) sport=4 following only root 'sport' → {(sport,'_all'):4}."""
    allocation = [_allocation("sport", 4)]
    follows = [_node("sport", depth_level=0)]

    demand = derive_user_subcategory_demand(
        allocation, follows, DEFAULT_FEED_ALLOCATION
    )

    # WHY: a depth-0 follow has no subcategory to size against, so the whole budget
    # routes to the "_all" cell (any subcategory in this category).
    assert demand == {("sport", "_all"): 4}


def test_no_row_user_inherits_default_as_all_cells_summing_to_30() -> None:
    """(c) empty allocation → default_allocation as '_all' cells summing to 30."""
    demand = derive_user_subcategory_demand([], [], DEFAULT_FEED_ALLOCATION)

    expected = {
        (category, "_all"): slot_count
        for category, slot_count in DEFAULT_FEED_ALLOCATION.items()
    }
    assert demand == expected
    # WHY: a brand-new user who never built their 30 must still be sized into the
    # pool at the universal default — and that default is a full 30-slot feed.
    assert sum(demand.values()) == 30


def test_remainder_goes_to_lexicographically_first_subcategory() -> None:
    """(d) markets=7 across crypto+stocks → crypto 4, stocks 3 (deterministic)."""
    allocation = [_allocation("markets", 7)]
    follows = [
        _node("markets.stocks", depth_level=1),  # input order reversed on purpose
        _node("markets.crypto.eth", depth_level=2),
    ]

    demand = derive_user_subcategory_demand(
        allocation, follows, DEFAULT_FEED_ALLOCATION
    )

    # WHY: the daily pool target must be reproducible — the leftover slot always
    # goes to the lexicographically-first key ('markets.crypto' < 'markets.stocks'),
    # regardless of follow input order.
    assert demand == {
        ("markets", "markets.crypto"): 4,
        ("markets", "markets.stocks"): 3,
    }
    assert sum(demand.values()) == 7


# --- SP3: compute_pool_target (aggregate max × buffer, floored) --------------


def test_pool_target_aggregates_max_not_sum_across_users() -> None:
    """(a) Two users each geopolitics=10 on the SAME subcat → max-sized, not summed.

    geopolitics maps to the ``world_politics`` category. Both users put all 10 into
    the ``geopolitics.elections`` subcategory (slugs ``.us`` and ``.eu`` both have
    first-two-segments ``geopolitics.elections``), so both demands land on the SAME
    cell. A SUM aggregate would size that cell to ceil(20 × 1.5) = 30; MAX sizes it
    to ceil(10 × 1.5) = 15. This is the load-bearing max-not-sum assertion.
    """
    buffer = 1.5
    # allocation_category is the screen category (world_politics); the geopolitics.*
    # follows supply the subcategory (geopolitics → world_politics via the slug map).
    alloc = {
        "u1": [_allocation("world_politics", 10)],
        "u2": [_allocation("world_politics", 10)],
    }
    follows = {
        "u1": [_node("geopolitics.elections.us", depth_level=2)],
        "u2": [_node("geopolitics.elections.eu", depth_level=2)],
    }

    target = compute_pool_target(
        alloc, follows, ["u1", "u2"], DEFAULT_FEED_ALLOCATION,
        buffer=buffer, category_floor={},
    )

    cell = ("world_politics", "geopolitics.elections")
    # WHY: both users demand the same cell at 10; the pool needs only the heaviest
    # single demand (max), buffered — NOT their sum. ceil(10×1.5)=15, not ceil(20×1.5)=30.
    assert target[cell] == math.ceil(10 * buffer) == 15
    assert sum(target.values()) == 15


def test_pool_target_distinct_subcats_size_independently() -> None:
    """(a') Two users, geopolitics=10 each on DIFFERENT subcats → each cell ceil(10×1.5).

    Distinct subcats can't collide, so each is sized to its single owner's buffered
    demand. The category total is 30 (15+15), which is still max-based (each cell is
    ceil(10×buffer)), NOT a single ceil(20×buffer) cell.
    """
    buffer = 1.5
    alloc = {
        "u1": [_allocation("world_politics", 10)],
        "u2": [_allocation("world_politics", 10)],
    }
    follows = {
        "u1": [_node("geopolitics.elections.us", depth_level=2)],
        "u2": [_node("geopolitics.conflict.me", depth_level=2)],
    }

    target = compute_pool_target(
        alloc, follows, ["u1", "u2"], DEFAULT_FEED_ALLOCATION,
        buffer=buffer, category_floor={},
    )

    assert target[("world_politics", "geopolitics.elections")] == 15
    assert target[("world_politics", "geopolitics.conflict")] == 15
    # WHY: every cell is ceil(10×buffer) — each owned by one user. No cell is 20-based.
    assert all(v == math.ceil(10 * buffer) for v in target.values())


def test_pool_target_floors_unallocated_topic_category() -> None:
    """(b) A topic category NOBODY allocates still appears at its CATEGORY_FLOOR.

    Both users only budget markets; nobody touches ``culture``. The floor must still
    surface culture at CATEGORY_FLOOR['culture'] (=3) via a single (culture,'_all')
    cell, so a live category is never starved out of the pool entirely.
    """
    buffer = 1.5
    alloc = {
        "u1": [_allocation("markets", 4)],
        "u2": [_allocation("markets", 4)],
    }
    follows = {"u1": [], "u2": []}

    target = compute_pool_target(
        alloc, follows, ["u1", "u2"], DEFAULT_FEED_ALLOCATION,
        buffer=buffer, category_floor=CATEGORY_FLOOR,
    )

    # WHY: nobody demanded culture, but it has a positive floor — it must appear at
    # exactly the floor via the "_all" sentinel cell so the category isn't starved.
    assert target[("culture", "_all")] == CATEGORY_FLOOR["culture"] == 3
    # Source categories have floor 0 → no phantom floor cell is created for them.
    assert ("youtube", "_all") not in target or target[("youtube", "_all")] > 0


def test_pool_target_buffer_one_no_floor_is_plain_max_demand() -> None:
    """(c) buffer=1.0 + empty category_floor reduces to plain max demand.

    No inflation (buffer=1), no floor cells (empty floor map) — the target is exactly
    the per-cell max over users of the raw demand.
    """
    alloc = {
        "u1": [_allocation("markets", 6)],
        "u2": [_allocation("markets", 6)],
    }
    follows = {
        "u1": [
            _node("markets.crypto.btc", depth_level=2),
            _node("markets.stocks", depth_level=1),
        ],
        "u2": [_node("markets.crypto.eth", depth_level=2)],
    }

    target = compute_pool_target(
        alloc, follows, ["u1", "u2"], DEFAULT_FEED_ALLOCATION,
        buffer=1.0, category_floor={},
    )

    # u1: crypto 3, stocks 3 (split of 6). u2: crypto 6 (all to crypto).
    # max per cell: crypto max(3,6)=6, stocks max(3,0)=3. No buffer, no floor.
    assert target == {
        ("markets", "markets.crypto"): 6,
        ("markets", "markets.stocks"): 3,
    }


def test_pool_target_two_user_shopping_list_is_correct_and_sane() -> None:
    """SP4 (integration): one customized + one default user → correct shopping list.

    Drives ``compute_pool_target`` end-to-end with the REAL config (``Settings``
    buffer + ``CATEGORY_FLOOR``) the daily batch wires in, encoding the load-bearing
    M2 guarantees (Rule 9 — WHY, not just WHAT):

      - **subcategory split for the customized user** — user A's markets=8 budget is
        sized as crypto + stocks cells, never a lumped markets cell;
      - **'_all' cells for the default user** — user B never built their 30, so every
        category they inherit is sized via the ``"_all"`` sentinel;
      - **max-over-users, ×buffer, ceil** — each cell is ``ceil(max_demand × buffer)``,
        NOT a sum across users;
      - **every topic-category floor is honored** — no live topic category sits below
        its ``CATEGORY_FLOOR``;
      - **grand total is sane** — at least the heaviest single user's 30 survives the
        buffer (the pool is never sized smaller than one full personalized feed).
    """
    from agents.shared.settings import Settings

    buffer = Settings().pool_buffer

    allocation_by_user = {
        # User A — customized: 8 markets slots, follows crypto + stocks subcats.
        "user_a": [_allocation("markets", 8)],
        # User B — default: NO allocation rows (inherits DEFAULT_FEED_ALLOCATION).
    }
    interest_nodes_by_user = {
        "user_a": [
            _node("markets.crypto.btc", depth_level=2),
            _node("markets.stocks", depth_level=1),
        ],
        # User B — NO follows.
    }
    active_user_ids = ["user_a", "user_b"]

    target = compute_pool_target(
        allocation_by_user,
        interest_nodes_by_user,
        active_user_ids,
        DEFAULT_FEED_ALLOCATION,
        buffer=buffer,
        category_floor=CATEGORY_FLOOR,
    )

    # ── Subcategory split for user A — 8 markets split 4/4 across crypto+stocks,
    # each ×buffer ceil. WHY: the customized user's budget is sized at subcategory
    # granularity, not lumped — the pool fetches crypto AND stocks separately.
    assert target[("markets", "markets.crypto")] == math.ceil(4 * buffer)
    assert target[("markets", "markets.stocks")] == math.ceil(4 * buffer)

    # ── '_all' default cells for user B — every default category B inherits is sized
    # via the "_all" sentinel at ceil(default × buffer). WHY: a brand-new user is
    # still sized into the shared pool at the universal default feed.
    for category, default_slots in DEFAULT_FEED_ALLOCATION.items():
        cell = (category, "_all")
        # markets._all is also present (B's markets default) alongside A's subcat cells.
        assert cell in target, f"missing default '_all' cell for {category}"
        assert target[cell] >= math.ceil(default_slots * buffer)

    # ── Max-over-users, not sum — A and B both touch markets, but via DIFFERENT cells
    # (A: subcats, B: _all), so no markets cell is inflated by adding the two users.
    # markets._all is purely B's default (4) → ceil(4×buffer), NOT A's 8 added in.
    assert target[("markets", "_all")] == math.ceil(
        DEFAULT_FEED_ALLOCATION["markets"] * buffer
    )

    # ── Every topic-category floor is honored (a live category is never starved).
    for category, floor in CATEGORY_FLOOR.items():
        if floor <= 0:
            continue
        category_total = sum(
            count for (cell_cat, _), count in target.items() if cell_cat == category
        )
        assert category_total >= floor, f"{category} total {category_total} < {floor}"

    # ── Grand total is sane — the pool is at least one full personalized feed (30)
    # after the buffer. WHY: sizing to demand must never under-fetch below a single
    # user's complete 30-slot feed.
    assert sum(target.values()) >= math.ceil(30 * buffer)
