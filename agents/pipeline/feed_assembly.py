"""Per-user feed allocator (Phase 1d SP4): turn scored candidates into a
~30-slot ordered ``daily_feeds`` feed, written idempotently (produce-once).

ADAPTED — there is no single TLDW analog. This implements the allocation layer
of ``reference/ranking-spec.md`` §3 ("Re-ranking / allocation") on top of the
SP3 scorer (``agents.pipeline.stages.ranking.score_candidates_for_user``):

    score_candidates_for_user(...)  → {followed_leaf_id: [ScoredCandidate]}  (SP3)
    assemble_user_feed(...)         → [AllocatedSlot]  (ordered 01..N)        (here)
    write_daily_feed(...)           → one daily_feeds row per slot, idempotent (here)

The allocator honors the §3 invariants:

  §3.1 Breaking tier   — top ~4 slots by Importance across ALL followed nodes.
  §3.2 Proportional    — remaining slots split ∝ normalized profile_weight.
  §3.3 Floor-1         — every followed leaf with ≥1 qualifying story gets ≥1 slot.
  §3.4 Cap ~40%        — no interest exceeds ~40% of N (unless single-interest/strict).
  §3.5 Fill            — fill each bucket from its top-Score candidates.
  §3.6 Redistribute    — unfilled slots flow to the next-highest-affinity interest.
  §3.7 Exploration ~10%— ~10% of slots for sibling/parent (adjacent) interests;
                         ``strict`` interests contribute NO exploration.
  §3.8 Don't-repeat    — exclude any story already in this user's prior daily_feeds.

A user whose allocation produces ZERO slots (no eligible stories anywhere) is
returned an empty list — the caller (``assemble_daily_feeds`` in the
orchestrator) SKIPS such a user and writes no ``daily_feeds`` rows for them
(no empty-feed row).

PRODUCE-ONCE / IDEMPOTENCY (SP4 DoD-b)
--------------------------------------
``write_daily_feed`` pre-checks for any existing ``daily_feeds`` row for the
``(feed_user_id, feed_date)`` pair and, when present, writes nothing and reports
``already_present``. Re-running the batch for the same day therefore does NOT
duplicate a user's feed. This pre-check is the application-level guard that pairs
with the DB constraint ``uq_daily_feed_position`` /
``uq_daily_feed_story`` (migration 0003) which would otherwise raise on a second
insert — we skip cleanly rather than relying on a constraint error.

The supabase client is INJECTED so this module never reads a secret and the test
suite mocks at the client boundary (CLAUDE.md mandate).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.produce_gate import compute_importance_score
from agents.pipeline.stages.ranking import (
    DEFAULT_SCORE_THRESHOLD,
    ScoredCandidate,
    UserProfileInterest,
    normalize_affinities,
    score_candidates_for_user,
)
from agents.shared.logger import get_logger

logger = get_logger("pipeline.feed_assembly")

# Reason: the §3 allocation constants (reference/ranking-spec.md §3). First-draft,
# confirmed at the SP4 2-user manual run. Single config source — never scattered.
FEED_SLOT_BUDGET = 30  # N ≈ 30 per-user feed budget (§3)
BREAKING_SLOT_COUNT = 4  # ~4 preempt slots for highest-Importance (§3.1)
INTEREST_CAP_FRACTION = 0.40  # no single interest > ~40% of N (§3.4)
EXPLORATION_FRACTION = 0.10  # ~10% of slots for adjacent interests (§3.7)

# Reason: feed_slot_kind enum-like values written to daily_feeds.feed_slot_kind
# (reference/ranking-spec.md §3: {breaking, interest, exploration}).
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
        feed_slot_kind: ``breaking`` / ``interest`` / ``exploration``.

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
        description="breaking / interest / exploration",
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


def _interest_budgets(
    affinities: dict[str, float],
    followed_leaf_ids: list[str],
    available_slots: int,
    cap_per_interest: int,
) -> dict[str, int]:
    """Split ``available_slots`` across followed leaves ∝ normalized affinity (§3.2/§3.4).

    Largest-remainder apportionment so the budgets sum to exactly
    ``available_slots`` (no rounding drift), with each interest capped at
    ``cap_per_interest`` (§3.4). The floor-1 and redistribution passes run later
    on the actual filled counts.

    Args:
        affinities: ``{interest_id: 0-1 affinity}`` for the followed leaves.
        followed_leaf_ids: The user's followed leaf interest ids (allocation order).
        available_slots: Slots left after the breaking + exploration reservations.
        cap_per_interest: Max slots any single interest may receive (§3.4).

    Returns:
        ``{interest_id: budget_slots}`` summing to ``min(available_slots,
        cap_per_interest * len(leaves))``.
    """
    if available_slots <= 0 or not followed_leaf_ids:
        return {leaf_id: 0 for leaf_id in followed_leaf_ids}

    affinity_sum = sum(
        max(affinities.get(leaf_id, 0.0), 0.0) for leaf_id in followed_leaf_ids
    )
    # Reason: zero-affinity (or empty) profile → split evenly so no leaf is starved.
    if affinity_sum <= 0.0:
        weights = {
            leaf_id: 1.0 / len(followed_leaf_ids) for leaf_id in followed_leaf_ids
        }
    else:
        weights = {
            leaf_id: max(affinities.get(leaf_id, 0.0), 0.0) / affinity_sum
            for leaf_id in followed_leaf_ids
        }

    # Largest-remainder apportionment of available_slots.
    raw = {leaf_id: weights[leaf_id] * available_slots for leaf_id in followed_leaf_ids}
    budgets = {leaf_id: int(raw[leaf_id]) for leaf_id in followed_leaf_ids}
    assigned = sum(budgets.values())
    remainder = available_slots - assigned
    # Hand the leftover slots to the largest fractional remainders, highest first.
    by_remainder = sorted(
        followed_leaf_ids,
        key=lambda leaf_id: raw[leaf_id] - int(raw[leaf_id]),
        reverse=True,
    )
    for leaf_id in by_remainder:
        if remainder <= 0:
            break
        budgets[leaf_id] += 1
        remainder -= 1

    # Apply the per-interest cap (§3.4).
    return {
        leaf_id: min(budget, cap_per_interest) for leaf_id, budget in budgets.items()
    }


def _take_top_qualifying(
    candidates: list[ScoredCandidate],
    count: int,
    used_story_ids: set[str],
    excluded_story_ids: set[str],
    score_threshold: float,
) -> list[ScoredCandidate]:
    """Take up to ``count`` top-Score qualifying candidates not yet used/excluded.

    A candidate is eligible when its ``score >= score_threshold`` (§3 fill uses
    the same `Score ≥ T` "good enough" bar, ranking-spec §1/§3.5), its story is
    not already placed in this feed, and it is not in the don't-repeat exclusion
    set (§3.8). ``candidates`` is assumed descending by score (the SP3 generator
    sorts it).

    Args:
        candidates: The interest's scored candidates (descending by score).
        count: Max candidates to take.
        used_story_ids: Story ids already placed in this feed (dedup within feed).
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


