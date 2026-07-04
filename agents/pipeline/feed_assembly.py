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

import re
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import (
    SOURCE_CATEGORIES,
    TOPIC_CATEGORIES,
    CategoryAllocation,
    FeedCategory,
)
from agents.pipeline.niche_allocation import BEYOND_BUBBLE_LABEL, NicheAllocationRow
from agents.pipeline.produce_gate import compute_importance_score
from agents.pipeline.stages.ranking import (
    DEFAULT_SCORE_THRESHOLD,
    FollowedEntity,
    ScoredCandidate,
    UserProfileInterest,
    _index_tags_by_story,
    _walk_ancestors,
    assign_category,
    normalize_affinities,
    score_and_classify_for_user,
    score_stories_for_interest,
)
from agents.shared.logger import get_logger

logger = get_logger("pipeline.feed_assembly")

# Reason: the feed budget (phase-5a). N = 30 — the "Build your 30" target. The
# user's per-category slot counts SUM to 30; the allocator's roll-over logic owns
# totalling to 30 when source categories (youtube/x) are budgeted-but-empty.
FEED_SLOT_BUDGET = 30  # N = 30 per-user feed budget ("Build your 30")

# Reason: a mute must match whole words, not substrings — a raw substring "ai" would nuke
# "Spain"/"rain", and "f1" inside "of10k"; word-boundary matching keeps a mute honest.
# Terms shorter than this are ignored (too broad to hard-filter safely).
_MIN_MUTE_TERM_LEN = 2


def _compile_mute_matcher(mute_terms: list[str] | None) -> re.Pattern[str] | None:
    """Compile the user's mute terms into ONE case-insensitive word-boundary matcher.

    Mutes are per-user HARD FILTERS applied at assembly ONLY (spec §8 / PRD story #11) —
    never at ingestion (the story pool is shared across users) and never as downranking.
    Each term is escaped (no regex injection from a typed/tapped mute term) and matched on
    whole-word boundaries so a short term can't collateral-match unrelated words.

    Args:
        mute_terms: The user's mute terms (from SKIP TUNE answers); ``None``/empty → no filter.

    Returns:
        A compiled pattern that matches any muted term, or ``None`` when nothing to filter.
    """
    cleaned = {
        term.strip()
        for term in (mute_terms or [])
        if term and len(term.strip()) >= _MIN_MUTE_TERM_LEN
    }
    if not cleaned:
        return None
    alternation = "|".join(re.escape(term) for term in sorted(cleaned))
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


