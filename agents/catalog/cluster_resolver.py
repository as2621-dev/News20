"""Pure cluster + no-dup resolver (Phase FSR-M1 SP2).

Given fixture rows for ONE of the 8 topic categories, return that category's
clusters with ordered, deduped, no-dup-honored members. This is the load-bearing
**trust contract** of the source-first thesis (PRD Decision #7): *a person is never
shown twice, and a followable never appears twice in a category.* If this leaks, the
onboarding grid shows the same human as a personality card AND as their raw YouTube
channel row — exactly the "randoms / duplicates" failure the product promises to
avoid.

It is intentionally **pure** — no DB, no network, no clock, no I/O — mirroring
``agents/pipeline/categories.py``. The live Supabase fetch that supplies these rows
is the deferred LIVE-E2E residual (a ``CatalogRepo`` in SP4 / M6), not this module.

Resolution pipeline (order matters):
  (a) FILTER clusters to ``cluster_category == category``, ordered by
      ``cluster_sort_order`` (stable secondary sort by ``cluster_slug`` so ties are
      deterministic — the seed may share sort orders).
  (b) NO-DUP SET: compute, across the whole category, the ``content_sources`` rows
      that a PRESENT personality bundles (its ``youtube_channel_ids`` matched vs
      ``youtube_channel`` ``external_id``; its ``aliases`` matched vs ``x_account``
      ``external_id``). These source rows are suppressed everywhere — the personality
      card already represents them. (PRD Decision #7.)
  (c) Per cluster, in ``member_sort_order``, expand each member ref to its row,
      SKIPPING a member whose row is missing or ``is_curated == False`` (don't raise),
      SKIPPING a source row in the no-dup set, and — FIRST-CLUSTER-WINS — skipping any
      followable (by underlying id) already rendered in an EARLIER cluster of this
      category, so a followable listed in two clusters renders once.
  (d) DROP a cluster left with zero rendered members (after dedup) — never surfaced.
"""

from __future__ import annotations

from agents.pipeline.categories import FeedCategory
from agents.catalog.models import (
    CatalogSourceRow,
    ClusterMemberRef,
    ClusterRow,
    PersonalityRow,
    ResolvedCluster,
    ResolvedClusterMember,
)

# Reason: the no-dup match is axis-specific — a personality's youtube_channel_ids
# only suppress youtube_channel source rows, and its aliases only suppress x_account
# rows. Pinned here so the match can't drift to a wrong axis.
_YOUTUBE_AXIS = "youtube_channel"
_X_AXIS = "x_account"


def resolve_category_clusters(
    category: FeedCategory,
    clusters: list[ClusterRow],
    members: list[ClusterMemberRef],
    sources: list[CatalogSourceRow],
    personalities: list[PersonalityRow],
) -> list[ResolvedCluster]:
    """Resolve one category's clusters to ordered, deduped, no-dup-honored members.

    Args:
        category: one of the 8 topic roots — clusters are filtered to it.
        clusters: all ``source_clusters`` rows (any category — filtered here).
        members: all ``source_cluster_members`` refs (any cluster — grouped here).
        sources: all ``content_sources`` rows (the source-row lookup pool).
        personalities: all ``personalities`` rows (the personality lookup pool).

    Returns:
        The category's curated, non-empty clusters in render order, each with its
        ordered members minus the no-dup-suppressed source rows and minus any
        followable already rendered in an earlier cluster (first-cluster-wins).
    """
    sources_by_id = {s.source_id: s for s in sources}
    personalities_by_id = {p.personality_id: p for p in personalities}

    # (a) Filter to this category + curated, ordered by sort then slug (stable tie).
    category_clusters = sorted(
        (c for c in clusters if c.cluster_category == category and c.is_curated),
        key=lambda c: (c.cluster_sort_order, c.cluster_slug),
    )
    category_cluster_ids = {c.cluster_id for c in category_clusters}

    # Group members by cluster, ordered within a cluster by member_sort_order. Only
    # members of THIS category's clusters matter.
    members_by_cluster: dict[str, list[ClusterMemberRef]] = {}
    for m in members:
        if m.cluster_id in category_cluster_ids:
            members_by_cluster.setdefault(m.cluster_id, []).append(m)
    for cluster_members in members_by_cluster.values():
        cluster_members.sort(key=lambda m: m.member_sort_order)

    # (b) NO-DUP SET — source_ids suppressed because a PRESENT personality bundles
    # them. A personality is "present in this category" iff it is a personality
    # member of any cluster in this category. (PRD Decision #7 — the trust contract.)
    present_personality_ids = {
        m.personality_id
        for cluster_members in members_by_cluster.values()
        for m in cluster_members
        if m.personality_id is not None
    }
    # The bundled handles of present personalities, by axis. We match against the
    # source pool's external_id, so build handle sets once.
    bundled_youtube_ids: set[str] = set()
    bundled_x_aliases: set[str] = set()
    for pid in present_personality_ids:
        p = personalities_by_id.get(pid)
        if p is None or not p.is_curated:
            continue
        bundled_youtube_ids.update(p.youtube_channel_ids)
        bundled_x_aliases.update(p.aliases)
    suppressed_source_ids = {
        s.source_id
        for s in sources
        if (s.content_source_type == _YOUTUBE_AXIS and s.external_id in bundled_youtube_ids)
        or (s.content_source_type == _X_AXIS and s.external_id in bundled_x_aliases)
    }

    # (c)+(f) Render members per cluster in order, applying skips and first-cluster-
    # wins dedup. ``rendered_followable_ids`` spans the whole category: once a
    # followable (by underlying source_id/personality_id) renders, later clusters
    # drop it — the FIRST cluster (in the order from step a) wins the duplicate.
    rendered_followable_ids: set[str] = set()
    resolved: list[ResolvedCluster] = []
    for cluster in category_clusters:
        rendered_members: list[ResolvedClusterMember] = []
        for m in members_by_cluster.get(cluster.cluster_id, []):
            if m.source_id is not None:
                # (d) skip suppressed (no-dup) source rows.
                if m.source_id in suppressed_source_ids:
                    continue
                row = sources_by_id.get(m.source_id)
                # (d) skip missing / un-curated.
                if row is None or not row.is_curated:
                    continue
                # (f) first-cluster-wins: skip a followable already rendered.
                if m.source_id in rendered_followable_ids:
                    continue
                rendered_followable_ids.add(m.source_id)
                rendered_members.append(
                    ResolvedClusterMember(
                        kind="source",
                        followable_id=row.source_id,
                        display_name=row.source_name,
                        popularity_score=row.popularity_score,
                    )
                )
            elif m.personality_id is not None:
                prow = personalities_by_id.get(m.personality_id)
                if prow is None or not prow.is_curated:
                    continue
                if m.personality_id in rendered_followable_ids:
                    continue
                rendered_followable_ids.add(m.personality_id)
                rendered_members.append(
                    ResolvedClusterMember(
                        kind="personality",
                        followable_id=prow.personality_id,
                        display_name=prow.display_name,
                        popularity_score=prow.popularity_score,
                    )
                )
        # (e) drop a cluster left empty after all skips/dedup — never surfaced.
        if rendered_members:
            resolved.append(
                ResolvedCluster(
                    cluster_slug=cluster.cluster_slug,
                    cluster_label=cluster.cluster_label,
                    cluster_category=cluster.cluster_category,
                    cluster_sort_order=cluster.cluster_sort_order,
                    members=rendered_members,
                )
            )
    return resolved
