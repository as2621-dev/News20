"""Pydantic row + output models for the catalog cluster resolver (Phase FSR-M1 SP2).

These mirror the on-disk schema so the pure resolver can be authored and unit-tested
against fixture rows with NO DB (the live Supabase fetch is the deferred LIVE-E2E
residual). Each row model carries ONLY the columns the resolver/tests read (Rule 2 —
minimum surface), not the full table:

  - ``CatalogSourceRow``   mirrors ``content_sources``      (0009_content_sources.sql)
  - ``PersonalityRow``     mirrors ``personalities``        (0009_content_sources.sql)
  - ``ClusterRow``         mirrors ``source_clusters``      (0022_source_clusters.sql)
  - ``ClusterMemberRef``   mirrors ``source_cluster_members`` (0022) — a member is
    EXACTLY ONE of ``source_id`` XOR ``personality_id`` (the table's XOR check).

The OUTPUT shapes are what a card/row renders:

  - ``ResolvedClusterMember`` — one rendered followable (kind ∈ {source, personality},
    its underlying id, display name, popularity) — enough to render a card/row.
  - ``ResolvedCluster``       — a cluster with its ordered, deduped members.

Pure data; no DB/clock/network. The category vocabulary is reused from
``agents.pipeline.categories`` (the 8 topic roots), not re-authored here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agents.pipeline.categories import FeedCategory

# Reason: a rendered followable is one of these two kinds. The personality card and
# the individual source row render differently, and the no-dup rule keys off kind
# (a personality's bundled source rows are suppressed). A Literal so a typo is a
# type error, not a silent miss — mirrors categories.py::FeedCategory.
FollowableKind = Literal["source", "personality"]


class CatalogSourceRow(BaseModel):
    """A row of ``content_sources`` — an individual followable source axis.

    Mirrors the columns the resolver reads (0009_content_sources.sql): the id, the
    axis ``content_source_type`` (``youtube_channel``/``x_account``/… — load-bearing
    for the no-dup match), the ``external_id`` (matched against a personality's
    ``youtube_channel_ids``/``aliases``), display name, tags, popularity, curation.
    """

    source_id: str = Field(..., description="content_sources.source_id (uuid as str)")
    content_source_type: str = Field(..., description="youtube_channel | podcast | x_account | personality")
    external_id: str = Field(..., description="platform id — matched vs a personality's bundled handles")
    source_name: str = Field(..., description="display name for the rendered row")
    topic_tags: list[str] = Field(default_factory=list, description="topic roots this source is tagged to")
    popularity_score: float = Field(default=50.0, description="ranking signal carried into the card")
    is_curated: bool = Field(default=True, description="only curated rows are rendered")


class PersonalityRow(BaseModel):
    """A row of ``personalities`` — a named creator that BUNDLES its own handles.

    Mirrors the columns the resolver reads (0009_content_sources.sql). The no-dup
    rule keys off ``youtube_channel_ids`` (matched vs ``content_sources.external_id``
    WHERE type=``youtube_channel``) and ``aliases`` (matched vs ``external_id`` WHERE
    type=``x_account``): when this personality card is shown, those individual source
    rows are suppressed.
    """

    personality_id: str = Field(..., description="personalities.personality_id (uuid as str)")
    display_name: str = Field(..., description="display name for the rendered card")
    aliases: list[str] = Field(default_factory=list, description="X handles — matched vs x_account external_id")
    youtube_channel_ids: list[str] = Field(default_factory=list, description="YT channel ids — matched vs youtube_channel external_id")
    topic_tags: list[str] = Field(default_factory=list, description="topic roots this personality is tagged to")
    popularity_score: float = Field(default=50.0, description="ranking signal carried into the card")
    is_curated: bool = Field(default=True, description="only curated rows are rendered")


class ClusterRow(BaseModel):
    """A row of ``source_clusters`` — a named editorial group within one topic root.

    Mirrors 0022_source_clusters.sql. ``cluster_category`` is one of the 8 topic
    roots (the resolver filters on it); ``cluster_sort_order`` drives cluster order.
    """

    cluster_id: str = Field(..., description="source_clusters.cluster_id (uuid as str)")
    cluster_slug: str = Field(..., description="stable seed key + deterministic tiebreak")
    cluster_label: str = Field(..., description="display label for the cluster")
    cluster_category: FeedCategory = Field(..., description="one of the 8 topic roots")
    cluster_sort_order: int = Field(default=0, description="cluster render order within the category")
    is_curated: bool = Field(default=True, description="only curated clusters are surfaced")


class ClusterMemberRef(BaseModel):
    """A row of ``source_cluster_members`` — EXACTLY ONE of source_id XOR personality_id.

    Mirrors 0022_source_clusters.sql (the table's XOR check). ``member_sort_order``
    is the ordered render position within the cluster.
    """

    cluster_id: str = Field(..., description="parent cluster")
    source_id: str | None = Field(default=None, description="content_sources ref (XOR personality_id)")
    personality_id: str | None = Field(default=None, description="personalities ref (XOR source_id)")
    member_sort_order: int = Field(..., description="ordered render position within the cluster")


class ResolvedClusterMember(BaseModel):
    """A rendered followable — enough to render a card/row.

    ``kind`` distinguishes the personality card from an individual source row;
    ``followable_id`` is the underlying ``personality_id``/``source_id`` (the dedup
    key — the same followable never renders twice in a category).
    """

    kind: FollowableKind = Field(..., description="source | personality")
    followable_id: str = Field(..., description="the underlying source_id / personality_id (dedup key)")
    display_name: str = Field(..., description="card/row label")
    popularity_score: float = Field(..., description="ranking signal")


class ResolvedCluster(BaseModel):
    """A cluster with its ordered, deduped, no-dup-honored members.

    Empty clusters are NOT emitted by the resolver, so ``members`` is always
    non-empty in resolver output.
    """

    cluster_slug: str = Field(..., description="stable slug")
    cluster_label: str = Field(..., description="display label")
    cluster_category: FeedCategory = Field(..., description="one of the 8 topic roots")
    cluster_sort_order: int = Field(..., description="cluster render order within the category")
    members: list[ResolvedClusterMember] = Field(..., description="ordered, deduped rendered followables")