def _select_breaking(
    candidates_by_interest: dict[str, list[ScoredCandidate]],
    stories_by_id: dict[str, CanonicalStory],
    breaking_slots: int,
    used_story_ids: set[str],
    excluded_story_ids: set[str],
) -> list[ScoredCandidate]:
    """Pick the top ``breaking_slots`` highest-Importance stories across all interests (§3.1).

    Breaking preempts the proportional split: the highest-Importance stories
    tagged to *any* followed node take the first slots. Each chosen story keeps
    the highest-Score candidate (its best interest attribution) for ordering.

    Args:
        candidates_by_interest: ``score_candidates_for_user`` output.
        stories_by_id: ``{story_id: CanonicalStory}`` for the Importance lookup.
        breaking_slots: How many breaking slots to fill.
        used_story_ids: Mutated — story ids already placed (breaking adds to it).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).

    Returns:
        Up to ``breaking_slots`` candidates, highest Importance first.
    """
    if breaking_slots <= 0:
        return []

    # Reason: keep the best (highest-Score) candidate per story so a story tagged
    # to several followed interests is considered once, attributed to its
    # strongest interest.
    best_by_story: dict[str, ScoredCandidate] = {}
    for candidates in candidates_by_interest.values():
        for candidate in candidates:
            existing = best_by_story.get(candidate.story_id)
            if existing is None or candidate.score > existing.score:
                best_by_story[candidate.story_id] = candidate

    eligible = [
        candidate
        for story_id, candidate in best_by_story.items()
        if story_id not in excluded_story_ids and story_id in stories_by_id
    ]
    # Sort by intrinsic Importance (the breaking signal), then Score as a tiebreak.
    eligible.sort(
        key=lambda c: (
            compute_importance_score(stories_by_id[c.story_id].story_outlet_count),
            c.score,
        ),
        reverse=True,
    )

    breaking: list[ScoredCandidate] = []
    for candidate in eligible:
        if len(breaking) >= breaking_slots:
            break
        if candidate.story_id in used_story_ids:
            continue
        breaking.append(candidate)
        used_story_ids.add(candidate.story_id)
    return breaking


