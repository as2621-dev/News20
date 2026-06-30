"""Per-user feed allocator (phase-5a SP3): turn a user's per-category slot
budgets + manual sequence ("Build your 30, in order") into a 30-slot ordered
``daily_feeds`` feed, written idempotently (produce-once).

REWRITE (phase-5a SP3). This REPLACES the old affinity-proportional allocator
(``reference/ranking-spec.md`` §3 — proportional split, floor-1, ~40% cap,
exploration) with the owner's two-layer "Build your 30" model (owner, 2026-06-05):

    Layer 1 — *allocation*: the user sets, per screen category, how many of their
              30 slots it gets (``allocation_slot_count``) and where it sits in the
              manual sequence (``allocation_sort_order``). This module honors those.
    Layer 2 — *scoring*: SP2's entity-aware ``score_and_classify_for_user`` decides
              WHICH stories fill each category's slots (top-Score, entity-aware).

The allocation pipeline (this module):

    score_and_classify_for_user(...) → {FeedCategory: [ScoredCandidate]}  (SP2)
    assemble_user_feed(...)          → [AllocatedSlot]  (ordered 01..30)  (here)
    write_daily_feed(...)            → one daily_feeds row per slot, idempotent (here)

Invariants this allocator honors:

  - **Per-category budgets** — each category is filled to exactly its
    ``allocation_slot_count`` from its SP2 bucket (top-Score, qualifying ``≥ T``),
    subject to story availability.
  - **Manual sequence** — categories are filled (and their slots ordered) by the
    user's ``allocation_sort_order`` (lower = earlier).
  - **Source soft-roll** — ``youtube``/``x`` are source-axis categories that no
    interest slug maps to (phase-5d, empty today); their budgeted slots roll into
    the remaining topic categories by sequence so the feed still totals 30.
  - **§3.8 don't-repeat** — exclude any story already in this user's prior
    ``daily_feeds`` (preserved from the old allocator).
  - **Within-feed dedup** — a story id appears at most once in one feed (preserved).
  - **Default allocation** — a user with NO ``user_feed_allocation`` rows gets the
    balanced fallback (an even split of all 30 slots across the topic categories
    that have available stories) so pre-screen users still get a feed.

A user whose allocation produces ZERO slots (no eligible stories anywhere) is
returned an empty list — the caller (``assemble_daily_feeds`` in the orchestrator)
SKIPS such a user and writes no ``daily_feeds`` rows for them (no empty-feed row).

PRODUCE-ONCE / IDEMPOTENCY
--------------------------
``write_daily_feed`` pre-checks for any existing ``daily_feeds`` row for the
``(feed_user_id, feed_date)`` pair and, when present, writes nothing and reports
``already_present``. Re-running the batch for the same day therefore does NOT
duplicate a user's feed.

The supabase client is INJECTED so this module never reads a secret and the test
suite mocks at the client boundary (CLAUDE.md mandate). ``assemble_user_feed`` is
a PURE function over its injected inputs (profile + entities + allocation + story
pool + taxonomy + prior feed) — no DB, no network — fully unit-testable.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import (
    SOURCE_CATEGORIES,
    TOPIC_CATEGORIES,
    CategoryAllocation,
    FeedCategory,
)
from agents.pipeline.stages.ranking import (
    DEFAULT_SCORE_THRESHOLD,
    FollowedEntity,
    ScoredCandidate,
    UserProfileInterest,
    score_and_classify_for_user,
)
from agents.shared.logger import get_logger

logger = get_logger("pipeline.feed_assembly")

# Reason: the feed budget (phase-5a). N = 30 — the "Build your 30" target. The
# user's per-category slot counts SUM to 30; the allocator's roll-over logic owns
# totalling to 30 when source categories (youtube/x) are budgeted-but-empty.
FEED_SLOT_BUDGET = 30  # N = 30 per-user feed budget ("Build your 30")

# Reason: feed_slot_kind enum-like values written to daily_feeds.feed_slot_kind.
# The category-budget allocator uses ``interest`` for every topic-category-filled
# slot (it carries the matched-interest attribution) and ``source`` for followed
# YouTube/X slots. The old ``exploration`` kind is retired here (the new model has
# no exploration reserve), but the constant is kept for the daily_feeds
# row-contract vocabulary + any reader that still references it. (phase-SP1 removed
# the ``breaking`` tier kind.)
SLOT_KIND_INTEREST = "interest"
SLOT_KIND_EXPLORATION = "exploration"
# Reason (phase-5d SP4): a slot filled by a story from one of the user's FOLLOWED
# sources (a YouTube upload / X post). Distinct from ``interest`` because it
# carries NO matched interest (it was placed by the source axis, not a slug) and
# the client renders the source attribution differently. ``feed_slot_kind`` is a
# plain ``text`` column (migration 0003 — no CHECK), so this value persists safely.
SLOT_KIND_SOURCE = "source"

# Reason: map a source-origin story's outlet domain to its source FeedCategory so
# a YouTube upload fills a ``youtube`` slot and an X post fills an ``x`` slot. The
# youtube/x adapters stamp these exact domains (agents.ingestion.dedup
# SOURCE_ORIGIN_DOMAINS); kept local so feed_assembly owns the slot mapping.
_SOURCE_DOMAIN_TO_CATEGORY: dict[str, FeedCategory] = {
    "youtube.com": "youtube",
    "x.com": "x",
}


class AllocatedSlot(BaseModel):
    """One position in a user's assembled feed — maps 1:1 to a ``daily_feeds`` row.

    Attributes:
        feed_story_id: The story filling this slot (``daily_feeds.feed_story_id``).
        feed_position: 1-based position in the ordered feed (``feed_position``).
        feed_score: The per-(user, story) Score that placed it (``feed_score``).
        feed_matched_interest_id: The followed interest this slot is attributed to
            (``feed_matched_interest_id``); None for a source slot with no
            specific attributed leaf.
        feed_slot_kind: ``interest`` / ``source`` (``exploration`` retired in the
            category-budget model).

    Example:
        >>> slot = AllocatedSlot(
        ...     feed_story_id="s1", feed_position=1, feed_score=0.7,
        ...     feed_matched_interest_id="int-arsenal", feed_slot_kind="interest",
        ... )
        >>> slot.feed_position
        1
    """

    feed_story_id: str = Field(..., description="Story filling this slot")
    feed_position: int = Field(..., ge=1, description="1-based ordered feed position")
    feed_score: float = Field(..., ge=0.0, description="The Score that placed it")
    feed_matched_interest_id: str | None = Field(
        default=None, description="The followed interest this slot is attributed to"
    )
    feed_slot_kind: str = Field(
        default=SLOT_KIND_INTEREST,
        description="interest / source",
    )


class FeedWriteResult(BaseModel):
    """Audit record of writing one user's feed (idempotent produce-once).

    Attributes:
        feed_user_id: The user the feed was assembled for.
        feed_date: The feed date (ISO).
        slots_written: Number of ``daily_feeds`` rows inserted this run.
        already_present: True when an existing feed for (user, date) was found and
            this run wrote nothing (produce-once skip).

    Example:
        >>> result = FeedWriteResult(
        ...     feed_user_id="u1", feed_date="2026-05-31", slots_written=12,
        ... )
        >>> result.slots_written
        12
    """

    feed_user_id: str = Field(..., description="The user the feed is for")
    feed_date: str = Field(..., description="ISO feed date")
    slots_written: int = Field(default=0, ge=0, description="daily_feeds rows inserted")
    already_present: bool = Field(
        default=False,
        description="True when a feed already existed (produce-once skip)",
    )


def _ordered_categories_from_allocation(
    category_allocation: list[CategoryAllocation],
) -> list[CategoryAllocation]:
    """Return the allocation rows in the user's manual sequence (sort_order asc).

    Tiebreak (equal ``allocation_sort_order`` — not DB-unique) is deterministic by
    category key so the same allocation always produces the same feed order.

    Args:
        category_allocation: The user's ``user_feed_allocation`` rows.

    Returns:
        The rows sorted by ``(allocation_sort_order, allocation_category)``.
    """
    return sorted(
        category_allocation,
        key=lambda row: (row.allocation_sort_order, row.allocation_category),
    )


def _default_allocation(
    buckets: dict[FeedCategory, list[ScoredCandidate]],
    feed_slot_budget: int,
) -> list[CategoryAllocation]:
    """Build the balanced default allocation for a user with no allocation rows.

    The pre-screen fallback (phase-SP1): all ``feed_slot_budget`` slots split EVENLY
    across the TOPIC categories that have at least one available candidate (empty
    categories are not budgeted, so their slots are not wasted). Largest-remainder
    apportionment hands any leftover slot to the earliest non-empty topic category
    so the budgets sum to ``feed_slot_budget``. Categories sort in
    :data:`TOPIC_CATEGORIES` order.

    Args:
        buckets: SP2's ``score_and_classify_for_user`` output (drives which topic
            categories are non-empty).
        feed_slot_budget: ``N`` — total feed slots (30).

    Returns:
        A synthetic ``CategoryAllocation`` list (non-empty topics, in
        :data:`TOPIC_CATEGORIES` order) summing to at most ``feed_slot_budget``.
    """
    non_empty_topics = [cat for cat in TOPIC_CATEGORIES if buckets.get(cat)]

    allocation: list[CategoryAllocation] = []
    sort_order = 0

    if non_empty_topics and feed_slot_budget > 0:
        base = feed_slot_budget // len(non_empty_topics)
        leftover = feed_slot_budget - base * len(non_empty_topics)
        # Largest-remainder: the earliest topics (by TOPIC_CATEGORIES order) absorb
        # the leftover so the split sums exactly to ``feed_slot_budget``.
        for index, category in enumerate(non_empty_topics):
            slot_count = base + (1 if index < leftover else 0)
            if slot_count <= 0:
                continue
            allocation.append(
                CategoryAllocation(
                    allocation_category=category,
                    allocation_slot_count=slot_count,
                    allocation_sort_order=sort_order,
                )
            )
            sort_order += 1

    return allocation


def _take_top_qualifying(
    candidates: list[ScoredCandidate],
    count: int,
    used_story_ids: set[str],
    excluded_story_ids: set[str],
    score_threshold: float,
) -> list[ScoredCandidate]:
    """Take up to ``count`` top-Score qualifying candidates not yet used/excluded.

    A candidate is eligible when its ``score >= score_threshold`` (the same
    ``Score ≥ T`` "good enough" bar, ranking-spec §1/§3.5), its story is not already
    placed in this feed, and it is not in the don't-repeat exclusion set (§3.8).
    ``candidates`` is assumed descending by score (SP2 sorts each bucket).

    Args:
        candidates: A category's scored candidates (descending by score).
        count: Max candidates to take.
        used_story_ids: Story ids already placed in this feed (mutated; dedup).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).
        score_threshold: ``T`` — the qualifying bar.

    Returns:
        Up to ``count`` eligible candidates, highest score first.
    """
    taken: list[ScoredCandidate] = []
    for candidate in candidates:
        if len(taken) >= count:
            break
        if candidate.score < score_threshold:
            continue
        if (
            candidate.story_id in used_story_ids
            or candidate.story_id in excluded_story_ids
        ):
            continue
        taken.append(candidate)
        used_story_ids.add(candidate.story_id)
    return taken


def _source_candidate(
    story: CanonicalStory, category: FeedCategory
) -> ScoredCandidate:
    """Wrap a produced source-origin story as a ``ScoredCandidate`` for a source slot.

    Source slots are not scored against an interest (the user asked for the creator,
    not a topic), so the synthetic candidate carries no matched interest and a flat
    qualifying score — its placement is driven by the source budget + cadence, not
    the Score ranking. ``feed_category`` records which source slot it fills.

    Args:
        story: The produced source-origin story (its outlet domain marks youtube/x).
        category: The source category this story fills (``youtube`` / ``x``).

    Returns:
        A :class:`ScoredCandidate` placeholder for the source slot.
    """
    return ScoredCandidate(
        story_id=story.canonical_story_id,
        matched_interest_id="",
        score=1.0,
        affinity=1.0,
        depth_match=1.0,
        importance=0.0,
        freshness=1.0,
        feed_category=category,
    )


def _source_recency_importance_sort_key(
    story: CanonicalStory,
) -> tuple[float, int, str]:
    """The documented over-budget source SPILL rule (SP2): recency, then importance.

    When a user's fresh followed-source items exceed the slots available (the source
    budget or — under the SP1 guarantee — the whole feed budget), they are ranked by
    this single deterministic key and the overflow is dropped (this phase does not
    carry overflow into a later day). Order (Open Q2, pinned):

      1. **Recency PRIMARY** — newer ``canonical_published_utc`` first. A follow's
         value is its freshness; the most recent uploads/posts lead.
      2. **Importance SECONDARY** — higher ``story_outlet_count`` first (the same
         coverage-breadth signal the produce gate uses). Breaks recency ties toward
         the more-covered item.
      3. **Story id TIEBREAK** — ascending ``canonical_story_id`` so the cap is fully
         deterministic on exact ties (no insertion-order dependence — Rule 9).

    Returned as a key for ``sorted(..., key=...)``; the two "first" signals are
    NEGATED so a plain ascending sort yields newest-then-most-important-first while
    the id tiebreak stays ascending.

    Args:
        story: A produced source-origin story.

    Returns:
        ``(-published_epoch, -outlet_count, story_id)`` — ascending sort = the rule.
    """
    published = story.canonical_published_utc
    published_epoch = published.timestamp() if published is not None else 0.0
    return (-published_epoch, -int(story.story_outlet_count or 0), story.canonical_story_id)


def _rank_source_stories(
    source_stories: list[CanonicalStory],
) -> list[CanonicalStory]:
    """Order a category's source stories by the SP2 recency+importance spill rule.

    A stable, deterministic ordering applied BEFORE the budget cap so that when more
    source items exist than slots, the kept items are the top-N by
    :func:`_source_recency_importance_sort_key` (newest, then most-covered, then id) —
    never insertion order.

    Args:
        source_stories: One category's produced source-origin stories.

    Returns:
        The same stories, ordered newest+most-important first (deterministic).
    """
    return sorted(source_stories, key=_source_recency_importance_sort_key)


def _fill_source_slots(
    source_stories: list[CanonicalStory],
    source_budgets: dict[FeedCategory, int],
    used_story_ids: set[str],
    excluded_story_ids: set[str],
    guaranteed_cap: int | None = None,
) -> dict[FeedCategory, list[ScoredCandidate]]:
    """Fill source slots from the user's produced source stories — guaranteed first.

    A source story fills the slot of the category its outlet domain maps to
    (youtube.com → ``youtube``, x.com → ``x``). Stories already placed in this feed or
    shown in a prior feed (§3.8) are skipped.

    **Guaranteed source priority (SP1).** Fresh followed-source items are the
    personalization (PRD Decision #8), so they take guaranteed slots AHEAD of topic
    fill — not merely their per-source-category budget. Each category fills up to
    ``max(its budget, all its eligible stories)``, and the TOTAL source fill is bounded
    only by ``guaranteed_cap`` (the feed budget, set by the caller). So a user who
    budgeted youtube=2 but has 6 fresh follows gets all 6 as priority slots (capped at
    the feed), with topic stories filling whatever the feed has left. When
    ``guaranteed_cap`` is ``None`` (legacy callers), each category fills only up to its
    own budget — the pre-SP1 behaviour.

    **Over-budget spill (SP2).** Within each category the stories are first ranked by
    the documented recency+importance rule (:func:`_rank_source_stories`); when the cap
    binds, the overflow is dropped (not carried to a later day this phase) and the kept
    items are the top-N by that rule — deterministic on ties.

    A category with no produced source story stays unfilled — its budget then rolls
    into the topic categories (the caller's existing soft-roll), so the feed still
    totals 30 when a source produced nothing this run.

    Args:
        source_stories: This user's PRODUCED source-origin stories (youtube/x).
        source_budgets: ``{source_category: slot_count}`` from the user's allocation.
        used_story_ids: Mutated — a placed source story is added (within-feed dedup).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).
        guaranteed_cap: The maximum TOTAL source slots to grant across all source
            categories (the feed budget). ``None`` keeps the legacy per-category-budget
            cap (no cross-category guarantee).

    Returns:
        ``{source_category: [ScoredCandidate]}`` for the categories actually filled.
    """
    by_category: dict[FeedCategory, list[CanonicalStory]] = {}
    for story in source_stories:
        category = _SOURCE_DOMAIN_TO_CATEGORY.get(
            (story.canonical_primary_outlet_domain or "").strip().lower()
        )
        if category is None:
            continue
        by_category.setdefault(category, []).append(story)

    # Reason: under the SP1 guarantee the per-category cap is lifted to "all this
    # category's eligible stories" (still bounded by the feed-wide guaranteed_cap),
    # so a user's fresh follows are not silently truncated to their source budget.
    # Legacy callers (guaranteed_cap=None) keep the strict per-category budget.
    granted_total = 0
    filled: dict[FeedCategory, list[ScoredCandidate]] = {}
    for category in source_budgets:
        budget = source_budgets.get(category, 0)
        ranked = _rank_source_stories(by_category.get(category, []))
        if guaranteed_cap is None:
            category_cap = budget
        else:
            category_cap = max(budget, len(ranked))
        if category_cap <= 0:
            continue
        taken: list[ScoredCandidate] = []
        for story in ranked:
            if len(taken) >= category_cap:
                break
            if guaranteed_cap is not None and granted_total >= guaranteed_cap:
                break
            story_id = story.canonical_story_id
            if story_id in used_story_ids or story_id in excluded_story_ids:
                continue
            taken.append(_source_candidate(story, category))
            used_story_ids.add(story_id)
            granted_total += 1
        if taken:
            filled[category] = taken
    return filled


def assemble_user_feed(
    profile_interests: list[UserProfileInterest],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    followed_entities: list[FollowedEntity] | None = None,
    category_allocation: list[CategoryAllocation] | None = None,
    prior_feed_story_ids: set[str] | None = None,
    exploration_candidates_by_interest: Any = None,
    source_stories: list[CanonicalStory] | None = None,
    cluster_importance_by_story: dict[str, float] | None = None,
    feed_slot_budget: int = FEED_SLOT_BUDGET,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    now_utc: Any = None,
) -> list[AllocatedSlot]:
    """Assemble one user's ordered 30-slot feed from their category budgets (phase-5a).

    Pure over its injected inputs (profile + entities + allocation + story pool +
    taxonomy + prior feed) — no DB, no network. Runs SP2's entity-aware
    ``score_and_classify_for_user`` once, then the category-budget passes:

      1. Resolve the allocation (user's rows, or the balanced default when none).
      2. Fill each TOPIC category to its budget from its top-Score qualifying
         candidates, in the user's sequence order.
      3. Soft-roll SOURCE category budgets (``youtube``/``x``, empty today) into the
         remaining topic categories by sequence so the feed still totals 30.
      4. Order the slots by the user's sequence (categories by
         ``allocation_sort_order``); never repeat a prior-feed story (§3.8); never
         place a story twice in one feed.

    Args:
        profile_interests: The user's followed interests (Affinity + strict flags).
        stories: The deduped candidate story pool (SP1 output).
        story_interest_tags: All ``story_interests`` tag payloads for the pool.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        followed_entities: The user's followed entities (EntityBonus source); empty
            → the feed scores identically to the no-entity baseline.
        category_allocation: The user's per-category slot budgets + manual sequence
            (``user_feed_allocation``). Empty/None → the balanced default.
        prior_feed_story_ids: Story ids already shown to this user (§3.8 exclusion).
        exploration_candidates_by_interest: Accepted for backward-compat with the
            old allocator's callers (sim/orchestrator); IGNORED in the category-budget
            model (the user reserves slots by category, not an exploration reserve).
        source_stories: This user's PRODUCED source-origin stories (followed YouTube
            uploads / X posts). They fill the ``youtube``/``x`` source-category slots
            (phase-5d) instead of those budgets soft-rolling into topics. A source
            category with no produced story still soft-rolls (graceful). ``None``/empty
            → the legacy all-soft-roll behaviour (no source slots).
        cluster_importance_by_story: ``{story_id: cluster_importance}`` — the E1
            within-category-normalized importance (FSR-M3 residual #2) for clustered
            stories, threaded into the entity-aware scorer so a clustered story's
            Importance term is its authority-weighted E1 score. Un-clustered stories
            (absent from the map) fall back to the raw outlet-count importance, so the
            seam is additive (``None``/empty → byte-identical to the pre-M3 feed).
        feed_slot_budget: ``N`` — total feed slots (30).
        score_threshold: ``T`` — the qualifying bar.
        now_utc: Current time for the freshness term (defaults to ``utcnow``).

    Returns:
        The ordered slots (``feed_position`` 1..len). EMPTY when no eligible story
        exists anywhere — the caller skips the user (no empty-feed row).

    Example:
        >>> # See tests/agents/pipeline/test_feed_assembly.py for the category-budget
        >>> # invariants (exact per-category counts, source soft-roll, sequence,
        >>> # don't-repeat) asserted here.
    """
    excluded = set(prior_feed_story_ids or set())

    if not profile_interests:
        logger.info(
            "assemble_user_feed_empty_profile",
            fix_suggestion="User has no followed interests; allocator returns no slots "
            "(caller writes no daily_feeds row).",
        )
        return []

    # ── Layer 2: entity-aware scoring + classification into the 7 categories ──
    buckets = score_and_classify_for_user(
        profile_interests=profile_interests,
        followed_entities=followed_entities or [],
        stories=stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        now_utc=now_utc,
        score_threshold=score_threshold,
        cluster_importance_by_story=cluster_importance_by_story,
    )

    # ── Layer 1: resolve the per-category budgets + manual sequence ──
    # Reason: a user with no user_feed_allocation rows (pre-screen) gets the
    # balanced default so they still receive a feed (phase-5a Open-Q3).
    allocation = list(category_allocation or [])
    if not allocation:
        allocation = _default_allocation(
            buckets=buckets,
            feed_slot_budget=feed_slot_budget,
        )

    ordered_allocation = _ordered_categories_from_allocation(allocation)

    # Reason: the feed target is the SUM of the user's per-category budgets, capped at
    # N. A user whose budgets sum to < N (e.g. dialed some categories to 0) gets a
    # SHORTER feed — the allocator never invents slots the user did not ask for. The
    # source soft-roll keeps the feed AT this target (it redistributes within the
    # sum, it does not inflate it). Bounded above by N so a mis-summed allocation
    # (the cross-category SUM==30 is NOT DB-enforced) can never overshoot 30.
    total_target = min(
        sum(row.allocation_slot_count for row in ordered_allocation),
        feed_slot_budget,
    )

    used_story_ids: set[str] = set()
    ordered: list[ScoredCandidate] = []
    slot_kinds: list[str] = []

    # ── Pass 1: fill SOURCE slots (youtube/x) from the user's produced source
    # stories — GUARANTEED FIRST, ahead of topic fill (SP1). Fresh followed-source
    # items are the personalization (PRD Decision #8), so they take priority slots up
    # to the whole feed budget (``total_target``), NOT just their per-source-category
    # budget. Whatever source slots remain unfilled (no produced story this run) stay
    # as roll-over that Pass 4 redistributes into topics — so the feed still totals 30
    # either way. Over-budget source items spill by the documented recency+importance
    # rule inside ``_fill_source_slots`` (SP2). ──
    source_budgets: dict[FeedCategory, int] = {}
    for row in ordered_allocation:
        if row.allocation_category in SOURCE_CATEGORIES:
            source_budgets[row.allocation_category] = (
                source_budgets.get(row.allocation_category, 0)
                + row.allocation_slot_count
            )
    source_filled_by_category = _fill_source_slots(
        source_stories or [],
        source_budgets,
        used_story_ids=used_story_ids,
        excluded_story_ids=excluded,
        guaranteed_cap=total_target,
    )
    source_filled_total = sum(len(v) for v in source_filled_by_category.values())

    # ── Pass 2: gather each TOPIC category's own budget, in the user's sequence ──
    # Reason: a source category's UNFILLED budget (no produced youtube/x story this
    # run) is banked as ``source_roll_slots`` and distributed — together with any
    # topic shortfall — across the topic categories by sequence in Pass 4
    # so the feed still totals 30 without overshooting N.
    topic_budgets: dict[FeedCategory, int] = {}
    topic_sequence: list[FeedCategory] = []
    # Reason: clamp at 0 — under the SP1 guarantee the source fill can EXCEED the
    # source budget (a user's fresh follows outnumber their youtube/x budget), in
    # which case there is no unfilled source budget to roll into topics.
    source_roll_slots = max(sum(source_budgets.values()) - source_filled_total, 0)
    for row in ordered_allocation:
        category = row.allocation_category
        if category in SOURCE_CATEGORIES:
            continue
        topic_budgets[category] = (
            topic_budgets.get(category, 0) + row.allocation_slot_count
        )
        if category not in topic_sequence:
            topic_sequence.append(category)

    # ── Pass 3: fill each topic category to its OWN budget, in sequence ──
    # Reason: a category never exceeds its own stated count in this pass — the
    # rolled-over source slots are a SEPARATE distribution (Pass 4), so the
    # per-category budgets are honored exactly when stories are available. The total
    # never overshoots the user's target.
    topic_capacity = max(total_target - source_filled_total, 0)
    placed_topic_slots = 0
    filled_by_category: dict[FeedCategory, list[ScoredCandidate]] = {}
    for category in topic_sequence:
        if placed_topic_slots >= topic_capacity:
            break
        want = min(topic_budgets.get(category, 0), topic_capacity - placed_topic_slots)
        if want <= 0:
            continue
        taken = _take_top_qualifying(
            candidates=buckets.get(category, []),
            count=want,
            used_story_ids=used_story_ids,
            excluded_story_ids=excluded,
            score_threshold=score_threshold,
        )
        filled_by_category[category] = taken
        placed_topic_slots += len(taken)

    # ── Pass 4: distribute the leftover capacity by sequence (the source soft-roll) ──
    # Reason: the remaining capacity is exactly the source-category budget PLUS any
    # topic shortfall (a category short of stories yields its slots forward).
    # Walk the topic sequence and hand each next category as many extra stories as it
    # still has, until the feed totals N (or every category is exhausted). This is
    # what makes ``len(feed) == 30`` hold when youtube/x are budgeted-but-empty.
    remaining_capacity = topic_capacity - placed_topic_slots
    if remaining_capacity > 0:
        for category in topic_sequence:
            if remaining_capacity <= 0:
                break
            extra = _take_top_qualifying(
                candidates=buckets.get(category, []),
                count=remaining_capacity,
                used_story_ids=used_story_ids,
                excluded_story_ids=excluded,
                score_threshold=score_threshold,
            )
            if extra:
                filled_by_category.setdefault(category, []).extend(extra)
                placed_topic_slots += len(extra)
                remaining_capacity -= len(extra)

    # ── Order: walk the user's sequence, emitting each category's filled slots at
    # its row's allocation_sort_order. The user's chosen #1 category leads the feed
    # (owner, 2026-06-16). ──
    emitted_categories: set[FeedCategory] = set()
    for row in ordered_allocation:
        category = row.allocation_category
        if category in SOURCE_CATEGORIES:
            if category in emitted_categories:
                continue
            # Reason: emit this source category's filled slots at its own sequence
            # position (phase-5d). Any UNFILLED source budget already rolled into
            # topics in Pass 4, so nothing is lost when a source produced nothing.
            source_taken = source_filled_by_category.get(category, [])
            ordered.extend(source_taken)
            slot_kinds.extend([SLOT_KIND_SOURCE] * len(source_taken))
            emitted_categories.add(category)
            continue
        if category in emitted_categories:
            continue  # a category emits once even if duplicated in the allocation
        taken = filled_by_category.get(category, [])
        ordered.extend(taken)
        slot_kinds.extend([SLOT_KIND_INTEREST] * len(taken))
        emitted_categories.add(category)

    # ── Materialize ordered slots (cap at the budget; assign 1-based positions) ──
    slots: list[AllocatedSlot] = []
    for position, (candidate, slot_kind) in enumerate(
        zip(ordered[:feed_slot_budget], slot_kinds[:feed_slot_budget]), start=1
    ):
        slots.append(
            AllocatedSlot(
                feed_story_id=candidate.story_id,
                feed_position=position,
                feed_score=candidate.score,
                feed_matched_interest_id=(
                    None
                    if slot_kind == SLOT_KIND_SOURCE
                    else candidate.matched_interest_id
                ),
                feed_slot_kind=slot_kind,
            )
        )

    logger.info(
        "assemble_user_feed_completed",
        followed_interest_count=len(profile_interests),
        followed_entity_count=len(followed_entities or []),
        allocation_row_count=len(allocation),
        source_slots=source_filled_total,
        source_roll_slots=source_roll_slots,
        total_slots=len(slots),
        excluded_prior_count=len(excluded),
    )
    return slots


def _existing_feed_count(
    supabase_client: Any,
    feed_user_id: str,
    feed_date_iso: str,
) -> int:
    """Count existing ``daily_feeds`` rows for one (user, date) — the produce-once gate.

    Args:
        supabase_client: The (real or mocked) supabase client.
        feed_user_id: The user to check.
        feed_date_iso: The ISO feed date to check.

    Returns:
        Number of existing rows (0 means safe to write).
    """
    response = (
        supabase_client.table("daily_feeds")
        .select("daily_feed_id")
        .eq("feed_user_id", feed_user_id)
        .eq("feed_date", feed_date_iso)
        .execute()
    )
    return len(getattr(response, "data", None) or [])


def write_daily_feed(
    supabase_client: Any,
    feed_user_id: str,
    feed_date: date,
    slots: list[AllocatedSlot],
) -> FeedWriteResult:
    """Write one user's assembled feed to ``daily_feeds``, idempotently (produce-once).

    Pre-checks for any existing row for ``(feed_user_id, feed_date)``; if present,
    writes nothing and reports ``already_present=True`` (re-running the batch does
    not duplicate the feed). An empty ``slots`` list writes nothing (the caller
    already decided to skip the user).

    Args:
        supabase_client: A service-role supabase client (injected; mocked in tests).
        feed_user_id: The user the feed is for.
        feed_date: The feed date.
        slots: The ordered allocated slots (``assemble_user_feed`` output).

    Returns:
        A :class:`FeedWriteResult` audit record.

    Example:
        >>> result = write_daily_feed(client, "u1", date(2026, 5, 31), slots)  # doctest: +SKIP
        >>> result.slots_written
        12
    """
    feed_date_iso = feed_date.isoformat()

    if not slots:
        # Reason: empty feed → skip the user entirely, no daily_feeds row.
        logger.info(
            "write_daily_feed_skipped_empty",
            feed_user_id=feed_user_id,
            feed_date=feed_date_iso,
            fix_suggestion="No eligible stories for this user; wrote no daily_feeds row.",
        )
        return FeedWriteResult(feed_user_id=feed_user_id, feed_date=feed_date_iso)

    existing_count = _existing_feed_count(supabase_client, feed_user_id, feed_date_iso)
    if existing_count > 0:
        # Reason: produce-once — a feed already exists for this (user, date); do
        # NOT re-insert (would violate uq_daily_feed_position/story anyway).
        logger.info(
            "write_daily_feed_already_present",
            feed_user_id=feed_user_id,
            feed_date=feed_date_iso,
            existing_count=existing_count,
            fix_suggestion="Feed already produced for this user/day; idempotent skip.",
        )
        return FeedWriteResult(
            feed_user_id=feed_user_id,
            feed_date=feed_date_iso,
            already_present=True,
        )

    rows = [
        {
            "feed_user_id": feed_user_id,
            "feed_story_id": slot.feed_story_id,
            "feed_date": feed_date_iso,
            "feed_position": slot.feed_position,
            "feed_score": slot.feed_score,
            "feed_matched_interest_id": slot.feed_matched_interest_id,
            "feed_slot_kind": slot.feed_slot_kind,
        }
        for slot in slots
    ]
    supabase_client.table("daily_feeds").insert(rows).execute()

    logger.info(
        "write_daily_feed_completed",
        feed_user_id=feed_user_id,
        feed_date=feed_date_iso,
        slots_written=len(rows),
    )
    return FeedWriteResult(
        feed_user_id=feed_user_id,
        feed_date=feed_date_iso,
        slots_written=len(rows),
    )
