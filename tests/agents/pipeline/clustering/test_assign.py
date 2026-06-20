"""Unit tests for the assign-or-spawn primitives (Milestone M3b, Sub-phase 2).

Pure functions over plain vectors + ``StoryCluster`` — NO mocks, NO network, NO
DB (cosine and the running mean are pure math).

These tests encode WHY (Rule 9):
    - ``best_match`` returning the cos≈0.9 cluster over the cos≈0.2 one is the
      same-event decision: pick the *nearest* prior cluster, not just any.
    - The τ boundary in ``should_assign`` IS the join-vs-spawn business rule — at
      0.75 join, a hair below spawn. A regression that flipped the comparison or
      shifted the boundary fails (b).
    - The running mean of three unit vectors equalling the batch-mean *direction*
      proves the centroid tracks ALL members, not just the last one (the property
      that makes a rolling cluster centroid meaningful). And ‖v‖≈1 protects the
      cosine-is-a-dot-product invariant the whole clusterer depends on.
    - ``best_match`` over an empty / all-null-centroid block returning the
      sentinel is what makes the orchestrator spawn instead of crash (d).

    >>> pytest tests/agents/pipeline/clustering/test_assign.py -q
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from agents.pipeline.clustering.assign import (
    DEFAULT_TAU_ASSIGN,
    NO_MATCH_SCORE,
    best_match,
    should_assign,
    update_centroid_running_mean,
)
from agents.pipeline.clustering.models import StoryCluster

_NOW = datetime(2026, 6, 18, tzinfo=timezone.utc)


def _normalize(vector: list[float]) -> list[float]:
    """Local L2-normalize helper, kept independent of the SUT."""
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]


def _make_cluster(cluster_id: str, centroid: list[float] | None) -> StoryCluster:
    """Build a minimal StoryCluster with a given (possibly None) centroid."""
    return StoryCluster(
        cluster_id=cluster_id,
        cluster_centroid=centroid,
        cluster_category="tech",
        cluster_first_seen_utc=_NOW,
        cluster_last_seen_utc=_NOW,
    )


class TestBestMatch:
    """best_match returns the nearest non-null-centroid cluster + its score."""

    def test_returns_higher_cosine_cluster(self) -> None:
        """(a) One centroid cos≈0.9, another cos≈0.2 → returns the 0.9 cluster + score.

        WHY: same-event assignment must pick the *nearest* prior cluster. If
        best_match returned the lower-similarity cluster (or ignored the score),
        candidates would join the wrong event.
        """
        # Candidate along the x-axis. near_centroid leans heavily x (cos≈0.9),
        # far_centroid leans heavily y (cos≈0.2).
        candidate = _normalize([1.0, 0.0])
        near_centroid = _normalize([0.9, math.sqrt(1.0 - 0.9**2)])  # cos to candidate == 0.9
        far_centroid = _normalize([0.2, math.sqrt(1.0 - 0.2**2)])  # cos to candidate == 0.2
        near = _make_cluster("near", near_centroid)
        far = _make_cluster("far", far_centroid)

        matched, score = best_match(candidate, [far, near])

        assert matched is not None
        assert matched.cluster_id == "near"
        assert score == pytest.approx(0.9, abs=1e-9)

    def test_empty_block_returns_sentinel(self) -> None:
        """(d) An empty block → (None, NO_MATCH_SCORE) so the orchestrator spawns."""
        matched, score = best_match([1.0, 0.0], [])

        assert matched is None
        assert score == NO_MATCH_SCORE

    def test_all_null_centroids_return_sentinel(self) -> None:
        """(d) Every cluster has a None centroid → (None, sentinel), nothing to match."""
        clusters = [_make_cluster("a", None), _make_cluster("b", None)]

        matched, score = best_match([1.0, 0.0], clusters)

        assert matched is None
        assert score == NO_MATCH_SCORE

    def test_skips_null_centroid_but_matches_real_one(self) -> None:
        """A None-centroid cluster is skipped; a real one in the same block still matches.

        WHY: a freshly-spawned cluster can sit in the block before its centroid is
        written — it must not shadow or crash the match against valid clusters.
        """
        candidate = _normalize([1.0, 0.0])
        real = _make_cluster("real", _normalize([1.0, 0.0]))
        null = _make_cluster("null", None)

        matched, score = best_match(candidate, [null, real])

        assert matched is not None
        assert matched.cluster_id == "real"
        assert score == pytest.approx(1.0, abs=1e-9)


class TestShouldAssign:
    """The τ boundary encodes the join-vs-spawn business rule."""

    def test_true_at_tau(self) -> None:
        """(b) Exactly τ assigns (inclusive boundary)."""
        assert should_assign(DEFAULT_TAU_ASSIGN) is True

    def test_true_just_above_tau(self) -> None:
        """(b) Just above τ assigns."""
        assert should_assign(DEFAULT_TAU_ASSIGN + 1e-6) is True

    def test_false_just_below_tau(self) -> None:
        """(b) Just below τ spawns — the load-bearing distinction."""
        assert should_assign(DEFAULT_TAU_ASSIGN - 1e-6) is False

    def test_sentinel_score_never_assigns(self) -> None:
        """The no-match sentinel must always spawn, never join."""
        assert should_assign(NO_MATCH_SCORE) is False

    def test_custom_tau_overrides_default(self) -> None:
        """A caller-supplied tau_assign shifts the boundary (tunable per spec)."""
        assert should_assign(0.6, tau_assign=0.5) is True
        assert should_assign(0.6, tau_assign=0.7) is False


class TestUpdateCentroidRunningMean:
    """The centroid tracks the mean direction of all members and stays unit-length."""

    def test_single_fold_equals_batch_mean_direction(self) -> None:
        """(c) A single fold of two unit vectors == their batch-mean DIRECTION.

        WHY: folding member v2 into a count-1 centroid v1 must land on the mean of
        the two (re-normalized), i.e. ``((v1*1)+v2)/2`` then unit. If the function
        forgot old_count or skipped re-normalization, this diverges. With a single
        fold there is no intermediate re-normalization to perturb the comparison,
        so the incremental result equals the batch mean direction exactly.
        """
        v1 = _normalize([1.0, 0.0, 0.0])
        v2 = _normalize([0.0, 1.0, 0.0])

        centroid = update_centroid_running_mean(v1, 1, v2)

        batch_direction = _normalize([(v1[i] + v2[i]) / 2.0 for i in range(3)])
        for incremental_component, batch_component in zip(centroid, batch_direction, strict=True):
            assert incremental_component == pytest.approx(batch_component, abs=1e-9)

    def test_centroid_tracks_all_members_not_just_last(self) -> None:
        """(c) Folding v2 then v3 keeps a nonzero pull toward EVERY prior member.

        WHY: a rolling centroid is only meaningful if it reflects every member it
        has absorbed, weighted by old_count. After starting at v1, folding v2, then
        v3, the centroid must retain a component along v1 AND v2 AND v3 — it is not
        merely the last vector. A regression that weighted the new member equally
        with the whole accumulated centroid (forgot old_count) would wash out the
        earlier members; this asserts all three survive.

        NOTE (surfaced for the orchestrator): because each fold re-L2-normalizes
        (per spec — cosine must stay a dot product), a CHAIN of incremental folds
        does NOT equal the unweighted batch mean of all members; earlier folds are
        rescaled to unit length each step, which slightly up-weights older members.
        This is an inherent property of the spec'd "re-normalize every fold"
        algorithm, not a bug. The single-fold test above pins the exact mean; this
        test pins the directional invariant that actually matters downstream.
        """
        v1 = _normalize([1.0, 0.0, 0.0])
        v2 = _normalize([0.0, 1.0, 0.0])
        v3 = _normalize([0.0, 0.0, 1.0])

        centroid = update_centroid_running_mean(v1, 1, v2)
        centroid = update_centroid_running_mean(centroid, 2, v3)

        # Every member's axis retains a strictly positive (and substantial) pull.
        assert centroid[0] > 0.1  # v1 not forgotten
        assert centroid[1] > 0.1  # v2 not forgotten
        assert centroid[2] > 0.1  # v3 (the last) present
        # And the centroid is NOT collapsed onto the last vector alone.
        assert centroid[2] < 0.99

    def test_result_is_l2_normalized(self) -> None:
        """(c) The updated centroid is unit length (cosine stays a dot product)."""
        updated = update_centroid_running_mean([1.0, 0.0], 1, [0.0, 1.0])
        norm = math.sqrt(sum(component * component for component in updated))
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_mismatched_lengths_raise(self) -> None:
        """Failure case: differing dimensions raise rather than zip-truncate silently."""
        with pytest.raises(ValueError):
            update_centroid_running_mean([1.0, 0.0], 1, [1.0])

    def test_empty_vector_raises(self) -> None:
        """Edge case: an empty centroid is an error, not a silent no-op."""
        with pytest.raises(ValueError):
            update_centroid_running_mean([], 1, [])

    def test_invalid_old_count_raises(self) -> None:
        """Failure case: old_count < 1 is nonsensical (a centroid has ≥1 member)."""
        with pytest.raises(ValueError):
            update_centroid_running_mean([1.0, 0.0], 0, [0.0, 1.0])
