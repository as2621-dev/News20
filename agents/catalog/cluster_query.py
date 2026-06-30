"""Category → ordered-clusters query/assembly layer (Phase FSR-M1 SP4).

The thin integrator over SP2's pure ``resolve_category_clusters``: given a
``FeedCategory`` and a ``CatalogRepo`` (the data source), fetch the rows that one
category needs and pipe them through the already-fixed resolver. This module owns
NO business logic — the no-dup rule, ordering, and dedup all live in SP2 and are
NOT re-implemented here (Rule 2/3/8). It owns only the *fetch + hand-off*.

Why a ``CatalogRepo`` Protocol (not a concrete client): the data source must be
swappable. M1's offline de-risking check drives it with an in-memory fixture impl;
the LIVE-E2E residual (M6a) swaps in a Supabase impl satisfying the same Protocol —
``clusters_for_category`` never changes, so the milestone behavior proven offline is
the behavior live. No DB/network in THIS module (the repo abstracts I/O).

IMPORTANT subtlety (the no-dup rule's data dependency): SP2 suppresses the raw
``content_sources`` rows that a present personality bundles. To detect them the
resolver must see the FULL candidate source pool for the category — not only the
rows directly referenced as cluster members, but also the personality-bundled
YouTube/X rows that may NOT be cluster members yet still must be suppressed if they
leak in elsewhere. So the repo intentionally exposes the category's candidate
``sources`` and ``personalities`` pools (not just the referenced ids), and this
layer hands BOTH pools to the resolver. A repo that returned only member-referenced
rows would blind the no-dup match — that is the design choice documented here.
"""

from __future__ import annotations

from typing import Protocol

from agents.catalog.cluster_resolver import resolve_category_clusters
from agents.catalog.models import (
    CatalogSourceRow,
    ClusterMemberRef,
    ClusterRow,
    PersonalityRow,
    ResolvedCluster,
)
from agents.pipeline.categories import FeedCategory


class CatalogRepo(Protocol):
    """The minimal read surface ``clusters_for_category`` needs for one category.

    Four reads, each scoped to a ``FeedCategory``. Kept minimal (Rule 2): only what
    the resolver hand-off requires. Any backing store (the in-memory fixture below,
    a live Supabase client in M6a) satisfies this by implementing these four methods.

    Contract for a live impl (M6a):
      - ``clusters_for_category``    → ``source_clusters`` rows WHERE
        ``cluster_category = category`` (curation filtering is the resolver's job —
        return all; the resolver drops un-curated).
      - ``members_for_clusters``     → ``source_cluster_members`` rows for the given
        cluster ids (the resolver orders + XOR-resolves them).
      - ``sources_for_category``     → the candidate ``content_sources`` POOL for the
        category. MUST include the personality-bundled YouTube/X rows (matched via
        ``topic_tags`` overlap with the category) so the no-dup match can see them,
        NOT only the rows referenced as members. This is the load-bearing subtlety.
      - ``personalities_for_category`` → the candidate ``personalities`` pool for the
        category (matched via ``topic_tags`` overlap), so the resolver can read each
        present personality's bundled handles.
    """

    def clusters_for_category(self, category: FeedCategory) -> list[ClusterRow]:
        """All ``source_clusters`` rows in ``category`` (any curation state)."""
        ...

    def members_for_clusters(self, cluster_ids: list[str]) -> list[ClusterMemberRef]:
        """All ``source_cluster_members`` refs whose ``cluster_id`` is in the set."""
        ...

    def sources_for_category(self, category: FeedCategory) -> list[CatalogSourceRow]:
        """The candidate ``content_sources`` pool for ``category`` (see no-dup note)."""
        ...

    def personalities_for_category(self, category: FeedCategory) -> list[PersonalityRow]:
        """The candidate ``personalities`` pool for ``category``."""
        ...


def clusters_for_category(
    category: FeedCategory, *, repo: CatalogRepo
) -> list[ResolvedCluster]:
    """Return ``category``'s ordered, deduped, no-dup-honored clusters.

    Loads the category's clusters + members + candidate source/personality pools from
    ``repo`` and pipes them through SP2's ``resolve_category_clusters`` (which owns ALL
    ordering / dedup / no-dup logic — not re-implemented here). A category with no
    clusters yields ``[]`` (the resolver short-circuits on an empty cluster list).

    Args:
        category: one of the 8 topic roots.
        repo: the data source (in-memory fixture or live Supabase impl).

    Returns:
        The category's curated, non-empty clusters in render order — the same
        contract SP2 guarantees, end-to-end over whatever ``repo`` supplies.
    """
    clusters = repo.clusters_for_category(category)
    cluster_ids = [c.cluster_id for c in clusters]
    members = repo.members_for_clusters(cluster_ids)
    sources = repo.sources_for_category(category)
    personalities = repo.personalities_for_category(category)
    return resolve_category_clusters(category, clusters, members, sources, personalities)


class InMemoryCatalogRepo:
    """A ``CatalogRepo`` backed by fixture lists — the offline impl + reference shape.

    Tiny by design (Rule 2): holds the four catalog lists and filters them with the
    same predicates a live SQL impl uses (``cluster_category == category`` for
    clusters; ``category in topic_tags`` for the source/personality pools). It returns
    the FULL category pool for sources/personalities (not just member-referenced rows)
    so the no-dup match has everything it needs — mirroring the live impl's contract.
    """

    def __init__(
        self,
        *,
        clusters: list[ClusterRow],
        members: list[ClusterMemberRef],
        sources: list[CatalogSourceRow],
        personalities: list[PersonalityRow],
    ) -> None:
        self._clusters = clusters
        self._members = members
        self._sources = sources
        self._personalities = personalities

    def clusters_for_category(self, category: FeedCategory) -> list[ClusterRow]:
        return [c for c in self._clusters if c.cluster_category == category]

    def members_for_clusters(self, cluster_ids: list[str]) -> list[ClusterMemberRef]:
        id_set = set(cluster_ids)
        return [m for m in self._members if m.cluster_id in id_set]

    def sources_for_category(self, category: FeedCategory) -> list[CatalogSourceRow]:
        return [s for s in self._sources if category in s.topic_tags]

    def personalities_for_category(self, category: FeedCategory) -> list[PersonalityRow]:
        return [p for p in self._personalities if category in p.topic_tags]
