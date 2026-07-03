"""Niche-section allocator (FSR slice #6): turn a user's deep micro-interest profile
into named ``user_feed_allocation`` sections — in their own vocabulary — plus a
"beyond your bubble" reserve and the untouched followed-source (youtube/x) slots.

The FSR revamp (``plans/prd.md`` Technical Foundation Decisions #6/#7) organizes a
user's 30 by their *niches*, not generic categories. This module is the ALLOCATION
half (Layer 1): given the interview's terminal micro-interests (persisted by slice
#2 as ``user_interest_profile`` rows carrying a per-user ``profile_display_label``,
each pointing at an ``interests`` node), it builds the section plan:

    build_niche_allocation(profile, nodes, source_budgets) → [NicheAllocationRow]

Each row maps 1:1 to a ``user_feed_allocation`` row (migration 0026 shape). Slice #7
(fallback-ladder assembly) consumes these rows to fill each section niche-first; slice
#8 renders the section headers. The row shape is the CONTRACT with those slices:

  - **Niche section** — ``allocation_interest_id`` set (the node), ``allocation_section_label``
    = the user's words; ``allocation_category`` = the node's screen root (for backbone
    accounting). Multiple sections can share a category (three ``sport`` niches).
  - **Beyond-bubble reserve** — 3–5 rows, ``allocation_interest_id`` NULL,
    ``allocation_section_label`` = :data:`BEYOND_BUBBLE_LABEL`, ``allocation_category`` =
    a root the user did NOT light up on (one slot per root — slice #7 fills each from that
    root's importance-ranked backbone; per-root spread is the serendipity, no new ranking
    machinery — Decision #7). Degenerate "lit up ALL roots" case documented below.

CONTRACT NOTE for slices #7/#8. A beyond-bubble row and a coarse/roots-only backbone row
BOTH carry a NULL interest ref + a topic category; they are told apart ONLY by the
``allocation_section_label`` (:data:`BEYOND_BUBBLE_LABEL` vs NULL). So: (a) every coarse
writer MUST leave the label NULL, and (b) #7/#8 MUST import :data:`BEYOND_BUBBLE_LABEL`
rather than re-hardcode the string.
  - **Source slot** — ``allocation_interest_id`` NULL, ``allocation_section_label`` NULL,
    ``allocation_category`` ∈ ``youtube``/``x``; carried through EXACTLY as today so the
    revamp does not disturb followed-source lead slots (Decision, PRD story #16).

ROOTS-ONLY / LEGACY (FSR baseline). A profile with NO deep (depth ≥ 1) interest — the
skip-everything path or a pre-revamp collapsed profile — is returned the coarse
per-category allocation EXACTLY as today (NULL interest ref, NULL label, no beyond-bubble
reserve): "a root is just a depth-0 section" (Decision #8). This is the highest-value
regression guard — a roots-only user's feed must not change.

Pure: no DB, no clock, no network (mirrors ``agents.pipeline.demand`` /
``feed_assembly.assemble_user_feed``). The persister
(:func:`write_user_niche_allocation`) is the only DB-touching function and injects its
client so the test suite mocks at the boundary (CLAUDE.md mandate).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import InterestNode
from agents.pipeline.categories import (
    DEFAULT_FEED_ALLOCATION,
    SOURCE_CATEGORIES,
    TOPIC_CATEGORIES,
    FeedCategory,
    category_for_slug,
)
from agents.shared.logger import get_logger

logger = get_logger("pipeline.niche_allocation")

# Reason: the feed budget (phase-5a) — N = 30. Niche sections + beyond-bubble + source
# slots sum to at most this; the caller can shrink it for tests.
FEED_SLOT_BUDGET = 30

# Reason: the "beyond your bubble" reserve band (Decision #7 — "3–5 slots"). We target
# BEYOND_BUBBLE_MAX distinct roots at one slot each, but never fewer than
# BEYOND_BUBBLE_MIN, so the reserve is always in the promised 3–5 band (8 topic roots
# always leave enough to draw from, even in the all-roots-lit degenerate case).
BEYOND_BUBBLE_MIN = 3
BEYOND_BUBBLE_MAX = 5

# Reason: the reserved section label every beyond-bubble row carries — how slice #7/#8
# tell a beyond-bubble reserve row (NULL interest ref) apart from a coarse/roots-only
# backbone row (NULL interest ref, NULL label). A single source of truth for the string.
BEYOND_BUBBLE_LABEL = "Beyond your bubble"


class ProfileInterestForAllocation(BaseModel):
    """One deep-profile row joined to its taxonomy node — the allocator's per-user input.

    Assembled by the loader (daily batch / demo script) from a ``user_interest_profile``
    row ⋈ its ``interests`` node: the node id + slug (for the screen category and depth),
    the per-user display label (slice #2's ``profile_display_label``), and the ranking
    weight (drives how many slots the section gets).

    Attributes:
        allocation_profile_interest_id: The ``interests.interest_id`` the user follows
            (== ``profile_interest_id``); the niche row's ``allocation_interest_id``.
        allocation_interest_slug: The node's dotted slug (``sport.cricket.ipl``); fixes
            the screen category (root segment) and the depth (segment count).
        allocation_display_label: The user's own vocabulary for this interest (section
            header). None for legacy/roots-only rows minted before slice #2.
        allocation_profile_weight: The ranking weight — deeper/stronger picks weigh more,
            so they get proportionally more niche slots.
    """

    allocation_profile_interest_id: str = Field(
        ..., description="interests.interest_id the user follows (the niche node)"
    )
    allocation_interest_slug: str = Field(
        ..., description="Dotted slug of the node (fixes category + depth)"
    )
    allocation_display_label: str | None = Field(
        default=None, description="User's own vocabulary for this interest (section header)"
    )
    allocation_profile_weight: float = Field(
        default=1.0, ge=0.0, description="Ranking weight (drives niche slot share)"
    )


class NicheAllocationRow(BaseModel):
    """One planned ``user_feed_allocation`` row (migration 0026 shape) — the #7/#8 contract.

    Attributes:
        allocation_category: The screen category for backbone accounting (one of the 10
            ``FeedCategory`` keys). A niche row uses the node's root; a source row uses
            ``youtube``/``x``; a beyond-bubble row uses an un-lit root.
        allocation_interest_id: The niche node this section is named for, or None for a
            coarse/beyond-bubble/source row.
        allocation_section_label: The user's vocabulary (niche), :data:`BEYOND_BUBBLE_LABEL`
            (beyond-bubble), or None (coarse/source).
        allocation_slot_count: How many of the 30 slots this section gets (``>= 0``).
        allocation_sort_order: Position in the user's feed sequence (lower = earlier):
            source slots lead, then niche sections (weight desc), then beyond-bubble.

    Example:
        >>> row = NicheAllocationRow(
        ...     allocation_category="sport", allocation_interest_id="int-ipl",
        ...     allocation_section_label="IPL", allocation_slot_count=4,
        ...     allocation_sort_order=2,
        ... )
        >>> row.allocation_section_label
        'IPL'
    """

    allocation_category: FeedCategory = Field(
        ..., description="Screen category for backbone accounting (one of the 10 keys)"
    )
    allocation_interest_id: str | None = Field(
        default=None, description="The niche node this section is named for, or None"
    )
    allocation_section_label: str | None = Field(
        default=None, description="User vocabulary / 'Beyond your bubble' / None"
    )
    allocation_slot_count: int = Field(
        ..., ge=0, description="Feed slots this section gets (0..30)"
    )
    allocation_sort_order: int = Field(
        ..., description="Feed sequence position (lower = earlier)"
    )


def _slug_depth(interest_slug: str) -> int:
    """Absolute taxonomy depth of a slug (segment count - 1; ``sport`` = 0)."""
    return len(interest_slug.split(".")) - 1 if interest_slug else 0


def _largest_remainder_split(
    total_budget: int, weights: list[float]
) -> list[int]:
    """Apportion ``total_budget`` across ``weights`` (largest-remainder), floor 1 each.

    Deterministic (Rule 5 — code answers): each entry gets at least 1 slot, the rest is
    split proportional to weight with the leftover handed to the largest fractional
    remainders (ties broken by original order). Assumes ``0 < len(weights) <=
    total_budget`` — the caller guarantees the floor fits.

    Args:
        total_budget: The slot budget to distribute (``>= len(weights)``).
        weights: The per-section ranking weights (non-empty; parallel to the output).

    Returns:
        A slot count per weight, each ``>= 1``, summing exactly to ``total_budget``.

    Example:
        >>> _largest_remainder_split(6, [3.0, 1.0])
        [4, 2]
    """
    section_count = len(weights)
    # Floor 1 each; distribute the surplus proportional to weight.
    surplus = total_budget - section_count
    weight_total = sum(weights)
    if weight_total <= 0:
        # All-zero weights → even split of the surplus, remainder to earliest.
        base, remainder = divmod(surplus, section_count)
        return [1 + base + (1 if index < remainder else 0) for index in range(section_count)]

    exact_shares = [surplus * (weight / weight_total) for weight in weights]
    floored = [int(share) for share in exact_shares]
    leftover = surplus - sum(floored)
    # Hand the leftover to the largest fractional remainders (order-stable tiebreak).
    remainder_order = sorted(
        range(section_count),
        key=lambda index: (exact_shares[index] - floored[index], -index),
        reverse=True,
    )
    for index in remainder_order[:leftover]:
        floored[index] += 1
    return [1 + extra for extra in floored]


def _source_rows(
    source_budgets: dict[FeedCategory, int],
    start_sort_order: int,
) -> tuple[list[NicheAllocationRow], int, int]:
    """Emit the followed-source (youtube/x) slots, carried through unchanged.

    Source rows lead the feed sequence and carry no interest ref and no label — the
    revamp does not disturb followed-source lead slots (Decision, PRD story #16). Shared
    by both the coarse baseline and the deep-profile path so the source shape is authored
    once.

    Args:
        source_budgets: ``{youtube|x: slot_count}`` to carry through.
        start_sort_order: The first ``allocation_sort_order`` to assign.

    Returns:
        ``(rows, next_sort_order, source_total)`` — the emitted source rows, the next free
        sort order, and the total source slots.
    """
    rows: list[NicheAllocationRow] = []
    sort_order = start_sort_order
    source_total = 0
    for source_category in SOURCE_CATEGORIES:
        budget = source_budgets.get(source_category, 0)
        if budget <= 0:
            continue
        rows.append(
            NicheAllocationRow(
                allocation_category=source_category,
                allocation_slot_count=budget,
                allocation_sort_order=sort_order,
            )
        )
        source_total += budget
        sort_order += 1
    return rows, sort_order, source_total


def _coarse_allocation_rows(
    lit_roots_in_order: list[FeedCategory],
    source_budgets: dict[FeedCategory, int],
) -> list[NicheAllocationRow]:
    """Build today's coarse per-category allocation (the roots-only / FSR baseline).

    Every lit topic root becomes a depth-0 section (NULL interest ref, NULL label) with
    its :data:`DEFAULT_FEED_ALLOCATION` slot budget; source categories carry their budget
    unchanged. NO beyond-bubble reserve — this path is byte-for-byte "the feed works
    exactly as today" (Decision #8). Sort order: source slots lead, then topic roots in
    the given order.

    Args:
        lit_roots_in_order: The topic roots the user lit up (depth-0 picks), ordered.
        source_budgets: ``{youtube|x: slot_count}`` to carry through unchanged.

    Returns:
        Coarse ``NicheAllocationRow``s (all with NULL interest ref + NULL label).
    """
    rows, sort_order, _ = _source_rows(source_budgets, start_sort_order=0)
    for root in lit_roots_in_order:
        rows.append(
            NicheAllocationRow(
                allocation_category=root,
                allocation_slot_count=DEFAULT_FEED_ALLOCATION.get(root, 0),
                allocation_sort_order=sort_order,
            )
        )
        sort_order += 1
    return rows


def _beyond_bubble_roots(
    lit_roots: set[FeedCategory],
    weight_by_root: dict[FeedCategory, float],
) -> list[FeedCategory]:
    """Pick the 3–5 roots the beyond-bubble reserve draws backbone stories from.

    Serendipity source (Decision #7 — "roots the user did NOT light up on"):

      1. **Primary — un-lit roots**, in :data:`TOPIC_CATEGORIES` order (deterministic).
      2. **Degenerate fallback — least-engaged lit roots.** When the user lit up ALL 8
         roots (or so many that fewer than :data:`BEYOND_BUBBLE_MIN` remain un-lit), the
         reserve is topped up from the LEAST-engaged lit roots — those with the lowest
         summed profile weight (ties broken by ``TOPIC_CATEGORIES`` order). This is the
         cheapest defensible "still give me serendipity" rule: it reuses the backbone and
         adds no ranking machinery, and it degrades gracefully (the roots the user cares
         least about are the closest thing to "outside the bubble" when nothing is un-lit).

    The reserve size is ``min(BEYOND_BUBBLE_MAX, max(BEYOND_BUBBLE_MIN, len(un_lit)))``
    clamped to the 8 topic roots — always in the promised 3–5 band.

    Args:
        lit_roots: The topic roots the user lit up (any depth).
        weight_by_root: ``{root: summed profile weight}`` for the least-engaged tiebreak.

    Returns:
        The 3–5 roots to reserve, un-lit first then least-engaged lit.
    """
    un_lit = [root for root in TOPIC_CATEGORIES if root not in lit_roots]
    least_engaged_lit = sorted(
        (root for root in TOPIC_CATEGORIES if root in lit_roots),
        key=lambda root: (weight_by_root.get(root, 0.0), TOPIC_CATEGORIES.index(root)),
    )
    candidates = un_lit + least_engaged_lit
    reserve_size = min(BEYOND_BUBBLE_MAX, max(BEYOND_BUBBLE_MIN, len(un_lit)))
    reserve_size = min(reserve_size, len(candidates))
    return candidates[:reserve_size]


def build_niche_allocation(
    profile_interests: list[ProfileInterestForAllocation],
    interest_nodes: dict[str, InterestNode],
    source_budgets: dict[FeedCategory, int] | None = None,
    feed_slot_budget: int = FEED_SLOT_BUDGET,
) -> list[NicheAllocationRow]:
    """Build a user's ``user_feed_allocation`` section plan from their deep profile.

    Pure over its inputs. See the module docstring for the row taxonomy (niche /
    beyond-bubble / source) and the roots-only baseline.

    Algorithm (deep-profile path):
      1. Validate every profile interest against ``interest_nodes`` — a row referencing a
         deleted/unknown node fails LOUD (Rule 12) with a ``fix_suggestion``, never a
         silent unlabeled section (backing-gate rule, commit 362741b).
      2. Source slots (youtube/x) are carried through unchanged, leading the sequence.
      3. Beyond-bubble reserve: 3–5 slots from un-lit roots (:func:`_beyond_bubble_roots`).
      4. The remaining budget is split across the niche sections by profile weight
         (largest-remainder, floor 1); if there are more sections than remaining slots,
         only the top-weighted sections that fit are kept.

    Args:
        profile_interests: The user's deep-profile rows ⋈ nodes. Empty → empty allocation.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup for validation.
        source_budgets: ``{youtube|x: slot_count}``; defaults to the youtube/x slice of
            :data:`DEFAULT_FEED_ALLOCATION` (carried through exactly as today).
        feed_slot_budget: ``N`` — total feed slots (30).

    Returns:
        The ordered ``NicheAllocationRow``s (sort_order 0..). Empty when the profile is
        empty. Roots-only profiles return the coarse baseline (no niche/beyond-bubble).

    Raises:
        ValueError: When a profile interest references a node absent from
            ``interest_nodes`` (deleted/unknown) — fail loud, never a blank section.
    """
    if source_budgets is None:
        source_budgets = {
            category: DEFAULT_FEED_ALLOCATION.get(category, 0)
            for category in SOURCE_CATEGORIES
        }

    if not profile_interests:
        logger.info(
            "build_niche_allocation_empty_profile",
            fix_suggestion="User has no profile interests; allocator returns no rows "
            "(caller writes no allocation).",
        )
        return []

    # ── 1. Validate every node reference (fail loud — backing-gate 362741b) ──
    for interest in profile_interests:
        if interest.allocation_profile_interest_id not in interest_nodes:
            logger.error(
                "build_niche_allocation_unknown_interest_node",
                interest_id=interest.allocation_profile_interest_id,
                interest_slug=interest.allocation_interest_slug,
                fix_suggestion="A user_interest_profile row references an interest node "
                "not in the taxonomy lookup (deleted or unminted). Re-run the interview "
                "for this user or prune the stale profile row; never allocate a section "
                "with no backing interest.",
            )
            raise ValueError(
                f"build_niche_allocation: profile references unknown interest node "
                f"'{interest.allocation_profile_interest_id}' "
                f"(slug '{interest.allocation_interest_slug}'). "
                "fix_suggestion: re-run the interview or prune the stale profile row."
            )

    # Lit roots + summed weight per root (for the beyond-bubble least-engaged tiebreak).
    lit_roots_in_order: list[FeedCategory] = []
    weight_by_root: dict[FeedCategory, float] = {}
    for interest in profile_interests:
        root = category_for_slug(interest.allocation_interest_slug)
        weight_by_root[root] = weight_by_root.get(root, 0.0) + interest.allocation_profile_weight
        if root not in lit_roots_in_order:
            lit_roots_in_order.append(root)
    lit_roots = set(lit_roots_in_order)

    # ── Roots-only / legacy baseline: NO deep interest → today's coarse allocation ──
    deep_interests = [
        interest
        for interest in profile_interests
        if _slug_depth(interest.allocation_interest_slug) >= 1
    ]
    if not deep_interests:
        rows = _coarse_allocation_rows(lit_roots_in_order, source_budgets)
        logger.info(
            "build_niche_allocation_roots_only",
            lit_roots=len(lit_roots_in_order),
            rows=len(rows),
            fix_suggestion="Roots-only profile → coarse baseline (feed unchanged from today).",
        )
        return rows

    # ── 2. Source slots lead (carried through unchanged) ──
    rows, sort_order, source_total = _source_rows(source_budgets, start_sort_order=0)

    # ── 3. Beyond-bubble reserve (3–5 slots, un-lit roots first), clamped to budget ──
    # Reason: never let source + beyond overshoot the feed budget (the budget is a
    # caller-shrinkable knob). With production defaults (source 4, beyond ≤5, N=30) the
    # clamp is a no-op; on a shrunk budget it truncates the reserve so total ≤ N holds.
    beyond_roots = _beyond_bubble_roots(lit_roots, weight_by_root)
    beyond_room = max(feed_slot_budget - source_total, 0)
    beyond_roots = beyond_roots[:beyond_room]
    beyond_total = len(beyond_roots)  # one slot per root

    # ── 4. Niche sections get whatever remains, split by weight (floor 1) ──
    # EVERY profile interest is a section — a depth-0 pick is "just a depth-0 section"
    # (Decision #8), so a user who picked a bare root alongside deep niches still gets
    # coverage for that root (else it would be lit-but-unallocated: no niche row AND
    # excluded from beyond-bubble).
    niche_capacity = max(feed_slot_budget - source_total - beyond_total, 0)
    # Sections in weight-desc order (stable tiebreak by original order) so the strongest
    # niches lead and, when capacity is tight, survive the floor-1 fit.
    ordered_sections = sorted(
        enumerate(profile_interests),
        key=lambda pair: (-pair[1].allocation_profile_weight, pair[0]),
    )
    kept_sections = [interest for _, interest in ordered_sections[:niche_capacity]]

    niche_rows: list[NicheAllocationRow] = []
    if kept_sections:
        counts = _largest_remainder_split(
            niche_capacity, [section.allocation_profile_weight for section in kept_sections]
        )
        for section, count in zip(kept_sections, counts):
            niche_rows.append(
                NicheAllocationRow(
                    allocation_category=category_for_slug(section.allocation_interest_slug),
                    allocation_interest_id=section.allocation_profile_interest_id,
                    allocation_section_label=(
                        section.allocation_display_label
                        or interest_nodes[section.allocation_profile_interest_id].interest_label
                    ),
                    allocation_slot_count=count,
                    allocation_sort_order=sort_order,
                )
            )
            sort_order += 1
    rows.extend(niche_rows)

    # Beyond-bubble rows trail the sequence (one slot each, un-lit root, reserved label).
    for root in beyond_roots:
        rows.append(
            NicheAllocationRow(
                allocation_category=root,
                allocation_section_label=BEYOND_BUBBLE_LABEL,
                allocation_slot_count=1,
                allocation_sort_order=sort_order,
            )
        )
        sort_order += 1

    logger.info(
        "build_niche_allocation_completed",
        profile_interests=len(profile_interests),
        deep_interests=len(deep_interests),
        niche_sections=len(niche_rows),
        beyond_bubble_slots=beyond_total,
        source_slots=source_total,
        total_slots=sum(row.allocation_slot_count for row in rows),
    )
    return rows


def write_user_niche_allocation(
    supabase_client: Any,
    user_id: str,
    rows: list[NicheAllocationRow],
) -> int:
    """Replace one user's ``user_feed_allocation`` with a fresh niche-section plan.

    Full replace (delete-then-insert) scoped to the user so the table reflects EXACTLY
    the new plan with no stale rows — idempotent (re-running writes the same set). The
    delete + insert both run under the injected (service-role) client, so RLS is bypassed
    as in the rest of the pipeline. The insert omits ``allocation_id`` so the DB default
    (``gen_random_uuid()``) mints the surrogate PK.

    An EMPTY plan is a NO-OP (it does NOT delete): the allocator returns ``[]`` only for an
    empty profile, and the niche writer must never wipe a user's existing rows (e.g. coarse
    "Build your 30" rows) just because they have no deep interests to allocate.

    NOTE (residual, deferred to slice #7 when this is wired into the daily batch): the
    delete + insert are two round-trips, not one transaction — a crash between them would
    leave the user with zero rows until the next run. Acceptable for the current demo/
    manual-run caller; the batch wiring should move this into a transactional RPC. See
    docs/residual-review-findings/slice-6-cross-writer-allocation-coupling.md.

    Args:
        supabase_client: A service-role supabase client (injected; mocked in tests).
        user_id: The user whose allocation is being (re)written.
        rows: The planned rows (:func:`build_niche_allocation` output).

    Returns:
        The number of rows written (0 for an empty plan, which writes and deletes nothing).
    """
    if not rows:
        logger.info(
            "write_user_niche_allocation_empty",
            user_id=user_id,
            fix_suggestion="No allocation rows to write (empty profile); left existing "
            "rows untouched (never wipe a user to zero rows).",
        )
        return 0

    supabase_client.table("user_feed_allocation").delete().eq(
        "follow_user_id", user_id
    ).execute()

    payload = [
        {
            "follow_user_id": user_id,
            "allocation_category": row.allocation_category,
            "allocation_interest_id": row.allocation_interest_id,
            "allocation_section_label": row.allocation_section_label,
            "allocation_slot_count": row.allocation_slot_count,
            "allocation_sort_order": row.allocation_sort_order,
        }
        for row in rows
    ]
    supabase_client.table("user_feed_allocation").insert(payload).execute()

    logger.info(
        "write_user_niche_allocation_completed",
        user_id=user_id,
        rows_written=len(payload),
    )
    return len(payload)
