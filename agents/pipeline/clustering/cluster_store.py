"""Cluster-store repository: load/upsert rolling centroids (Milestone M3a, Sub-phase 4).

The Stage-(C) persistence layer of the shared-pool clusterer (spec §2C/§3). It is a
thin repository over the ``story_clusters`` + ``story_cluster_members`` tables
(``0018_story_clusters.sql``) so the in-memory assign-or-spawn engine (M3b) can:

    - LOAD active rolling centroids inside a time window (cross-day continuity),
    - UPSERT a cluster after its centroid / counts roll forward,
    - ADD the member URLs that rolled into a centroid.

Every database call goes through an INJECTED supabase client (typed ``Any``, the same
convention as ``daily_batch._load_*``) — there is no connection or secret here, so
tests mock the client at the ``.table(...).select/upsert(...).execute()`` boundary
(CLAUDE.md §6 mocking mandate; no real DB).

Centroid wire form
------------------
``story_clusters.cluster_centroid`` is a pgvector ``vector(768)`` column. pgvector's
text representation is ``"[0.1,0.2,...]"`` (square-bracketed, comma-separated, NO
spaces). ``serialize_centroid`` produces that string for writes; ``deserialize_centroid``
parses it back — and also passes through a list, because PostgREST/Supabase may return
the column already decoded as a JSON array. The pair round-trips a 768-float vector
exactly (full ``repr`` precision is used so no fidelity is lost).

Centroids are NEVER logged (768 floats are noise) — only counts and ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.pipeline.clustering.models import ClusterMember, StoryCluster
from agents.shared.logger import get_logger

logger = get_logger("pipeline.clustering.cluster_store")


def serialize_centroid(vec: list[float]) -> str:
    """Serialize a centroid vector to the pgvector text form ``"[0.1,0.2,...]"``.

    pgvector accepts (and emits) a bracketed, comma-separated, space-free list. Full
    ``repr`` precision is used for each float so ``deserialize_centroid`` round-trips
    the value exactly.

    Args:
        vec: The centroid components (e.g. a 768-d L2-normalized embedding).

    Returns:
        The pgvector text literal, e.g. ``"[0.1,0.2,0.3]"``. An empty vector serializes
        to ``"[]"``.

    Example:
        >>> serialize_centroid([0.1, 0.2, 0.3])
        '[0.1,0.2,0.3]'
    """
    # Reason: repr(float) gives the shortest string that round-trips to the same
    # double, so serialize→deserialize is exact (test (c)).
    return "[" + ",".join(repr(float(component)) for component in vec) + "]"


def deserialize_centroid(raw: str | list[float]) -> list[float]:
    """Parse a centroid from the pgvector text form OR pass through a decoded list.

    Supabase / PostgREST may return ``cluster_centroid`` either as the pgvector text
    literal (``"[...]"``) or as an already-decoded JSON array — this handles both so
    the caller never has to care which shape came back.

    Args:
        raw: Either the pgvector text literal ``"[0.1,0.2,...]"`` or a ``list[float]``.

    Returns:
        The centroid as a ``list[float]``.

    Raises:
        TypeError: If ``raw`` is neither a string nor a list.

    Example:
        >>> deserialize_centroid("[0.1,0.2,0.3]")
        [0.1, 0.2, 0.3]
        >>> deserialize_centroid([0.1, 0.2, 0.3])
        [0.1, 0.2, 0.3]
    """
    if isinstance(raw, list):
        return [float(component) for component in raw]
    if isinstance(raw, str):
        body = raw.strip().lstrip("[").rstrip("]").strip()
        if not body:
            return []
        return [float(piece) for piece in body.split(",")]
    raise TypeError(f"deserialize_centroid expects str or list, got {type(raw).__name__}")


def _row_to_story_cluster(row: dict[str, Any]) -> StoryCluster:
    """Build a ``StoryCluster`` from a raw ``story_clusters`` row dict.

    Deserializes ``cluster_centroid`` (text-or-list) into a Python list; a NULL
    centroid stays ``None``. All other columns map straight through Pydantic
    validation (which coerces ISO timestamps to ``datetime``).
    """
    raw_centroid = row.get("cluster_centroid")
    centroid = deserialize_centroid(raw_centroid) if raw_centroid is not None else None
    return StoryCluster(
        cluster_id=row["cluster_id"],
        cluster_centroid=centroid,
        cluster_category=row["cluster_category"],
        cluster_subcategory=row.get("cluster_subcategory"),
        cluster_reel_format=row.get("cluster_reel_format", "event"),
        cluster_member_count=row.get("cluster_member_count", 1),
        cluster_outlet_count=row.get("cluster_outlet_count", 1),
        cluster_first_seen_utc=row["cluster_first_seen_utc"],
        cluster_last_seen_utc=row["cluster_last_seen_utc"],
        cluster_importance=row.get("cluster_importance"),
        cluster_velocity=row.get("cluster_velocity"),
        cluster_status=row.get("cluster_status", "active"),
    )


def load_active_clusters(
    client: Any,
    *,
    since_utc: datetime,
    category: str | None = None,
) -> list[StoryCluster]:
    """Load rolling clusters last seen within the window (cross-day continuity).

    Reads ``story_clusters`` filtered to ``cluster_last_seen_utc >= since_utc`` (the
    "active in the last N hours/days" window the engine re-matches against), optionally
    scoped to one ``cluster_category``. Uses the (category, last_seen) index from the
    DDL. The supabase client is INJECTED (mocked in tests).

    Args:
        client: A service-role supabase client (injected; mocked in tests).
        since_utc: Only clusters with ``cluster_last_seen_utc >= since_utc`` are loaded.
        category: Optional ``feed_category`` to scope the load to a single category.

    Returns:
        The matching clusters as ``StoryCluster`` objects (centroids deserialized).
        An empty / NULL ``.data`` yields ``[]``.

    Example:
        >>> from datetime import datetime, timezone
        >>> clusters = load_active_clusters(  # doctest: +SKIP
        ...     client, since_utc=datetime(2026, 6, 17, tzinfo=timezone.utc), category="tech"
        ... )
    """
    query = client.table("story_clusters").select("*").gte("cluster_last_seen_utc", since_utc.isoformat())
    if category is not None:
        query = query.eq("cluster_category", category)
    rows = getattr(query.execute(), "data", None) or []
    clusters = [_row_to_story_cluster(row) for row in rows]
    logger.info(
        "load_active_clusters_completed",
        count=len(clusters),
        since_utc=since_utc.isoformat(),
        category=category,
    )
    return clusters


def _story_cluster_to_row(cluster: StoryCluster) -> dict[str, Any]:
    """Build the ``story_clusters`` row dict for an upsert.

    Serializes the centroid to the pgvector text form and timestamps to ISO strings
    (the JSON-safe shapes the supabase client sends). A ``None`` centroid is written
    as SQL NULL.
    """
    return {
        "cluster_id": cluster.cluster_id,
        "cluster_centroid": (serialize_centroid(cluster.cluster_centroid) if cluster.cluster_centroid is not None else None),
        "cluster_category": cluster.cluster_category,
        "cluster_subcategory": cluster.cluster_subcategory,
        "cluster_reel_format": cluster.cluster_reel_format,
        "cluster_member_count": cluster.cluster_member_count,
        "cluster_outlet_count": cluster.cluster_outlet_count,
        "cluster_first_seen_utc": cluster.cluster_first_seen_utc.isoformat(),
        "cluster_last_seen_utc": cluster.cluster_last_seen_utc.isoformat(),
        "cluster_importance": cluster.cluster_importance,
        "cluster_velocity": cluster.cluster_velocity,
        "cluster_status": cluster.cluster_status,
    }


def upsert_cluster(client, cluster: StoryCluster) -> None:
    """Upsert one rolling cluster into ``story_clusters``.

    Upsert (not insert) so the engine can roll a cluster's centroid / counts forward
    on every batch and write the same ``cluster_id`` idempotently. The centroid is
    serialized to the pgvector text form; timestamps to ISO strings.

    Args:
        client: A service-role supabase client (injected; mocked in tests).
        cluster: The cluster to persist.

    Example:
        >>> upsert_cluster(client, cluster)  # doctest: +SKIP
    """
    row = _story_cluster_to_row(cluster)
    client.table("story_clusters").upsert(row).execute()
    logger.info(
        "upsert_cluster_completed",
        cluster_id=cluster.cluster_id,
        cluster_category=cluster.cluster_category,
        cluster_member_count=cluster.cluster_member_count,
    )


def add_cluster_members(client, cluster_id: str, members: list[ClusterMember]) -> None:
    """Batch-upsert the member URLs that rolled into a cluster's centroid.

    One ``.upsert`` of all member rows into ``story_cluster_members`` (no per-member
    round-trip). Upsert (not insert) so re-running a batch that re-sees the same
    ``(cluster_id, member_url)`` pair is idempotent. A no-op on an empty list (no DB
    call at all).

    Args:
        client: A service-role supabase client (injected; mocked in tests).
        cluster_id: The parent cluster id every member is attributed to (overrides any
            id carried on the member, so all rows FK to this cluster).
        members: The members to persist; ``[]`` is a no-op.

    Example:
        >>> add_cluster_members(client, "clu-1", members)  # doctest: +SKIP
    """
    if not members:
        return
    rows = [
        {
            "cluster_id": cluster_id,
            "member_url": member.member_url,
            "member_outlet": member.member_outlet,
            "member_seen_utc": member.member_seen_utc.isoformat(),
        }
        for member in members
    ]
    client.table("story_cluster_members").upsert(rows).execute()
    logger.info("add_cluster_members_completed", cluster_id=cluster_id, member_count=len(rows))
