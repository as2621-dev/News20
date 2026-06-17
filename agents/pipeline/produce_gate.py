"""Produce-once gate (Phase 1d SP2) — decide whether a story is worth generating.

The gate keeps generation cost down: News20 produces **one canonical audio
asset per story** (Decision #3), shared across every user whose interest it
serves. Before the expensive script → TTS → poster pipeline runs (SP3), this
gate filters the deduped story pool to only the stories worth producing. A story
is produced only when ALL of:

  1. It serves **at least one** active interest — i.e. it has ≥1
     ``story_interests`` tag (``reference/ranking-spec.md`` §0/§2: a story that
     serves no followed interest reaches no user, so producing it is wasted
     cost).
  2. It clears the **importance/freshness floor** — a configurable minimum on
     the Importance and Freshness terms (the same 0–1 signals the per-user
     scorer uses, ``ranking-spec.md`` §1). Stale or trivial stories are skipped.
  3. It **lacks a current digest** — no row in ``digests`` with
     ``digest_is_current = true`` for this story (``reference/supabase-schema.md``:
     one current digest per story). A story produced on a prior run is not
     re-produced.

This module is **pure over its injected inputs** (mirroring SP1's
``interest_keyed_pipeline``): the "does a current digest exist?" lookup and the
"now" clock are passed in, so the gate is fully unit-testable with no DB and no
clock dependency. The actual Supabase ``digest_is_current`` read is the SP3/SP4
orchestrator's job to wire.

Example:
    >>> decision = evaluate_story_for_production(
    ...     story=canonical_story,
    ...     story_interest_tags=tags,
    ...     has_current_digest=False,
    ...     now_utc=datetime.now(timezone.utc),
    ... )
    >>> decision.should_produce
    True
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timezone

from agents.ingestion.dedup import is_source_origin_domain
from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.models import ProduceDecision
from agents.shared.logger import get_logger

logger = get_logger("pipeline.produce_gate")

# Reason: Freshness is an exponential decay on the story's first-reported time
# with a ~24h half-life (reference/ranking-spec.md §1). Importance is a
# normalized outlet count: a story carried by many outlets fast is "breaking".
# These constants are first-draft (confirmed by the SP4 manual run); they live
# here as the single source of truth for the gate's floor.
_FRESHNESS_HALF_LIFE_HOURS = 24.0

# Reason: a story covered by this many distinct outlets saturates the Importance
# term at 1.0; below it scales linearly. A single-outlet story scores low.
_IMPORTANCE_SATURATION_OUTLET_COUNT = 12

# Reason: the importance/freshness floor. A story must clear BOTH to be worth
# the per-story generation cost. Tuned conservatively so a fresh single-outlet
# breaking item still passes on freshness, and a stale many-outlet story still
# passes on importance — but a stale, single-outlet trivial story is skipped.
_DEFAULT_MIN_IMPORTANCE = 0.05
_DEFAULT_MIN_FRESHNESS = 0.10

# Machine-readable skip reasons (so SP3 logs/metrics can bucket skips).
SKIP_REASON_NO_INTEREST = "serves_no_active_interest"
SKIP_REASON_BELOW_FLOOR = "below_importance_freshness_floor"
SKIP_REASON_HAS_CURRENT_DIGEST = "has_current_digest"


def compute_importance_score(
    story_outlet_count: int,
    saturation_outlet_count: int = _IMPORTANCE_SATURATION_OUTLET_COUNT,
) -> float:
    """Compute the 0–1 Importance term from a story's distinct outlet count.

    Importance is the intrinsic magnitude signal (``reference/ranking-spec.md``
    §1): a story carried by many outlets fast is more important. Scales linearly
    from 0 up to 1.0 at ``saturation_outlet_count`` outlets, then clamps at 1.0.

    Args:
        story_outlet_count: Distinct covering outlets (``story_outlet_count``).
        saturation_outlet_count: Outlet count at which Importance saturates to 1.0.

    Returns:
        The Importance score in ``[0.0, 1.0]``.

    Example:
        >>> compute_importance_score(0)
        0.0
        >>> compute_importance_score(6, saturation_outlet_count=12)
        0.5
    """
    if story_outlet_count <= 0 or saturation_outlet_count <= 0:
        return 0.0
    return min(1.0, story_outlet_count / saturation_outlet_count)


def compute_freshness_score(
    first_reported_utc: datetime,
    now_utc: datetime,
    half_life_hours: float = _FRESHNESS_HALF_LIFE_HOURS,
) -> float:
    """Compute the 0–1 Freshness term via exponential decay on the report time.

    Freshness halves every ``half_life_hours`` since the story was first reported
    (``reference/ranking-spec.md`` §1, ~24h half-life). A story reported "now"
    scores ~1.0; one a day old scores ~0.5. Future-dated or now-dated stories
    clamp at 1.0; the decay never goes negative.

    Args:
        first_reported_utc: When the story was first reported (tz-aware UTC).
        now_utc: The current time (tz-aware UTC) — injected for testability.
        half_life_hours: Hours for the freshness to halve.

    Returns:
        The Freshness score in ``[0.0, 1.0]``.

    Example:
        >>> from datetime import datetime, timedelta, timezone
        >>> now = datetime(2026, 5, 31, 12, tzinfo=timezone.utc)
        >>> round(compute_freshness_score(now - timedelta(hours=24), now), 2)
        0.5
    """
    age_hours = (now_utc - first_reported_utc).total_seconds() / 3600.0
    if age_hours <= 0:
        return 1.0
    if half_life_hours <= 0:
        return 0.0
    return float(math.pow(0.5, age_hours / half_life_hours))


def evaluate_story_for_production(
    story: CanonicalStory,
    story_interest_tags: Iterable[StoryInterestTag],
    *,
    has_current_digest: bool,
    now_utc: datetime | None = None,
    min_importance: float = _DEFAULT_MIN_IMPORTANCE,
    min_freshness: float = _DEFAULT_MIN_FRESHNESS,
    is_source_origin: bool = False,
) -> ProduceDecision:
    """Decide whether one canonical story should be produced into a digest.

    Evaluates the three produce-once checks in cost order (cheapest first):
    interest membership, then the importance/freshness floor, then the
    existing-digest lookup. The first failing check short-circuits and is named
    in ``skip_reason``; only a story passing all three is produced.

    **Source-origin exemption (Phase 5d SP3):** a story that came from a *followed*
    source (a YouTube upload / X post — ``is_source_origin=True``) is intrinsically
    wanted by the user who follows that source, so it BYPASSES checks 1 and 2: it
    needs no ``story_interests`` tag (it is tagged to the user directly, not to an
    interest) and is not gated out for a low outlet count (a single-source item has
    ``story_outlet_count`` 1 by nature). The produce-once check (3) STILL applies —
    a source item already produced is not re-produced. Topic-news gating is
    unchanged when ``is_source_origin=False``.

    Args:
        story: The deduped canonical story (carries ``story_outlet_count`` and
            ``canonical_published_utc``).
        story_interest_tags: The story's ``story_interests`` tag payloads (SP1
            output). A story with zero tags serves no active interest. Ignored for
            the interest check when ``is_source_origin`` is True.
        has_current_digest: Injected lookup — True if ``digests`` already holds a
            ``digest_is_current = true`` row for ``story.canonical_story_id``.
        now_utc: Current time for the freshness decay (defaults to ``utcnow``);
            injected so tests are deterministic.
        min_importance: Importance floor a story must clear.
        min_freshness: Freshness floor a story must clear.
        is_source_origin: True when the story came from a followed source (YouTube /
            X). Exempts it from the interest-membership and importance/freshness
            floor checks (it is intrinsically wanted); the produce-once check still
            applies.

    Returns:
        A :class:`ProduceDecision` with the verdict, the failing reason (if any),
        and the computed importance/freshness scores.

    Example:
        >>> decision = evaluate_story_for_production(
        ...     story=story, story_interest_tags=tags, has_current_digest=False,
        ... )
        >>> decision.skip_reason
        ''
    """
    now = now_utc or datetime.now(timezone.utc)

    # Only this story's tags count — defend against callers passing the whole pool.
    relevant_tags = [
        tag
        for tag in story_interest_tags
        if tag.story_interest_story_id == story.canonical_story_id
    ]
    serves_interest_count = len(
        {tag.story_interest_interest_id for tag in relevant_tags}
    )

    importance_score = compute_importance_score(story.story_outlet_count)
    freshness_score = compute_freshness_score(story.canonical_published_utc, now)

    skip_reason = ""
    if is_source_origin:
        # Reason: a followed-source item is intrinsically wanted — it serves the
        # user who follows that source, not an interest, and a single-source item
        # has outlet count 1, so checks 1 + 2 would wrongly gate it out. Only the
        # produce-once economics (check 3) apply.
        if has_current_digest:
            skip_reason = SKIP_REASON_HAS_CURRENT_DIGEST
    # Check 1 (cheapest): serves at least one active interest.
    elif serves_interest_count == 0:
        skip_reason = SKIP_REASON_NO_INTEREST
    # Check 2: clears the importance/freshness floor.
    elif importance_score < min_importance or freshness_score < min_freshness:
        skip_reason = SKIP_REASON_BELOW_FLOOR
    # Check 3 (last — the produce-once economics): no current digest already.
    elif has_current_digest:
        skip_reason = SKIP_REASON_HAS_CURRENT_DIGEST

    should_produce = skip_reason == ""

    if should_produce:
        logger.info(
            "produce_gate_passed",
            story_id=story.canonical_story_id,
            serves_interest_count=serves_interest_count,
            importance_score=round(importance_score, 4),
            freshness_score=round(freshness_score, 4),
            is_source_origin=is_source_origin,
        )
    else:
        logger.info(
            "produce_gate_skipped",
            story_id=story.canonical_story_id,
            skip_reason=skip_reason,
            serves_interest_count=serves_interest_count,
            importance_score=round(importance_score, 4),
            freshness_score=round(freshness_score, 4),
            is_source_origin=is_source_origin,
            fix_suggestion="Story did not clear the produce-once gate; not generated this run",
        )

    return ProduceDecision(
        story_id=story.canonical_story_id,
        should_produce=should_produce,
        skip_reason=skip_reason,
        serves_interest_count=serves_interest_count,
        importance_score=importance_score,
        freshness_score=freshness_score,
    )


def select_stories_to_produce(
    stories: Iterable[CanonicalStory],
    story_interest_tags: Iterable[StoryInterestTag],
    has_current_digest_lookup: dict[str, bool],
    *,
    now_utc: datetime | None = None,
    min_importance: float = _DEFAULT_MIN_IMPORTANCE,
    min_freshness: float = _DEFAULT_MIN_FRESHNESS,
) -> tuple[list[CanonicalStory], list[ProduceDecision]]:
    """Apply the produce-once gate across the whole canonical story pool.

    Convenience batch wrapper over :func:`evaluate_story_for_production`. Returns
    the subset of stories to produce plus the full per-story decision list (so
    the orchestrator can log/skip-count the rejects).

    Args:
        stories: The deduped canonical story pool (SP1 ``IngestionResult``).
        story_interest_tags: All ``story_interests`` tag payloads for the pool.
        has_current_digest_lookup: Injected map ``story_id -> bool`` — True when a
            current digest already exists. Missing keys default to False
            (no digest yet).
        now_utc: Current time for freshness (injected; defaults to ``utcnow``).
        min_importance: Importance floor.
        min_freshness: Freshness floor.

    Returns:
        ``(stories_to_produce, all_decisions)``.

    Example:
        >>> to_make, decisions = select_stories_to_produce(pool, tags, {})
        >>> len(to_make) <= len(decisions)
        True
    """
    now = now_utc or datetime.now(timezone.utc)
    tags_list = list(story_interest_tags)

    to_produce: list[CanonicalStory] = []
    decisions: list[ProduceDecision] = []
    source_origin_count = 0
    for story in stories:
        # Reason: a followed-source story is recognised by its outlet domain
        # (youtube.com / x.com — set by the source adapters), so the batch gate
        # auto-exempts it from the interest + floor checks without the caller
        # threading a separate flag. Topic-news stories keep the full gating.
        is_source_origin = is_source_origin_domain(
            story.canonical_primary_outlet_domain
        )
        if is_source_origin:
            source_origin_count += 1
        decision = evaluate_story_for_production(
            story=story,
            story_interest_tags=tags_list,
            has_current_digest=has_current_digest_lookup.get(
                story.canonical_story_id, False
            ),
            now_utc=now,
            min_importance=min_importance,
            min_freshness=min_freshness,
            is_source_origin=is_source_origin,
        )
        decisions.append(decision)
        if decision.should_produce:
            to_produce.append(story)

    logger.info(
        "produce_gate_batch_completed",
        total_stories=len(decisions),
        produced=len(to_produce),
        skipped=len(decisions) - len(to_produce),
        source_origin_stories=source_origin_count,
    )
    return to_produce, decisions
