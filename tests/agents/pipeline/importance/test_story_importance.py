"""Unit tests for E1 ``story_importance`` (FSR-M3 SP2 + SP3).

SP2 DoD (Rule 9 — assert the *reason*):
  (a) authority beats syndication burst — an authority-varied 10-distinct-outlet cluster
      scores a strictly HIGHER raw importance than a 20-outlet single-wire content-farm
      burst (the headline M3 DoD);
  (b) syndication dampening — adding N reprints from the SAME already-counted outlet does
      not raise the breadth term (distinct-outlet, not member-count);
  (c) recency clamp — a future-dated ``cluster_last_seen_utc`` clamps recency at 1.0;
  (d) missing/None ``cluster_velocity`` degrades to 0 contribution, not a crash.

SP3 DoD:
  (a) within-category normalization — each category's top cluster reaches the category-max
      INDEPENDENTLY (the biggest sport story is not suppressed by a bigger war story);
  (b) single-cluster / single-category edge — no div-by-zero, NOT inflated to 1.0;
  (c) empty category → empty result, no crash;
  (d) returned ``StoryCluster`` objects carry ``cluster_importance ∈ [0, 1]``.

All inputs are pure data — no DB, no clock, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.pipeline.clustering.models import StoryCluster
from agents.pipeline.importance.story_importance import (
    _SINGLE_CLUSTER_NEUTRAL_IMPORTANCE,
    ImportanceTerms,
    combine,
    compute_story_importance_terms,
    normalize_importance_within_category,
    score_clusters,
)

_NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)

# A representative spread of distinct high-authority, ideologically-varied outlets.
_VARIED_AUTHORITATIVE = [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "nytimes.com",
    "wsj.com",
    "theguardian.com",
    "washingtonpost.com",
    "aljazeera.com",
    "bloomberg.com",
    "npr.org",
]


def _cluster(
    cluster_id: str,
    *,
    category: str = "geopolitics",
    outlet_count: int = 1,
    velocity: float | None = None,
    last_seen: datetime = _NOW,
) -> StoryCluster:
    """Build a minimal StoryCluster with the E1-relevant fields set."""
    return StoryCluster(
        cluster_id=cluster_id,
        cluster_category=category,
        cluster_outlet_count=outlet_count,
        cluster_velocity=velocity,
        cluster_first_seen_utc=last_seen,
        cluster_last_seen_utc=last_seen,
    )


# --------------------------------------------------------------------------------------
# SP2 — un-normalized terms + combine
# --------------------------------------------------------------------------------------


def test_authority_varied_story_beats_single_wire_syndication_burst() -> None:
    """SP2 DoD (a): authority+diversity beats raw syndication volume on RAW importance.

    WHY (the headline M3 property, US22): a syndicated wire reprinted by 20 content-farm
    outlets must NOT out-importance a real story carried by 10 varied authoritative
    outlets. The burst has higher raw outlet count (20 > 10) so a naive
    ``min(1, count/12)`` would tie or favour it — E1's authority+diversity term is what
    flips it. If authority stopped mattering, this would regress.
    """
    varied = _cluster("varied", outlet_count=10)
    varied_terms = compute_story_importance_terms(
        varied, _VARIED_AUTHORITATIVE, _NOW
    )

    # A 20-outlet burst, but every outlet is the SAME content-farm wire (no diversity,
    # no authority). Distinct-outlet count is 20 for breadth, yet authority collapses.
    burst = _cluster("burst", outlet_count=20)
    burst_outlets = ["newswirebot.example"] * 20
    burst_terms = compute_story_importance_terms(burst, burst_outlets, _NOW)

    assert combine(varied_terms) > combine(burst_terms)
    # The reason is authority, not breadth: the burst's breadth saturates higher.
    assert burst_terms.breadth >= varied_terms.breadth
    assert varied_terms.authority > burst_terms.authority


def test_syndication_reprints_do_not_raise_breadth() -> None:
    """SP2 DoD (b): N reprints from an already-counted outlet do not raise breadth.

    WHY (syndication dampening): breadth must reflect DISTINCT covering outlets
    (``cluster_outlet_count``), never raw member/reprint count. If breadth ever read
    member-count, a single outlet reprinting itself could fake a broad story.
    """
    # Same distinct-outlet count (3) regardless of how many reprint members exist.
    three_distinct = _cluster("c", outlet_count=3)
    terms_few = compute_story_importance_terms(
        three_distinct, ["reuters.com", "bbc.com", "apnews.com"], _NOW
    )
    # Add 20 reprints from one already-counted outlet — distinct count is unchanged (3),
    # so the cluster's cluster_outlet_count stays 3 and breadth must not move.
    terms_many = compute_story_importance_terms(
        three_distinct,
        ["reuters.com", "bbc.com", "apnews.com"] + ["reuters.com"] * 20,
        _NOW,
    )
    assert terms_few.breadth == terms_many.breadth
    # Authority is also distinct-outlet based, so the reprints must not lift it either.
    assert terms_few.authority == terms_many.authority


def test_future_dated_last_seen_clamps_recency_at_one() -> None:
    """SP2 DoD (c): a future-dated ``cluster_last_seen_utc`` clamps recency at 1.0.

    WHY: a clock-skewed / future ``seendate`` must not inflate recency above 1.0 and let
    a stale-but-misdated story leapfrog. Reuses ``produce_gate.compute_freshness_score``'s
    clamp rather than re-deriving decay.
    """
    future = _cluster("future", last_seen=_NOW + timedelta(hours=12))
    terms = compute_story_importance_terms(future, ["reuters.com"], _NOW)
    assert terms.recency == 1.0


def test_missing_velocity_degrades_to_zero_no_crash() -> None:
    """SP2 DoD (d): a missing/None ``cluster_velocity`` contributes 0, not a crash.

    WHY: ``cluster_velocity`` is produced upstream by the clusterer and may be absent on
    real runs; E1 must degrade gracefully (velocity term 0) rather than failing the whole
    importance pass.
    """
    no_velocity = _cluster("nv", velocity=None)
    terms = compute_story_importance_terms(no_velocity, ["reuters.com"], _NOW)
    assert terms.velocity == 0.0
    # A present velocity DOES contribute (the signal is wired, just graceful when absent).
    with_velocity = _cluster("wv", velocity=1.0)
    assert compute_story_importance_terms(
        with_velocity, ["reuters.com"], _NOW
    ).velocity > 0.0


def test_entity_prominence_optional_and_clamped() -> None:
    """The entity term defaults to 0 (no signal persisted) and clamps to [0, 1]."""
    cluster = _cluster("e")
    assert compute_story_importance_terms(cluster, ["reuters.com"], _NOW).entity == 0.0
    assert (
        compute_story_importance_terms(
            cluster, ["reuters.com"], _NOW, entity_prominence=2.0
        ).entity
        == 1.0
    )


# --------------------------------------------------------------------------------------
# SP3 — within-category normalization + cluster wiring
# --------------------------------------------------------------------------------------


def test_normalization_is_within_category_independent() -> None:
    """SP3 DoD (a): each category's top cluster reaches the category-max independently.

    WHY (US23 — defensible/normalized): the biggest SPORT story must compete with sport,
    not be suppressed by a bigger WORLD story. Two clusters per category; each category's
    leader normalizes to ~1.0 and its laggard to 0.0, regardless of the other category's
    raw magnitudes.
    """
    triples = [
        ("sport_big", "sport", 0.30),
        ("sport_small", "sport", 0.10),
        ("world_big", "geopolitics", 0.90),
        ("world_small", "geopolitics", 0.50),
    ]
    norm = normalize_importance_within_category(triples)
    # Each category leader reaches 1.0; each laggard 0.0 — independently.
    assert norm["sport_big"] == 1.0
    assert norm["sport_small"] == 0.0
    assert norm["world_big"] == 1.0
    assert norm["world_small"] == 0.0
    # The big sport story (raw 0.30) is NOT suppressed by the bigger world story (0.90):
    # within its own category it still reaches the top.
    assert norm["sport_big"] == norm["world_big"]


def test_single_cluster_category_neutral_not_inflated() -> None:
    """SP3 DoD (b): a one-cluster category gets a neutral mid value, not 1.0, no crash.

    WHY (PRD edge case): with one cluster there is no min-max range; inflating it to 1.0
    would falsely assert "maximally important", and dividing by a zero span would crash.
    We assign a documented neutral mid-value.
    """
    norm = normalize_importance_within_category([("only", "sport", 0.42)])
    assert norm["only"] == _SINGLE_CLUSTER_NEUTRAL_IMPORTANCE
    assert norm["only"] != 1.0
    # A flat tie (equal raws) in a category also takes the neutral value (no div-by-zero).
    tie = normalize_importance_within_category(
        [("a", "tech", 0.5), ("b", "tech", 0.5)]
    )
    assert tie["a"] == _SINGLE_CLUSTER_NEUTRAL_IMPORTANCE
    assert tie["b"] == _SINGLE_CLUSTER_NEUTRAL_IMPORTANCE


def test_empty_input_yields_empty_result() -> None:
    """SP3 DoD (c): no clusters → empty result, no crash."""
    assert normalize_importance_within_category([]) == {}
    assert score_clusters([], {}, _NOW) == []


def test_score_clusters_populates_importance_in_unit_range() -> None:
    """SP3 DoD (d): returned clusters carry ``cluster_importance ∈ [0, 1]``.

    WHY: the batch persists ``cluster_importance`` and the ranking term reads it; it must
    be a populated, in-range number, and the authority-varied story must out-rank the
    syndication burst end-to-end through ``score_clusters`` (not just at the raw-term
    level).
    """
    varied = _cluster("varied", category="geopolitics", outlet_count=10)
    burst = _cluster("burst", category="geopolitics", outlet_count=20)
    scored = score_clusters(
        [varied, burst],
        {
            "varied": _VARIED_AUTHORITATIVE,
            "burst": ["newswirebot.example"] * 20,
        },
        _NOW,
    )
    by_id = {c.cluster_id: c for c in scored}
    for cluster in scored:
        assert cluster.cluster_importance is not None
        assert 0.0 <= cluster.cluster_importance <= 1.0
    # End-to-end: the authority-varied story normalizes ABOVE the syndication burst.
    assert by_id["varied"].cluster_importance > by_id["burst"].cluster_importance
    # The originals are not mutated (pure transform — returns copies).
    assert varied.cluster_importance is None


def test_combine_uses_all_terms() -> None:
    """``combine`` is the W_* weighted sum — a higher term raises the score (no dead weight)."""
    base = ImportanceTerms(breadth=0.0, authority=0.0, velocity=0.0, recency=0.0, entity=0.0)
    assert combine(base) == 0.0
    for field in ("breadth", "authority", "velocity", "recency", "entity"):
        lifted = base.model_copy(update={field: 1.0})
        assert combine(lifted) > combine(base), f"{field} must contribute to the score"
