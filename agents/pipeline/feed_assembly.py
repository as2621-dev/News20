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
  - **Breaking tier** — ``breaking`` is a user-budgeted category filled by
    top-``Importance`` across ALL topic buckets; a chosen story is REMOVED from its
    topic bucket so it is never double-placed.
  - **Source soft-roll** — ``youtube``/``x`` are source-axis categories that no
    interest slug maps to (phase-5d, empty today); their budgeted slots roll into
    the remaining topic categories by sequence so the feed still totals 30.
  - **§3.8 don't-repeat** — exclude any story already in this user's prior
    ``daily_feeds`` (preserved from the old allocator).
  - **Within-feed dedup** — a story id appears at most once in one feed (preserved).
  - **Default allocation** — a user with NO ``user_feed_allocation`` rows gets the
    balanced fallback (``breaking 4`` + an even split of the remaining 26 across the
    topic categories that have available stories) so pre-screen users still get a feed.

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

# Reason: the balanced default for a user with NO user_feed_allocation rows
# (pre-screen users). breaking gets DEFAULT_BREAKING_SLOTS; the remaining slots
# split evenly across the TOPIC categories that actually have available stories
# (don't budget empty categories). Owner-locked default (phase-5a Open-Q3).
DEFAULT_BREAKING_SLOTS = 4

# Reason: feed_slot_kind enum-like values written to daily_feeds.feed_slot_kind.
# The category-budget allocator uses two kinds: ``breaking`` for the top-Importance
# tier, and ``interest`` for every category-filled slot (it carries the
# matched-interest attribution). The old ``exploration`` kind is retired here (the
# new model has no exploration reserve), but the constant is kept for the
# daily_feeds row-contract vocabulary + any reader that still references it.
SLOT_KIND_BREAKING = "breaking"
SLOT_KIND_INTEREST = "interest"
SLOT_KIND_EXPLORATION = "exploration"


