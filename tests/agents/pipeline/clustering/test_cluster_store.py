"""Unit tests for the cluster-store repository (Milestone M3a, Sub-phase 4).

The supabase client is MOCKED at the ``.table(...).<builder>...execute()`` boundary —
no real DB, no connection, no cost (CLAUDE.md §6). The chainable-query stub mirrors the
``_FakeQuery`` style in ``tests/agents/pipeline/test_daily_batch.py`` so the mocking
convention stays consistent across the pipeline suite. Each test encodes WHY the
behaviour matters (Rule 9):

    (a) load_active_clusters parses BOTH centroid wire shapes (text + list) and applies
        the cross-day window filter (.gte) + optional category filter (.eq);
    (b) upsert writes the centroid as the SERIALIZED string (pgvector text form), not a
        Python list;
    (c) serialize → deserialize round-trips a 768-float vector exactly;
    (d) add_cluster_members is a no-op on [] (no .execute) and one batched upsert for 2.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.pipeline.clustering import cluster_store
from agents.pipeline.clustering.models import ClusterMember, StoryCluster


class _RecordingQuery:
    """Chainable Supabase query stub: builder methods record their calls and return
    self; ``execute`` returns the seeded rows. Mirrors test_daily_batch._FakeQuery."""

    def __init__(self, data: list[dict], calls: list[tuple]) -> None:
        self._data = data
        self._calls = calls

    def select(self, *args, **_kwargs):
        self._calls.append(("select", args))
        return self

    def gte(self, *args, **_kwargs):
        self._calls.append(("gte", args))
        return self

    def eq(self, *args, **_kwargs):
        self._calls.append(("eq", args))
        return self

    def upsert(self, payload, *args, **_kwargs):
        self._calls.append(("upsert", payload))
        return self

    def execute(self):
        self._calls.append(("execute", None))
        return SimpleNamespace(data=self._data)


class _RecordingClient:
    """Supabase client stub: routes ``.table(name)`` to a per-table _RecordingQuery and
    records the chained calls so tests can assert the filters / payloads applied."""

    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self._rows_by_table = rows_by_table
        self.calls_by_table: dict[str, list[tuple]] = {}

    def table(self, name: str):
        calls = self.calls_by_table.setdefault(name, [])
        return _RecordingQuery(self._rows_by_table.get(name, []), calls)


def _centroid_768(seed: int) -> list[float]:
    """Build a deterministic 768-float vector (not normalized — fidelity is the point)."""
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(768)]


def test_load_active_clusters_parses_both_centroid_shapes_and_applies_window_filter() -> None:
    """(a) The cross-day load must (1) parse a centroid whether Supabase returns the
    pgvector text literal OR a decoded JSON array, and (2) actually scope the read to the
    time window (.gte) and category (.eq) — otherwise it would re-match against the whole
    table, breaking cross-day continuity and category isolation."""
    since_utc = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    first_seen = datetime(2026, 6, 16, tzinfo=timezone.utc)
    last_seen = datetime(2026, 6, 18, tzinfo=timezone.utc)

    centroid_as_text = cluster_store.serialize_centroid(_centroid_768(1))
    centroid_as_list = _centroid_768(2)
    rows = [
        {
            "cluster_id": "clu-text",
            "cluster_centroid": centroid_as_text,  # pgvector text literal
            "cluster_category": "tech",
            "cluster_first_seen_utc": first_seen.isoformat(),
            "cluster_last_seen_utc": last_seen.isoformat(),
        },
        {
            "cluster_id": "clu-list",
            "cluster_centroid": centroid_as_list,  # already-decoded JSON array
            "cluster_category": "tech",
            "cluster_first_seen_utc": first_seen.isoformat(),
            "cluster_last_seen_utc": last_seen.isoformat(),
        },
    ]
    client = _RecordingClient({"story_clusters": rows})

    clusters = cluster_store.load_active_clusters(client, since_utc=since_utc, category="tech")

    assert len(clusters) == 2
    assert all(len(c.cluster_centroid) == 768 for c in clusters)

    calls = client.calls_by_table["story_clusters"]
    gte_calls = [args for name, args in calls if name == "gte"]
    eq_calls = [args for name, args in calls if name == "eq"]
    # The window filter is applied with the since_utc ISO string on cluster_last_seen_utc.
    assert ("cluster_last_seen_utc", since_utc.isoformat()) in gte_calls
    # The category filter is applied when category is given.
    assert ("cluster_category", "tech") in eq_calls


def test_load_active_clusters_omits_category_filter_when_none() -> None:
    """(a, cont.) With no category, NO .eq filter is applied — the load spans all
    categories (the engine sometimes re-matches the whole active window)."""
    since_utc = datetime(2026, 6, 17, tzinfo=timezone.utc)
    client = _RecordingClient({"story_clusters": []})

    result = cluster_store.load_active_clusters(client, since_utc=since_utc)

    assert result == []
    calls = client.calls_by_table["story_clusters"]
    assert not [args for name, args in calls if name == "eq"]
    assert ("cluster_last_seen_utc", since_utc.isoformat()) in [args for name, args in calls if name == "gte"]


def test_upsert_cluster_writes_serialized_string_centroid() -> None:
    """(b) The centroid MUST reach Supabase as the pgvector text literal (a string
    starting with "["), not a raw Python list — pgvector's vector(768) wire form is the
    bracketed string; sending a list would be the wrong type at the DB boundary."""
    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    cluster = StoryCluster(
        cluster_id="clu-1",
        cluster_centroid=_centroid_768(3),
        cluster_category="tech",
        cluster_first_seen_utc=now,
        cluster_last_seen_utc=now,
    )
    client = _RecordingClient({})

    cluster_store.upsert_cluster(client, cluster)

    calls = client.calls_by_table["story_clusters"]
    upsert_payloads = [payload for name, payload in calls if name == "upsert"]
    assert len(upsert_payloads) == 1
    payload = upsert_payloads[0]
    assert isinstance(payload["cluster_centroid"], str)
    assert payload["cluster_centroid"].startswith("[")
    # Timestamps are serialized to ISO strings for the JSON boundary.
    assert payload["cluster_first_seen_utc"] == now.isoformat()
    assert ("execute", None) in calls


def test_centroid_serialize_deserialize_round_trips_768_floats_exactly() -> None:
    """(c) serialize→deserialize must round-trip a full 768-float vector with no
    fidelity loss — a lossy round-trip would corrupt every loaded centroid and silently
    degrade cluster matching."""
    vec = _centroid_768(7)
    assert len(vec) == 768

    restored = cluster_store.deserialize_centroid(cluster_store.serialize_centroid(vec))

    assert len(restored) == 768
    assert restored == pytest.approx(vec, rel=0, abs=0)


def test_add_cluster_members_empty_is_noop() -> None:
    """(d) An empty member list must NOT touch the DB at all (no .upsert / .execute) —
    a wasted round-trip on the common no-new-members path."""
    client = _RecordingClient({})

    cluster_store.add_cluster_members(client, "clu-1", [])

    # The table was never even addressed → no recorded calls for it.
    assert "story_cluster_members" not in client.calls_by_table


def test_add_cluster_members_batches_two_members_in_one_upsert() -> None:
    """(d, cont.) Two members must be written in ONE batched .upsert (2 rows), not two
    round-trips, and every row must be FK'd to the passed cluster_id."""
    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    members = [
        ClusterMember(cluster_id="ignored", member_url="https://bbc.com/a", member_outlet="bbc.com", member_seen_utc=now),
        ClusterMember(cluster_id="ignored", member_url="https://cnn.com/b", member_outlet="cnn.com", member_seen_utc=now),
    ]
    client = _RecordingClient({})

    cluster_store.add_cluster_members(client, "clu-1", members)

    calls = client.calls_by_table["story_cluster_members"]
    upsert_payloads = [payload for name, payload in calls if name == "upsert"]
    assert len(upsert_payloads) == 1  # ONE batched call
    rows = upsert_payloads[0]
    assert len(rows) == 2
    assert {row["cluster_id"] for row in rows} == {"clu-1"}  # overrides the member's own id
    assert {row["member_url"] for row in rows} == {"https://bbc.com/a", "https://cnn.com/b"}
    assert all(row["member_seen_utc"] == now.isoformat() for row in rows)