def _filter_muted_stories(
    stories: list[CanonicalStory], matcher: re.Pattern[str] | None
) -> list[CanonicalStory]:
    """Drop every story whose title or body matches a mute term (hard filter, not demotion).

    A muted story is REMOVED from the candidate pool entirely before any scoring/placement,
    so it can never appear in this user's feed at any position — "mute" means gone, not
    demoted (PRD story #11). Pure: the shared input list is never mutated.

    Args:
        stories: The candidate story pool (shared, never mutated).
        matcher: The compiled mute matcher, or ``None`` (returns the pool unchanged).

    Returns:
        The stories with no muted match, order preserved.
    """
    if matcher is None:
        return stories
    kept: list[CanonicalStory] = []
    dropped = 0
    for story in stories:
        haystack = f"{story.canonical_title}\n{story.canonical_body_text or ''}"
        if matcher.search(haystack):
            dropped += 1
            continue
        kept.append(story)
    if dropped:
        logger.info("feed_mute_filter_applied", stories_muted=dropped, pool_after=len(kept))
    return kept

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
        feed_matched_interest_id: The interest node this slot was actually FILLED
            from (``feed_matched_interest_id``); None for a source/beyond-bubble slot.
            On a climbed niche slot this is the ANCESTOR node that matched (cricket),
            not the section's own leaf — that distinction is the honesty signal.
        feed_slot_kind: ``interest`` / ``source`` (``exploration`` retired in the
            category-budget model).
        feed_section_label: The user-vocabulary section header this slot belongs to
            (``daily_feeds.feed_section_label``; slice #7) — the user's words for a
            niche section, :data:`BEYOND_BUBBLE_LABEL` for a beyond-bubble slot, or
            None for a coarse/roots-only or source slot.
        feed_section_interest_id: The interest node the SECTION is named for (the
            followed leaf; ``daily_feeds.feed_section_interest_id``). Differs from
            ``feed_matched_interest_id`` exactly when the ladder climbed. None on
            coarse/beyond-bubble/source slots.
        feed_fallback_source_level: How far the fill climbed the ladder for this
            section (``daily_feeds.feed_fallback_source_level``): 0 direct/leaf, 1
            parent, 2 grandparent. > 0 is the honesty stamp — the UI must say so.

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
        default=None, description="The interest node this slot was filled from"
    )
    feed_slot_kind: str = Field(
        default=SLOT_KIND_INTEREST,
        description="interest / source",
    )
    feed_section_label: str | None = Field(
        default=None,
        description="User-vocab section header / 'Beyond your bubble' / None (slice #7)",
    )
    feed_section_interest_id: str | None = Field(
        default=None,
        description="The interest node the section is named for (leaf); None if coarse",
    )
    feed_fallback_source_level: int = Field(
        default=0,
        ge=0,
        le=2,
        description="Ladder climb for this section: 0 direct / 1 parent / 2 grandparent",
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
    mute_terms: list[str] | None = None,
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

    # Mutes: HARD FILTER the shared pool up front (spec §8) — assembly-only, never demotion.
    # No-op when the niche path already filtered and delegated here (mute_terms defaults None).
    mute_matcher = _compile_mute_matcher(mute_terms)
    stories = _filter_muted_stories(stories, mute_matcher)
    if source_stories is not None:
        source_stories = _filter_muted_stories(source_stories, mute_matcher)

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


def _fill_niche_section(
    section_interest_id: str,
    affinity: float,
    is_strict: bool,
    want: int,
    stories: list[CanonicalStory],
    tags_by_story: dict[str, dict[str, int]],
    interest_nodes: dict[str, InterestNode],
    now_utc: datetime,
    used_story_ids: set[str],
    excluded_story_ids: set[str],
    score_threshold: float,
    cluster_importance_by_story: dict[str, float] | None,
) -> list[tuple[ScoredCandidate, int]]:
    """Fill ONE niche section leaf-first, climbing the ladder one level at a time.

    The honesty core (FSR slice #7, ``plans/prd.md`` Decisions #6/#7). Fills up to
    ``want`` slots for the section's followed leaf:

      1. **Direct fill** — take top-``Score ≥ T`` stories tagged at the leaf
         (``fallback_depth == 0``) — no fallback metadata; the interview's promise kept.
      2. **One-level climb** — if the section is still short AND not strict, climb to the
         parent (``fallback_depth == 1``), then the grandparent (2), taking only enough to
         top up. Each climbed slot is stamped with its climb level so the UI labels the
         substitution honestly. Levels are never skipped (leaf → parent → grandparent).
      3. **Strict** — a strict section climbs NOTHING (``climb_path == [leaf]``): a dry
         strict section returns fewer than ``want`` slots and its remainder is handed to
         the beyond-bubble backfill by the caller — never silently substituted.

    Shares ``used_story_ids`` with every other section so a story tagged to two niches
    lands in exactly one slot (deduped across sections); ``excluded_story_ids`` is the
    §3.8 don't-repeat set. Reuses the ranking primitives (:func:`_walk_ancestors`,
    :func:`score_stories_for_interest`, :func:`_take_top_qualifying`) so the climb walks the
    SAME ladder (same order + 3-level cap) as the scorer's fallback tree — one source of
    truth (Rule 7). It differs deliberately in fill policy: the scorer stops at the first
    level with any qualifier, whereas a section here TOPS UP across levels to reach ``want``.

    Args:
        section_interest_id: The followed leaf the section is named for.
        affinity: The user's normalized 0–1 affinity for this leaf.
        is_strict: When True, cap the climb at the leaf (no upward broadening).
        want: How many slots this section may fill.
        stories: The shared candidate story pool.
        tags_by_story: ``{story_id: {interest_id: match_depth}}`` index.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy (drives the climb).
        now_utc: Current time for the freshness term.
        used_story_ids: Mutated — a placed story is added (dedup across sections).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).
        score_threshold: ``T`` — the qualifying bar (also the climb-stop bar).
        cluster_importance_by_story: E1 importance map threaded to the scorer.

    Returns:
        ``[(candidate, fallback_depth), ...]`` in fill order (leaf slots first, then any
        climbed slots). ``candidate.matched_interest_id`` is the node actually filled from
        (the ancestor on a climbed slot); ``fallback_depth`` is the climb level (0/1/2).
    """
    climb_path = (
        [section_interest_id]
        if is_strict
        else _walk_ancestors(section_interest_id, interest_nodes)
    )
    placed: list[tuple[ScoredCandidate, int]] = []
    for fallback_depth, node_id in enumerate(climb_path):
        remaining = want - len(placed)
        if remaining <= 0:
            break
        node_scored = score_stories_for_interest(
            interest_id=node_id,
            affinity=affinity,
            stories=stories,
            tags_by_story=tags_by_story,
            now_utc=now_utc,
            fallback_depth=fallback_depth,
            cluster_importance_by_story=cluster_importance_by_story,
        )
        taken = _take_top_qualifying(
            candidates=node_scored,
            count=remaining,
            used_story_ids=used_story_ids,
            excluded_story_ids=excluded_story_ids,
            score_threshold=score_threshold,
        )
        placed.extend((candidate, fallback_depth) for candidate in taken)
    return placed


def _beyond_bubble_ranked(
    stories: list[CanonicalStory],
    tags_by_story: dict[str, dict[str, int]],
    interest_nodes: dict[str, InterestNode],
    reserve_roots: set[FeedCategory],
    used_story_ids: set[str],
    excluded_story_ids: set[str],
    cluster_importance_by_story: dict[str, float],
) -> list[tuple[str, float]]:
    """Importance-rank the beyond-bubble backbone: un-lit-root stories, best first.

    The serendipity pool (Decision #7). A candidate qualifies when it classifies
    (:func:`assign_category`) into one of the beyond-bubble ``reserve_roots`` (roots the
    user did NOT light up), is not already placed in this feed, and is not in the §3.8
    exclusion. Ranked by the same importance signal the produce gate uses — the E1
    ``cluster_importance`` when the story is clustered, else the raw outlet-count
    importance — so the "outside your bubble" slots surface the day's biggest stories in
    those roots, not noise. No affinity/threshold gate (the user follows none of these
    roots — importance is the whole signal).

    Args:
        stories: The shared candidate pool.
        tags_by_story: ``{story_id: {interest_id: match_depth}}`` index.
        interest_nodes: Taxonomy lookup (classifies each story).
        reserve_roots: The beyond-bubble roots to draw from.
        used_story_ids: Story ids already placed (excluded here; NOT mutated).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).
        cluster_importance_by_story: E1 importance map (falls back to outlet count).

    Returns:
        ``[(story_id, importance), ...]`` descending by importance, then story id
        (deterministic tiebreak).
    """
    ranked: list[tuple[str, float]] = []
    for story in stories:
        story_id = story.canonical_story_id
        if story_id in used_story_ids or story_id in excluded_story_ids:
            continue
        if assign_category(story_id, tags_by_story, interest_nodes) not in reserve_roots:
            continue
        importance = cluster_importance_by_story.get(story_id)
        if importance is None:
            importance = compute_importance_score(story.story_outlet_count)
        ranked.append((story_id, importance))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def assemble_niche_feed(
    profile_interests: list[UserProfileInterest],
    niche_allocation: list[NicheAllocationRow],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    followed_entities: list[FollowedEntity] | None = None,
    prior_feed_story_ids: set[str] | None = None,
    source_stories: list[CanonicalStory] | None = None,
    cluster_importance_by_story: dict[str, float] | None = None,
    mute_terms: list[str] | None = None,
    feed_slot_budget: int = FEED_SLOT_BUDGET,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    now_utc: Any = None,
) -> list[AllocatedSlot]:
    """Assemble one user's feed niche-first, with the honest fallback ladder (slice #7).

    The FSR feed assembler. Fills a user's named niche sections (:class:`NicheAllocationRow`
    from slice #6) from directly-tagged stories, climbing each section's ladder ONE level
    at a time when it is short, and stamping every climbed slot so the UI can be honest.
    Whatever the niches (and a dry strict section) leave unfilled goes to the beyond-bubble
    backfill so the feed is never short. Pure over its injected inputs (no DB/clock/network).

    Routing (the roots-only regression guard):
      * If NO row carries an ``allocation_interest_id`` (a roots-only / legacy profile, or
        an empty allocation), this delegates to :func:`assemble_user_feed` with the rows
        projected to :class:`CategoryAllocation` — so a roots-only user's feed is
        byte-for-byte "the feed as today" (PRD Decision #8; acceptance: legacy path
        unchanged). Section metadata stays absent (all defaults) on that path.
      * Otherwise the niche path below runs.

    Niche path passes:
      1. **Source slots** — followed YouTube/X reels fill their slots GUARANTEED-first
         (reusing :func:`_fill_source_slots`), exactly as :func:`assemble_user_feed` —
         they lead the feed (PRD story #16), bounded by the feed budget.
      2. **Niche sections** — each section fills leaf-first, climbing one level at a time
         (:func:`_fill_niche_section`), in the user's ``allocation_sort_order``. Direct
         slots carry no fallback stamp; climbed slots carry their climb level; a strict
         section never climbs.
      3. **Beyond-bubble backfill** — every slot the niches (and dry strict sections) did
         not fill, up to the feed budget, is drawn from the importance-ranked backbone of
         the reserve roots (:func:`_beyond_bubble_ranked`) so the feed reaches its target.
      4. **Order + materialize** — emit each row's slots at its sequence position (source
         lead, niches, beyond-bubble trailing), assign 1-based positions, cap at the budget.

    Args:
        profile_interests: The user's followed interests (Affinity + strict flags). The
            section's affinity + strict flag are looked up here by interest id.
        niche_allocation: The user's ``user_feed_allocation`` section plan (slice #6:
            niche / beyond-bubble / source / coarse rows).
        stories: The shared deduped/produced candidate pool.
        story_interest_tags: All ``story_interests`` tag payloads for the pool.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        followed_entities: Forwarded to the coarse delegate only (the niche path does not
            apply the EntityBonus — see the residual note; a follow-on can thread it).
        prior_feed_story_ids: Story ids already shown to this user (§3.8 exclusion).
        source_stories: This user's PRODUCED followed-source stories (youtube/x).
        cluster_importance_by_story: E1 within-category-normalized importance map.
        feed_slot_budget: ``N`` — total feed slots (30).
        score_threshold: ``T`` — the qualifying/climb-stop bar.
        now_utc: Current time for freshness (defaults to ``utcnow``).

    Returns:
        The ordered slots (``feed_position`` 1..len), each carrying its section metadata.
        EMPTY when nothing is eligible — the caller skips the user (no empty-feed row).
    """
    excluded = set(prior_feed_story_ids or set())
    rows = list(niche_allocation or [])

    # ── Mutes: HARD FILTER the shared candidate pool BEFORE any pass (spec §8) ──
    # Applied here at assembly (never at ingestion — the pool is shared across users) and
    # BEFORE routing, so both the niche path and the delegated coarse path see a pool with
    # the muted stories already gone. "Mute" means gone, not demoted (PRD story #11).
    mute_matcher = _compile_mute_matcher(mute_terms)
    stories = _filter_muted_stories(stories, mute_matcher)
    if source_stories is not None:
        source_stories = _filter_muted_stories(source_stories, mute_matcher)

    # ── Routing: roots-only / legacy / empty → the unchanged coarse allocator ──
    # Reason: a profile with no niche section (NULL interest ref on every row) is "the
    # feed as today" (Decision #8) — delegate to assemble_user_feed so its behaviour is
    # byte-identical (the single strongest regression guard for this slice).
    if not any(row.allocation_interest_id is not None for row in rows):
        category_allocation = [
            CategoryAllocation(
                allocation_category=row.allocation_category,
                allocation_slot_count=row.allocation_slot_count,
                allocation_sort_order=row.allocation_sort_order,
            )
            for row in rows
        ]
        return assemble_user_feed(
            profile_interests=profile_interests,
            stories=stories,
            story_interest_tags=story_interest_tags,
            interest_nodes=interest_nodes,
            followed_entities=followed_entities,
            category_allocation=category_allocation or None,
            prior_feed_story_ids=excluded,
            source_stories=source_stories,
            cluster_importance_by_story=cluster_importance_by_story,
            feed_slot_budget=feed_slot_budget,
            score_threshold=score_threshold,
            now_utc=now_utc,
        )

    now = now_utc or datetime.now(timezone.utc)
    cluster_importance = cluster_importance_by_story or {}
    tags_by_story = _index_tags_by_story(story_interest_tags)
    affinities = normalize_affinities(profile_interests)
    strict_by_interest = {
        interest.profile_interest_id: interest.profile_is_strict
        for interest in profile_interests
    }

    # Reason: deterministic sequence — sort_order asc, then category, then the section's
    # node so equal-sort rows always order the same (Rule 9 — no insertion dependence).
    ordered_rows = sorted(
        rows,
        key=lambda row: (
            row.allocation_sort_order,
            row.allocation_category,
            row.allocation_interest_id or "",
        ),
    )
    # The feed target is the SUM of budgets, capped at N (the beyond-bubble backfill
    # keeps the feed AT this target when niches come up short — it never overshoots).
    total_target = min(
        sum(row.allocation_slot_count for row in ordered_rows), feed_slot_budget
    )

    used_story_ids: set[str] = set()

    # ── Pass 1: source slots (guaranteed-first, ahead of niches — PRD story #16) ──
    source_budgets: dict[FeedCategory, int] = {}
    for row in ordered_rows:
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

    # ── Pass 2: niche sections — leaf-first, honest one-level climb, in sequence ──
    niche_capacity = max(total_target - source_filled_total, 0)
    niche_fills: dict[int, list[tuple[ScoredCandidate, int]]] = {}
    niche_placed_total = 0
    for index, row in enumerate(ordered_rows):
        if row.allocation_interest_id is None:
            continue
        want = min(
            row.allocation_slot_count, max(niche_capacity - niche_placed_total, 0)
        )
        if want <= 0:
            niche_fills[index] = []
            continue
        section_id = row.allocation_interest_id
        placed = _fill_niche_section(
            section_interest_id=section_id,
            # Reason: a deliberately-allocated section that is somehow absent from the
            # profile defaults to full affinity — the user chose it, so score it strong.
            affinity=affinities.get(section_id, 1.0),
            # Reason (honesty fail-safe): if a section's strict flag is unknown (its
            # interest is missing from the profile — a stale/diverged row), default to
            # STRICT. An unknown section may then only under-fill into beyond-bubble, never
            # silently broaden up the ladder — the conservative direction for the owner's
            # no-silent-substitution decision. Normal sections carry their real flag.
            is_strict=strict_by_interest.get(section_id, True),
            want=want,
            stories=stories,
            tags_by_story=tags_by_story,
            interest_nodes=interest_nodes,
            now_utc=now,
            used_story_ids=used_story_ids,
            excluded_story_ids=excluded,
            score_threshold=score_threshold,
            cluster_importance_by_story=cluster_importance,
        )
        niche_fills[index] = placed
        niche_placed_total += len(placed)

    # ── Pass 3: beyond-bubble backfill — every unfilled slot (incl. dry strict + niche
    # shortfall), so the feed is never short (Decision #7 / PRD story #27). ──
    beyond_reserve_roots = {
        row.allocation_category
        for row in ordered_rows
        if row.allocation_interest_id is None
        and row.allocation_section_label == BEYOND_BUBBLE_LABEL
    }
    beyond_capacity = max(
        total_target - source_filled_total - niche_placed_total, 0
    )
    beyond_fills: list[tuple[str, float]] = []
    if beyond_reserve_roots and beyond_capacity > 0:
        ranked = _beyond_bubble_ranked(
            stories=stories,
            tags_by_story=tags_by_story,
            interest_nodes=interest_nodes,
            reserve_roots=beyond_reserve_roots,
            used_story_ids=used_story_ids,
            excluded_story_ids=excluded,
            cluster_importance_by_story=cluster_importance,
        )
        for story_id, importance in ranked[:beyond_capacity]:
            beyond_fills.append((story_id, importance))
            used_story_ids.add(story_id)

    # ── Pass 4: emit in sequence (source lead → niches → beyond-bubble trailing) ──
    slots: list[AllocatedSlot] = []
    emitted_source: set[FeedCategory] = set()
    emitted_beyond = False
    position = 0
    for index, row in enumerate(ordered_rows):
        if position >= feed_slot_budget:
            break
        if row.allocation_category in SOURCE_CATEGORIES:
            if row.allocation_category in emitted_source:
                continue
            for candidate in source_filled_by_category.get(row.allocation_category, []):
                position += 1
                slots.append(
                    AllocatedSlot(
                        feed_story_id=candidate.story_id,
                        feed_position=position,
                        feed_score=candidate.score,
                        feed_matched_interest_id=None,
                        feed_slot_kind=SLOT_KIND_SOURCE,
                    )
                )
            emitted_source.add(row.allocation_category)
        elif row.allocation_interest_id is not None:
            for candidate, fallback_depth in niche_fills.get(index, []):
                position += 1
                slots.append(
                    AllocatedSlot(
                        feed_story_id=candidate.story_id,
                        feed_position=position,
                        feed_score=candidate.score,
                        # The node actually filled from (the ancestor on a climbed slot).
                        feed_matched_interest_id=candidate.matched_interest_id,
                        feed_slot_kind=SLOT_KIND_INTEREST,
                        feed_section_label=row.allocation_section_label,
                        feed_section_interest_id=row.allocation_interest_id,
                        feed_fallback_source_level=fallback_depth,
                    )
                )
        elif row.allocation_section_label == BEYOND_BUBBLE_LABEL:
            # Beyond-bubble rows share one pool + one label — emit the whole block once,
            # at the first beyond-bubble row's (trailing) sequence position.
            if emitted_beyond:
                continue
            for story_id, importance in beyond_fills:
                position += 1
                slots.append(
                    AllocatedSlot(
                        feed_story_id=story_id,
                        feed_position=position,
                        feed_score=importance,
                        feed_matched_interest_id=None,
                        feed_slot_kind=SLOT_KIND_INTEREST,
                        feed_section_label=BEYOND_BUBBLE_LABEL,
                        feed_section_interest_id=None,
                        feed_fallback_source_level=0,
                    )
                )
            emitted_beyond = True

    slots = slots[:feed_slot_budget]
    logger.info(
        "assemble_niche_feed_completed",
        followed_interest_count=len(profile_interests),
        niche_section_count=sum(
            1 for row in ordered_rows if row.allocation_interest_id is not None
        ),
        source_slots=source_filled_total,
        niche_slots=niche_placed_total,
        beyond_bubble_slots=len(beyond_fills),
        climbed_slots=sum(
            1 for fills in niche_fills.values() for _, depth in fills if depth > 0
        ),
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
            # FSR slice #7 section metadata (migration 0027). All default to
            # None/None/0 on a coarse (roots-only) or source slot, so the coarse
            # allocator's rows persist their honest "direct fill, no section" values.
            "feed_section_label": slot.feed_section_label,
            "feed_section_interest_id": slot.feed_section_interest_id,
            "feed_fallback_source_level": slot.feed_fallback_source_level,
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
