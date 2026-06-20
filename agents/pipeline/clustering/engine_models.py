"""Engine I/O models for the online assign-or-spawn clusterer (Milestone M3b, Sub-phase 1).

These are the typed in-memory boundary the M3b engine speaks: ``ClusterInput`` is one
candidate article handed to the clusterer, and ``ClusterRun`` is the result holder the
orchestrator (Sub-phase 3) fills and the persistence/id-bridge (Sub-phase 4) consumes.

They sit ABOVE the persistence models in ``agents.pipeline.clustering.models``
(``StoryCluster`` / ``ClusterMember``, which mirror the ``0018_story_clusters.sql``
columns). ``ClusterRun`` simply carries lists of those persistence models plus a map
from each kept candidate's ``input_index`` to the ``cluster_id`` it landed in, so the
caller can trace which input produced which cluster without re-deriving it.

A "kept" input is one that survived the near-dup prefilter (a dropped reprint is folded
into its representative's cluster as a ``ClusterMember`` but does not get its own
``input_cluster_map`` entry — the representative's index carries the mapping).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agents.pipeline.clustering.models import ClusterMember, StoryCluster


class ClusterInput(BaseModel):
    """One candidate article fed into the online clusterer.

    ``input_text`` is the headline + lead concatenation used for BOTH the near-dup
    shingling (M3a ``near_dup``) and the Gemini embedding (M3a ``embeddings``) — one
    text field drives both so the prefilter and the centroid see the same content.

    Example:
        >>> from datetime import datetime, timezone
        >>> candidate = ClusterInput(
        ...     input_index=0,
        ...     input_text="Fed holds rates steady as inflation cools",
        ...     input_url="https://reuters.com/markets/fed-holds",
        ...     input_outlet="reuters.com",
        ...     input_published_utc=datetime(2026, 6, 19, 14, 0, tzinfo=timezone.utc),
        ...     input_provisional_category="markets",
        ... )
        >>> candidate.input_index
        0
    """

    input_index: int = Field(
        ..., description="Stable index of this candidate within the batch (drives input_cluster_map keys)."
    )
    input_text: str = Field(
        ..., description="Headline + lead text used for both near-dup shingling and embedding."
    )
    input_url: str = Field(..., description="The candidate article URL (becomes a ClusterMember.member_url).")
    input_outlet: str | None = Field(
        default=None, description="The candidate's outlet domain (drives distinct-outlet counting; nullable)."
    )
    input_published_utc: datetime = Field(
        ..., description="UTC tz-aware publish timestamp (drives the time-window blocking against clusters)."
    )
    input_provisional_category: str = Field(
        ...,
        description=(
            "Caller-supplied provisional feed_category used as a blocking aid at spawn time; "
            "M3c's centroid classifier overwrites the authoritative cluster_category."
        ),
    )


class ClusterRun(BaseModel):
    """Result of one online clustering pass over a batch of ``ClusterInput`` candidates.

    Holds the clusters touched/created this run, every member appended this run, and a
    map from each kept candidate's ``input_index`` to the ``cluster_id`` it joined or
    spawned. The id-bridge + persistence step (Sub-phase 4) consumes this holder; it is
    intentionally a flat, plain-data container (no behaviour) so its shape can be frozen
    early and SP3/SP4 build against it.

    Example:
        >>> run = ClusterRun(clusters=[], members=[], input_cluster_map={})
        >>> run.input_cluster_map
        {}
    """

    clusters: list[StoryCluster] = Field(
        default_factory=list, description="Clusters created or updated during this run."
    )
    members: list[ClusterMember] = Field(
        default_factory=list, description="All member rows appended to clusters during this run."
    )
    input_cluster_map: dict[int, str] = Field(
        default_factory=dict,
        description="Map of a kept candidate's input_index -> the cluster_id it joined or spawned.",
    )
