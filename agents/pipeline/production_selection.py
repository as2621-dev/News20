"""Select each user's 30 BEFORE producing, and promote standbys on failure (#74).

Founder directive 2026-07-26. The batch used to produce the ENTIRE capped pool
(per-category caps × a 2.0 attrition headroom — 76 reels for one 30-slot user) and
only then cut each user's feed from the survivors, so ~60% of the script/TTS/poster
bill bought reels that never shipped.

The inverted order this module implements::

    gates + caps → CANDIDATE POOL
                     ├── per-user selection (the same rule assembly will apply)
                     │      └── UNION → the only stories production pays for
                     └── everything else → per-category RANKED STANDBY (free)

A selected story that fails production (script gate, similarity drop, TTS error,
poster safety block, editorial-JSON failure) promotes the next standby in ITS
category; the caller produces that one and re-slots. Standbys exhausted → the pool
is short and the existing honest fallback ladder writes a short section, loudly.

Both halves are pure over their injected inputs — no DB, no clock, no network. The
selection calls :func:`agents.pipeline.orchestrator.select_user_slots`, the SAME
seam the post-production assembly uses, so the batch can never pay to produce one
set of stories and ship another.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import FeedCategory
from agents.pipeline.orchestrator import ActiveUserFeedInputs, select_user_slots
from agents.pipeline.x_theme_ladder import XThemeReelCandidate
from agents.shared.logger import get_logger

logger = get_logger("pipeline.production_selection")


class UserSelection(BaseModel):
    """One user's pre-production cut — the stories their feed would be built from.

    Attributes:
        selection_user_id: The active user this cut belongs to.
        selection_story_ids: The selected story ids in feed order (position 1..N).
    """

    selection_user_id: str = Field(..., description="Active user id")
    selection_story_ids: list[str] = Field(
        default_factory=list, description="Selected story ids, in feed order"
    )


class ProductionSelectionPlan(BaseModel):
    """What the run will produce, and what it will fall back to (issue #74).

    Attributes:
        selection_production_story_ids: The UNION of every user's selection, deduped
            and in candidate-pool order — the ONLY stories the write/render waves pay
            for on the first pass.
        selection_by_user: Each user's own cut, so the founder review shows the real
            feed rather than the candidate pool.
        selection_standby_story_ids_by_category: ``{category: [story_id, ...]}`` —
            the unselected candidates, ranked best-first, consumed by
            :func:`promote_standbys` when a selected story fails production.
        selection_candidate_pool_size: Candidates the cut chose from (audit).
    """

    selection_production_story_ids: list[str] = Field(default_factory=list)
    selection_by_user: list[UserSelection] = Field(default_factory=list)
    selection_standby_story_ids_by_category: dict[str, list[str]] = Field(
        default_factory=dict
    )
    selection_candidate_pool_size: int = Field(default=0, ge=0)


def build_standby_lists(
    standby_stories: list[CanonicalStory],
    selected_story_ids: set[str],
    category_by_story: dict[str, FeedCategory],
    score_by_story: dict[str, tuple[float, float]],
    eligible_categories: set[FeedCategory] | None = None,
) -> dict[str, list[str]]:
    """Rank the unselected candidates into a per-category promotion queue.

    Args:
        standby_stories: The candidate pool standbys may be drawn from (the gated,
            deduped pool — a story below a category's cap line is a perfectly good
            replacement now that being in the pool costs nothing).
        selected_story_ids: Story ids some user already selected (never standbys).
        category_by_story: ``{story_id: FeedCategory}`` — the batch's resolve-once
            verdicts (issue #70). A story missing from the map is skipped: promoting
            it would guess at a category the batch never resolved.
        score_by_story: ``{story_id: (importance, freshness)}`` from the produce
            gate's decisions — the ranking key, highest first.
        eligible_categories: Only queue these categories (the ones with a produce
            cap, i.e. some user asked for them). ``None`` queues every category.

    Returns:
        ``{category: [story_id, ...]}`` best-first, empty categories omitted.

    Example:
        >>> build_standby_lists([], set(), {}, {})
        {}
    """
    by_category: dict[str, list[CanonicalStory]] = {}
    for story in standby_stories:
        story_id = story.canonical_story_id
        if story_id in selected_story_ids:
            continue
        category = category_by_story.get(story_id)
        if category is None:
            continue
        if eligible_categories is not None and category not in eligible_categories:
            continue
        by_category.setdefault(str(category), []).append(story)

    standby: dict[str, list[str]] = {}
    for category, stories in sorted(by_category.items()):
        ordered = sorted(
            stories,
            key=lambda story: (
                -score_by_story.get(story.canonical_story_id, (0.0, 0.0))[0],
                -score_by_story.get(story.canonical_story_id, (0.0, 0.0))[1],
                story.canonical_story_id,
            ),
        )
        standby[category] = [story.canonical_story_id for story in ordered]
    return standby


def select_stories_for_production(
    selection_pool: list[CanonicalStory],
    standby_pool: list[CanonicalStory],
    active_user_inputs: list[ActiveUserFeedInputs],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    category_by_story: dict[str, FeedCategory],
    score_by_story: dict[str, tuple[float, float]] | None = None,
    eligible_categories: set[FeedCategory] | None = None,
    source_stories_by_user: dict[str, list[CanonicalStory]] | None = None,
    x_theme_candidates_by_user: dict[str, list[XThemeReelCandidate]] | None = None,
    cluster_importance_by_story: dict[str, float] | None = None,
    now_utc: Any = None,
) -> ProductionSelectionPlan:
    """Cut every user's feed from the PRE-production pool and union the result.

    Args:
        selection_pool: The candidate pool a user's feed may be filled from (post
            gate, dedup, caps and the source/theme merges) — what production WOULD
            have received under the old order.
        standby_pool: The wider gated pool standbys are ranked from (a superset of
            ``selection_pool``; pass the same list to keep standbys to the capped set).
        active_user_inputs: The loaded per-user feed inputs (Stage D loader).
        story_interest_tags: The batch's ``story_interests`` tags.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        category_by_story: The batch's resolve-once category verdicts (issue #70),
            used to bucket standbys.
        score_by_story: ``{story_id: (importance, freshness)}`` for the standby
            ranking; ``None`` falls back to outlet count then story id.
        eligible_categories: Categories a standby may be queued for (those with a
            produce cap). ``None`` queues every category.
        source_stories_by_user: The users' followed-source stories (phase-5d).
        x_theme_candidates_by_user: The users' X theme reels (slice #31).
        cluster_importance_by_story: E1 importance map (FSR-M3).
        now_utc: Freshness clock, forwarded to the assembler.

    Returns:
        The :class:`ProductionSelectionPlan` — what to produce, per user, plus the
        standby queues.

    Example:
        >>> plan = select_stories_for_production(  # doctest: +SKIP
        ...     pool, pool, users, tags, nodes, verdicts)
        >>> len(plan.selection_production_story_ids) <= 30  # doctest: +SKIP
        True
    """
    if score_by_story is None:
        score_by_story = {
            story.canonical_story_id: (float(story.story_outlet_count), 0.0)
            for story in standby_pool
        }

    selections: list[UserSelection] = []
    selected_story_ids: set[str] = set()
    for user_inputs in active_user_inputs:
        slots = select_user_slots(
            user_inputs,
            stories=selection_pool,
            story_interest_tags=story_interest_tags,
            interest_nodes=interest_nodes,
            now_utc=now_utc,
            source_stories_by_user=source_stories_by_user,
            x_theme_candidates_by_user=x_theme_candidates_by_user,
            cluster_importance_by_story=cluster_importance_by_story,
            category_override_by_story=category_by_story or None,
        )
        story_ids = [slot.feed_story_id for slot in slots]
        selections.append(
            UserSelection(
                selection_user_id=user_inputs.active_user_id,
                selection_story_ids=story_ids,
            )
        )
        selected_story_ids.update(story_ids)

    # Reason: the union in POOL order (not per-user order) so the write wave's
    # cross-reel opener rotation sees a stable, category-interleaved sequence, and a
    # re-run with the same pool produces in the same order.
    production_story_ids = [
        story.canonical_story_id
        for story in selection_pool
        if story.canonical_story_id in selected_story_ids
    ]
    standby = build_standby_lists(
        standby_stories=standby_pool,
        selected_story_ids=selected_story_ids,
        category_by_story=category_by_story,
        score_by_story=score_by_story,
        eligible_categories=eligible_categories,
    )

    if selection_pool and not production_story_ids:
        # Reason: a non-empty pool that nobody selected produces NOTHING under the
        # inverted order. That is correct (paying for reels no feed shows is the
        # waste this slice removes) but it must never be silent — it is also the
        # signature of a broken allocation/tag stream.
        logger.error(
            "production_selection_empty",
            candidate_pool_size=len(selection_pool),
            active_user_count=len(active_user_inputs),
            fix_suggestion=(
                "No active user selected ANY candidate, so this run produces zero "
                "reels. Check the users' user_feed_allocation rows and that the "
                "pool's story_interests tags match their followed interests."
            ),
        )
    logger.info(
        "production_selection_completed",
        candidate_pool_size=len(selection_pool),
        active_user_count=len(active_user_inputs),
        production_count=len(production_story_ids),
        standby_count=sum(len(ids) for ids in standby.values()),
        standby_by_category={
            category: len(ids) for category, ids in sorted(standby.items())
        },
    )
    return ProductionSelectionPlan(
        selection_production_story_ids=production_story_ids,
        selection_by_user=selections,
        selection_standby_story_ids_by_category=standby,
        selection_candidate_pool_size=len(selection_pool),
    )


def promote_standbys(
    failed_story_ids: Iterable[str],
    standby_by_category: dict[str, list[str]],
    category_by_story: dict[str, FeedCategory],
    *,
    exclude_story_ids: set[str],
    max_promotions: int | None = None,
) -> list[str]:
    """Draw one same-category replacement per story that failed production.

    CONSUMES ``standby_by_category``: every id returned is popped from its queue, so
    a later round of the promotion loop can never hand back a story this run already
    attempted.

    Args:
        failed_story_ids: The selected stories that did not publish this round.
        standby_by_category: The ranked queues from
            :func:`build_standby_lists` (mutated — see above).
        category_by_story: ``{story_id: FeedCategory}`` resolve-once verdicts, used
            to find the failed story's queue.
        exclude_story_ids: Stories already attempted this run (never re-promote).
        max_promotions: Remaining production budget (``MAX_PRODUCE``); ``None`` is
            unbounded.

    Returns:
        The promoted story ids, in failure order. EMPTY when every relevant queue is
        exhausted — the caller then lets the honest fallback ladder run short.

    Example:
        >>> queues = {"business": ["b2"]}
        >>> promote_standbys(["b1"], queues, {"b1": "business"}, exclude_story_ids=set())
        ['b2']
        >>> queues
        {'business': []}
    """
    promoted: list[str] = []
    exhausted_categories: list[str] = []
    for failed_story_id in failed_story_ids:
        if max_promotions is not None and len(promoted) >= max_promotions:
            logger.warning(
                "standby_promotion_budget_exhausted",
                failed_story_id=failed_story_id,
                promoted_count=len(promoted),
                max_promotions=max_promotions,
                fix_suggestion=(
                    "The MAX_PRODUCE ceiling stopped this replacement; the feed will "
                    "be short by one slot. Raise MAX_PRODUCE (or set 0 for no overall "
                    "ceiling) if the run should backfill every failure."
                ),
            )
            break
        category = category_by_story.get(failed_story_id)
        queue = standby_by_category.get(str(category)) if category is not None else None
        replacement: str | None = None
        while queue:
            candidate = queue.pop(0)
            if candidate in exclude_story_ids or candidate in promoted:
                continue
            replacement = candidate
            break
        if replacement is None:
            exhausted_categories.append(str(category))
            continue
        promoted.append(replacement)
        logger.info(
            "standby_promoted_after_production_failure",
            failed_story_id=failed_story_id,
            promoted_story_id=replacement,
            category=str(category),
            remaining_standby=len(queue or []),
            fix_suggestion=(
                "A selected reel failed production and the next standby in its "
                "category was promoted to keep the feed full. Check the "
                "produce_write_* / produce_render_* error for the failed story if "
                "promotions are frequent."
            ),
        )
    for category in exhausted_categories:
        logger.error(
            "standby_exhausted_feed_will_be_short",
            category=category,
            failed_count=len(exhausted_categories),
            fix_suggestion=(
                "A reel failed and this category has no standby left, so the feed "
                "runs SHORT here via the honest fallback ladder. Widen ingestion for "
                "this category (more candidates) or fix the production failure."
            ),
        )
    return promoted
