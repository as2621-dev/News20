"""Cross-day story-id bridge + persistence wiring (Milestone M3b, Sub-phase 4).

This is the corner-locking step of the online clusterer: it turns the in-memory
:class:`ClusterRun` (produced by ``online_clusterer.cluster_candidates``) into

    1. a ``cluster_id -> story_id`` map that PRESERVES a multi-day event's story id
       across the day boundary (cross-day continuity), and
    2. persisted rows in ``story_clusters`` / ``story_cluster_members`` via the M3a
       ``cluster_store`` repository.

Cross-day continuity (the load-bearing reuse)
---------------------------------------------
A real-world event that runs for several days is re-clustered every day (a new
batch, possibly a different earliest member, so a freshly minted ``cluster_id``).
For produce-once / don't-repeat to stay correct, that event must keep ONE stable
``story_id`` across days. We get that by normalizing each cluster's member URLs and
asking the injected resolver (``daily_batch.build_story_id_resolver``'s contract)
which of them already alias to an existing ``stories.story_id``. If ANY member of a
cluster maps to an existing id, we REUSE it instead of minting a new one.

URL-normalization parity (the silent-failure guard)
---------------------------------------------------
The resolver looks rows up by ``story_url_aliases.alias_normalized_url``, which the
WRITE path (``persist_helpers.build_story_url_alias_rows``) keys with
``agents.ingestion.dedup.normalize_url``. This module reuses that SAME function
verbatim — if member URLs were normalized any other way, every cross-day lookup
would silently miss and each day would mint a fresh id, quietly breaking
produce-once. Do not introduce a second normalizer here.

Every database call goes through an INJECTED supabase client (mocked in tests,
CLAUDE.md §6) — no connection, no secret, no real DB / Gemini in any test.
"""

from __future__ import annotations

from typing import Any, Callable

from agents.ingestion.dedup import normalize_url
from agents.pipeline.clustering.cluster_store import add_cluster_members, upsert_cluster
from agents.pipeline.clustering.engine_models import ClusterRun
from agents.shared.logger import get_logger

logger = get_logger("pipeline.clustering.continuity")


