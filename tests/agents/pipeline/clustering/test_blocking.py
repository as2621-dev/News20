"""Unit tests for time-window + category blocking (Milestone M3b, Sub-phase 1).

These are pure (no mocks): ``select_block`` is a pure function over plain ``StoryCluster``
models, and the engine I/O models validate plain data. Each test encodes WHY the
behaviour matters (Rule 9):

    (a) the window math is correct on BOTH sides of the boundary — a cluster last seen
        47h before the candidate is in the block, one at 49h is out — so the assign step
        only sees plausibly-same-event clusters;
    (b) the optional category filter excludes an in-window cluster of a different category
        (blocking is time-window AND provisional category);
    (c) the exact 48h edge is INCLUSIVE (the documented choice) — a cluster precisely at
        the window edge is kept, one a microsecond past is dropped — so the boundary rule
        is pinned and can't silently flip;
    (d) ClusterInput / ClusterRun validate a minimal example (the frozen engine I/O shape).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.pipeline.clustering.blocking import DEFAULT_WINDOW_HOURS, select_block
from agents.pipeline.clustering.engine_models import ClusterInput, ClusterRun
from agents.pipeline.clustering.models import ClusterMember, StoryCluster

CANDIDATE_TIME = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


def _make_cluster(
    cluster_id: str,
    *,
    hours_before_candidate: float,
    category: str = "tech",
) -> StoryCluster:
    """Build a StoryCluster whose last_seen is ``hours_before_candidate`` before CANDIDATE_TIME."""
    last_seen = CANDIDATE_TIME - timedelta(hours=hours_before_candidate)
    return StoryCluster(
        cluster_id=cluster_id,
        cluster_category=category,
        cluster_first_seen_utc=last_seen,
        cluster_last_seen_utc=last_seen,
    )


def test_default_window_hours_is_48() -> None:
    """The shipped default window is 48h (spec §2C step 3)."""
    assert DEFAULT_WINDOW_HOURS == 48


def test_cluster_47h_before_is_in_block_and_49h_is_out() -> None:
    """Window math holds on both sides: 47h in, 49h out (the core narrowing rule)."""
    in_window = _make_cluster("in-47h", hours_before_candidate=47)
    out_window = _make_cluster("out-49h", hours_before_candidate=49)

    block = select_block([in_window, out_window], candidate_published_utc=CANDIDATE_TIME)

    kept_ids = [cluster.cluster_id for cluster in block]
    assert kept_ids == ["in-47h"]


def test_window_is_symmetric_for_future_candidate() -> None:
    """abs() gap means a cluster last_seen AFTER the candidate is still in-window if close.

    A candidate published 10h before a cluster's last_seen is still within 48h, so
    blocking must not assume the cluster is always older than the candidate.
    """
    future_cluster = _make_cluster("future-10h", hours_before_candidate=-10)

    block = select_block([future_cluster], candidate_published_utc=CANDIDATE_TIME)

    assert [c.cluster_id for c in block] == ["future-10h"]


def test_category_filter_excludes_in_window_other_category_cluster() -> None:
    """category='tech' drops an in-window 'sport' cluster: blocking is time AND category."""
    tech_cluster = _make_cluster("tech-in", hours_before_candidate=5, category="tech")
    sport_cluster = _make_cluster("sport-in", hours_before_candidate=5, category="sport")

    block = select_block(
        [tech_cluster, sport_cluster],
        candidate_published_utc=CANDIDATE_TIME,
        category="tech",
    )

    assert [c.cluster_id for c in block] == ["tech-in"]


def test_exactly_48h_edge_is_inclusive_and_just_past_is_excluded() -> None:
    """The documented boundary: exactly 48h is KEPT (inclusive), a hair past 48h is DROPPED."""
    exactly_at_edge = _make_cluster("edge-48h", hours_before_candidate=48)
    just_past_edge = StoryCluster(
        cluster_id="just-past",
        cluster_category="tech",
        cluster_first_seen_utc=CANDIDATE_TIME,
        cluster_last_seen_utc=CANDIDATE_TIME - timedelta(hours=48, microseconds=1),
    )

    block = select_block([exactly_at_edge, just_past_edge], candidate_published_utc=CANDIDATE_TIME)

    assert [c.cluster_id for c in block] == ["edge-48h"]


def test_custom_window_hours_narrows_the_block() -> None:
    """A tighter window_hours drops a cluster that the default 48h would keep."""
    cluster_30h = _make_cluster("c-30h", hours_before_candidate=30)

    with_default = select_block([cluster_30h], candidate_published_utc=CANDIDATE_TIME)
    with_tight = select_block([cluster_30h], candidate_published_utc=CANDIDATE_TIME, window_hours=24)

    assert [c.cluster_id for c in with_default] == ["c-30h"]
    assert with_tight == []


def test_cluster_input_validates_minimal_example() -> None:
    """ClusterInput accepts a minimal valid candidate and exposes its fields."""
    candidate = ClusterInput(
        input_index=3,
        input_text="Fed holds rates steady as inflation cools",
        input_url="https://reuters.com/markets/fed-holds",
        input_outlet="reuters.com",
        input_published_utc=CANDIDATE_TIME,
        input_provisional_category="markets",
    )

    assert candidate.input_index == 3
    assert candidate.input_outlet == "reuters.com"
    assert candidate.input_provisional_category == "markets"


def test_cluster_input_outlet_defaults_to_none() -> None:
    """An outlet-less candidate is valid (input_outlet is optional)."""
    candidate = ClusterInput(
        input_index=0,
        input_text="headline only",
        input_url="https://example.com/x",
        input_published_utc=CANDIDATE_TIME,
        input_provisional_category="tech",
    )

    assert candidate.input_outlet is None


def test_cluster_run_validates_minimal_example() -> None:
    """ClusterRun holds clusters, members, and the input_index -> cluster_id map."""
    cluster = _make_cluster("clu-1", hours_before_candidate=0)
    member = ClusterMember(
        cluster_id="clu-1",
        member_url="https://example.com/x",
        member_outlet="example.com",
        member_seen_utc=CANDIDATE_TIME,
    )

    run = ClusterRun(clusters=[cluster], members=[member], input_cluster_map={0: "clu-1"})

    assert run.clusters[0].cluster_id == "clu-1"
    assert run.members[0].member_url == "https://example.com/x"
    assert run.input_cluster_map == {0: "clu-1"}


def test_cluster_run_defaults_to_empty_containers() -> None:
    """A freshly constructed ClusterRun starts empty (default_factory containers)."""
    run = ClusterRun()

    assert run.clusters == []
    assert run.members == []
    assert run.input_cluster_map == {}
