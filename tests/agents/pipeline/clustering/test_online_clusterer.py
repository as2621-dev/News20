"""Unit tests for the online assign-or-spawn orchestrator (Milestone M3b, Sub-phase 3).

``embed_texts`` is MOCKED (patched at the name imported into ``online_clusterer``) to
return deterministic unit vectors keyed by input text — NO real Gemini, NO network, NO
cost (CLAUDE.md §6). ``mint_cluster_id`` is an injected counter so spawned ids are
predictable and "no id minted on join" is assertable.

Each test encodes WHY the behaviour matters (Rule 9):

    (a) two byte-near-duplicate inputs collapse to ONE cluster with two members — proves
        the near-dup prefilter is wired (else we'd pay for + cluster a reprint twice).
    (b) two inputs with cos >= tau land in ONE cluster and the centroid moves toward
        their mean — proves join + running-mean fold (centroid tracks members, not the
        last vector).
    (c) an orthogonal input SPAWNS a second cluster — proves the tau boundary separates
        distinct events.
    (d) an input matching a passed-in existing cluster JOINS it across the day boundary
        with NO new id minted, advancing last_seen/member_count — the load-bearing
        cross-day continuity path.
    (e) cluster_outlet_count counts DISTINCT outlets — proves coverage diversity is real,
        not a raw member count.

    >>> pytest tests/agents/pipeline/clustering/test_online_clusterer.py -q
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agents.pipeline.clustering.engine_models import ClusterInput
from agents.pipeline.clustering.models import StoryCluster
from agents.pipeline.clustering.online_clusterer import cluster_candidates

_BASE_TIME = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)

# Distinct, non-near-duplicate story texts (>= 4 words each, share almost no shingles)
# so the near-dup prefilter keeps them separate and only the embedding decides clustering.
_TEXT_QUAKE = "a powerful earthquake struck the coastal city damaging hundreds of buildings overnight"
_TEXT_QUAKE_VARIANT = "a powerful earthquake rattled the coastal city wrecking hundreds of buildings overnight"
_TEXT_BUDGET = "the national legislature approved a sweeping new federal budget cutting corporate taxes sharply"
_TEXT_GALAXY = "an orbiting space telescope captured a breathtaking image of a remote spiral galaxy"


def _unit_vector_for(text: str) -> list[float]:
    """Deterministic 3-d unit vector keyed by text — controls cosine geometry exactly.

    The quake stories map to the SAME direction (cos = 1, so they join), the budget story
    to a tilted-but-similar direction (cos >= tau, so it joins the quake cluster), and the
    galaxy story to an orthogonal axis (cos = 0, so it spawns).
    """
    if text in (_TEXT_QUAKE, _TEXT_QUAKE_VARIANT):
        return [1.0, 0.0, 0.0]
    if text == _TEXT_BUDGET:
        # cos to [1,0,0] = 0.9 >= 0.75 → joins the quake cluster (used in test b).
        return _l2([0.9, math.sqrt(1.0 - 0.81), 0.0])
    if text == _TEXT_GALAXY:
        return [0.0, 1.0, 0.0]
    raise AssertionError(f"unexpected text in test embedding map: {text!r}")


def _l2(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]


def _patched_embed_texts() -> AsyncMock:
    """An AsyncMock for ``embed_texts`` that maps each passed text → its unit vector."""

    async def _fake_embed(texts: list[str], *, llm_client) -> list[list[float]]:  # noqa: ANN001
        return [_unit_vector_for(text) for text in texts]

    return AsyncMock(side_effect=_fake_embed)


def _counter_minter():
    """A ``mint_cluster_id`` counter returning clu-1, clu-2, … and recording call count."""
    state = {"count": 0}

    def _mint() -> str:
        state["count"] += 1
        return f"clu-{state['count']}"

    _mint.state = state  # type: ignore[attr-defined]
    return _mint


def _candidate(index: int, text: str, *, outlet: str, url: str | None = None, hours_offset: int = 0) -> ClusterInput:
    return ClusterInput(
        input_index=index,
        input_text=text,
        input_url=url or f"https://{outlet}/story-{index}",
        input_outlet=outlet,
        input_published_utc=_BASE_TIME + timedelta(hours=hours_offset),
        input_provisional_category="world",
    )


@pytest.mark.asyncio
async def test_byte_near_duplicate_inputs_collapse_to_one_cluster_with_two_members():
    """(a) Two identical-text reprints collapse to ONE cluster with two members.

    Near-dup prefilter wired: the reprint folds into the representative's cluster as a
    member, and embed_texts is called with only ONE text (we don't pay to embed a reprint).
    """
    candidates = [
        _candidate(0, _TEXT_QUAKE, outlet="reuters.com"),
        _candidate(1, _TEXT_QUAKE, outlet="ap.org"),
    ]
    mint = _counter_minter()
    embed = _patched_embed_texts()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=embed):
        run = await cluster_candidates(
            candidates, llm_client=None, existing_clusters=[], mint_cluster_id=mint
        )

    assert len(run.clusters) == 1
    assert run.clusters[0].cluster_member_count == 2
    assert len(run.members) == 2
    # Only the representative (index 0) keys the map; the dropped reprint does not.
    assert run.input_cluster_map == {0: "clu-1"}
    assert mint.state["count"] == 1  # only one id minted
    # Reason: the reprint must NOT be embedded — embed called with exactly one text.
    embed.assert_awaited_once()
    assert embed.await_args.args[0] == [_TEXT_QUAKE]


@pytest.mark.asyncio
async def test_two_similar_embeddings_join_one_cluster_and_centroid_moves_to_mean():
    """(b) Two distinct stories with cos >= tau land in ONE cluster; centroid -> mean.

    The centroid must move toward the mean of the two member vectors (running mean),
    proving it tracks members rather than staying pinned to the first/last one.
    """
    candidates = [
        _candidate(0, _TEXT_QUAKE, outlet="reuters.com"),  # [1,0,0]
        _candidate(1, _TEXT_BUDGET, outlet="bbc.com"),  # ~[0.9, 0.436, 0]
    ]
    mint = _counter_minter()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed_texts()):
        run = await cluster_candidates(
            candidates, llm_client=None, existing_clusters=[], mint_cluster_id=mint
        )

    assert len(run.clusters) == 1
    cluster = run.clusters[0]
    assert cluster.cluster_member_count == 2
    # Centroid is between the two member directions: its 2nd component is strictly
    # positive (it moved off the seed [1,0,0]) but less than the second member's.
    assert cluster.cluster_centroid[1] > 0.0
    assert cluster.cluster_centroid[1] < _unit_vector_for(_TEXT_BUDGET)[1]
    # Still L2-normalized (cosine stays a dot product).
    assert math.isclose(math.sqrt(sum(c * c for c in cluster.cluster_centroid)), 1.0, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_orthogonal_embedding_spawns_second_cluster():
    """(c) An orthogonal input spawns a SECOND cluster (tau boundary separates events)."""
    candidates = [
        _candidate(0, _TEXT_QUAKE, outlet="reuters.com"),  # [1,0,0]
        _candidate(1, _TEXT_GALAXY, outlet="bbc.com"),  # [0,1,0] — cos 0 < tau
    ]
    mint = _counter_minter()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed_texts()):
        run = await cluster_candidates(
            candidates, llm_client=None, existing_clusters=[], mint_cluster_id=mint
        )

    assert len(run.clusters) == 2
    assert mint.state["count"] == 2
    assert run.input_cluster_map == {0: "clu-1", 1: "clu-2"}


@pytest.mark.asyncio
async def test_input_joins_existing_cluster_across_day_boundary_no_new_id():
    """(d) A candidate matching a passed-in existing cluster JOINS it across days.

    No new id is minted; the existing cluster's last_seen advances and member_count bumps.
    This is the cross-day continuity path: yesterday's cluster keeps its id today.
    """
    yesterday = _BASE_TIME - timedelta(hours=20)  # in the 48h window
    existing = StoryCluster(
        cluster_id="clu-existing",
        cluster_centroid=[1.0, 0.0, 0.0],  # same direction as today's quake story
        cluster_category="world",
        cluster_member_count=1,
        cluster_outlet_count=1,
        cluster_first_seen_utc=yesterday,
        cluster_last_seen_utc=yesterday,
    )
    candidates = [_candidate(0, _TEXT_QUAKE, outlet="reuters.com")]  # today, cos 1.0
    mint = _counter_minter()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed_texts()):
        run = await cluster_candidates(
            candidates, llm_client=None, existing_clusters=[existing], mint_cluster_id=mint
        )

    assert mint.state["count"] == 0  # NO new id minted — joined the existing cluster
    assert run.input_cluster_map == {0: "clu-existing"}
    assert len(run.clusters) == 1
    joined = run.clusters[0]
    assert joined.cluster_id == "clu-existing"
    assert joined.cluster_member_count == 2
    assert joined.cluster_last_seen_utc == _BASE_TIME  # advanced to today's publish time


@pytest.mark.asyncio
async def test_cluster_outlet_count_counts_distinct_outlets():
    """(e) cluster_outlet_count is the number of DISTINCT outlets, not raw member count.

    Three reprints from two outlets (one repeated) → member_count 3 but outlet_count 2.
    """
    candidates = [
        _candidate(0, _TEXT_QUAKE, outlet="reuters.com", url="https://reuters.com/a"),
        _candidate(1, _TEXT_QUAKE, outlet="ap.org", url="https://ap.org/b"),
        _candidate(2, _TEXT_QUAKE, outlet="reuters.com", url="https://reuters.com/c"),
    ]
    mint = _counter_minter()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed_texts()):
        run = await cluster_candidates(
            candidates, llm_client=None, existing_clusters=[], mint_cluster_id=mint
        )

    assert len(run.clusters) == 1
    cluster = run.clusters[0]
    assert cluster.cluster_member_count == 3
    assert cluster.cluster_outlet_count == 2  # reuters.com counted once


@pytest.mark.asyncio
async def test_empty_candidates_returns_empty_run_without_embedding():
    """Edge case: an empty batch returns an empty run and never calls embed_texts."""
    embed = _patched_embed_texts()
    mint = _counter_minter()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=embed):
        run = await cluster_candidates(
            [], llm_client=None, existing_clusters=[], mint_cluster_id=mint
        )

    assert run.clusters == []
    assert run.members == []
    assert run.input_cluster_map == {}
    embed.assert_not_awaited()
    assert mint.state["count"] == 0
