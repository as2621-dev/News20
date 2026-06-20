"""Time-window + category blocking for the online clusterer (Milestone M3b, Sub-phase 1).

Before the (paid) cosine assign-or-spawn decision runs, a candidate only needs to be
compared against clusters that are *plausibly the same rolling event* — i.e. clusters
last seen close in time and (optionally) in the same provisional category. ``select_block``
narrows the active-cluster set to that block so the assign step is O(block), not O(n²),
exactly as the engine spec (§2C step 3) requires.

The persisted schema (migration 0018) has NO entity/theme column, so blocking is purely
**time-window + provisional category**; the cosine match against ``cluster_centroid``
(Sub-phase 2) makes the actual same-event decision. This module deliberately invents no
entity/theme structure.

Boundary semantics (documented, tested): a cluster is in-block when the absolute gap
between the candidate's publish time and the cluster's ``cluster_last_seen_utc`` is
**<= window_hours** — i.e. the edge is **INCLUSIVE**. A cluster exactly ``window_hours``
old is kept; one even a microsecond past it is dropped. All timestamps are UTC tz-aware.

Example:
    >>> from datetime import datetime, timedelta, timezone
    >>> now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
    >>> from agents.pipeline.clustering.models import StoryCluster
    >>> recent = StoryCluster(
    ...     cluster_id="c1", cluster_category="markets",
    ...     cluster_first_seen_utc=now, cluster_last_seen_utc=now - timedelta(hours=10),
    ... )
    >>> [c.cluster_id for c in select_block([recent], candidate_published_utc=now)]
    ['c1']
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agents.pipeline.clustering.models import StoryCluster

DEFAULT_WINDOW_HOURS = 48


def select_block(
    active_clusters: list[StoryCluster],
    *,
    candidate_published_utc: datetime,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    category: str | None = None,
) -> list[StoryCluster]:
    """Narrow active clusters to those a candidate could plausibly join.

    A cluster is kept when the absolute time gap between ``candidate_published_utc`` and
    the cluster's ``cluster_last_seen_utc`` is at most ``window_hours`` (the
    ``window_hours`` edge is **inclusive**), and — when ``category`` is given — its
    ``cluster_category`` equals ``category``. Order of the surviving clusters is
    preserved from ``active_clusters`` (stable, for deterministic downstream iteration).

    All timestamps must be timezone-aware UTC datetimes; comparing a naive datetime
    against a tz-aware one would raise, which is the desired loud failure rather than a
    silent wrong block.

    Args:
        active_clusters: Candidate clusters to filter (typically the cross-day window load).
        candidate_published_utc: The candidate article's UTC tz-aware publish timestamp.
        window_hours: Inclusive window half-width in hours; defaults to
            ``DEFAULT_WINDOW_HOURS`` (48).
        category: Optional provisional category; when set, only same-category clusters pass.

    Returns:
        The subset of ``active_clusters`` within the time window (and category), in input order.

    Example:
        >>> from datetime import datetime, timedelta, timezone
        >>> from agents.pipeline.clustering.models import StoryCluster
        >>> now = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)
        >>> in_window = StoryCluster(
        ...     cluster_id="in", cluster_category="tech",
        ...     cluster_first_seen_utc=now, cluster_last_seen_utc=now - timedelta(hours=47),
        ... )
        >>> out_window = StoryCluster(
        ...     cluster_id="out", cluster_category="tech",
        ...     cluster_first_seen_utc=now, cluster_last_seen_utc=now - timedelta(hours=49),
        ... )
        >>> [c.cluster_id for c in select_block([in_window, out_window], candidate_published_utc=now)]
        ['in']
    """
    window_delta = timedelta(hours=window_hours)
    block: list[StoryCluster] = []
    for cluster in active_clusters:
        if category is not None and cluster.cluster_category != category:
            continue
        time_gap = abs(candidate_published_utc - cluster.cluster_last_seen_utc)
        if time_gap <= window_delta:
            block.append(cluster)
    return block
