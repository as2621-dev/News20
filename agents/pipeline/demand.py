"""Per-user (category, subcategory) demand — Stage (A) of the shared-pool pipeline.

The shared-pool rework (``reference/shared-pool-pipeline.md`` §2) sizes a single
shared story pool to *aggregate user demand* instead of ingesting per user. Stage
(A) starts here: turn one user's "Build your 30" per-category slot budget into a
**subcategory-granular** demand map, splitting each category's budget across the
subcategories that user actually follows.

This module mirrors :mod:`agents.pipeline.produce_caps` (pure helpers — no DB, no
clock, no network — fully unit-testable). M2 SP2 ships only the per-user split; SP3
adds the cross-user ``max × buffer``, floored aggregate alongside it.

Subcategory key convention (per the master plan):

  - A followed interest slug is dotted: ``markets.crypto.btc``. Its **category** is
    fixed by the root segment (``markets``) via the existing slug → category map;
    its **subcategory** is the first TWO segments (``markets.crypto``).
  - A depth-0 follow (a single-segment slug like ``sport``) has NO subcategory — it
    contributes to the category's ``"_all"`` sentinel cell, meaning "any
    subcategory in this category".
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from agents.ingestion.models import InterestNode
from agents.pipeline.categories import (
    CategoryAllocation,
    FeedCategory,
    SLUG_TO_CATEGORY,
    category_for_slug,
)
from agents.shared.logger import get_logger

logger = get_logger("pipeline.demand")

# Reason: the sentinel subcategory key for "any subcategory in this category" — used
# when a user budgets a category but follows no subcategory in it (only its root, or
# nothing), and for no-row users who inherit the flat default allocation. Downstream
# ingest (M3) treats an ``"_all"`` cell as "fill from any subcategory".
ALL_SUBCATEGORY_SENTINEL: str = "_all"


def _subcategory_key_for_slug(interest_slug: str) -> str | None:
    """Return the first-two-segment subcategory key for a slug, or None at depth 0.

    Args:
        interest_slug: A dotted taxonomy slug (``'markets.crypto.btc'``, ``'sport'``).

    Returns:
        The first two dotted segments joined (``'markets.crypto'``) for a slug with
        depth >= 1, or ``None`` for a single-segment (depth-0) slug — a depth-0
        follow has no subcategory.

    Example:
        >>> _subcategory_key_for_slug("markets.crypto.btc")
        'markets.crypto'
        >>> _subcategory_key_for_slug("sport") is None
        True
    """
    segments = interest_slug.split(".") if interest_slug else []
    if len(segments) < 2:
        return None
    return ".".join(segments[:2])


def _even_split_with_remainder(
    total_budget: int, subcategory_keys: list[str]
) -> dict[str, int]:
    """Split a budget evenly across keys, remainder to lexicographically-first keys.

    Deterministic by construction (Rule 5 — code answers, no model): every key gets
    ``total_budget // n``; the leftover ``total_budget % n`` is handed out one each
    to the lexicographically-first keys so the result is stable regardless of input
    order.

    Args:
        total_budget: The category's slot budget to distribute (``>= 0``).
        subcategory_keys: The distinct subcategory keys to split across (non-empty).

    Returns:
        ``{subcategory_key: slot_count}`` summing to ``total_budget``.

    Example:
        >>> _even_split_with_remainder(7, ["markets.stocks", "markets.crypto"])
        {'markets.crypto': 4, 'markets.stocks': 3}
    """
    ordered_keys = sorted(subcategory_keys)
    key_count = len(ordered_keys)
    base_share, remainder = divmod(total_budget, key_count)
    return {
        key: base_share + (1 if index < remainder else 0)
        for index, key in enumerate(ordered_keys)
    }


def derive_user_subcategory_demand(
    category_allocation: list[CategoryAllocation],
    followed_interest_nodes: list[InterestNode],
    default_allocation: dict[FeedCategory, int],
) -> dict[tuple[FeedCategory, str], int]:
    """Split one user's per-category slot budget across the subcategories they follow.

    The user's "Build your 30" allocation (``category_allocation``) sets a slot
    budget per screen category. This refines each category's budget to the
    subcategory granularity the shared pool is sized at:

      - **No-row user** (empty ``category_allocation``): inherits
        ``default_allocation`` flat — one ``"_all"`` cell per category holding its
        full default budget (sums to 30).
      - **For each allocation row** (category ``C``, budget ``N``):
          - Find the DISTINCT subcategories the user follows in ``C`` — a followed
            subcategory is the first two segments of any followed slug whose root
            maps to ``C`` (depth-0 follows contribute none).
          - If they follow >= 1 subcategory in ``C``: split ``N`` evenly across
            those subcategories, remainder to the lexicographically-first keys
            (deterministic).
          - If they follow none in ``C`` (only the root, or nothing): one
            ``(C, "_all"): N`` cell.

    A row whose ``allocation_category`` does not map to a valid :data:`FeedCategory`
    is skipped with a structured warning (defensive — the type system already
    constrains it, but a bad seed/DB write should fail loud, not silently miscount).

    Args:
        category_allocation: The user's ``user_feed_allocation`` rows
            (``list[CategoryAllocation]``); empty for a no-row user.
        followed_interest_nodes: The user's followed taxonomy nodes
            (``list[InterestNode]`` — SP4 resolves each
            ``UserProfileInterest.profile_interest_id`` through the
            ``{interest_id: InterestNode}`` taxonomy lookup to build this list, since
            the slug lives on ``InterestNode``, not ``UserProfileInterest``).
        default_allocation: ``{category: slot_count}`` a no-row user inherits
            (``agents.pipeline.categories.DEFAULT_FEED_ALLOCATION``).

    Returns:
        ``{(category, subcategory_key): slot_count}`` — the user's subcategory-
        granular demand. ``subcategory_key`` is a two-segment slug
        (``'markets.crypto'``) or the ``"_all"`` sentinel.

    Example:
        >>> from agents.pipeline.categories import CategoryAllocation
        >>> from agents.ingestion.models import InterestNode
        >>> alloc = [CategoryAllocation(allocation_category="markets",
        ...                             allocation_slot_count=6,
        ...                             allocation_sort_order=0)]
        >>> follows = [
        ...     InterestNode(interest_id="i1", interest_slug="markets.crypto.btc",
        ...                  interest_label="BTC", depth_level=2),
        ...     InterestNode(interest_id="i2", interest_slug="markets.stocks",
        ...                  interest_label="Stocks", depth_level=1),
        ... ]
        >>> derive_user_subcategory_demand(alloc, follows, {"markets": 4})
        {('markets', 'markets.crypto'): 3, ('markets', 'markets.stocks'): 3}
    """
    # No-row user → inherit the flat default as one "_all" cell per category.
    if not category_allocation:
        demand_default: dict[tuple[FeedCategory, str], int] = {
            (category, ALL_SUBCATEGORY_SENTINEL): slot_count
            for category, slot_count in default_allocation.items()
        }
        logger.info(
            "user_subcategory_demand_default",
            cells=len(demand_default),
            total_slots=sum(default_allocation.values()),
        )
        return demand_default

    subcategories_by_category = _followed_subcategories_by_category(
        followed_interest_nodes
    )

    demand: dict[tuple[FeedCategory, str], int] = {}
    for row in category_allocation:
        category = row.allocation_category
        # Reason: the Literal type already constrains valid values, but a bad
        # seed/DB write could carry an unknown string — fail loud (Rule 12).
        if category not in SLUG_TO_CATEGORY.values():
            logger.warning(
                "user_subcategory_demand_unknown_category",
                allocation_category=category,
                fix_suggestion="Map the allocation_category to a FeedCategory in "
                "agents.pipeline.categories.SLUG_TO_CATEGORY, or fix the offending "
                "user_feed_allocation row.",
            )
            continue

        budget = row.allocation_slot_count
        followed_here = subcategories_by_category.get(category, set())
        if followed_here:
            split = _even_split_with_remainder(budget, sorted(followed_here))
            for subcategory_key, slot_count in split.items():
                demand[(category, subcategory_key)] = slot_count
        else:
            demand[(category, ALL_SUBCATEGORY_SENTINEL)] = budget

    logger.info(
        "user_subcategory_demand_computed",
        cells=len(demand),
        rows=len(category_allocation),
        followed_nodes=len(followed_interest_nodes),
    )
    return demand


def _followed_subcategories_by_category(
    followed_interest_nodes: Iterable[InterestNode],
) -> dict[FeedCategory, set[str]]:
    """Group followed subcategory keys under their screen category.

    Resolves each followed node's slug to (category, subcategory) and collects the
    DISTINCT subcategory keys per category. Depth-0 follows (single-segment slugs)
    have no subcategory and contribute nothing here (their category gets the
    ``"_all"`` cell in the caller when no subcategory is followed).

    Args:
        followed_interest_nodes: The user's followed taxonomy nodes.

    Returns:
        ``{category: {subcategory_key, ...}}`` — only categories with >= 1 followed
        subcategory appear.

    Example:
        >>> from agents.ingestion.models import InterestNode
        >>> nodes = [InterestNode(interest_id="i1",
        ...                       interest_slug="markets.crypto.btc",
        ...                       interest_label="BTC", depth_level=2)]
        >>> _followed_subcategories_by_category(nodes)
        {'markets': {'markets.crypto'}}
    """
    grouped: dict[FeedCategory, set[str]] = {}
    for node in followed_interest_nodes:
        subcategory_key = _subcategory_key_for_slug(node.interest_slug)
        if subcategory_key is None:
            continue  # depth-0 follow → no subcategory
        category = category_for_slug(node.interest_slug)
        grouped.setdefault(category, set()).add(subcategory_key)
    return grouped


def compute_pool_target(
    allocation_by_user: dict[str, list[CategoryAllocation]],
    interest_nodes_by_user: dict[str, list[InterestNode]],
    active_user_ids: list[str],
    default_allocation: dict[FeedCategory, int],
    *,
    buffer: float,
    category_floor: dict[FeedCategory, int],
) -> dict[tuple[FeedCategory, str], int]:
    """Aggregate per-user demand into the shared-pool shopping list (Stage A).

    Folds every active user's :func:`derive_user_subcategory_demand` into one
    subcategory-granular target — the daily "shopping list" the shared pool is
    sized to (``reference/shared-pool-pipeline.md`` §2A):

      1. **Per user:** derive their ``{(category, subcategory): slots}`` demand from
         their allocation rows + followed interest nodes (a no-row user inherits
         ``default_allocation``).
      2. **Aggregate with MAX, not SUM:** ``agg[cell] = max(agg[cell], user[cell])``.
         Two users each wanting 10 geopolitics stories want *different* 10s, so the
         pool only needs the heaviest single demand per cell — not their sum.
      3. **Buffer:** ``target[cell] = ceil(agg[cell] × buffer)`` (over-fetch headroom
         for dedup/clustering loss downstream).
      4. **Category floor:** for each category ``C`` with ``category_floor[C] > 0``,
         guarantee the category's *total* target across its cells is at least the
         floor — so a live category is never starved. If the existing cells fall
         short, a single ``(C, "_all")`` floor cell is added/raised to make up the
         gap (Open Question #1: simplest correct approach — ingest treats ``"_all"``
         as "any subcategory in this category"). No existing cell is ever reduced.

    Pure: no DB, clock, network, or ``Settings`` access (the caller passes
    ``buffer`` and ``category_floor`` explicitly).

    Args:
        allocation_by_user: ``{user_id: [CategoryAllocation, ...]}`` — each active
            user's "Build your 30" rows (missing/empty ⇒ no-row default user).
        interest_nodes_by_user: ``{user_id: [InterestNode, ...]}`` — each active
            user's followed taxonomy nodes (missing ⇒ no follows).
        active_user_ids: The active user set to size the pool for.
        default_allocation: ``{category: slot_count}`` a no-row user inherits
            (``agents.pipeline.categories.DEFAULT_FEED_ALLOCATION``).
        buffer: The over-fetch multiplier (``>= 1.0``;
            ``agents.shared.settings.Settings().pool_buffer``).
        category_floor: ``{category: min_total_target}`` per-category unique-story
            floor (``agents.pipeline.categories.CATEGORY_FLOOR``); ``0`` (or absent)
            ⇒ no floor for that category.

    Returns:
        ``{(category, subcategory_key): slot_count}`` — the aggregated shopping list.
        ``subcategory_key`` is a two-segment slug (``'markets.crypto'``) or the
        ``"_all"`` sentinel.

    Example:
        >>> from agents.pipeline.categories import CategoryAllocation
        >>> alloc = {"u1": [CategoryAllocation(allocation_category="markets",
        ...                                    allocation_slot_count=4,
        ...                                    allocation_sort_order=0)]}
        >>> target = compute_pool_target(
        ...     alloc, {"u1": []}, ["u1"], {"markets": 4},
        ...     buffer=1.5, category_floor={"markets": 3},
        ... )
        >>> target[("markets", "_all")]
        6
    """
    aggregated: dict[tuple[FeedCategory, str], int] = {}
    for user_id in active_user_ids:
        user_demand = derive_user_subcategory_demand(
            allocation_by_user.get(user_id, []),
            interest_nodes_by_user.get(user_id, []),
            default_allocation,
        )
        # Reason: MAX across users (spec §2A) — two users wanting 10 of a cell want
        # *different* 10s, so the pool only needs the heaviest single demand.
        for cell, slot_count in user_demand.items():
            aggregated[cell] = max(aggregated.get(cell, 0), slot_count)

    # Buffer (over-fetch headroom), then ceil to whole stories.
    target: dict[tuple[FeedCategory, str], int] = {
        cell: math.ceil(slot_count * buffer)
        for cell, slot_count in aggregated.items()
    }

    _apply_category_floor(target, category_floor)

    logger.info(
        "pool_target_computed",
        cells=len(target),
        total_target=sum(target.values()),
        active_users=len(active_user_ids),
        buffer=buffer,
    )
    return target


def _apply_category_floor(
    target: dict[tuple[FeedCategory, str], int],
    category_floor: dict[FeedCategory, int],
) -> None:
    """Lift each category's total target to its floor via a single ``"_all"`` cell.

    Mutates ``target`` in place (Open Question #1 — simplest correct approach): for
    every category whose floor is positive, if the summed target across its current
    cells is below the floor, add/raise that category's ``(C, "_all")`` cell by the
    shortfall so the category total reaches the floor. A category with no cells at
    all gets a fresh ``(C, "_all"): floor`` cell. No existing cell is ever reduced.

    Args:
        target: The buffered ``{(category, subcategory): slots}`` map to floor
            (mutated in place).
        category_floor: ``{category: min_total_target}``; non-positive values are
            no-ops.

    Example:
        >>> t: dict = {}
        >>> _apply_category_floor(t, {"sport": 3})
        >>> t
        {('sport', '_all'): 3}
    """
    for category, floor in category_floor.items():
        if floor <= 0:
            continue
        category_total = sum(
            slot_count
            for (cell_category, _), slot_count in target.items()
            if cell_category == category
        )
        if category_total >= floor:
            continue
        shortfall = floor - category_total
        # Reason: route the floor gap to the "_all" sentinel cell (ingest reads it as
        # "any subcategory in this category") — never reduce a real demand cell.
        all_cell = (category, ALL_SUBCATEGORY_SENTINEL)
        target[all_cell] = target.get(all_cell, 0) + shortfall