def assemble_user_feed(
    profile_interests: list[UserProfileInterest],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    prior_feed_story_ids: set[str] | None = None,
    exploration_candidates_by_interest: dict[str, list[ScoredCandidate]] | None = None,
    feed_slot_budget: int = FEED_SLOT_BUDGET,
    breaking_slot_count: int = BREAKING_SLOT_COUNT,
    interest_cap_fraction: float = INTEREST_CAP_FRACTION,
    exploration_fraction: float = EXPLORATION_FRACTION,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    now_utc: Any = None,
) -> list[AllocatedSlot]:
    """Assemble one user's ordered ~N-slot feed from their scored candidates (§3).

    Pure over its injected inputs (profile + story pool + taxonomy + prior feed) —
    no DB, no network. Runs the SP3 scorer once, then the §3 allocation passes:
    breaking preempt → proportional split (cap ~40%) → floor-1 → fill → redistribute
    → exploration (~10%, excluding ``strict`` interests). Stories already in the
    user's ``prior_feed_story_ids`` are never repeated (§3.8).

    Args:
        profile_interests: The user's followed interests (Affinity + strict flags).
        stories: The deduped candidate story pool (SP1 output).
        story_interest_tags: All ``story_interests`` tag payloads for the pool.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        prior_feed_story_ids: Story ids already shown to this user (§3.8 exclusion).
        exploration_candidates_by_interest: Optional pre-scored candidates for
            adjacent (sibling/parent) interests the user does NOT follow — fills
            the ~10% exploration slots. When omitted, exploration is skipped.
        feed_slot_budget: ``N`` — total feed slots.
        breaking_slot_count: Breaking-tier reservation (§3.1).
        interest_cap_fraction: Per-interest cap as a fraction of ``N`` (§3.4).
        exploration_fraction: Exploration reservation as a fraction of ``N`` (§3.7).
        score_threshold: ``T`` — the qualifying bar.
        now_utc: Current time for the freshness term (defaults to ``utcnow``).

    Returns:
        The ordered slots (``feed_position`` 1..len). EMPTY when no eligible
        story exists anywhere — the caller skips the user (no empty-feed row).

    Example:
        >>> # See tests/pipeline/test_feed_assembly.py for the allocator invariants
        >>> # (floor-1, ~40% cap, breaking preempt, don't-repeat) asserted here.
    """
    excluded = set(prior_feed_story_ids or set())
    stories_by_id = {story.canonical_story_id: story for story in stories}

    if not profile_interests:
        logger.info(
            "assemble_user_feed_empty_profile",
            fix_suggestion="User has no followed interests; allocator returns no slots "
            "(caller writes no daily_feeds row). Sparse-profile recency fallback is "
            "ranking-spec §3.8 future work.",
        )
        return []

    candidates_by_interest = score_candidates_for_user(
        profile_interests=profile_interests,
        stories=stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        now_utc=now_utc,
        score_threshold=score_threshold,
    )
    affinities = normalize_affinities(profile_interests)
    strict_by_interest = {
        interest.profile_interest_id: interest.profile_is_strict
        for interest in profile_interests
    }
    followed_leaf_ids = [interest.profile_interest_id for interest in profile_interests]
    is_single_interest = len(followed_leaf_ids) == 1

    used_story_ids: set[str] = set()
    ordered: list[ScoredCandidate] = []
    slot_kinds: list[str] = []

    # ── §3.1 Breaking tier (preempt) ──
    breaking = _select_breaking(
        candidates_by_interest=candidates_by_interest,
        stories_by_id=stories_by_id,
        breaking_slots=min(breaking_slot_count, feed_slot_budget),
        used_story_ids=used_story_ids,
        excluded_story_ids=excluded,
    )
    ordered.extend(breaking)
    slot_kinds.extend([SLOT_KIND_BREAKING] * len(breaking))

    # ── Reserve exploration slots (§3.7), then split the rest across interests ──
    remaining_after_breaking = max(feed_slot_budget - len(ordered), 0)
    has_exploration = bool(exploration_candidates_by_interest)
    exploration_reserve = (
        round(feed_slot_budget * exploration_fraction) if has_exploration else 0
    )
    exploration_reserve = min(exploration_reserve, remaining_after_breaking)
    interest_slots = remaining_after_breaking - exploration_reserve

    # Cap: §3.4 — relaxed for a single-interest or strict user (may fill its budget).
    if is_single_interest:
        cap_per_interest = feed_slot_budget
    else:
        cap_per_interest = max(round(feed_slot_budget * interest_cap_fraction), 1)

    budgets = _interest_budgets(
        affinities=affinities,
        followed_leaf_ids=followed_leaf_ids,
        available_slots=interest_slots,
        cap_per_interest=cap_per_interest,
    )

    # ── §3.3 Floor-1: every leaf with ≥1 qualifying story gets at least 1 slot ──
    for leaf_id in followed_leaf_ids:
        has_qualifier = any(
            c.score >= score_threshold for c in candidates_by_interest.get(leaf_id, [])
        )
        if has_qualifier and budgets.get(leaf_id, 0) < 1:
            budgets[leaf_id] = 1

    # Reason: the cap is a HARD per-interest ceiling on the final fill count for
    # a multi-interest user — it must survive floor-1 AND redistribution, else a
    # high-affinity glut interest reclaims every spare slot and blows past ~40%
    # (§3.4). ``filled_count`` tracks the running placement per interest so both
    # the fill and redistribute passes respect it.
    filled_count: dict[str, int] = {leaf_id: 0 for leaf_id in followed_leaf_ids}

    # ── §3.5 Fill each bucket from its top-Score candidates (capped) ──
    for leaf_id in followed_leaf_ids:
        budget = min(budgets.get(leaf_id, 0), cap_per_interest)
        taken = _take_top_qualifying(
            candidates=candidates_by_interest.get(leaf_id, []),
            count=budget,
            used_story_ids=used_story_ids,
            excluded_story_ids=excluded,
            score_threshold=score_threshold,
        )
        filled_count[leaf_id] += len(taken)
        ordered.extend(taken)
        slot_kinds.extend([SLOT_KIND_INTEREST] * len(taken))

    # ── §3.6 Redistribute unfilled slots to the highest-affinity interests ──
    # (still capped per interest — §3.4 holds through redistribution).
    placed_interest_slots = sum(filled_count.values())
    unfilled = interest_slots - placed_interest_slots
    if unfilled > 0:
        # Highest-affinity interests get first refusal on the spare slots.
        by_affinity = sorted(
            followed_leaf_ids,
            key=lambda leaf_id: affinities.get(leaf_id, 0.0),
            reverse=True,
        )
        for leaf_id in by_affinity:
            if unfilled <= 0:
                break
            headroom = cap_per_interest - filled_count[leaf_id]
            if headroom <= 0:
                continue
            extra = _take_top_qualifying(
                candidates=candidates_by_interest.get(leaf_id, []),
                count=min(unfilled, headroom),
                used_story_ids=used_story_ids,
                excluded_story_ids=excluded,
                score_threshold=score_threshold,
            )
            filled_count[leaf_id] += len(extra)
            ordered.extend(extra)
            slot_kinds.extend([SLOT_KIND_INTEREST] * len(extra))
            unfilled -= len(extra)

    # ── §3.7 Exploration: adjacent interests, excluding strict followed nodes ──
    if has_exploration and exploration_reserve > 0:
        # Reason: strict interests "disable exploration for that interest" — drop
        # any adjacent bucket keyed to a followed-strict node (ranking-spec §3.7).
        strict_followed = {
            leaf_id for leaf_id, strict in strict_by_interest.items() if strict
        }
        explore_pool: list[ScoredCandidate] = []
        for adjacent_id, candidates in (
            exploration_candidates_by_interest or {}
        ).items():
            if adjacent_id in strict_followed:
                continue
            explore_pool.extend(candidates)
        explore_pool.sort(key=lambda c: c.score, reverse=True)
        explore_taken = _take_top_qualifying(
            candidates=explore_pool,
            count=exploration_reserve,
            used_story_ids=used_story_ids,
            excluded_story_ids=excluded,
            score_threshold=score_threshold,
        )
        ordered.extend(explore_taken)
        slot_kinds.extend([SLOT_KIND_EXPLORATION] * len(explore_taken))

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
        followed_interest_count=len(followed_leaf_ids),
        breaking_slots=len(breaking),
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
    not duplicate the feed — SP4 DoD-b). An empty ``slots`` list writes nothing
    (the caller already decided to skip the user — SP4 DoD-c).

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
        # Reason: empty feed → skip the user entirely, no daily_feeds row (DoD-c).
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
