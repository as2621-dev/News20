"""Pydantic models for the cluster-store repository (Milestone M3a, Sub-phase 4).

These mirror the ``0018_story_clusters.sql`` schema EXACTLY — one model per table:

    - ``StoryCluster``      ↔ ``story_clusters``        (rolling-centroid cluster)
    - ``ClusterMember``     ↔ ``story_cluster_members``  (member URLs per cluster)

They are the typed boundary between the online assign-or-spawn engine (M3b, which
holds clusters in memory) and Supabase persistence (this repository). The centroid
is carried as a plain ``list[float]`` (the 768-d L2-normalized Gemini
``text-embedding-004`` vector, per ``embeddings.py``); the pgvector
``vector(768)`` text wire form is produced/parsed by ``cluster_store``'s
(de)serialization helpers, NOT here — the model always speaks Python lists.

Defaults match the DDL column defaults so a freshly-minted cluster validates the
same way the database row would (e.g. ``cluster_reel_format='event'``,
``cluster_member_count=1``, ``cluster_status='active'``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StoryCluster(BaseModel):
    """One rolling-centroid cluster (mirrors ``story_clusters``).

    Each field maps 1:1 to a ``story_clusters`` column. ``cluster_centroid`` is the
    running-mean of member-article Gemini ``text-embedding-004`` vectors — a 768-d,
    L2-normalized ``list[float]`` (cosine == dot product). It is nullable in the DDL
    (a cluster row can exist before its first centroid is written), so it defaults to
    ``None`` here.

    Example:
        >>> from datetime import datetime, timezone
        >>> now = datetime(2026, 6, 18, tzinfo=timezone.utc)
        >>> cluster = StoryCluster(
        ...     cluster_id="clu-1",
        ...     cluster_centroid=[0.0] * 768,
        ...     cluster_category="tech",
        ...     cluster_first_seen_utc=now,
        ...     cluster_last_seen_utc=now,
        ... )
        >>> cluster.cluster_reel_format
        'event'
    """

    cluster_id: str = Field(..., description="Synthetic text PK minted by the engine (story_clusters.cluster_id).")
    cluster_centroid: list[float] | None = Field(
        default=None,
        description="Running-mean 768-d L2-normalized Gemini text-embedding-004 centroid (vector(768); nullable).",
    )
    cluster_category: str = Field(..., description="feed_category enum value (story_clusters.cluster_category).")
    cluster_subcategory: str | None = Field(
        default=None, description="Optional finer-grained sub-bucket (story_clusters.cluster_subcategory)."
    )
    cluster_reel_format: str = Field(
        default="event", description="Reel format for this cluster (DDL default 'event')."
    )
    cluster_member_count: int = Field(
        default=1, description="Number of member articles rolled into the centroid (DDL default 1)."
    )
    cluster_outlet_count: int = Field(
        default=1, description="Number of distinct outlets covering the cluster (DDL default 1)."
    )
    cluster_first_seen_utc: datetime = Field(
        ..., description="UTC timestamp the cluster was first seen (story_clusters.cluster_first_seen_utc)."
    )
    cluster_last_seen_utc: datetime = Field(
        ..., description="UTC timestamp the cluster was most recently seen (drives the cross-day window load)."
    )
    cluster_importance: float | None = Field(
        default=None, description="Optional importance score (real; nullable)."
    )
    cluster_velocity: float | None = Field(
        default=None, description="Optional velocity / momentum score (real; nullable)."
    )
    cluster_status: str = Field(
        default="active", description="Lifecycle status (DDL default 'active')."
    )


class ClusterMember(BaseModel):
    """One member URL that rolled into a cluster's centroid (mirrors ``story_cluster_members``).

    The ``(cluster_id, member_url)`` pair is the composite PK in the DDL, so a member
    is uniquely identified by its parent cluster + normalized URL.

    Example:
        >>> from datetime import datetime, timezone
        >>> member = ClusterMember(
        ...     cluster_id="clu-1",
        ...     member_url="https://bbc.com/x",
        ...     member_outlet="bbc.com",
        ...     member_seen_utc=datetime(2026, 6, 18, tzinfo=timezone.utc),
        ... )
        >>> member.member_outlet
        'bbc.com'
    """

    cluster_id: str = Field(..., description="Parent cluster id (FK → story_clusters.cluster_id, on delete cascade).")
    member_url: str = Field(..., description="The member article URL (story_cluster_members.member_url).")
    member_outlet: str | None = Field(
        default=None, description="The member's outlet domain (story_cluster_members.member_outlet; nullable)."
    )
    member_seen_utc: datetime = Field(
        ..., description="UTC timestamp this member was seen (story_cluster_members.member_seen_utc)."
    )
