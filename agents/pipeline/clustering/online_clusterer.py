"""Online assign-or-spawn clustering orchestrator (Milestone M3b, Sub-phase 3).

This is the loop that turns a batch of deduped candidate articles into rolling story
clusters. It composes the M3a/M3b primitives — ``near_dup`` (cheap reprint prefilter),
``embeddings`` (paid Gemini vectors), ``blocking`` (time + category narrowing), and
``assign`` (cosine join-vs-spawn decision + running-mean centroid) — into the four-step
pipeline of spec §2C:

    1. NEAR-DUP PREFILTER — group near-duplicate reprints; keep one representative per
       group (smallest ``input_index``). Dropped reprints are NOT discarded — they fold
       into their representative's cluster as ``ClusterMember`` rows so the member /
       outlet counts reflect the true coverage of the event.
    2. EMBED — embed only the representatives' text (one paid vector per kept story).
    3. BLOCK + MATCH — for each representative (deterministic order), narrow the active
       clusters (existing + spawned-this-run) to a time+category block, then pick the
       best cosine match.
    4. ASSIGN or SPAWN — ``should_assign`` decides: join the matched cluster (append
       members, bump counts, advance ``last_seen``, fold the centroid) or spawn a new
       one (mint id, centroid = the representative's embedding).

``clusters`` in the returned :class:`ClusterRun` contains EVERY cluster touched this run
— both freshly spawned clusters AND pre-existing clusters that gained a member — because
the SP4 persistence step upserts whatever this returns, and an updated existing cluster
must be re-persisted with its new centroid / counts / ``last_seen``. Existing clusters
that were untouched are NOT included.

Cross-day continuity (a candidate today matching a cluster last seen yesterday) falls out
for free: ``existing_clusters`` are passed in by the caller (the SP4 window load), and an
in-window match against one of them joins it with no new id minted — the same story id is
preserved downstream.

Every test mocks ``embed_texts`` (no real Gemini) and injects ``mint_cluster_id``
(CLAUDE.md §6).

Example:
    >>> import asyncio
    >>> from datetime import datetime, timezone
    >>> from unittest.mock import AsyncMock, patch
    >>> candidates = [
    ...     ClusterInput(
    ...         input_index=0, input_text="quake hits coastal city damaging homes",
    ...         input_url="https://a.com/x", input_outlet="a.com",
    ...         input_published_utc=datetime(2026, 6, 19, tzinfo=timezone.utc),
    ...         input_provisional_category="world",
    ...     )
    ... ]
    >>> ids = iter(["clu-1"])
    >>> with patch(
    ...     "agents.pipeline.clustering.online_clusterer.embed_texts",
    ...     new=AsyncMock(return_value=[[1.0, 0.0]]),
    ... ):
    ...     run = asyncio.run(cluster_candidates(
    ...         candidates, llm_client=None, existing_clusters=[],
    ...         mint_cluster_id=lambda: next(ids),
    ...     ))
    >>> run.input_cluster_map
    {0: 'clu-1'}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from agents.pipeline.clustering.assign import (
    DEFAULT_TAU_ASSIGN,
    best_match,
    should_assign,
    update_centroid_running_mean,
)
from agents.pipeline.clustering.blocking import DEFAULT_WINDOW_HOURS, select_block
from agents.pipeline.clustering.continuity import persist_run, resolve_cluster_story_ids
from agents.pipeline.clustering.embeddings import embed_texts
from agents.pipeline.clustering.engine_models import ClusterInput, ClusterRun
from agents.pipeline.clustering.models import ClusterMember, StoryCluster
from agents.pipeline.clustering.near_dup import NearDupItem, group_near_duplicates
from agents.shared.logger import get_logger

if TYPE_CHECKING:
    from agents.pipeline.llm_clients import LLMClient

logger = get_logger("pipeline.clustering.online_clusterer")


async def cluster_candidates(
    candidates: list[ClusterInput],
    *,
    llm_client: "LLMClient",
    existing_clusters: list[StoryCluster],
    mint_cluster_id: Callable[[], str],
    tau_assign: float = DEFAULT_TAU_ASSIGN,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> ClusterRun:
    """Cluster a batch of candidate articles into rolling story clusters.

    Runs the four-step online pipeline (near-dup prefilter → embed → block+match →
    assign-or-spawn) over ``candidates``. Each kept representative either JOINS the
    nearest in-window existing/spawned cluster (cosine ``>= tau_assign``) or SPAWNS a new
    cluster. Dropped near-duplicate reprints are attached as members of whatever cluster
    their representative lands in, so cluster member / outlet counts reflect real coverage.

    Representatives are processed in ascending ``input_index`` order so the run is
    deterministic (id minting order, centroid fold order, and ``clusters`` ordering are
    all reproducible for tests).

    Args:
        candidates: The batch of candidate articles. ``input_index`` must be unique
            within the batch (it keys ``input_cluster_map``).
        llm_client: The shared ``LLMClient`` forwarded to ``embed_texts`` (mocked in
            tests). Unused when ``candidates`` is empty.
        existing_clusters: Active clusters loaded from the cross-day window (SP4). A
            candidate matching one of these joins it — no new id is minted — which is how
            cross-day continuity is realized.
        mint_cluster_id: A zero-arg callable returning a fresh unique cluster id, called
            once per spawned cluster.
        tau_assign: Cosine join threshold (default :data:`DEFAULT_TAU_ASSIGN`, 0.75).
        window_hours: Time-window half-width for blocking (default
            :data:`DEFAULT_WINDOW_HOURS`, 48; edge inclusive).

    Returns:
        A :class:`ClusterRun` whose ``clusters`` holds every cluster created OR updated
        this run (spawned + touched existing), ``members`` holds every member row appended
        this run (representatives + their dropped reprints), and ``input_cluster_map`` maps
        each kept representative's ``input_index`` to its cluster id.

    Example:
        >>> # See module docstring for a runnable (mocked) example.
        >>> ClusterRun(clusters=[], members=[]).input_cluster_map
        {}
    """
    if not candidates:
        logger.info("cluster_candidates_empty", candidate_count=0)
        return ClusterRun()

    candidate_by_index = {candidate.input_index: candidate for candidate in candidates}

    # Step 1 — NEAR-DUP PREFILTER. Group reprints; the smallest input_index in each group
    # is the representative, the rest fold in as members of the rep's cluster.
    near_dup_items = [
        NearDupItem(item_index=candidate.input_index, item_text=candidate.input_text)
        for candidate in candidates
    ]
    groups = group_near_duplicates(near_dup_items)
    # Reason: deterministic order — representatives ascending so id minting / centroid
    # folding / clusters ordering are reproducible across runs.
    representative_to_group: dict[int, list[int]] = {
        min(group_indices): sorted(group_indices) for group_indices in groups
    }
    representative_indices = sorted(representative_to_group.keys())

    # Step 2 — EMBED only the representatives (one paid vector per kept story), aligned 1:1.
    representative_texts = [candidate_by_index[rep_index].input_text for rep_index in representative_indices]
    representative_embeddings = await embed_texts(representative_texts, llm_client=llm_client)
    embedding_by_representative = dict(zip(representative_indices, representative_embeddings, strict=True))

    logger.info(
        "cluster_candidates_started",
        candidate_count=len(candidates),
        representative_count=len(representative_indices),
        existing_cluster_count=len(existing_clusters),
        window_hours=window_hours,
        tau_assign=tau_assign,
    )

    # Clusters created or mutated this run, keyed by id so a join + later re-match touch
    # the same object. Existing clusters that gain a member are tracked here too (so SP4
    # re-persists them); untouched existing clusters are not.
    touched_clusters_by_id: dict[str, StoryCluster] = {}
    members: list[ClusterMember] = []
    input_cluster_map: dict[int, str] = {}

    for rep_index in representative_indices:
        representative = candidate_by_index[rep_index]
        rep_embedding = embedding_by_representative[rep_index]

        # The members this representative contributes: itself + every dropped reprint.
        member_indices = representative_to_group[rep_index]

        # Step 3 — BLOCK + MATCH against existing clusters PLUS clusters spawned/updated
        # earlier this run (so two same-event candidates in one batch land together).
        active_clusters = _merge_active_clusters(existing_clusters, touched_clusters_by_id)
        block = select_block(
            active_clusters,
            candidate_published_utc=representative.input_published_utc,
            window_hours=window_hours,
            category=representative.input_provisional_category,
        )
        matched_cluster, score = best_match(rep_embedding, block)

        # Step 4 — ASSIGN or SPAWN.
        if matched_cluster is not None and should_assign(score, tau_assign=tau_assign):
            cluster = _assign_to_cluster(
                matched_cluster,
                representative=representative,
                member_indices=member_indices,
                candidate_by_index=candidate_by_index,
                rep_embedding=rep_embedding,
                members=members,
            )
            logger.info(
                "cluster_candidate_joined",
                input_index=rep_index,
                cluster_id=cluster.cluster_id,
                score=score,
                member_count=cluster.cluster_member_count,
            )
        else:
            cluster = _spawn_cluster(
                representative=representative,
                member_indices=member_indices,
                candidate_by_index=candidate_by_index,
                rep_embedding=rep_embedding,
                mint_cluster_id=mint_cluster_id,
                members=members,
            )
            logger.info(
                "cluster_candidate_spawned",
                input_index=rep_index,
                cluster_id=cluster.cluster_id,
                best_score=score,
                member_count=cluster.cluster_member_count,
            )

        touched_clusters_by_id[cluster.cluster_id] = cluster
        input_cluster_map[rep_index] = cluster.cluster_id

    run = ClusterRun(
        clusters=list(touched_clusters_by_id.values()),
        members=members,
        input_cluster_map=input_cluster_map,
    )
    logger.info(
        "cluster_candidates_completed",
        candidate_count=len(candidates),
        cluster_count=len(run.clusters),
        member_count=len(run.members),
    )
    return run


def _merge_active_clusters(
    existing_clusters: list[StoryCluster],
    touched_clusters_by_id: dict[str, StoryCluster],
) -> list[StoryCluster]:
    """Merge passed-in existing clusters with this-run's touched clusters for blocking.

    A touched cluster (spawned or already-joined this run) overrides its same-id existing
    counterpart so the freshest centroid / ``last_seen`` is what later candidates match
    against. Spawned-this-run clusters (no existing counterpart) are appended.

    Args:
        existing_clusters: The cross-day window load handed to the orchestrator.
        touched_clusters_by_id: Clusters created or mutated earlier in this run.

    Returns:
        The active-cluster set the next candidate should block against, existing-first
        then any purely-new spawned clusters (stable for deterministic blocking).
    """
    merged: list[StoryCluster] = []
    seen_ids: set[str] = set()
    for cluster in existing_clusters:
        current = touched_clusters_by_id.get(cluster.cluster_id, cluster)
        merged.append(current)
        seen_ids.add(current.cluster_id)
    for cluster_id, cluster in touched_clusters_by_id.items():
        if cluster_id not in seen_ids:
            merged.append(cluster)
    return merged


def _assign_to_cluster(
    cluster: StoryCluster,
    *,
    representative: ClusterInput,
    member_indices: list[int],
    candidate_by_index: dict[int, ClusterInput],
    rep_embedding: list[float],
    members: list[ClusterMember],
) -> StoryCluster:
    """Fold a representative (and its reprints) into an existing cluster, in place.

    Appends one :class:`ClusterMember` per member index to ``members``, bumps the
    cluster's member count, recomputes the distinct-outlet count over all members,
    advances ``cluster_last_seen_utc`` to the latest member time, and folds the
    representative's embedding into the centroid via the running mean. The centroid is
    weighted by the count BEFORE this representative was added (one fold per representative
    — reprints share the rep's vector and are not embedded, so they do not re-fold).

    Args:
        cluster: The matched cluster to update (mutated and returned).
        representative: The kept candidate joining the cluster.
        member_indices: Indices of the representative plus its dropped reprints.
        candidate_by_index: Lookup from ``input_index`` to its ``ClusterInput``.
        rep_embedding: The representative's embedding (the only vector folded).
        members: The run-wide member accumulator (appended to in place).

    Returns:
        The same ``cluster`` object, updated.
    """
    new_members = _build_members(
        cluster.cluster_id,
        member_indices=member_indices,
        candidate_by_index=candidate_by_index,
    )
    members.extend(new_members)

    # Reason: fold the centroid ONCE, weighted by the pre-join member count, before the
    # count is bumped — so the running mean stays consistent with member_count.
    cluster.cluster_centroid = update_centroid_running_mean(
        cluster.cluster_centroid if cluster.cluster_centroid is not None else rep_embedding,
        cluster.cluster_member_count,
        rep_embedding,
    )
    cluster.cluster_member_count += len(new_members)
    # Reason: prior members of an existing cluster are NOT in this run's `members` list
    # (cross-day join), so the distinct count over this-run members is a LOWER bound for
    # an existing cluster. Never let outlet diversity regress below its persisted value.
    cluster.cluster_outlet_count = max(
        cluster.cluster_outlet_count,
        _distinct_outlet_count(cluster.cluster_id, members),
    )
    cluster.cluster_last_seen_utc = max(
        cluster.cluster_last_seen_utc,
        *(member.member_seen_utc for member in new_members),
    )
    return cluster


def _spawn_cluster(
    *,
    representative: ClusterInput,
    member_indices: list[int],
    candidate_by_index: dict[int, ClusterInput],
    rep_embedding: list[float],
    mint_cluster_id: Callable[[], str],
    members: list[ClusterMember],
) -> StoryCluster:
    """Mint a brand-new cluster seeded by a representative (and its reprints).

    The new cluster's centroid is the representative's embedding, its category is the
    representative's provisional category (M3c overwrites this), its first/last-seen span
    the member times, and its counts reflect the actual members attached.

    Args:
        representative: The kept candidate seeding the new cluster.
        member_indices: Indices of the representative plus its dropped reprints.
        candidate_by_index: Lookup from ``input_index`` to its ``ClusterInput``.
        rep_embedding: The representative's embedding (becomes the seed centroid).
        mint_cluster_id: Zero-arg id minter, called exactly once here.
        members: The run-wide member accumulator (appended to in place).

    Returns:
        The freshly minted :class:`StoryCluster`.
    """
    cluster_id = mint_cluster_id()
    new_members = _build_members(
        cluster_id,
        member_indices=member_indices,
        candidate_by_index=candidate_by_index,
    )
    members.extend(new_members)

    member_times = [member.member_seen_utc for member in new_members]
    cluster = StoryCluster(
        cluster_id=cluster_id,
        cluster_centroid=rep_embedding,
        cluster_category=representative.input_provisional_category,
        cluster_member_count=len(new_members),
        cluster_outlet_count=_distinct_outlet_count(cluster_id, members),
        cluster_first_seen_utc=min(member_times),
        cluster_last_seen_utc=max(member_times),
    )
    return cluster


def _build_members(
    cluster_id: str,
    *,
    member_indices: list[int],
    candidate_by_index: dict[int, ClusterInput],
) -> list[ClusterMember]:
    """Build the ``ClusterMember`` rows for a representative group, in index order.

    Args:
        cluster_id: The parent cluster id the members attach to.
        member_indices: The representative's index plus its dropped reprint indices.
        candidate_by_index: Lookup from ``input_index`` to its ``ClusterInput``.

    Returns:
        One member per index, sorted by ``input_index`` for deterministic output.
    """
    return [
        ClusterMember(
            cluster_id=cluster_id,
            member_url=candidate_by_index[member_index].input_url,
            member_outlet=candidate_by_index[member_index].input_outlet,
            member_seen_utc=candidate_by_index[member_index].input_published_utc,
        )
        for member_index in sorted(member_indices)
    ]


def _distinct_outlet_count(cluster_id: str, members: list[ClusterMember]) -> int:
    """Count distinct non-null outlets among a cluster's members.

    Null outlets are ignored (an unknown outlet should not inflate diversity). If every
    member outlet is null the count is 0, faithfully reflecting "no known outlets" rather
    than the DDL default of 1.

    Args:
        cluster_id: The cluster whose members to tally.
        members: The run-wide member list (filtered to ``cluster_id``).

    Returns:
        The number of distinct ``member_outlet`` values (excluding ``None``).
    """
    return len(
        {
            member.member_outlet
            for member in members
            if member.cluster_id == cluster_id and member.member_outlet is not None
        }
    )


async def run_and_persist(
    candidates: list[ClusterInput],
    *,
    llm_client: "LLMClient",
    client: Any,
    existing_clusters: list[StoryCluster],
    mint_cluster_id: Callable[[], str],
    mint_story_id: Callable[[], str],
    resolve_existing_story_ids: Callable[[list[str]], dict[str, str]],
    tau_assign: float = DEFAULT_TAU_ASSIGN,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> tuple[ClusterRun, dict[str, str]]:
    """Run the clusterer, bridge cross-day story ids, and persist — the M3c entry point.

    A thin sequencer so the M3c batch has ONE call: it runs
    :func:`cluster_candidates` (near-dup → embed → block+match → assign-or-spawn),
    then :func:`continuity.resolve_cluster_story_ids` (reuse a prior-day story id when a
    member URL already aliases to one, else mint), then
    :func:`continuity.persist_run` (upsert clusters + add member rows). It adds no
    clustering logic of its own — all behaviour lives in those three composed functions.

    Args:
        candidates: The batch of candidate articles to cluster.
        llm_client: The shared ``LLMClient`` forwarded to ``embed_texts`` (mocked in
            tests).
        client: A service-role supabase client for persistence (injected; mocked in
            tests).
        existing_clusters: Active clusters from the cross-day window (drives continuity).
        mint_cluster_id: Zero-arg minter for a fresh ``cluster_id`` per spawned cluster.
        mint_story_id: Zero-arg minter for a fresh ``story_id`` per cluster with no
            existing alias.
        resolve_existing_story_ids: The injected
            ``(normalized_urls) -> {normalized_url: story_id}`` resolver
            (``daily_batch.build_story_id_resolver``).
        tau_assign: Cosine join threshold (default :data:`DEFAULT_TAU_ASSIGN`, 0.75).
        window_hours: Time-window half-width for blocking (default
            :data:`DEFAULT_WINDOW_HOURS`, 48).

    Returns:
        A ``(run, cluster_story_ids)`` tuple: the :class:`ClusterRun` and the
        ``cluster_id -> story_id`` map (so the M3c caller can attach story ids without
        re-deriving them).

    Example:
        >>> # See test_continuity.py for the runnable (mocked) wiring.
        >>> run_and_persist  # doctest: +SKIP
    """
    run = await cluster_candidates(
        candidates,
        llm_client=llm_client,
        existing_clusters=existing_clusters,
        mint_cluster_id=mint_cluster_id,
        tau_assign=tau_assign,
        window_hours=window_hours,
    )
    cluster_story_ids = resolve_cluster_story_ids(
        run,
        resolve_existing_story_ids=resolve_existing_story_ids,
        mint_story_id=mint_story_id,
    )
    persist_run(client, run)
    logger.info(
        "run_and_persist_completed",
        candidate_count=len(candidates),
        cluster_count=len(run.clusters),
        story_id_count=len(cluster_story_ids),
    )
    return run, cluster_story_ids
