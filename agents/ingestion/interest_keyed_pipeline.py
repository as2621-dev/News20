"""Interest-keyed ingestion pipeline (Phase 1d SP1) — the SP1 orchestration.

Builds the **active-interest set** (the distinct union of all users' followed
interest nodes that carry a search query), fans out a news search per interest,
clusters the results into a deduped canonical story pool with covering-outlet
counts, optionally extracts each story's body, and tags every story to its
matched interest + ancestors. Output is row *payloads* (IngestionResult) — no DB
writes (persistence is SP3; the Supabase reads that build the inputs are the SP4
orchestrator's job). This module is pure over its injected inputs (followed
interest ids, the taxonomy map, the adapter), so it is fully unit-testable with a
mocked adapter and no network.

Example:
    >>> result = await ingest_active_interests(
    ...     followed_interest_ids=["arsenal"],
    ...     interest_nodes=interest_nodes,
    ...     adapter=mock_adapter,
    ... )
    >>> result.canonical_stories[0].story_outlet_count
    3
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.ancestor_tagging import merge_story_tags
from agents.ingestion.authority_domains import domains_for_category
from agents.ingestion.dedup import StoryClusterer, normalize_url
from agents.ingestion.models import (
    ActiveInterest,
    CanonicalStory,
    IngestionResult,
    InterestNode,
)
from agents.pipeline.categories import TOPIC_CATEGORIES
from agents.shared.exceptions import AdapterFetchError, IngestionError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

# Reason: the pipeline runs daily at midnight ET, so a 24h window keeps the
# catalog to "today's" news; a missed run is self-healed by the 05:00 ET
# readiness cron (Phase 7c SP3) rather than a wider lookback.
_DEFAULT_LOOKBACK_DAYS = 1

# Reason: M4 trusted-outlet gap-fill. A category cell whose first fetch returns fewer
# than the floor is re-fetched ONCE with a window widened by the bounded delta — a
# thin category should widen, not silently under-deliver ("is that really all?"). The
# delta is capped at the DOC 3-day ceiling by ``_compute_timespan`` downstream, so the
# widen never exceeds the source window. Tuning values (PRD open item); the MECHANISM
# (one bounded widen + fail-loud log) is the contract, not the constants.
_DEFAULT_MIN_STORIES_PER_CATEGORY = 5
_DEFAULT_GAP_FILL_WIDEN = timedelta(days=1)


@dataclass
class TrustedOutletResult:
    """Output of one trusted-outlet (category + domain-set) ingestion batch.

    Distinct from ``IngestionResult`` (the interest-keyed shape): M4 keys the fetch
    on category, so the pool is grouped by category and the resilience/gap-fill
    bookkeeping is per-category cell.

    Attributes:
        canonical_stories_by_category: Deduped canonical pool per category cell.
        failed_categories: Categories whose fetch raised (skipped — batch survived).
        under_filled_categories: Categories still below the floor after gap-fill
            (fail-loud signal — surfaced, not silently swallowed).
        total_candidates_fetched: Raw candidate count across all cells (monitoring).
    """

    canonical_stories_by_category: dict[str, list[CanonicalStory]] = field(
        default_factory=dict
    )
    failed_categories: list[str] = field(default_factory=list)
    under_filled_categories: list[str] = field(default_factory=list)
    total_candidates_fetched: int = 0


def build_active_interest_set(
    followed_interest_ids: Iterable[str],
    interest_nodes: dict[str, InterestNode],
) -> list[ActiveInterest]:
    """Build the distinct active-interest set — followed interests with a query.

    The active-interest set is the *unit of ingestion*: the distinct union of all
    users' followed interest nodes (``user_interest_profile.profile_interest_id``)
    that carry a non-empty ``interest_search_query``. A followed interest with no
    query (or unknown to the taxonomy) is skipped with a warning — it cannot be
    ingested, but it does not abort the batch.

    Args:
        followed_interest_ids: All users' followed interest ids (may contain
            duplicates across users; deduped here).
        interest_nodes: Taxonomy map ``interest_id -> InterestNode``.

    Returns:
        The distinct, ingestible active interests (deterministic order: by slug).

    Raises:
        IngestionError: If ``followed_interest_ids`` is empty — there are no user
            profiles to ingest for (the DoD's empty-safe fail-loud).

    Example:
        >>> build_active_interest_set([], {})
        Traceback (most recent call last):
        agents.shared.exceptions.IngestionError: ...
    """
    all_ids = list(followed_interest_ids)
    if not all_ids:
        raise IngestionError(
            message="active-interest set is empty — no user profiles to ingest for",
            fix_suggestion="Seed at least one user_interest_profile (Phase 1e) before running ingestion",
        )

    active: list[ActiveInterest] = []
    seen: set[str] = set()
    skipped_no_query = 0
    skipped_unknown = 0
    for interest_id in all_ids:
        if interest_id in seen:
            continue
        seen.add(interest_id)

        node = interest_nodes.get(interest_id)
        if node is None:
            skipped_unknown += 1
            logger.warning(
                "active_interest_unknown_node",
                interest_id=interest_id,
                fix_suggestion="A followed interest is missing from the taxonomy map — backfill the interests read",
            )
            continue

        query = (node.interest_search_query or "").strip()
        if not query:
            skipped_no_query += 1
            logger.debug(
                "active_interest_no_search_query",
                interest_id=interest_id,
                interest_slug=node.interest_slug,
            )
            continue

        active.append(
            ActiveInterest(
                interest_id=interest_id,
                interest_slug=node.interest_slug,
                interest_search_query=query,
            )
        )

    active.sort(key=lambda ai: ai.interest_slug)
    logger.info(
        "active_interest_set_built",
        followed_total=len(all_ids),
        distinct_followed=len(seen),
        active_interests=len(active),
        skipped_no_query=skipped_no_query,
        skipped_unknown=skipped_unknown,
    )
    return active


async def ingest_active_interests(
    followed_interest_ids: Iterable[str],
    interest_nodes: dict[str, InterestNode],
    adapter: BaseNewsAdapter,
    *,
    clusterer: StoryClusterer | None = None,
    since_utc: datetime | None = None,
    extract_bodies: bool = True,
    resolve_existing_story_ids: Callable[[list[str]], dict[str, str]] | None = None,
) -> IngestionResult:
    """Run one interest-keyed ingestion batch → a deduped, tagged story pool.

    Steps: build the active-interest set → search per interest (stamping the
    matched interest on each candidate) → cluster into canonical stories with
    outlet counts → resolve cross-day identity (reuse an existing story id when a
    member URL was seen before) → (optionally) extract each NEW story's body →
    ancestor-tag each story into ``story_interests`` payloads.

    Args:
        followed_interest_ids: All users' followed interest ids (deduped inside).
        interest_nodes: Taxonomy map ``interest_id -> InterestNode``.
        adapter: A BaseNewsAdapter (GDELT in prod; a mock in tests).
        clusterer: Optional StoryClusterer (defaults to one with standard thresholds).
        since_utc: Lower-bound publish time (defaults to ~1 day ago).
        extract_bodies: When True, fetch + extract each canonical story's body.
        resolve_existing_story_ids: Optional ``(normalized_urls) -> {url: story_id}``
            cross-day identity resolver (migration 0006 / ``story_url_aliases``).
            When given, a freshly-clustered story whose ANY member URL is already
            aliased REUSES that ``story_id`` (so produce-once + don't-repeat hold
            across days) and SKIPS body extraction (it's already produced). ``None``
            keeps the function pure (no DB) — the unit-test/fixture path.

    Returns:
        An IngestionResult with the canonical pool, story_interest tag payloads,
        the active-interest set, and the raw candidate count.

    Raises:
        IngestionError: If there are no user profiles (via build_active_interest_set).

    Example:
        >>> result = await ingest_active_interests(["arsenal"], nodes, adapter)
        >>> len(result.story_interest_tags) >= len(result.canonical_stories)
        True
    """
    clusterer = clusterer or StoryClusterer()
    since = since_utc or (
        datetime.now(timezone.utc) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    )

    active = build_active_interest_set(followed_interest_ids, interest_nodes)

    # --- Fan out searches; stamp each candidate's matched interest ---
    # Adapters exposing search_active_interests (e.g. GdeltBigQueryAdapter) ingest
    # the WHOLE active-interest set in ONE call and return pre-stamped candidates
    # (no per-IP throttle, no 250-record cap); others fall back to one query per
    # interest (the DOC path), stamping the matched interest after each fetch.
    all_candidates = []
    failed_interests = 0
    batch_search = getattr(adapter, "search_active_interests", None)
    if callable(batch_search):
        try:
            all_candidates = await batch_search(active, since)
        except AdapterFetchError as exc:
            # Reason: the batched call is all-or-nothing — a failure skips the run,
            # not one interest, so mark the whole set failed (fail loud, not silent).
            failed_interests = len(active)
            logger.warning(
                "ingest_batch_search_failed",
                active_interests=len(active),
                error_message=str(exc)[:300],
                fix_suggestion="Batched source query failed; the whole batch is skipped this run",
            )
            all_candidates = []
    else:
        for active_interest in active:
            try:
                candidates = await adapter.search(
                    active_interest.interest_search_query, since
                )
            except AdapterFetchError as exc:
                # Reason: one interest's source failure must not abort the whole batch.
                failed_interests += 1
                logger.warning(
                    "ingest_interest_search_failed",
                    interest_slug=active_interest.interest_slug,
                    error_message=str(exc)[:300],
                    fix_suggestion="Source query failed; this interest is skipped this run",
                )
                continue
            for candidate in candidates:
                candidate.candidate_matched_interest_id = active_interest.interest_id
                candidate.candidate_matched_interest_slug = (
                    active_interest.interest_slug
                )
            all_candidates.extend(candidates)

    total_candidates = len(all_candidates)

    # --- Dedup/cluster into the canonical story pool (with outlet counts) ---
    canonical_stories = clusterer.cluster_candidates(all_candidates)

    # --- Cross-day identity: reuse an existing story id when a member URL was seen
    # before, so a multi-day event keeps ONE id (produce-once + don't-repeat hold).
    # The newly-derived canonical_story_id is replaced in place, so the ancestor
    # tags built below FK to the reused id. Already-persisted stories skip the
    # (paid) body fetch — they will be gated out of production by their digest.
    already_persisted_ids: set[str] = set()
    if resolve_existing_story_ids is not None and canonical_stories:
        member_urls_by_story = {
            story.canonical_story_id: [
                normalized
                for member_url in story.member_candidate_ids
                if (normalized := normalize_url(member_url))
            ]
            for story in canonical_stories
        }
        all_urls = sorted(
            {url for urls in member_urls_by_story.values() for url in urls}
        )
        existing_by_url = resolve_existing_story_ids(all_urls) or {}
        for story in canonical_stories:
            for url in member_urls_by_story[story.canonical_story_id]:
                existing_id = existing_by_url.get(url)
                if existing_id:
                    story.canonical_story_id = existing_id
                    already_persisted_ids.add(existing_id)
                    break

    # --- Extract each NEW canonical story's body from its representative member ---
    if extract_bodies and canonical_stories:
        candidate_by_external_id = {c.candidate_external_id: c for c in all_candidates}
        for story in canonical_stories:
            if story.canonical_story_id in already_persisted_ids:
                continue  # already produced on a prior day — skip the paid re-fetch
            representative = candidate_by_external_id.get(
                story.canonical_representative_external_id
            )
            if representative is None:
                continue
            enriched = await adapter.extract_body(representative)
            story.canonical_body_text = enriched.candidate_body_text

    # --- Ancestor-tag each canonical story into story_interests payloads ---
    story_interest_tags = []
    for story in canonical_stories:
        story_interest_tags.extend(
            merge_story_tags(
                story.canonical_story_id,
                story.canonical_matched_interest_ids,
                interest_nodes,
            )
        )

    logger.info(
        "interest_keyed_ingestion_completed",
        active_interests=len(active),
        failed_interests=failed_interests,
        total_candidates=total_candidates,
        canonical_stories=len(canonical_stories),
        reused_existing_story_ids=len(already_persisted_ids),
        story_interest_tags=len(story_interest_tags),
    )
    return IngestionResult(
        canonical_stories=canonical_stories,
        story_interest_tags=story_interest_tags,
        active_interests=active,
        total_candidates_fetched=total_candidates,
    )


async def ingest_trusted_outlets(
    adapter: BaseNewsAdapter,
    *,
    categories: Sequence[str] = TOPIC_CATEGORIES,
    domain_accessor: Callable[[str], list[str]] = domains_for_category,
    clusterer: StoryClusterer | None = None,
    since_utc: datetime | None = None,
    min_stories_per_category: int = _DEFAULT_MIN_STORIES_PER_CATEGORY,
    gap_fill_widen: timedelta = _DEFAULT_GAP_FILL_WIDEN,
) -> TrustedOutletResult:
    """Fetch the day's biggest stories per category from each category's trusted domains.

    The M4 trusted-outlet fetch: one cell per (category, curated domain-set). For each
    category the adapter is asked for that category's curated authority domains (routed
    through the DOC ``domainis:`` builder by default — the adapter's ``search`` accepts
    a ``domains`` kwarg), the returned candidates are clustered into a per-category
    canonical pool. Replaces keyword-first fetch for news; ``ingest_active_interests``
    is retained (a later hybrid stays possible), so this is additive, not a removal.

    Resilience is **per-cell**: one category's fetch raising ``AdapterFetchError`` skips
    ONLY that category (recorded in ``failed_categories``) — the batch never aborts
    (mirrors the keyword path's ``failed_interests`` loop).

    Gap-fill is **bounded**: when a cell returns fewer clustered stories than
    ``min_stories_per_category`` it is re-fetched EXACTLY once with the time window
    widened by ``gap_fill_widen`` (``since`` pushed back). If still short the category
    is recorded in ``under_filled_categories`` and an ``under_filled`` warning is logged
    (fail loud, never silent). The widen happens at most once per cell (no unbounded
    loop). The DOC ``_compute_timespan`` caps the effective window at the 3-day ceiling.

    Pure over its injected inputs (categories, domain accessor, adapter, clock), so it
    is fully unit-testable with a fake adapter and no network.

    Args:
        adapter: A BaseNewsAdapter whose ``search`` accepts a ``domains`` kwarg
            (GDELT DOC in prod; a fake in tests).
        categories: The category cells to fetch (defaults to the 8 topic roots).
        domain_accessor: ``category -> [domain, …]`` (defaults to SP1's accessor;
            injectable for tests).
        clusterer: Optional StoryClusterer (defaults to standard thresholds).
        since_utc: Lower-bound publish time for the FIRST fetch (defaults to ~1 day
            ago); gap-fill widens it per cell.
        min_stories_per_category: Floor below which a cell triggers the one widen.
        gap_fill_widen: How much earlier ``since`` is pushed for the widened re-fetch.

    Returns:
        A TrustedOutletResult with the per-category canonical pools, the failed and
        under-filled category lists, and the raw candidate count.

    Note:
        Theme→category tagging (``assign_category``) is M2's surface and is NOT done
        here — this entry only fetches + clusters the trusted-outlet pool per category.
    """
    clusterer = clusterer or StoryClusterer()
    base_since = since_utc or (
        datetime.now(timezone.utc) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    )

    result = TrustedOutletResult()
    for category in categories:
        domains = domain_accessor(category)
        try:
            candidates = await adapter.search("", base_since, domains=domains)
        except AdapterFetchError as exc:
            # Reason: one category's source failure must not blank the whole feed.
            result.failed_categories.append(category)
            logger.warning(
                "trusted_outlet_cell_failed",
                category=category,
                error_message=str(exc)[:300],
                fix_suggestion="Trusted-outlet fetch failed for this category; skipped this run",
            )
            continue

        stories = clusterer.cluster_candidates(candidates)
        candidate_count = len(candidates)

        # --- Bounded gap-fill: ONE widened-window re-fetch when under the floor ---
        if len(stories) < min_stories_per_category:
            widened_since = base_since - gap_fill_widen
            logger.warning(
                "trusted_outlet_gap_fill",
                category=category,
                stories=len(stories),
                floor=min_stories_per_category,
                widened_since=widened_since.isoformat(),
                fix_suggestion="Cell under floor; widening the window once and re-fetching",
            )
            try:
                refetched = await adapter.search("", widened_since, domains=domains)
            except AdapterFetchError as exc:
                # A failed widen still must not abort the batch — keep the thin pool
                # and flag under-filled (the widen is best-effort, bounded to once).
                logger.warning(
                    "trusted_outlet_gap_fill_failed",
                    category=category,
                    error_message=str(exc)[:300],
                    fix_suggestion="Widened re-fetch failed; keeping the first-pass pool",
                )
                refetched = []
            if refetched:
                stories = clusterer.cluster_candidates(refetched)
                candidate_count = len(refetched)
            if len(stories) < min_stories_per_category:
                # Still short after the single bounded widen — surface it (fail loud).
                result.under_filled_categories.append(category)
                logger.warning(
                    "trusted_outlet_under_filled",
                    category=category,
                    stories=len(stories),
                    floor=min_stories_per_category,
                    fix_suggestion="Category still below floor after one widen; "
                    "broaden the curated domain set or lower the floor",
                )

        result.canonical_stories_by_category[category] = stories
        result.total_candidates_fetched += candidate_count

    logger.info(
        "trusted_outlet_ingestion_completed",
        categories=len(categories),
        failed_categories=len(result.failed_categories),
        under_filled_categories=len(result.under_filled_categories),
        total_candidates=result.total_candidates_fetched,
    )
    return result
