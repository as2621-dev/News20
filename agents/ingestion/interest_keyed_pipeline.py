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

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.ancestor_tagging import merge_story_tags
from agents.ingestion.dedup import StoryClusterer, normalize_url
from agents.ingestion.models import (
    ActiveInterest,
    IngestionResult,
    InterestNode,
)
from agents.shared.exceptions import AdapterFetchError, IngestionError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

# Reason: GDELT timespan is coarse (1d–3d); a 2-day lookback is a sensible daily
# default that tolerates a missed run without re-ingesting stale news.
_DEFAULT_LOOKBACK_DAYS = 2


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
        since_utc: Lower-bound publish time (defaults to ~2 days ago).
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

    # --- Fan out one search per active interest; stamp the matched interest ---
    all_candidates = []
    failed_interests = 0
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
            candidate.candidate_matched_interest_slug = active_interest.interest_slug
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
