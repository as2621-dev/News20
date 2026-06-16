"""Per-category produce caps — bound how many reels each category generates.

The produce-once gate (:mod:`agents.pipeline.produce_gate`) keeps a story only if
it serves an interest and clears an importance/freshness floor, but it applies NO
per-category limit: a pool dominated by one topic (e.g. 39 markets candidates)
renders 39 markets reels and starves every other category. This module sits
between the gate and the paid render fan-out and caps each category at the
**maximum slot count any single user explicitly requested** for it (the "Build
your 30" ``user_feed_allocation`` budgets), so a batch stays category-balanced and
never produces more of a category than the most-demanding user actually wants.

Three pure helpers (no DB, no clock, no network — fully unit-testable):

  - :func:`compute_category_produce_caps` — fold the per-user allocation rows into
    ``{category: max slot_count over all users}`` (explicit rows only) plus the
    cross-user max ``breaking`` budget (a tier, not a produce-category).
  - :func:`cap_stories_per_category` — classify each gated story, keep the top-N
    by importance per category (N = its cap), drop categories nobody picked, and
    union in the top-importance stories that feed the breaking tier.
  - :func:`enforce_overall_ceiling` — an optional overall ceiling applied AFTER the
    caps, trimmed round-robin across categories so balance is preserved.

``breaking`` is a *tier* the feed allocator fills by top-Importance across all
topics (:mod:`agents.pipeline.feed_assembly`), and :func:`assign_category` never
returns it — so the breaking budget is honored here as global importance headroom
(decision: keep the top-N highest-importance stories regardless of category cap),
not as a producible bucket.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import FeedCategory
from agents.pipeline.models import ProduceDecision
from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category
from agents.shared.logger import get_logger

logger = get_logger("pipeline.produce_caps")

# Reason: ``breaking`` is a tier the allocator fills from the topic buckets, not a
# slug-mapped produce-category. Its cross-user-max budget becomes global importance
# headroom (see module docstring), so it is split out from the per-category caps.
_BREAKING_CATEGORY: FeedCategory = "breaking"


def compute_category_produce_caps(
    allocation_by_user: dict[str, list],
    active_user_ids: list[str],
    default_allocation: dict[FeedCategory, int],
) -> tuple[dict[FeedCategory, int], int]:
    """Fold per-user allocations into per-category produce caps + breaking headroom.

    The cap for a category is the **maximum** slot count any single active user
    wants for it (the user's mental model: produce at most as many as the most-
    demanding user asked for). A user who has NOT customized their "Build your 30"
    (no allocation rows) is treated as having ``default_allocation`` — the universal
    default everyone inherits — so the caps reflect that default even before anyone
    finishes onboarding. A user WITH rows uses exactly those rows: a category they
    left out means they don't want it (contributes 0), not the default.

    ``breaking`` is split out: it is a tier, not a produce-category, so its cross-
    user max becomes global importance headroom for :func:`cap_stories_per_category`.

    Args:
        allocation_by_user: ``{user_id: [CategoryAllocation, ...]}`` — the shape
            :func:`agents.pipeline.daily_batch._load_category_allocation` returns.
        active_user_ids: Every active user id (so no-row users count as default).
        default_allocation: ``{category: slot_count}`` a no-row user inherits
            (``agents.pipeline.categories.DEFAULT_FEED_ALLOCATION``).

    Returns:
        ``(caps, breaking_headroom)`` where ``caps`` is
        ``{category: max slot_count}`` over the topic/source categories (excludes
        ``breaking``) and ``breaking_headroom`` is the cross-user max breaking
        budget (0 if none).

    Example:
        >>> from agents.pipeline.categories import CategoryAllocation
        >>> allocs = {
        ...     "u1": [CategoryAllocation(allocation_category="markets",
        ...                               allocation_slot_count=7,
        ...                               allocation_sort_order=0)],
        ... }
        >>> caps, breaking = compute_category_produce_caps(
        ...     allocs, ["u1"], {"markets": 4, "breaking": 2}
        ... )
        >>> caps["markets"], breaking  # u1's explicit 7 beats the default 4
        (7, 0)
    """
    caps: dict[FeedCategory, int] = {}
    breaking_headroom = 0
    users_using_default = 0
    for user_id in active_user_ids:
        rows = allocation_by_user.get(user_id)
        if rows:
            pairs: list[tuple[FeedCategory, int]] = [
                (row.allocation_category, row.allocation_slot_count) for row in rows
            ]
        else:
            # No customized allocation → this user inherits the universal default.
            users_using_default += 1
            pairs = list(default_allocation.items())
        for category, slot_count in pairs:
            if category == _BREAKING_CATEGORY:
                breaking_headroom = max(breaking_headroom, slot_count)
                continue
            caps[category] = max(caps.get(category, 0), slot_count)
    logger.info(
        "category_produce_caps_computed",
        caps=caps,
        breaking_headroom=breaking_headroom,
        active_users=len(active_user_ids),
        users_with_allocation=len(active_user_ids) - users_using_default,
        users_using_default=users_using_default,
    )
    return caps, breaking_headroom


def cap_stories_per_category(
    to_produce: list[CanonicalStory],
    decisions: Iterable[ProduceDecision],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    caps: dict[FeedCategory, int],
    breaking_headroom: int,
    *,
    default_cap: int,
) -> list[CanonicalStory]:
    """Cap the gated pool per category, keeping the most important stories.

    For each gated story: classify it into one screen category
    (:func:`assign_category`), then within each category keep the top-N by
    importance (N = ``caps[category]``), tiebroken by freshness then story id for
    determinism. A category absent from ``caps`` is dropped entirely (nobody asked
    for it) — UNLESS ``caps`` is empty (no user has any allocation row), in which
    case ``default_cap`` is applied to every category as a safe fallback. Finally,
    the top ``breaking_headroom`` stories by importance across the WHOLE gated pool
    are unioned back in so the breaking tier always has candidates to draw from at
    feed assembly, even if their category cap was already hit.

    Args:
        to_produce: The stories the produce-once gate passed.
        decisions: The full per-story :class:`ProduceDecision` list (source of the
            importance/freshness scores used to rank within a category).
        story_interest_tags: All ``story_interests`` tags for the pool (classify).
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup (classify).
        caps: ``{category: max kept}`` from :func:`compute_category_produce_caps`.
        breaking_headroom: Top-N by importance kept regardless of category cap.
        default_cap: Per-category cap used only when ``caps`` is empty.

    Returns:
        The capped subset of ``to_produce`` (original order preserved).

    Example:
        >>> # 39 markets stories, markets cap 7 → exactly 7 kept (highest importance).
        >>> # See tests/agents/pipeline/test_produce_caps.py for the full DoD asserts.
    """
    if not to_produce:
        return []

    score_by_story = {
        decision.story_id: (decision.importance_score, decision.freshness_score)
        for decision in decisions
    }
    tags_by_story = _index_tags_by_story(story_interest_tags)

    # Group gated stories by their single best-fit category.
    by_category: dict[FeedCategory, list[CanonicalStory]] = {}
    for story in to_produce:
        category = assign_category(
            story.canonical_story_id, tags_by_story, interest_nodes
        )
        by_category.setdefault(category, []).append(story)

    use_default = not caps  # no user has any allocation → uniform fallback cap
    kept_ids: set[str] = set()
    kept_per_category: dict[FeedCategory, int] = {}
    for category, stories in by_category.items():
        cap = default_cap if use_default else caps.get(category, 0)
        if cap <= 0:
            continue
        # Most important first; tiebreak freshness desc, then id (deterministic).
        ordered = sorted(
            stories,
            key=lambda s: (
                -score_by_story.get(s.canonical_story_id, (0.0, 0.0))[0],
                -score_by_story.get(s.canonical_story_id, (0.0, 0.0))[1],
                s.canonical_story_id,
            ),
        )
        for story in ordered[:cap]:
            kept_ids.add(story.canonical_story_id)
        kept_per_category[category] = min(cap, len(stories))

    # Breaking headroom: union in the top-N most important across the whole pool so
    # the breaking tier (filled at assembly from topic buckets) is never starved.
    if breaking_headroom > 0:
        pool_by_importance = sorted(
            to_produce,
            key=lambda s: (
                -score_by_story.get(s.canonical_story_id, (0.0, 0.0))[0],
                -score_by_story.get(s.canonical_story_id, (0.0, 0.0))[1],
                s.canonical_story_id,
            ),
        )
        for story in pool_by_importance[:breaking_headroom]:
            kept_ids.add(story.canonical_story_id)

    capped = [s for s in to_produce if s.canonical_story_id in kept_ids]
    logger.info(
        "produce_caps_applied",
        gated=len(to_produce),
        kept=len(capped),
        dropped=len(to_produce) - len(capped),
        kept_per_category=kept_per_category,
        used_default_cap=use_default,
        breaking_headroom=breaking_headroom,
    )
    return capped


def enforce_overall_ceiling(
    stories: list[CanonicalStory],
    decisions: Iterable[ProduceDecision],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    max_total: int,
) -> list[CanonicalStory]:
    """Trim a capped pool to an overall ceiling, round-robin across categories.

    The optional global safety ceiling (``MAX_PRODUCE``). Applied AFTER
    :func:`cap_stories_per_category`, it preserves category balance by taking the
    most-important story from each category in turn (round-robin) until the ceiling
    is met — so the largest categories shed first instead of one topic dominating.

    Args:
        stories: The already category-capped pool.
        decisions: Per-story :class:`ProduceDecision` (importance/freshness ranking).
        story_interest_tags: All ``story_interests`` tags (classify).
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup (classify).
        max_total: The overall ceiling. ``<= 0`` or ``>= len(stories)`` is a no-op.

    Returns:
        At most ``max_total`` stories (original order preserved), balanced across
        categories.

    Example:
        >>> # 20 stories across 4 categories, ceiling 8 → 8 kept, ~2 per category.
        >>> # See tests/agents/pipeline/test_produce_caps.py for the DoD asserts.
    """
    if max_total <= 0 or len(stories) <= max_total:
        return list(stories)

    score_by_story = {
        decision.story_id: (decision.importance_score, decision.freshness_score)
        for decision in decisions
    }
    tags_by_story = _index_tags_by_story(story_interest_tags)

    by_category: dict[FeedCategory, list[CanonicalStory]] = {}
    for story in stories:
        category = assign_category(
            story.canonical_story_id, tags_by_story, interest_nodes
        )
        by_category.setdefault(category, []).append(story)

    # Each category sorted most-important first; stable category order by key.
    for category in by_category:
        by_category[category].sort(
            key=lambda s: (
                -score_by_story.get(s.canonical_story_id, (0.0, 0.0))[0],
                -score_by_story.get(s.canonical_story_id, (0.0, 0.0))[1],
                s.canonical_story_id,
            )
        )
    ordered_categories = sorted(by_category.keys())

    kept_ids: set[str] = set()
    cursor = 0
    while len(kept_ids) < max_total:
        progressed = False
        for category in ordered_categories:
            bucket = by_category[category]
            if cursor < len(bucket):
                kept_ids.add(bucket[cursor].canonical_story_id)
                progressed = True
                if len(kept_ids) >= max_total:
                    break
        if not progressed:
            break  # exhausted every bucket (defensive; can't exceed total)
        cursor += 1

    trimmed = [s for s in stories if s.canonical_story_id in kept_ids]
    logger.info(
        "produce_overall_ceiling_enforced",
        before=len(stories),
        after=len(trimmed),
        max_total=max_total,
    )
    return trimmed
