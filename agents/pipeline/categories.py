"""Feed-category taxonomy — the single source of truth for the 8 screen buckets.

Phase 5a, Sub-phase 2. The "Build your 30, in order" screen (owner, 2026-06-05)
arranges a user's feed across **8 fixed categories**, drawn as:

    Breaking News · World & Politics · Tech & Science · YouTube ·
    Markets · Sport · X · Culture

This module mirrors that taxonomy for the Python ranking/allocation path:

  - ``FeedCategory`` — the 8 keys, mirroring the Postgres ``feed_category`` enum
    (migration 0008) **verbatim and in order** (one source of truth; a story is
    bucketed into exactly one of these for clean 30-slot accounting).
  - ``SLUG_TO_CATEGORY`` — the locked map from a seeded interest slug up into its
    screen category (``reference/ranking-spec.md`` / phase-5a "slug → category").
  - ``category_for_slug`` — the best-fit lookup: resolve any interest slug
    (leaf/parent/grandparent, dotted or depth-0) to its screen category.
  - ``CategoryAllocation`` — one ``user_feed_allocation`` row (the per-category
    slot budget + manual sequence) the loader hydrates and the SP3 allocator reads.

It is intentionally tiny and **pure** (no DB, no clock, no network) — the
classifier and allocator both import the map from here rather than re-deriving it.

``breaking`` is a *tier* (top-Importance across all categories — SP3 owns that
fill), NOT a slug bucket; ``youtube``/``x`` are *source-axis* categories that no
interest slug maps to (empty until phase-5d source ingestion). So every story
still classifies into one of the five **topic** categories below.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Reason: mirrors the Postgres ``feed_category`` enum (migration 0008) verbatim,
# in enum order. The 8 keys the "Build your 30" screen draws. A Literal (not a
# str) so a typo at a call boundary is a type error, not a silent miss.
FeedCategory = Literal[
    "breaking",
    "world_politics",
    "tech_science",
    "youtube",
    "markets",
    "sport",
    "x",
    "culture",
]

# Reason: the ordered topic categories an interest slug can classify into. EXCLUDES
# ``breaking`` (a tier SP3 fills by top-Importance across all categories) and the
# source-axis ``youtube``/``x`` (no slug maps to them — empty until phase-5d). Used
# to seed empty buckets so ``score_and_classify_for_user`` always returns all 8 keys.
TOPIC_CATEGORIES: tuple[FeedCategory, ...] = (
    "world_politics",
    "tech_science",
    "markets",
    "sport",
    "culture",
)

# Reason: the source-axis categories — budgeted on the screen but fed by source
# ingestion (phase-5d), not interest slugs. Zero items today; their budgeted slots
# soft-roll into the topic categories by sequence (SP3). Listed so the 8-key bucket
# dict is complete + forward-compatible.
SOURCE_CATEGORIES: tuple[FeedCategory, ...] = ("youtube", "x")

# Reason: the locked slug → screen-category map (phase-5a, owner-confirmed
# 2026-06-05). Keys are interest *root* slugs. The three ambiguous defaults are:
# ``climate`` → world_politics, ``health`` → tech_science, ``wildcard`` → culture.
# Includes the seeded depth-0 slugs (``world``, ``tech``, ``business`` ...) AND the
# segment-accent aliases the locked map names (``geopolitics``, ``markets``,
# ``wildcard``) so the map matches the spec verbatim and stays forward-compatible
# even though no *interest* row currently carries those exact slugs.
SLUG_TO_CATEGORY: dict[str, FeedCategory] = {
    # World & Politics
    "geopolitics": "world_politics",
    "world": "world_politics",
    "climate": "world_politics",
    "politics": "world_politics",
    "environment": "world_politics",
    # Tech & Science
    "ai": "tech_science",
    "tech": "tech_science",
    "science": "tech_science",
    "health": "tech_science",
    # Markets
    "business": "markets",
    "markets": "markets",
    "crypto": "markets",
    # Sport
    "sport": "sport",
    # Culture
    "arts": "culture",
    "entertainment": "culture",
    "lifestyle": "culture",
    "wildcard": "culture",
}

# Reason: best-fit fallback when a slug's root is not in the map — Culture is the
# long-tail catch-all (mirrors the ``wildcard`` segment accent in interests.sql).
# Keeps every story classifiable into exactly one topic category (no gap, no crash).
DEFAULT_CATEGORY: FeedCategory = "culture"

# Reason: the default "Build your 30" allocation a user inherits until they
# customize it — the ONE source of truth is the frontend
# ``src/lib/feedBuckets.ts`` ``DEFAULT_ALLOCATION_SEGMENTS`` (the onboarding
# screen's pre-filled default). Mirrored here so the produce cap can treat a user
# who never built their 30 as having this exact distribution (Rule 7: keep the two
# in sync — if the TS default changes, change this too). Sums to 30.
DEFAULT_FEED_ALLOCATION: dict[FeedCategory, int] = {
    "breaking": 2,
    "world_politics": 4,
    "tech_science": 5,
    "youtube": 6,
    "markets": 4,
    "sport": 3,
    "x": 3,
    "culture": 3,
}


def category_for_slug(interest_slug: str) -> FeedCategory:
    """Resolve an interest slug to its screen ``FeedCategory`` (best-fit).

    Interest slugs are dotted paths rooted at a depth-0 category, e.g.
    ``sport.cricket.india`` or ``business.equities.semis``. The screen category is
    fixed by the **root** segment (the depth-0 slug), so a leaf, its parent, and
    its grandparent all classify into the same category. A slug whose root is not
    in :data:`SLUG_TO_CATEGORY` (or an empty slug) falls back to
    :data:`DEFAULT_CATEGORY` so every story is always classifiable.

    Args:
        interest_slug: A taxonomy slug (``'sport'``, ``'sport.cricket.india'``,
            ``'business.equities'`` ...).

    Returns:
        The owner-locked screen category for that slug's root.

    Example:
        >>> category_for_slug("sport.cricket.india")
        'sport'
        >>> category_for_slug("business.equities.semis")
        'markets'
        >>> category_for_slug("unknown.thing")
        'culture'
    """
    root_slug = interest_slug.split(".", 1)[0] if interest_slug else ""
    return SLUG_TO_CATEGORY.get(root_slug, DEFAULT_CATEGORY)


def empty_category_buckets() -> dict[FeedCategory, list]:
    """Return all 8 ``FeedCategory`` keys mapped to fresh empty lists.

    The classifier seeds its output with this so ``score_and_classify_for_user``
    always returns a complete 8-key dict (source categories present-but-empty) —
    the SP3 allocator can read every budgeted category without a ``KeyError``.

    Returns:
        ``{feed_category: []}`` for all 8 keys, in enum order.
    """
    return {
        "breaking": [],
        "world_politics": [],
        "tech_science": [],
        "youtube": [],
        "markets": [],
        "sport": [],
        "x": [],
        "culture": [],
    }


class CategoryAllocation(BaseModel):
    """One ``user_feed_allocation`` row — a per-category slot budget + sequence.

    The Layer-1 control surface (phase-5a): the user sets, per screen category,
    how many of their 30 slots it gets (``allocation_slot_count``) and where it
    sits in the manual sequence (``allocation_sort_order``). The SP3 allocator
    reads these to fill each category's slots from SP2's entity-aware scored
    candidates, in the user's order. The DB does NOT enforce the cross-category
    ``SUM(slot_count) == 30`` invariant (a per-row CHECK can't see siblings) — the
    writer (UI/seed) + the allocator's roll-over logic own it (SP1 report §7.4).

    Attributes:
        allocation_category: Which of the 8 screen categories this budget is for.
        allocation_slot_count: How many feed slots the user gave this category
            (0..30; 0 means "don't show me this category").
        allocation_sort_order: The category's position in the user's manual
            sequence (lower = earlier; not unique).

    Example:
        >>> alloc = CategoryAllocation(
        ...     allocation_category="markets",
        ...     allocation_slot_count=5,
        ...     allocation_sort_order=2,
        ... )
        >>> alloc.allocation_category
        'markets'
    """

    allocation_category: FeedCategory = Field(
        ..., description="Which of the 8 screen categories this budget is for"
    )
    allocation_slot_count: int = Field(
        ..., ge=0, le=30, description="Feed slots the user gave this category (0..30)"
    )
    allocation_sort_order: int = Field(
        ..., description="Position in the user's manual sequence (lower = earlier)"
    )
