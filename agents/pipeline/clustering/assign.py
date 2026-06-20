"""Assign-or-spawn decision + running-mean centroid update (Milestone M3b, Sub-phase 2).

The online clusterer, for each candidate article, must decide whether the article
**joins** the nearest existing story cluster or **spawns** a new one. This module
holds the three pure, side-effect-free primitives that encode that decision over
plain vectors and :class:`~agents.pipeline.clustering.models.StoryCluster`:

    - :func:`best_match`  — the nearest in-block cluster by cosine similarity (the
      same-event candidate), skipping clusters that have no centroid yet.
    - :func:`should_assign` — the τ-boundary join-vs-spawn business rule.
    - :func:`update_centroid_running_mean` — incrementally fold a new member's
      embedding into a cluster centroid so the centroid tracks the *mean* of all
      members (not just the last one), re-L2-normalized so cosine stays a dot
      product.

Cosine similarity is reused from :mod:`agents.pipeline.clustering.embeddings`
(``cosine_similarity`` == dot product on the L2-normalized Gemini
``text-embedding-004`` vectors). Everything here is pure Python (no numpy),
matching the dependency-free style of ``embeddings.py``.

Example:
    >>> from agents.pipeline.clustering.assign import should_assign, DEFAULT_TAU_ASSIGN
    >>> should_assign(0.80)
    True
    >>> should_assign(0.50)
    False
    >>> DEFAULT_TAU_ASSIGN
    0.75
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from agents.pipeline.clustering.embeddings import cosine_similarity
from agents.shared.logger import get_logger

if TYPE_CHECKING:
    from agents.pipeline.clustering.models import StoryCluster

logger = get_logger("pipeline.clustering.assign")

# Reason: τ_assign default 0.75 (phase spec §3 banner, owner execution delta).
# Tunable; real-corpus tuning of this threshold is deferred to M6.
DEFAULT_TAU_ASSIGN = 0.75

# Reason: a "no candidate" sentinel that is strictly below any real cosine score
# (cosine on unit vectors is in [-1, 1]), so should_assign(NO_MATCH_SCORE) is
# always False and the orchestrator unconditionally spawns. Returned by best_match
# when the block is empty or every cluster's centroid is None.
NO_MATCH_SCORE = -1.0


def best_match(
    candidate_embedding: list[float],
    block_clusters: list["StoryCluster"],
) -> tuple["StoryCluster | None", float]:
    """Find the in-block cluster most cosine-similar to a candidate embedding.

    Scans ``block_clusters`` (already narrowed to the time/category window by the
    blocking step), computes cosine similarity of the candidate against each
    cluster's centroid, and returns the single best (cluster, score) pair.
    Clusters whose ``cluster_centroid`` is ``None`` are **skipped** — a centroid
    has not been written yet, so there is nothing to compare against.

    Args:
        candidate_embedding: The L2-normalized embedding of the candidate article.
        block_clusters: The candidate's blocking window — the clusters eligible to
            be matched. May be empty.

    Returns:
        A ``(cluster, score)`` tuple. ``cluster`` is the best-matching
        :class:`StoryCluster` and ``score`` its cosine similarity to the
        candidate. When there is no candidate to compare (empty block, or every
        cluster has a ``None`` centroid), returns ``(None, NO_MATCH_SCORE)`` so
        the caller spawns a fresh cluster.

    Example:
        >>> from datetime import datetime, timezone
        >>> from agents.pipeline.clustering.models import StoryCluster
        >>> now = datetime(2026, 6, 18, tzinfo=timezone.utc)
        >>> near = StoryCluster(
        ...     cluster_id="near", cluster_centroid=[1.0, 0.0], cluster_category="tech",
        ...     cluster_first_seen_utc=now, cluster_last_seen_utc=now,
        ... )
        >>> far = StoryCluster(
        ...     cluster_id="far", cluster_centroid=[0.0, 1.0], cluster_category="tech",
        ...     cluster_first_seen_utc=now, cluster_last_seen_utc=now,
        ... )
        >>> matched, score = best_match([1.0, 0.0], [far, near])
        >>> matched.cluster_id, round(score, 3)
        ('near', 1.0)
    """
    best_cluster: "StoryCluster | None" = None
    best_score = NO_MATCH_SCORE

    for cluster in block_clusters:
        centroid = cluster.cluster_centroid
        if centroid is None:
            # Reason: no centroid written yet → nothing to compare against; skip.
            continue
        score = cosine_similarity(candidate_embedding, centroid)
        if best_cluster is None or score > best_score:
            best_cluster = cluster
            best_score = score

    if best_cluster is None:
        logger.info("best_match_no_candidate", block_size=len(block_clusters))
        return None, NO_MATCH_SCORE

    logger.info(
        "best_match_selected",
        block_size=len(block_clusters),
        matched_cluster_id=best_cluster.cluster_id,
        score=best_score,
    )
    return best_cluster, best_score


def should_assign(score: float, *, tau_assign: float = DEFAULT_TAU_ASSIGN) -> bool:
    """Decide whether a candidate joins the best-matching cluster (vs spawning).

    The τ boundary encodes the join-vs-spawn business rule: a candidate whose best
    cosine score reaches ``tau_assign`` is the *same event* and joins; below it,
    it is a *distinct event* and the caller spawns a new cluster. The boundary is
    **inclusive** (``score >= tau_assign`` assigns), so the default 0.75 itself
    assigns.

    Args:
        score: The best cosine similarity from :func:`best_match` (the
            ``NO_MATCH_SCORE`` sentinel always returns ``False``).
        tau_assign: The assign threshold (defaults to :data:`DEFAULT_TAU_ASSIGN`,
            0.75).

    Returns:
        ``True`` when ``score >= tau_assign`` (join), ``False`` otherwise (spawn).

    Example:
        >>> should_assign(0.75)
        True
        >>> should_assign(0.7499)
        False
    """
    return score >= tau_assign


def update_centroid_running_mean(
    old_centroid: list[float],
    old_count: int,
    new_embedding: list[float],
) -> list[float]:
    """Fold a new member embedding into a centroid via an incremental running mean.

    Computes the new mean as ``((old_centroid * old_count) + new_embedding) / (old_count + 1)``
    component-wise, then **re-L2-normalizes** the result so cosine similarity
    against it stays a plain dot product (the invariant the whole clusterer relies
    on). Because the running mean weights the existing centroid by its member
    count, the centroid tracks the mean *direction* of all members, not just the
    most recent one.

    Args:
        old_centroid: The cluster's current L2-normalized centroid.
        old_count: The number of members already folded into ``old_centroid``
            (i.e. ``cluster_member_count`` before adding the new member). Must be
            ``>= 1``.
        new_embedding: The L2-normalized embedding of the new member (same length
            as ``old_centroid``).

    Returns:
        The updated, L2-normalized centroid (one component per input dimension).

    Raises:
        ValueError: When the vectors are empty, their lengths differ, or
            ``old_count < 1``.

    Example:
        >>> # Two orthogonal unit vectors → mean points at 45°, re-normalized to unit length.
        >>> updated = update_centroid_running_mean([1.0, 0.0], 1, [0.0, 1.0])
        >>> round(updated[0], 4), round(updated[1], 4)
        (0.7071, 0.7071)
    """
    if not old_centroid or not new_embedding:
        raise ValueError("update_centroid_running_mean requires two non-empty vectors")
    if len(old_centroid) != len(new_embedding):
        raise ValueError(
            f"update_centroid_running_mean requires equal-length vectors, "
            f"got {len(old_centroid)} and {len(new_embedding)}"
        )
    if old_count < 1:
        raise ValueError(f"update_centroid_running_mean requires old_count >= 1, got {old_count}")

    new_count = old_count + 1
    mean = [
        ((old_component * old_count) + new_component) / new_count
        for old_component, new_component in zip(old_centroid, new_embedding, strict=True)
    ]
    return _l2_normalize(mean)


def _l2_normalize(vector: list[float]) -> list[float]:
    """Return the L2-normalized copy of a vector (unit length).

    Mirrors ``embeddings._l2_normalize`` (which is private to that module) so the
    running-mean centroid stays a unit vector and cosine remains a dot product.

    Args:
        vector: The raw (un-normalized) vector components.

    Returns:
        The vector scaled to unit L2 norm. A zero vector is returned unchanged —
        it has no meaningful direction to normalize, and dividing by its zero norm
        would be undefined.

    Example:
        >>> _l2_normalize([3.0, 4.0])
        [0.6, 0.8]
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # Reason: a zero vector cannot be normalized; return as-is rather than
        # dividing by zero (matches embeddings._l2_normalize behavior).
        return vector
    return [component / norm for component in vector]
