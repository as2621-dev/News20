"""Feed-category taxonomy — the single source of truth for the 10 screen buckets.

Phase SP3 (taxonomy unification on the picker tree). The onboarding picker, the
"Build your 30, in order" screen, and the reel chip all draw the **same canonical
category set = the 8 onboarding picker roots** + the 2 source axes:

    AI · Geopolitics · Business · Environment ·
    Politics · Tech · Sport · Arts · YouTube · X

The 8 roots are the depth-0 ids of ``src/lib/pickerSeedTree.ts`` (the single source
of truth). There is **no folding**: ``ai`` stays ``ai`` (NOT ``tech_science``);
``geopolitics``/``politics``/``environment`` are three distinct roots (NOT a single
``world_politics``); ``arts`` replaces the old ``culture`` catch-all. The earlier
"5 topic categories" fold (``world_politics, tech_science, markets, sport,
culture``) is retired.

This module mirrors that taxonomy for the Python ranking/allocation path:

  - ``FeedCategory`` — the 10 keys (8 topic roots + ``youtube``/``x``), mirroring the
    Postgres ``feed_category`` enum (SP3 migration 0020 adds the 8 roots additively).
    A story is bucketed into exactly one of these for clean 30-slot accounting. Old
    folded enum values (``world_politics`` etc.) are retained-unused in Postgres for
    reversibility; the Python taxonomy no longer emits them.
  - ``SLUG_TO_CATEGORY`` — each root maps to itself, and every known subcategory root
    / legacy alias maps UP to its picker root (no cross-fold).
  - ``category_for_slug`` — the best-fit lookup: resolve any interest slug
    (leaf/parent/grandparent, dotted or depth-0) to its screen category via its root.
  - ``CategoryAllocation`` — one ``user_feed_allocation`` row (the per-category
    slot budget + manual sequence) the loader hydrates and the SP3 allocator reads.

It is intentionally tiny and **pure** (no DB, no clock, no network) — the
classifier and allocator both import the map from here rather than re-deriving it.

``youtube``/``x`` are *source-axis* categories that no interest slug maps to
(empty until source ingestion delivers a followed reel). So every story still
classifies into one of the eight **topic** roots below.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Reason: the 10 keys every category surface draws — the 8 onboarding picker roots
# (src/lib/pickerSeedTree.ts depth-0 ids) plus the 2 source axes. Mirrors the in-use
# values of the Postgres ``feed_category`` enum (SP3 migration 0020); old folded
# values are retained-unused in Postgres but never emitted here. A Literal (not a
# str) so a typo at a call boundary is a type error, not a silent miss.
FeedCategory = Literal[
    "ai",
    "geopolitics",
    "business",
    "environment",
    "politics",
    "tech",
    "sport",
    "arts",
    "youtube",
    "x",
]

# Reason: the 8 topic roots an interest slug can classify into. EXCLUDES the
# source-axis ``youtube``/``x`` (no slug maps to them — empty until a followed
# source reel exists). Used to seed empty buckets so ``score_and_classify_for_user``
# always returns all 10 keys.
TOPIC_CATEGORIES: tuple[FeedCategory, ...] = (
    "ai",
    "geopolitics",
    "business",
    "environment",
    "politics",
    "tech",
    "sport",
    "arts",
)

# Reason: the source-axis categories — budgeted on the screen but fed by source
# ingestion (followed YouTube channels / X handles), not interest slugs. Zero items
# until a user follows a source; their budgeted slots soft-roll into the topic
# categories by sequence. Listed so the 10-key bucket dict is complete.
SOURCE_CATEGORIES: tuple[FeedCategory, ...] = ("youtube", "x")

# Reason: the slug → picker-root map. Each of the 8 roots maps to itself; every
# known subcategory root and every legacy alias the old map carried maps UP to its
# picker root with NO cross-fold. Subcategories arrive as dotted slugs (e.g.
# ``sport.cricket.india``) and resolve via ``category_for_slug`` on the root segment,
# so they do not each need an entry here — only depth-0 roots + legacy aliases do.
# The four legacy aliases that are *ambiguous* between two new roots are remapped
# deterministically (see the inline ``# Reason:`` on each).
SLUG_TO_CATEGORY: dict[str, FeedCategory] = {
    # The 8 picker roots — identity (no fold).
    "ai": "ai",
    "geopolitics": "geopolitics",
    "business": "business",
    "environment": "environment",
    "politics": "politics",
    "tech": "tech",
    "sport": "sport",
    "arts": "arts",
    # Legacy / alias slugs the old taxonomy carried, remapped to the new roots.
    # Reason: old ``world`` (the world_politics root) now lands on geopolitics — the
    # closest of the three split roots (geopolitics/politics/environment).
    "world": "geopolitics",
    # Reason: ``climate`` is environment's own concern now (was folded into
    # world_politics); environment is a first-class root.
    "climate": "environment",
    # Reason: ``science`` has no dedicated root; tech is its nearest picker root
    # (the picker keeps space/semiconductors/etc. under tech).
    "science": "tech",
    # Reason: ``crypto`` lives under the picker's business → "Crypto & fintech" sub.
    "crypto": "business",
    # Reason: ``markets`` is the picker's business → "Markets & investing" sub
    # (the old ``markets`` fold root collapses into business).
    "markets": "business",
    # Reason: ``entertainment`` maps to arts (the old culture catch-all → arts).
    "entertainment": "arts",
    # Reason: ``lifestyle`` has no dedicated root; arts is the long-tail catch-all.
    "lifestyle": "arts",
    # Reason: ``wildcard`` was the culture/long-tail accent; arts inherits it.
    "wildcard": "arts",
    # Reason: ``health`` has no dedicated root; tech is its nearest picker root
    # (mirrors the old ``health`` → tech_science choice, now un-folded to tech).
    "health": "tech",
}

# Reason: best-fit fallback when a slug's root is not in the map — Arts is the
# long-tail catch-all (it replaces the old ``culture`` catch-all). Keeps every story
# classifiable into exactly one topic root (no gap, no crash).
DEFAULT_CATEGORY: FeedCategory = "arts"

# Reason: the default "Build your 30" allocation a user inherits until they
# customize it. Owner-locked split (2026-06-18) across the 10 categories — kept in
# sync with the frontend ``src/lib/feedBuckets.ts`` ``DEFAULT_ALLOCATION_SEGMENTS``
# (Rule 7 twins: if one changes, change both). Sums to 30:
# ai 4 + tech 4 + geopolitics 4 + business 4 + politics 2 + environment 2 +
# sport 3 + arts 3 + youtube 2 + x 2 = 30.
DEFAULT_FEED_ALLOCATION: dict[FeedCategory, int] = {
    "ai": 4,
    "tech": 4,
    "geopolitics": 4,
    "business": 4,
    "politics": 2,
    "environment": 2,
    "sport": 3,
    "arts": 3,
    "youtube": 2,
    "x": 2,
}


# Reason: per-category minimum unique-story floor for the shared-pool target so a
# live (allocated) category is never starved below a usable depth even when no user
# demands much of it (reference/shared-pool-pipeline.md §2A). The 8 TOPIC roots get a
# small uniform floor of 3; the 2 SOURCE categories (``youtube``/``x``) get 0 because
# they are follow-gated — a story only exists there if the user follows a YouTube
# channel / X handle, so there is nothing to floor-ingest. M2 applies this floor
# after max-over-users × pool_buffer; the values themselves are placeholders tuned
# in M6 (per the master plan open questions).
CATEGORY_FLOOR: dict[FeedCategory, int] = {
    "ai": 3,
    "geopolitics": 3,
    "business": 3,
    "environment": 3,
    "politics": 3,
    "tech": 3,
    "sport": 3,
    "arts": 3,
    "youtube": 0,
    "x": 0,
}


def category_for_slug(interest_slug: str) -> FeedCategory:
    """Resolve an interest slug to its screen ``FeedCategory`` (best-fit).

    Interest slugs are dotted paths rooted at a depth-0 picker root, e.g.
    ``sport.cricket.india`` or ``business.equities.semis``. The screen category is
    fixed by the **root** segment (the depth-0 slug), so a leaf, its parent, and
    its grandparent all classify into the same root. A slug whose root is not in
    :data:`SLUG_TO_CATEGORY` (or an empty slug) falls back to
    :data:`DEFAULT_CATEGORY` so every story is always classifiable.

    Args:
        interest_slug: A taxonomy slug (``'sport'``, ``'sport.cricket.india'``,
            ``'business.equities'`` ...).

    Returns:
        The picker root for that slug's root segment.

    Example:
        >>> category_for_slug("sport.cricket.india")
        'sport'
        >>> category_for_slug("business.equities.semis")
        'business'
        >>> category_for_slug("ai.interpretability")
        'ai'
        >>> category_for_slug("unknown.thing")
        'arts'
    """
    root_slug = interest_slug.split(".", 1)[0] if interest_slug else ""
    return SLUG_TO_CATEGORY.get(root_slug, DEFAULT_CATEGORY)


def root_interest_slug_for_category(category: FeedCategory) -> str | None:
    """Resolve a ``FeedCategory`` to the depth-0 ROOT interest slug it tags onto.

    The stable category→root-interest contract M2's ingest-time tagging (SP3) uses:
    to tag a story's category onto a real interest node, emit a depth-0
    ``story_interests`` tag on the interest whose ``interest_slug`` this returns.
    Migration ``0023_root_interest_nodes.sql`` mints exactly one depth-0 interest
    per **topic** root, with ``interest_slug`` equal to the category key — so for the
    8 topic roots this is the identity (``ai`` → ``"ai"`` …), and it is the inverse
    of :func:`category_for_slug` on a bare root slug (``category_for_slug(root) ==
    category`` and ``root_interest_slug_for_category(category) == root``).

    The 2 **source-axis** categories (``youtube``/``x``) have NO interest node — no
    interest slug maps to them (they are follow-gated) and ``0023`` mints no root for
    them — so this returns ``None`` for those: there is nothing to tag.

    Args:
        category: One of the 10 :data:`FeedCategory` keys.

    Returns:
        The depth-0 root interest slug for a topic category (== the category key), or
        ``None`` for the source-axis categories ``youtube``/``x`` (no interest node).

    Example:
        >>> root_interest_slug_for_category("ai")
        'ai'
        >>> root_interest_slug_for_category("business")
        'business'
        >>> root_interest_slug_for_category("youtube") is None
        True
    """
    return category if category in TOPIC_CATEGORIES else None


class ProfileInterestRow(BaseModel):
    """The collapse-relevant fields of one ``user_interest_profile`` row.

    The pure input/output shape of :func:`collapse_profile_rows_to_roots` — the
    in-memory mirror of the SQL migration's per-row repoint (``0024``). Carries only
    what the collapse needs: which user the row belongs to, the ``interest_slug`` of
    the interest it currently points at (deep or root), the ranking weight, and the
    source. The DB ``profile_interest_id`` UUID is intentionally NOT modeled here —
    this transform decides the destination by SLUG (``category_for_slug``); the
    UUID-FK repoint is the SQL twin's job (SP3). Frozen so a collapsed row is a fresh
    value, never an in-place mutation of the input.

    Attributes:
        profile_user_id: The user the row belongs to (collapse dedups per user).
        interest_slug: The dotted slug of the interest this row points at
            (``'sport.soccer.epl'`` deep, or ``'sport'`` already-root).
        profile_weight: The ranking weight (the dedup keeps the HIGHER on collision).
        profile_source: Where the pick came from (carried through unchanged).
    """

    model_config = {"frozen": True}

    profile_user_id: str = Field(..., description="The user the row belongs to")
    interest_slug: str = Field(
        ..., description="The slug of the interest this row points at"
    )
    profile_weight: float = Field(
        ..., description="Ranking weight (max-wins on collapse-dedup)"
    )
    profile_source: str = Field(
        ..., description="Pick source, carried through unchanged"
    )


def collapse_profile_rows_to_roots(
    rows: list[ProfileInterestRow],
) -> list[ProfileInterestRow]:
    """Collapse deep ``user_interest_profile`` rows to their depth-0 root interest.

    The pure, idempotent transform M5 defines once and the SQL migration ``0024``
    mirrors (Rule 7 — author the rule here, twin it in SQL; SP4 asserts parity). For
    each input row, the destination root is its slug's screen category via
    :func:`category_for_slug` (so ``sport.soccer.epl`` → ``sport``,
    ``business.equities.semis`` → ``business``); the collapsed row points at the
    depth-0 root interest whose ``interest_slug`` equals that category key
    (:func:`root_interest_slug_for_category`, always defined for the 8 topic roots
    ``category_for_slug`` returns). Rows are then deduped per ``(profile_user_id,
    root_slug)`` keeping the **higher** ``profile_weight`` (NOT summed/averaged), with
    ``profile_source`` carried from the kept (max-weight) row.

    Properties (encoded by ``tests/agents/pipeline/test_interest_collapse.py``):
      * **deep → root** — every slug maps to its root segment's category.
      * **dedup on conflict** — two deep rows of one user collapsing to the same root
        yield ONE row with the max weight; the lower-weight row is dropped.
      * **idempotent** — feeding the function its own output is a fixed point (a row
        already at its root maps to itself; no new dupes).
      * **root unchanged** — an already-root row (``sport``) stays ``sport``.
      * **unknown root** — a slug whose root is unknown falls back per
        ``category_for_slug`` (→ ``arts``) without crashing.

    Pure: no DB, no clock, no network (mirrors the rest of this module). Output order
    is deterministic — first-seen ``(user, root)`` order of the input.

    Args:
        rows: The collapse-relevant fields of a user's (or many users') profile rows.

    Returns:
        The collapsed rows: one per ``(profile_user_id, root_slug)``, each pointing at
        a depth-0 root slug, max-weight on any collision.

    Example:
        >>> collapsed = collapse_profile_rows_to_roots([
        ...     ProfileInterestRow(profile_user_id="u1", interest_slug="sport.soccer",
        ...                        profile_weight=1.0, profile_source="typed"),
        ...     ProfileInterestRow(profile_user_id="u1", interest_slug="sport.cricket",
        ...                        profile_weight=3.0, profile_source="signal"),
        ... ])
        >>> [(row.interest_slug, row.profile_weight) for row in collapsed]
        [('sport', 3.0)]
    """
    # Reason: keep first-seen (user, root) order deterministic — dict preserves
    # insertion order, so the output mirrors the input's first appearance of each key.
    collapsed: dict[tuple[str, str], ProfileInterestRow] = {}
    for row in rows:
        # The root slug == the category key for a topic root (always non-None: the 8
        # topic roots are the entire codomain of category_for_slug). The `or` guard is
        # defensive — category_for_slug never returns a source-axis category from a
        # slug, so root_interest_slug_for_category never returns None here.
        root_slug = (
            root_interest_slug_for_category(category_for_slug(row.interest_slug))
            or DEFAULT_CATEGORY
        )
        key = (row.profile_user_id, root_slug)
        collapsed_row = ProfileInterestRow(
            profile_user_id=row.profile_user_id,
            interest_slug=root_slug,
            profile_weight=row.profile_weight,
            profile_source=row.profile_source,
        )
        existing = collapsed.get(key)
        # Max-weight wins (NOT sum/avg): keep the higher-weight row, source and all.
        if existing is None or collapsed_row.profile_weight > existing.profile_weight:
            collapsed[key] = collapsed_row
    return list(collapsed.values())


def empty_category_buckets() -> dict[FeedCategory, list]:
    """Return all 10 ``FeedCategory`` keys mapped to fresh empty lists.

    The classifier seeds its output with this so ``score_and_classify_for_user``
    always returns a complete 10-key dict (source categories present-but-empty) —
    the allocator can read every budgeted category without a ``KeyError``.

    Returns:
        ``{feed_category: []}`` for all 10 keys, in enum order.
    """
    return {
        "ai": [],
        "geopolitics": [],
        "business": [],
        "environment": [],
        "politics": [],
        "tech": [],
        "sport": [],
        "arts": [],
        "youtube": [],
        "x": [],
    }


class CategoryAllocation(BaseModel):
    """One ``user_feed_allocation`` row — a per-category slot budget + sequence.

    The Layer-1 control surface (phase-5a): the user sets, per screen category,
    how many of their 30 slots it gets (``allocation_slot_count``) and where it
    sits in the manual sequence (``allocation_sort_order``). The allocator reads
    these to fill each category's slots from the entity-aware scored candidates, in
    the user's order. The DB does NOT enforce the cross-category
    ``SUM(slot_count) == 30`` invariant (a per-row CHECK can't see siblings) — the
    writer (UI/seed) + the allocator's roll-over logic own it.

    Attributes:
        allocation_category: Which of the 10 screen categories this budget is for.
        allocation_slot_count: How many feed slots the user gave this category
            (0..30; 0 means "don't show me this category").
        allocation_sort_order: The category's position in the user's manual
            sequence (lower = earlier; not unique).

    Example:
        >>> alloc = CategoryAllocation(
        ...     allocation_category="business",
        ...     allocation_slot_count=4,
        ...     allocation_sort_order=2,
        ... )
        >>> alloc.allocation_category
        'business'
    """

    allocation_category: FeedCategory = Field(
        ..., description="Which of the 10 screen categories this budget is for"
    )
    allocation_slot_count: int = Field(
        ..., ge=0, le=30, description="Feed slots the user gave this category (0..30)"
    )
    allocation_sort_order: int = Field(
        ..., description="Position in the user's manual sequence (lower = earlier)"
    )