def resolve_cluster_story_ids(
    run: ClusterRun,
    *,
    resolve_existing_story_ids: Callable[[list[str]], dict[str, str]],
    mint_story_id: Callable[[], str],
) -> dict[str, str]:
    """Map every cluster in ``run`` to a story id, reusing an existing id when one exists.

    For each cluster, its members' URLs are normalized with the canonical
    ``normalize_url`` (the SAME function ``story_url_aliases`` is keyed on — see the
    module docstring), the injected resolver is called ONCE over the union of all
    normalized URLs, and then:

        - if ANY of a cluster's normalized member URLs already aliases to an existing
          ``story_id``, that id is REUSED (cross-day continuity — a multi-day event
          keeps its id, so produce-once / don't-repeat stay correct);
        - otherwise a fresh id is minted via ``mint_story_id()``.

    Tie-break (a cluster mapping to MORE THAN ONE existing story id): pick the
    lexicographically smallest ``story_id`` among the matches. This is deterministic
    and reproducible from the resolver output alone (the resolver returns no
    seen-count / recency signal to prefer "most seen", so a stable string ordering is
    the defensible choice). The case is logged so a real multi-id collision is visible.

    Args:
        run: The clustering run whose clusters need story ids. ``run.members`` carries
            every member row (including dropped near-dup reprints), so a reprint whose
            URL aliases to a prior-day story still contributes to continuity.
        resolve_existing_story_ids: The injected resolver, contract
            ``(normalized_urls) -> {normalized_url: existing_story_id}`` (the
            ``daily_batch.build_story_id_resolver`` callable). Called exactly once.
        mint_story_id: Zero-arg callable returning a fresh unique story id, called once
            per cluster that has no existing alias.

    Returns:
        A ``cluster_id -> story_id`` map with one entry per cluster in ``run.clusters``.

    Example:
        >>> from datetime import datetime, timezone
        >>> from agents.pipeline.clustering.models import ClusterMember, StoryCluster
        >>> now = datetime(2026, 6, 19, tzinfo=timezone.utc)
        >>> run = ClusterRun(
        ...     clusters=[StoryCluster(
        ...         cluster_id="clu-1", cluster_category="tech",
        ...         cluster_first_seen_utc=now, cluster_last_seen_utc=now,
        ...     )],
        ...     members=[ClusterMember(
        ...         cluster_id="clu-1", member_url="https://reuters.com/x", member_seen_utc=now,
        ...     )],
        ... )
        >>> resolve_cluster_story_ids(
        ...     run,
        ...     resolve_existing_story_ids=lambda urls: {"https://reuters.com/x": "story-7"},
        ...     mint_story_id=lambda: "minted",
        ... )
        {'clu-1': 'story-7'}
    """
    # Reason: group each cluster's member URLs once, normalizing with the canonical
    # normalizer so the keys match the alias write-path exactly (parity = no silent miss).
    normalized_urls_by_cluster: dict[str, list[str]] = {}
    for member in run.members:
        normalized = normalize_url(member.member_url)
        if not normalized:
            continue
        normalized_urls_by_cluster.setdefault(member.cluster_id, []).append(normalized)

    # Reason: one resolver call over the union of all normalized URLs (the resolver
    # chunks its own .in_() lookups), not one call per cluster.
    all_normalized_urls = sorted(
        {url for urls in normalized_urls_by_cluster.values() for url in urls}
    )
    existing_by_url = resolve_existing_story_ids(all_normalized_urls) if all_normalized_urls else {}

    cluster_story_ids: dict[str, str] = {}
    reused_count = 0
    minted_count = 0
    for cluster in run.clusters:
        cluster_urls = normalized_urls_by_cluster.get(cluster.cluster_id, [])
        existing_ids = {
            existing_by_url[url] for url in cluster_urls if url in existing_by_url
        }
        if existing_ids:
            # Tie-break: smallest story id when a cluster's members span >1 existing id.
            story_id = min(existing_ids)
            cluster_story_ids[cluster.cluster_id] = story_id
            reused_count += 1
            if len(existing_ids) > 1:
                logger.info(
                    "resolve_cluster_story_ids_multi_id_tiebreak",
                    cluster_id=cluster.cluster_id,
                    candidate_story_ids=sorted(existing_ids),
                    chosen_story_id=story_id,
                )
        else:
            cluster_story_ids[cluster.cluster_id] = mint_story_id()
            minted_count += 1

    logger.info(
        "resolve_cluster_story_ids_completed",
        cluster_count=len(run.clusters),
        reused_count=reused_count,
        minted_count=minted_count,
    )
    return cluster_story_ids


def persist_run(client: Any, run: ClusterRun) -> None:
    """Persist a clustering run: upsert each cluster, then add its member rows.

    For every cluster in ``run.clusters`` this calls ``cluster_store.upsert_cluster``
    (rolling the centroid / counts / ``last_seen`` forward idempotently), then groups
    ``run.members`` by ``cluster_id`` and calls ``cluster_store.add_cluster_members``
    once per cluster (a single batched member upsert per cluster). The supabase client
    is INJECTED (mocked in tests — no real DB).

    A cluster with no members in this run (it should not happen, but is defended) still
    gets its row upserted; ``add_cluster_members`` is a no-op on an empty list.

    Args:
        client: A service-role supabase client (injected; mocked in tests).
        run: The clustering run to persist (clusters + members).

    Example:
        >>> persist_run(client, run)  # doctest: +SKIP
    """
    members_by_cluster: dict[str, list] = {}
    for member in run.members:
        members_by_cluster.setdefault(member.cluster_id, []).append(member)

    for cluster in run.clusters:
        upsert_cluster(client, cluster)
        add_cluster_members(client, cluster.cluster_id, members_by_cluster.get(cluster.cluster_id, []))

    logger.info(
        "persist_run_completed",
        cluster_count=len(run.clusters),
        member_count=len(run.members),
    )