class AllocatedSlot(BaseModel):
    """One position in a user's assembled feed — maps 1:1 to a ``daily_feeds`` row.

    Attributes:
        feed_story_id: The story filling this slot (``daily_feeds.feed_story_id``).
        feed_position: 1-based position in the ordered feed (``feed_position``).
        feed_score: The per-(user, story) Score that placed it (``feed_score``).
        feed_matched_interest_id: The followed interest this slot is attributed to
            (``feed_matched_interest_id``); None for a breaking slot with no
            specific attributed leaf.
        feed_slot_kind: ``breaking`` / ``interest`` (``exploration`` retired in the
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
        description="breaking / interest",
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
    breaking_slots: int,
) -> list[CategoryAllocation]:
    """Build the balanced default allocation for a user with no allocation rows.

    The owner-locked pre-screen fallback (phase-5a): ``breaking`` gets
    ``breaking_slots``; the remaining slots split EVENLY across the TOPIC
    categories that have at least one available candidate (empty categories are not
    budgeted, so their slots are not wasted). Largest-remainder apportionment hands
    any leftover slot to the earliest non-empty topic category so the budgets sum
    to ``feed_slot_budget``. ``breaking`` always sorts first, then the topic
    categories in :data:`TOPIC_CATEGORIES` order.

    Args:
        buckets: SP2's ``score_and_classify_for_user`` output (drives which topic
            categories are non-empty + whether breaking has any eligible story).
        feed_slot_budget: ``N`` — total feed slots (30).
        breaking_slots: How many slots ``breaking`` gets in the default.

    Returns:
        A synthetic ``CategoryAllocation`` list (breaking first, then non-empty
        topics) summing to at most ``feed_slot_budget``.
    """
    # Reason: a breaking story is the top-Importance story across the TOPIC buckets;
    # if every topic bucket is empty there is nothing to promote, so don't budget
    # breaking either (its slots would only waste the budget).
    has_any_topic_story = any(buckets.get(cat) for cat in TOPIC_CATEGORIES)
    non_empty_topics = [cat for cat in TOPIC_CATEGORIES if buckets.get(cat)]

    allocation: list[CategoryAllocation] = []
    sort_order = 0

    effective_breaking = breaking_slots if has_any_topic_story else 0
    effective_breaking = min(effective_breaking, feed_slot_budget)
    if effective_breaking > 0:
        allocation.append(
            CategoryAllocation(
                allocation_category="breaking",
                allocation_slot_count=effective_breaking,
                allocation_sort_order=sort_order,
            )
        )
        sort_order += 1

    remaining = feed_slot_budget - effective_breaking
    if non_empty_topics and remaining > 0:
        base = remaining // len(non_empty_topics)
        leftover = remaining - base * len(non_empty_topics)
        # Largest-remainder: the earliest topics (by TOPIC_CATEGORIES order) absorb
        # the leftover so the split sums exactly to ``remaining``.
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


def _select_breaking(
    buckets: dict[FeedCategory, list[ScoredCandidate]],
    breaking_slots: int,
    used_story_ids: set[str],
    excluded_story_ids: set[str],
) -> list[ScoredCandidate]:
    """Pick the top ``breaking_slots`` highest-Importance stories across all topics.

    ``breaking`` is a TIER, not a slug bucket: SP2 leaves its bucket empty and the
    allocator fills the user's breaking budget from the highest-``importance``
    candidates across ALL topic buckets. Each chosen story is REMOVED from its topic
    bucket (in place) so it is never double-placed in a topic slot later.

    Args:
        buckets: SP2's ``score_and_classify_for_user`` output (topic buckets are
            MUTATED — a promoted story is removed from its source bucket).
        breaking_slots: How many breaking slots to fill.
        used_story_ids: Mutated — story ids already placed (breaking adds to it).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).

    Returns:
        Up to ``breaking_slots`` candidates, highest Importance first (Score
        tiebreak). The chosen stories are removed from their topic buckets.
    """
    if breaking_slots <= 0:
        return []

    # Gather every topic candidate (breaking promotes across topics only — source
    # buckets are empty, and a breaking story must come from a real classified story).
    eligible: list[ScoredCandidate] = []
    for category in TOPIC_CATEGORIES:
        for candidate in buckets.get(category, []):
            if candidate.story_id in excluded_story_ids:
                continue
            eligible.append(candidate)

    # Highest intrinsic Importance first (the breaking signal); Score as a tiebreak.
    eligible.sort(key=lambda c: (c.importance, c.score), reverse=True)

    chosen: list[ScoredCandidate] = []
    chosen_story_ids: set[str] = set()
    for candidate in eligible:
        if len(chosen) >= breaking_slots:
            break
        if candidate.story_id in used_story_ids:
            continue
        chosen.append(candidate)
        chosen_story_ids.add(candidate.story_id)
        used_story_ids.add(candidate.story_id)

    # Remove the promoted stories from their topic buckets so they are not also
    # placed as a topic slot (no double-placement).
    if chosen_story_ids:
        for category in TOPIC_CATEGORIES:
            bucket = buckets.get(category)
            if not bucket:
                continue
            buckets[category] = [
                candidate
                for candidate in bucket
                if candidate.story_id not in chosen_story_ids
            ]

    return chosen


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


def assemble_user_feed(
    profile_interests: list[UserProfileInterest],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    followed_entities: list[FollowedEntity] | None = None,
    category_allocation: list[CategoryAllocation] | None = None,
    prior_feed_story_ids: set[str] | None = None,
    exploration_candidates_by_interest: Any = None,
    feed_slot_budget: int = FEED_SLOT_BUDGET,
    default_breaking_slots: int = DEFAULT_BREAKING_SLOTS,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    now_utc: Any = None,
) -> list[AllocatedSlot]:
    """Assemble one user's ordered 30-slot feed from their category budgets (phase-5a).

    Pure over its injected inputs (profile + entities + allocation + story pool +
    taxonomy + prior feed) — no DB, no network. Runs SP2's entity-aware
    ``score_and_classify_for_user`` once, then the category-budget passes:

      1. Resolve the allocation (user's rows, or the balanced default when none).
      2. Fill ``breaking`` by top-``Importance`` across all topic buckets (removed
         from their topic bucket so they are not double-placed).
      3. Fill each TOPIC category to its budget from its top-Score qualifying
         candidates, in the user's sequence order.
      4. Soft-roll SOURCE category budgets (``youtube``/``x``, empty today) into the
         remaining topic categories by sequence so the feed still totals 30.
      5. Order the slots by the user's sequence (breaking first, then categories by
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
        feed_slot_budget: ``N`` — total feed slots (30).
        default_breaking_slots: Breaking budget in the no-allocation default.
        score_threshold: ``T`` — the qualifying bar.
        now_utc: Current time for the freshness term (defaults to ``utcnow``).

    Returns:
        The ordered slots (``feed_position`` 1..len). EMPTY when no eligible story
        exists anywhere — the caller skips the user (no empty-feed row).

    Example:
        >>> # See tests/agents/pipeline/test_feed_assembly.py for the category-budget
        >>> # invariants (exact per-category counts, source soft-roll, sequence,
        >>> # breaking tier, don't-repeat) asserted here.
    """
    excluded = set(prior_feed_story_ids or set())

    if not profile_interests:
        logger.info(
            "assemble_user_feed_empty_profile",
            fix_suggestion="User has no followed interests; allocator returns no slots "
            "(caller writes no daily_feeds row).",
        )
        return []

    # ── Layer 2: entity-aware scoring + classification into the 8 categories ──
    buckets = score_and_classify_for_user(
        profile_interests=profile_interests,
        followed_entities=followed_entities or [],
        stories=stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        now_utc=now_utc,
        score_threshold=score_threshold,
    )

    # ── Layer 1: resolve the per-category budgets + manual sequence ──
    # Reason: a user with no user_feed_allocation rows (pre-screen) gets the
    # balanced default so they still receive a feed (phase-5a Open-Q3).
    allocation = list(category_allocation or [])
    if not allocation:
        allocation = _default_allocation(
            buckets=buckets,
            feed_slot_budget=feed_slot_budget,
            breaking_slots=default_breaking_slots,
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

    # ── Pass 1: fill the breaking tier (removes promoted stories from topics) ──
    breaking_budget = sum(
        row.allocation_slot_count
        for row in ordered_allocation
        if row.allocation_category == "breaking"
    )
    breaking_budget = min(breaking_budget, total_target)
    breaking = _select_breaking(
        buckets=buckets,
        breaking_slots=breaking_budget,
        used_story_ids=used_story_ids,
        excluded_story_ids=excluded,
    )

    # ── Pass 2: gather each TOPIC category's own budget, in the user's sequence ──
    # Reason: source categories (youtube/x) carry a budget but ZERO candidates today
    # (phase-5d). We bank their budget as ``source_roll_slots`` and distribute it —
    # together with any breaking/topic shortfall — across the topic categories by
    # sequence in Pass 4 so the feed still totals 30 without overshooting N.
    topic_budgets: dict[FeedCategory, int] = {}
    topic_sequence: list[FeedCategory] = []
    source_roll_slots = 0
    for row in ordered_allocation:
        category = row.allocation_category
        if category == "breaking":
            continue
        if category in SOURCE_CATEGORIES:
            source_roll_slots += row.allocation_slot_count
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
    # never overshoots the user's target (breaking already consumed ``len(breaking)``).
    topic_capacity = max(total_target - len(breaking), 0)
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
    # breaking/topic shortfall (a category short of stories yields its slots forward).
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

    # ── Order: breaking first, then topic categories in the user's sequence ──
    ordered.extend(breaking)
    slot_kinds.extend([SLOT_KIND_BREAKING] * len(breaking))
    for category in topic_sequence:
        taken = filled_by_category.get(category, [])
        ordered.extend(taken)
        slot_kinds.extend([SLOT_KIND_INTEREST] * len(taken))

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
                    if slot_kind == SLOT_KIND_BREAKING
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
        breaking_slots=len(breaking),
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
