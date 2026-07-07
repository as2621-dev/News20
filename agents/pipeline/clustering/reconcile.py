"""Semantic story-id reconciliation for the daily batch (FSR-M3 live wiring).

The dedup fix that closes the "two reels, one event" bug (e.g. the two reworded
"Egypt beats Australia on penalties" headlines that ingestion's URL+0.85-title
:class:`~agents.ingestion.dedup.StoryClusterer` left as two ``canonical_story_id`` s).

This module is the ONE seam that wires the dormant M3a/M3b online semantic clusterer
(``agents.pipeline.clustering.online_clusterer``) into ``run_daily_pipeline``. Run once,
right after ingestion and BEFORE the produce-once gate, it:

    1. Embeds the deduped candidate pool and runs assign-or-spawn clustering (paid
       Gemini ``gemini-embedding-001`` vectors) so same-real-world-event candidates land
       in one :class:`~agents.pipeline.clustering.models.StoryCluster`.
    2. Resolves ONE stable ``story_id`` per cluster — reusing a prior-day id when a
       member URL already aliases (cross-day continuity, via the SAME resolver +
       ``normalize_url`` the alias write-path is keyed on), else the cluster's own
       representative candidate's deterministic URL-derived id (stable across same-day
       re-runs, unlike a random mint).
    3. COLLAPSES every candidate sharing a resolved id into one representative
       :class:`~agents.ingestion.models.CanonicalStory` (unioning covering outlets /
       matched interests / themes) and REMAPS the ``story_interests`` tags onto it.
    4. Scores the clusters (E1 importance, normalized within category) and persists them
       so tomorrow's window load sees today's clusters, returning a
       ``{story_id: cluster_importance}`` map the assembler threads into the Importance
       score term (closing the ``daily_batch`` FSR-M3 residual).

After this runs, the produce-once gate, per-category caps, and ``feed_assembly``'s exact
``story_id`` dedup all operate on collapsed stories — so two same-event candidates can no
longer both be produced or both reach a feed.

Every DB + Gemini dependency is INJECTED (mocked in tests; CLAUDE.md §6). The whole path
is gated OFF by default in ``run_daily_pipeline`` (``enable_semantic_clustering=False``)
so it costs nothing and changes nothing until a caller opts in.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from agents.ingestion.dedup import normalize_url
from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.clustering.assign import DEFAULT_TAU_ASSIGN
from agents.pipeline.clustering.blocking import DEFAULT_WINDOW_HOURS
from agents.pipeline.clustering.cluster_store import load_active_clusters
from agents.pipeline.clustering.continuity import persist_run
from agents.pipeline.clustering.engine_models import ClusterInput, ClusterRun
from agents.pipeline.clustering.online_clusterer import cluster_candidates
from agents.pipeline.categories import FeedCategory, category_for_slug
from agents.pipeline.importance.story_importance import score_clusters
from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category
from agents.shared.logger import get_logger

logger = get_logger("pipeline.clustering.reconcile")

# Reason: how many leading body characters to append to the headline for the embedding /
# near-dup text. The headline drives same-event similarity; a short lead disambiguates
# two events that share a headline stem without letting full-body drift dominate.
_LEAD_SNIPPET_CHARS = 300


class ReconcileResult:
    """The output of :func:`reconcile_story_ids_via_clustering`.

    Attributes:
        reconciled_stories: The candidate pool after collapsing same-event stories onto
            one shared ``canonical_story_id`` each (one story per cluster).
        reconciled_tags: The ``story_interests`` tags remapped onto the shared ids and
            de-duplicated per (story_id, interest_id) at the lowest match depth.
        cluster_importance_by_story: ``{shared_story_id: cluster_importance ∈ [0, 1]}``
            for the assembler's Importance term; empty when nothing clustered.
    """

    __slots__ = ("reconciled_stories", "reconciled_tags", "cluster_importance_by_story")

    def __init__(
        self,
        reconciled_stories: list[CanonicalStory],
        reconciled_tags: list[StoryInterestTag],
        cluster_importance_by_story: dict[str, float],
    ) -> None:
        self.reconciled_stories = reconciled_stories
        self.reconciled_tags = reconciled_tags
        self.cluster_importance_by_story = cluster_importance_by_story


async def reconcile_story_ids_via_clustering(
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    *,
    supabase_client: Any,
    llm_client: Any,
    interest_nodes: dict[str, InterestNode],
    resolve_existing_story_ids: Callable[[list[str]], dict[str, str]],
    now_utc: datetime,
    tau_assign: float = DEFAULT_TAU_ASSIGN,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    block_by_category: bool = False,
    mint_cluster_id: Callable[[], str] | None = None,
) -> ReconcileResult:
    """Collapse same-event candidates onto shared story ids via semantic clustering.

    See the module docstring for the four-step contract. This is a pure orchestration
    over injected primitives; it embeds (paid), persists cluster rows, and returns the
    reconciled pool + tags + importance map. It never mutates its inputs.

    Args:
        stories: The deduped candidate pool from ingestion (each with its own provisional
            ``canonical_story_id``). Source-origin stories are NOT passed here — only the
            interest pool is clustered.
        story_interest_tags: All ``story_interests`` tags for ``stories`` (remapped onto
            the collapsed ids).
        supabase_client: Service-role client (injected) — reads the cross-day cluster
            window and persists the scored clusters.
        llm_client: The Gemini ``LLMClient`` forwarded to the embedder (mocked in tests).
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup used to derive
            each story's provisional feed category (blocking aid + cluster category).
        resolve_existing_story_ids: The cross-day resolver
            (``daily_batch.build_story_id_resolver``): ``(normalized_urls) ->
            {normalized_url: existing_story_id}``. Reused ids preserve produce-once.
        now_utc: Current UTC time (freshness decay + the window's lower bound).
        tau_assign: Cosine join threshold (default 0.75).
        window_hours: Cross-day window half-width in hours (default 48).
        block_by_category: Forwarded to the clusterer. Default False here (unlike the
            engine default) so cross-category same-event candidates can still merge —
            the live path's whole point (see module + clusterer docstrings).
        mint_cluster_id: Optional zero-arg cluster-id minter (injected in tests for
            determinism); defaults to a random ``clu-<uuid>`` factory.

    Returns:
        A :class:`ReconcileResult`. When ``stories`` is empty, returns the inputs
        unchanged with an empty importance map.

    Example:
        >>> # See tests/agents/pipeline/test_reconcile.py for the mocked wiring:
        >>> # two reworded headlines for one event collapse to a single story id.
        >>> reconcile_story_ids_via_clustering  # doctest: +SKIP
    """
    if not stories:
        logger.info("reconcile_story_ids_skipped_empty", candidate_count=0)
        return ReconcileResult(list(stories), list(story_interest_tags), {})

    mint_cluster_id = mint_cluster_id or (lambda: f"clu-{uuid.uuid4().hex}")
    tags_by_story = _index_tags_by_story(story_interest_tags)

    # Step 1 — build ClusterInput (index-aligned to ``stories``) and cluster.
    inputs = [
        ClusterInput(
            input_index=index,
            input_text=_embedding_text(story),
            input_url=story.canonical_url,
            input_outlet=story.canonical_primary_outlet_domain,
            input_published_utc=_as_utc(story.canonical_published_utc),
            input_provisional_category=assign_category(
                story.canonical_story_id, tags_by_story, interest_nodes
            ),
        )
        for index, story in enumerate(stories)
    ]
    existing_clusters = load_active_clusters(
        supabase_client, since_utc=_window_start(now_utc, window_hours)
    )
    run = await cluster_candidates(
        inputs,
        llm_client=llm_client,
        existing_clusters=existing_clusters,
        mint_cluster_id=mint_cluster_id,
        tau_assign=tau_assign,
        window_hours=window_hours,
        block_by_category=block_by_category,
    )

    # Step 2 — resolve one shared story id per cluster.
    cluster_id_by_story_index = _cluster_id_by_story_index(run, stories)
    cluster_story_ids = _resolve_shared_story_ids(
        run,
        stories=stories,
        cluster_id_by_story_index=cluster_id_by_story_index,
        resolve_existing_story_ids=resolve_existing_story_ids,
    )

    # Step 3 — collapse candidates + remap tags onto the shared ids.
    shared_id_by_index = {
        index: cluster_story_ids[cluster_id]
        for index, cluster_id in cluster_id_by_story_index.items()
        if cluster_id in cluster_story_ids
    }
    reconciled_stories = _collapse_stories(stories, shared_id_by_index)
    reconciled_tags = _remap_tags(
        story_interest_tags, stories=stories, shared_id_by_index=shared_id_by_index
    )
    _log_cross_category_merge_conflicts(
        reconciled_tags,
        stories=stories,
        shared_id_by_index=shared_id_by_index,
        provisional_categories=[
            cluster_input.input_provisional_category for cluster_input in inputs
        ],
        interest_nodes=interest_nodes,
    )

    # Step 4 — score clusters, persist, and build the importance map keyed by story id.
    scored_run = _score_run(run, now_utc=now_utc)
    persist_run(supabase_client, scored_run)
    cluster_importance_by_story = {
        cluster_story_ids[cluster.cluster_id]: cluster.cluster_importance
        for cluster in scored_run.clusters
        if cluster.cluster_id in cluster_story_ids
        and cluster.cluster_importance is not None
    }

    logger.info(
        "reconcile_story_ids_completed",
        candidate_count=len(stories),
        cluster_count=len(run.clusters),
        collapsed_story_count=len(reconciled_stories),
        merged_away=len(stories) - len(reconciled_stories),
        importance_entries=len(cluster_importance_by_story),
        block_by_category=block_by_category,
    )
    return ReconcileResult(
        reconciled_stories, reconciled_tags, cluster_importance_by_story
    )


def _embedding_text(story: CanonicalStory) -> str:
    """Headline (+ short lead) used for both near-dup shingling and the embedding."""
    if story.canonical_body_text:
        return f"{story.canonical_title}\n{story.canonical_body_text[:_LEAD_SNIPPET_CHARS]}"
    return story.canonical_title


def _as_utc(when: datetime) -> datetime:
    """Coerce a timestamp to tz-aware UTC (blocking raises on naive vs aware)."""
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _window_start(now_utc: datetime, window_hours: int) -> datetime:
    """The cross-day window's lower bound (``now - window_hours``)."""
    from datetime import timedelta

    return _as_utc(now_utc) - timedelta(hours=window_hours)


def _cluster_id_by_story_index(run: ClusterRun, stories: list[CanonicalStory]) -> dict[int, str]:
    """Map each story's batch index to the cluster id it landed in.

    ``run.input_cluster_map`` only carries kept representatives; a near-dup reprint folds
    in as a member with NO map entry. So we resolve every story through the member rows
    (each ``ClusterInput.input_url`` becomes exactly one ``ClusterMember.member_url``),
    which covers representatives AND dropped reprints alike. A story whose URL is missing
    from the run (defensive; should not happen) is simply left unmapped and passes through
    un-collapsed.
    """
    cluster_id_by_url: dict[str, str] = {
        member.member_url: member.cluster_id for member in run.members
    }
    mapping: dict[int, str] = {}
    for index, story in enumerate(stories):
        cluster_id = cluster_id_by_url.get(story.canonical_url)
        if cluster_id is not None:
            mapping[index] = cluster_id
    return mapping


def _resolve_shared_story_ids(
    run: ClusterRun,
    *,
    stories: list[CanonicalStory],
    cluster_id_by_story_index: dict[int, str],
    resolve_existing_story_ids: Callable[[list[str]], dict[str, str]],
) -> dict[str, str]:
    """Pick one stable ``story_id`` per cluster: reuse an existing alias, else the rep id.

    Cross-day continuity FIRST: normalize every clustered story's URL with the canonical
    ``normalize_url`` (parity with the alias write-path — see continuity.py) and reuse an
    existing ``story_id`` if any member URL already aliases to one (tie-break: smallest
    id, matching ``continuity.resolve_cluster_story_ids``). Otherwise fall back to the
    cluster's representative candidate's OWN ``canonical_story_id`` — a deterministic,
    URL-derived id that is stable across same-day re-runs (a random mint would not be,
    breaking produce-once before aliases are written).

    Args:
        run: The clustering run (its clusters need ids).
        stories: The candidate pool (source of representative ids + URLs).
        cluster_id_by_story_index: ``{story_index: cluster_id}`` from
            :func:`_cluster_id_by_story_index`.
        resolve_existing_story_ids: The injected cross-day alias resolver.

    Returns:
        ``{cluster_id: shared_story_id}`` for every cluster in ``run.clusters``.
    """
    # Group story indices per cluster (deterministic ascending order).
    indices_by_cluster: dict[str, list[int]] = {}
    for index, cluster_id in sorted(cluster_id_by_story_index.items()):
        indices_by_cluster.setdefault(cluster_id, []).append(index)

    # One resolver call over the union of all normalized member URLs.
    normalized_by_index = {
        index: normalize_url(stories[index].canonical_url)
        for index in cluster_id_by_story_index
    }
    all_normalized = sorted({url for url in normalized_by_index.values() if url})
    existing_by_url = (
        resolve_existing_story_ids(all_normalized) if all_normalized else {}
    )

    cluster_story_ids: dict[str, str] = {}
    reused = 0
    for cluster in run.clusters:
        member_indices = indices_by_cluster.get(cluster.cluster_id, [])
        existing_ids = {
            existing_by_url[normalized_by_index[index]]
            for index in member_indices
            if normalized_by_index.get(index) in existing_by_url
        }
        if existing_ids:
            cluster_story_ids[cluster.cluster_id] = min(existing_ids)
            reused += 1
        elif member_indices:
            # Representative = smallest batch index (matches the clusterer's own choice).
            rep_index = member_indices[0]
            cluster_story_ids[cluster.cluster_id] = stories[rep_index].canonical_story_id
        # A cluster with no in-batch members (e.g. a touched existing cross-day cluster)
        # gets no story id here; nothing in this batch maps to it, so it is harmless.

    logger.info(
        "reconcile_resolve_shared_story_ids",
        cluster_count=len(run.clusters),
        reused_existing=reused,
    )
    return cluster_story_ids


def _collapse_stories(
    stories: list[CanonicalStory], shared_id_by_index: dict[int, str]
) -> list[CanonicalStory]:
    """Collapse every candidate sharing a resolved id into one representative story.

    Within each shared-id group the representative is the member whose own
    ``canonical_story_id`` equals the shared id (URL↔id consistency), else the earliest
    published (tie-break: id). The representative absorbs the union of the group's
    covering outlets, matched interests, themes, and member candidate ids; its
    ``story_outlet_count`` is recomputed as the distinct covering-outlet count. Stories
    with no resolved id (unmapped) pass through untouched.

    Output order is stable: a group is emitted at the position of its earliest member.
    """
    groups: dict[str, list[int]] = {}
    passthrough: list[int] = []
    for index in range(len(stories)):
        shared_id = shared_id_by_index.get(index)
        if shared_id is None:
            passthrough.append(index)
        else:
            groups.setdefault(shared_id, []).append(index)

    # Emit each output at the position of its earliest contributing index (stable order).
    emit_at: dict[int, CanonicalStory] = {}
    for index in passthrough:
        emit_at[index] = stories[index]
    for shared_id, indices in groups.items():
        representative_index = _pick_representative_index(stories, indices, shared_id)
        merged = _merge_group(stories, indices, shared_id, representative_index)
        emit_at[min(indices)] = merged

    return [emit_at[index] for index in sorted(emit_at)]


def _pick_representative_index(
    stories: list[CanonicalStory], indices: list[int], shared_id: str
) -> int:
    """The group member to keep: the id-matching one, else earliest published then id."""
    for index in indices:
        if stories[index].canonical_story_id == shared_id:
            return index
    return min(
        indices,
        key=lambda index: (
            _as_utc(stories[index].canonical_published_utc),
            stories[index].canonical_story_id,
        ),
    )


def _merge_group(
    stories: list[CanonicalStory],
    indices: list[int],
    shared_id: str,
    representative_index: int,
) -> CanonicalStory:
    """Build the single collapsed story for a shared-id group (representative + unions)."""
    representative = stories[representative_index]
    covering_outlets: set[str] = set()
    matched_interest_ids: list[str] = []
    themes: list[str] = []
    member_candidate_ids: list[str] = []
    for index in indices:
        story = stories[index]
        covering_outlets.update(story.covering_outlets)
        if story.canonical_primary_outlet_domain:
            covering_outlets.add(story.canonical_primary_outlet_domain)
        _extend_unique(matched_interest_ids, story.canonical_matched_interest_ids)
        _extend_unique(themes, story.canonical_themes)
        _extend_unique(member_candidate_ids, story.member_candidate_ids)

    return representative.model_copy(
        update={
            "canonical_story_id": shared_id,
            "covering_outlets": sorted(covering_outlets),
            "story_outlet_count": len(covering_outlets),
            "canonical_matched_interest_ids": matched_interest_ids,
            "canonical_themes": themes,
            "member_candidate_ids": member_candidate_ids,
        }
    )


def _extend_unique(target: list[str], values: list[str]) -> None:
    """Append values not already present, preserving first-seen order."""
    seen = set(target)
    for value in values:
        if value not in seen:
            target.append(value)
            seen.add(value)


def _remap_tags(
    story_interest_tags: list[StoryInterestTag],
    *,
    stories: list[CanonicalStory],
    shared_id_by_index: dict[int, str],
) -> list[StoryInterestTag]:
    """Remap ``story_interests`` tags onto shared ids, keeping the lowest match depth.

    A tag whose story merged into a shared id is rewritten to that id; unmerged tags keep
    their id. Duplicate (story_id, interest_id) edges (two merged stories both tagged on
    one interest) collapse to the single lowest ``match_depth`` (the most specific hit),
    carrying that row's relevance. Output order is deterministic (by story then interest).
    """
    old_to_new: dict[str, str] = {}
    for index, shared_id in shared_id_by_index.items():
        old_to_new[stories[index].canonical_story_id] = shared_id

    best_by_edge: dict[tuple[str, str], StoryInterestTag] = {}
    for tag in story_interest_tags:
        new_story_id = old_to_new.get(tag.story_interest_story_id, tag.story_interest_story_id)
        edge = (new_story_id, tag.story_interest_interest_id)
        current = best_by_edge.get(edge)
        if current is None or tag.story_interest_match_depth < current.story_interest_match_depth:
            best_by_edge[edge] = tag.model_copy(
                update={"story_interest_story_id": new_story_id}
            )

    return [best_by_edge[edge] for edge in sorted(best_by_edge)]


def _log_cross_category_merge_conflicts(
    reconciled_tags: list[StoryInterestTag],
    *,
    stories: list[CanonicalStory],
    shared_id_by_index: dict[int, str],
    provisional_categories: list[FeedCategory],
    interest_nodes: dict[str, InterestNode],
) -> None:
    """Detect + log cross-category merge conflicts (issue #34) — detection ONLY.

    A cross-category merge (deliberate — ``block_by_category=False``) unions tags from
    members whose FETCHING interests live under different roots. Downstream
    ``assign_category`` picks the lowest-depth tag, so an absorbed member's lower-depth
    foreign tag can FLIP the surviving story's category away from its fetching
    interest's — contradicting the #35 precedence doctrine. This logs every such
    contest as a structured ``reconcile_category_conflict`` event (extending #35's
    conflict-visibility pattern).

    It deliberately does NOT touch ``story_interest_match_depth``: that field is also
    the ranker's DepthMatch input and is persisted verbatim to ``story_interests``
    (``persist_helpers.build_story_interest_rows``), so clamping it to steer the
    category contest would corrupt a genuine follower's affinity (issue #34 review-
    panel HIGH). Enforcement — pinning the merged story to its representative's
    category WITHOUT touching depths (a ``category_override_by_story`` map threaded to
    the ``assign_category`` call sites) — is the recorded #34 remainder.

    Args:
        reconciled_tags: The remapped/deduped tags from :func:`_remap_tags` (read-only).
        stories: The ORIGINAL candidate pool (representative choice parity with
            :func:`_collapse_stories`).
        shared_id_by_index: ``{story_index: shared_story_id}`` merge mapping.
        provisional_categories: Index-aligned per-story pre-merge categories (the
            ``ClusterInput.input_provisional_category`` values — fetching-interest
            derived via ``assign_category``).
        interest_nodes: Taxonomy lookup to resolve each tag's root category.
    """
    indices_by_shared_id: dict[str, list[int]] = {}
    for index, shared_id in shared_id_by_index.items():
        indices_by_shared_id.setdefault(shared_id, []).append(index)

    for shared_id, indices in indices_by_shared_id.items():
        if len(indices) < 2:
            continue
        # Resolve each merged tag's root category (orphan tags cannot be categorized —
        # assign_category ignores them too, so they cannot cause a flip). The conflict
        # decision keys on the MERGED TAGS' categories, not the members' provisional
        # ones — an absorbed member with no tags contributes nothing to the contest.
        tag_categories = {
            category_for_slug(interest_nodes[tag.story_interest_interest_id].interest_slug)
            for tag in reconciled_tags
            if tag.story_interest_story_id == shared_id
            and tag.story_interest_interest_id in interest_nodes
        }
        if len(tag_categories) <= 1:
            continue
        representative_index = _pick_representative_index(stories, sorted(indices), shared_id)
        logger.info(
            "reconcile_category_conflict",
            story_id=shared_id,
            representative_category=provisional_categories[representative_index],
            contender_categories=sorted(tag_categories),
            guard_enforced=False,
        )


def _score_run(run: ClusterRun, *, now_utc: datetime) -> ClusterRun:
    """Return a copy of ``run`` whose clusters carry E1 importance (for persist + map)."""
    member_outlets_by_cluster: dict[str, list[str | None]] = {}
    for member in run.members:
        member_outlets_by_cluster.setdefault(member.cluster_id, []).append(member.member_outlet)
    scored_clusters = score_clusters(
        run.clusters, member_outlets_by_cluster, _as_utc(now_utc)
    )
    return ClusterRun(
        clusters=scored_clusters,
        members=run.members,
        input_cluster_map=run.input_cluster_map,
    )
