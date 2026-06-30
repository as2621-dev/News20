"""E1 ``story_importance`` — per-cluster terms, weight combine, within-category norm.

The shared-pool E1 intrinsic-importance model (``reference/shared-pool-pipeline.md`` §4,
``plans/shared-pool-rework-master-plan.md``), implemented — NOT forked (PRD Decision #5,
Rule 7)::

    story_importance(cluster) =
          W_breadth   · norm(distinct outlet count)        # syndication-dampened breadth
        + W_authority · authority_and_diversity(outlets)    # SP1 — authoritative AND varied
        + W_velocity  · norm(cluster_velocity)              # coverage acceleration
        + W_recency   · freshness(cluster_last_seen_utc)    # ~24h half-life (reuses produce_gate)
        + W_entity    · entity_prominence(cluster)          # involves registry entities
    → normalized WITHIN cluster_category

This replaces ``produce_gate.compute_importance_score``'s raw ``min(1, outlet_count/12)``
as the intrinsic importance of a *clustered* story. It is a pure transform over
``StoryCluster`` rows + their member outlets (no DB write — the batch persists via the
existing ``cluster_store.upsert``).

Layering:
  - SP2: :func:`compute_story_importance_terms` (the five un-normalized 0–1 terms) +
    :func:`combine` (the ``W_*`` weighted sum → a raw score).
  - SP3: :func:`normalize_importance_within_category` (min-max within ``cluster_category``)
    + :func:`score_clusters` (terms → combine → normalize → populate ``cluster_importance``).

All functions are **pure** over injected inputs — fully offline-unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from agents.pipeline.clustering.models import StoryCluster
from agents.pipeline.importance.source_tiers import authority_and_diversity
from agents.pipeline.produce_gate import compute_freshness_score
from agents.shared.logger import get_logger

logger = get_logger("pipeline.importance.story_importance")

# Reason: the E1 ``W_*`` term weights — the single config source for how the five
# components combine (no scattered constants). The spec says "start breadth-heavy; tune"
# (``reference/shared-pool-pipeline.md`` §4). This first draft is breadth-heavy with
# authority a close second (the two signals that distinguish a real story from a
# syndication burst), velocity/recency/entity lighter. They need NOT sum to 1 — the raw
# score is normalized within category afterward, so only their *relative* sizes matter.
# Tuning the exact values is a residual (PRD M3 Open items).
W_BREADTH: float = 0.40
W_AUTHORITY: float = 0.30
W_VELOCITY: float = 0.10
W_RECENCY: float = 0.10
W_ENTITY: float = 0.10

# Reason: the distinct-outlet count at which the breadth term saturates to 1.0. Mirrors
# ``produce_gate``'s ``_IMPORTANCE_SATURATION_OUTLET_COUNT`` (12) so "what a broad story
# looks like" is one shared notion across the gate and E1. Breadth counts DISTINCT
# outlets (``cluster_outlet_count``), never member/reprint count — that is the syndication
# dampening (SP2 DoD (b)).
_BREADTH_SATURATION_OUTLET_COUNT: int = 12

# Reason: the ``cluster_velocity`` value at which the velocity term saturates to 1.0.
# Coverage acceleration is unbounded above; clamp/scale it linearly to [0, 1] so it sits
# on the same 0–1 footing as the other terms. First-draft scale; a tuning residual.
_VELOCITY_SATURATION: float = 1.0


class ImportanceTerms(BaseModel):
    """The five un-normalized E1 component terms for one cluster (all in ``[0, 1]``).

    Carried as a model (not a bare tuple) so the combine step, the normalizer, and the
    tests can assert on each component independently — encoding WHY a score is what it is
    (Rule 9), not just the final number.

    Attributes:
        breadth: ``norm(distinct outlet count)`` — syndication-dampened coverage breadth.
        authority: ``authority_and_diversity(member_outlets)`` (SP1) — authoritative AND
            ideologically-varied outlets beat raw volume.
        velocity: ``norm(cluster_velocity)`` — coverage acceleration (the ex-"breaking"
            signal); 0.0 when ``cluster_velocity`` is missing/None (graceful).
        recency: ``compute_freshness_score(cluster_last_seen_utc, now)`` — ~24h decay,
            clamped at 1.0 (future-dated last-seen does NOT inflate above 1.0).
        entity: entity-prominence over the cluster — 0.0 when no entity signal is
            supplied (the persisted ``StoryCluster`` carries no entity column yet, so this
            degrades gracefully until the clusterer threads one in).
    """

    breadth: float = Field(..., ge=0.0, le=1.0)
    authority: float = Field(..., ge=0.0, le=1.0)
    velocity: float = Field(..., ge=0.0, le=1.0)
    recency: float = Field(..., ge=0.0, le=1.0)
    entity: float = Field(..., ge=0.0, le=1.0)


def _norm_outlet_count(distinct_outlet_count: int) -> float:
    """Scale a distinct-outlet count linearly to ``[0, 1]``, saturating at the cap."""
    if distinct_outlet_count <= 0:
        return 0.0
    return min(1.0, distinct_outlet_count / _BREADTH_SATURATION_OUTLET_COUNT)


def _norm_velocity(cluster_velocity: float | None) -> float:
    """Scale ``cluster_velocity`` linearly to ``[0, 1]``; None/negative → 0.0 (graceful).

    A missing velocity signal (the clusterer has not produced it on a real run yet)
    contributes nothing rather than crashing (SP2 DoD (d)).
    """
    if cluster_velocity is None or cluster_velocity <= 0.0 or _VELOCITY_SATURATION <= 0.0:
        return 0.0
    return min(1.0, cluster_velocity / _VELOCITY_SATURATION)


def compute_story_importance_terms(
    cluster: StoryCluster,
    member_outlets: Iterable[str | None],
    now_utc: datetime,
    *,
    entity_prominence: float = 0.0,
) -> ImportanceTerms:
    """Compute the five un-normalized E1 component terms for one cluster (SP2).

    Per ``reference/shared-pool-pipeline.md`` §4. Each term is a 0–1 signal; they are
    NOT yet combined or normalized (that is :func:`combine` / SP3).

      - **breadth** uses ``cluster.cluster_outlet_count`` — the count of DISTINCT outlets,
        NOT ``cluster_member_count`` (the raw reprint/member count). This is the
        syndication dampening: N reprints from an already-counted outlet do not raise
        breadth (SP2 DoD (b)).
      - **authority** is SP1's :func:`authority_and_diversity` over the cluster's member
        outlet domains — varied authoritative outlets beat a syndication pile.
      - **velocity** scales ``cluster.cluster_velocity``; None → 0.0 (SP2 DoD (d)).
      - **recency** reuses ``produce_gate.compute_freshness_score`` on
        ``cluster.cluster_last_seen_utc`` — clamped at 1.0 for a future-dated last-seen
        (SP2 DoD (c)); the decay primitive is NOT re-implemented (Rule 3/7).
      - **entity** is the injected ``entity_prominence`` (default 0.0 — graceful, since
        the persisted cluster carries no entity column yet).

    Args:
        cluster: The cluster being scored (carries distinct-outlet count, velocity,
            last-seen).
        member_outlets: The cluster's member outlet domains (raw; may repeat / be None) —
            the authority+diversity input. Distinct-outlet dedup happens inside SP1.
        now_utc: Current time for the recency decay (injected for tests).
        entity_prominence: Optional 0–1 entity-prominence signal for the cluster.

    Returns:
        The :class:`ImportanceTerms` for this cluster.
    """
    breadth = _norm_outlet_count(cluster.cluster_outlet_count)
    authority = authority_and_diversity(member_outlets)
    velocity = _norm_velocity(cluster.cluster_velocity)
    recency = compute_freshness_score(cluster.cluster_last_seen_utc, now_utc)
    entity = min(1.0, max(0.0, entity_prominence))
    return ImportanceTerms(
        breadth=breadth,
        authority=authority,
        velocity=velocity,
        recency=recency,
        entity=entity,
    )


def combine(terms: ImportanceTerms) -> float:
    """Combine the five E1 terms into one raw (un-normalized) importance score.

    The ``W_*``-weighted sum (``reference/shared-pool-pipeline.md`` §4). The result is a
    raw magnitude — it is normalized WITHIN category afterward (:func:`score_clusters`),
    so it is intentionally NOT clamped to 1.0 here.

    Args:
        terms: The cluster's five component terms.

    Returns:
        The raw weighted-sum importance.
    """
    return (
        W_BREADTH * terms.breadth
        + W_AUTHORITY * terms.authority
        + W_VELOCITY * terms.velocity
        + W_RECENCY * terms.recency
        + W_ENTITY * terms.entity
    )


# ---------------------------------------------------------------------------------------
# SP3 — within-category normalization + cluster wiring
# ---------------------------------------------------------------------------------------

# Reason: the importance assigned to the sole cluster in a single-cluster category. PRD
# M3 edge case: a one-story category must NOT divide-by-zero and must NOT be spuriously
# inflated to 1.0 (that would falsely claim "this is a maximally-important story"). We
# choose a NEUTRAL MID value: the cluster is the category leader by default, but its
# importance is not asserted as max — it competes on the β-weighted term at a defensible
# middle, neither suppressed to 0 nor inflated to 1. (Documented + tested, SP3 DoD (b).)
_SINGLE_CLUSTER_NEUTRAL_IMPORTANCE: float = 0.5


def normalize_importance_within_category(
    clusters_with_raw: Sequence[tuple[str, str, float]],
) -> dict[str, float]:
    """Min-max normalize each cluster's raw E1 score WITHIN its category (SP3).

    ``reference/shared-pool-pipeline.md`` §4: "normalize WITHIN category (a big sport
    story competes with sport, not with a war)". For each ``cluster_category`` group,
    map raw scores to ``[0, 1]`` so the category's top cluster reaches ~1.0 and its
    bottom reaches 0.0 — INDEPENDENTLY per category, so a big war story cannot suppress
    the biggest sport story (SP3 DoD (a)).

    Degenerate cases (SP3 DoD (b)/(c)):
      - a category with ONE cluster → min == max → no min-max range. We do NOT divide by
        zero and do NOT inflate it to 1.0; we assign :data:`_SINGLE_CLUSTER_NEUTRAL_IMPORTANCE`
        (a documented neutral mid-value). The same neutral value is used if every cluster
        in a category has an identical raw score (range 0).
      - an empty input → an empty result (no crash).

    Args:
        clusters_with_raw: ``(cluster_id, cluster_category, raw_score)`` triples.

    Returns:
        ``{cluster_id: importance_in_[0,1]}``.
    """
    by_category: dict[str, list[tuple[str, float]]] = {}
    for cluster_id, category, raw in clusters_with_raw:
        by_category.setdefault(category, []).append((cluster_id, raw))

    result: dict[str, float] = {}
    for category, members in by_category.items():
        raws = [raw for _cid, raw in members]
        lo, hi = min(raws), max(raws)
        span = hi - lo
        for cluster_id, raw in members:
            if len(members) == 1 or span <= 0.0:
                # Single cluster, or a flat tie: neutral mid — no div-by-zero, no
                # spurious inflation to 1.0 (PRD edge case, SP3 DoD (b)).
                result[cluster_id] = _SINGLE_CLUSTER_NEUTRAL_IMPORTANCE
            else:
                result[cluster_id] = (raw - lo) / span
    return result


def score_clusters(
    clusters: Sequence[StoryCluster],
    member_outlets_by_cluster: Mapping[str, Iterable[str | None]],
    now_utc: datetime,
    *,
    entity_prominence_by_cluster: Mapping[str, float] | None = None,
) -> list[StoryCluster]:
    """Score a batch of clusters with E1 importance, normalized within category (SP3).

    The end-to-end pure transform: for each cluster compute its raw E1 terms (SP2),
    combine to a raw score, normalize WITHIN ``cluster_category`` (SP3), and return COPIES
    of the clusters with ``cluster_importance ∈ [0, 1]`` populated. This replaces
    ``produce_gate.compute_importance_score``'s raw ``min(1, outlet_count/12)`` as the
    intrinsic importance source for clustered stories.

    It performs **no DB write** — the batch persists the returned clusters via the
    existing ``cluster_store.upsert``.

    Args:
        clusters: The clusters to score.
        member_outlets_by_cluster: ``{cluster_id: [outlet_domain, ...]}`` — the
            authority+diversity input per cluster. A cluster missing here is scored with
            no member outlets (authority term 0.0).
        now_utc: Current time for the recency decay (injected for tests).
        entity_prominence_by_cluster: Optional ``{cluster_id: 0-1}`` entity-prominence
            per cluster (default: all 0.0 — graceful, no entity signal persisted yet).

    Returns:
        New :class:`StoryCluster` objects (copies) with ``cluster_importance`` populated
        in ``[0, 1]``. Empty input → empty list.
    """
    if not clusters:
        return []

    entity_map = entity_prominence_by_cluster or {}
    raw_triples: list[tuple[str, str, float]] = []
    for cluster in clusters:
        terms = compute_story_importance_terms(
            cluster=cluster,
            member_outlets=member_outlets_by_cluster.get(cluster.cluster_id, []),
            now_utc=now_utc,
            entity_prominence=entity_map.get(cluster.cluster_id, 0.0),
        )
        raw_triples.append((cluster.cluster_id, cluster.cluster_category, combine(terms)))

    normalized = normalize_importance_within_category(raw_triples)

    scored = [
        cluster.model_copy(
            update={"cluster_importance": normalized.get(cluster.cluster_id)}
        )
        for cluster in clusters
    ]

    logger.info(
        "story_importance_scored_clusters",
        cluster_count=len(scored),
        category_count=len({c.cluster_category for c in clusters}),
    )
    return scored
