"""Unit tests for the cross-day id bridge + persistence wiring (M3b, Sub-phase 4).

The resolver and the supabase client are MOCKED (CLAUDE.md §6) — no real DB, no real
Gemini, no cost. The supabase client stub mirrors the ``_RecordingClient`` /
``_RecordingQuery`` style in ``test_cluster_store.py`` so the mocking convention stays
consistent across the clustering suite. Each test encodes WHY the behaviour matters
(Rule 9):

    (a) a cluster whose member URL the resolver maps to story S REUSES S (cross-day
        continuity — a multi-day event keeps its id) and does NOT mint;
    (b) a cluster with only unseen URLs MINTS a fresh id;
    (c) ``persist_run`` upserts once per cluster and adds that cluster's members
        (assert call args, mocked client — no live DB);
    (d) the URL normalization used here is the SAME ``normalize_url`` the alias
        write-path keys ``story_url_aliases`` on — if it diverged, every cross-day
        lookup would silently miss. This is the load-bearing silent-failure guard.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.ingestion.dedup import normalize_url
from agents.ingestion.models import CanonicalStory
from agents.pipeline.clustering.continuity import persist_run, resolve_cluster_story_ids
from agents.pipeline.clustering.engine_models import ClusterRun
from agents.pipeline.clustering.models import ClusterMember, StoryCluster
from agents.pipeline.persist_helpers import build_story_url_alias_rows

_NOW = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


def _cluster(cluster_id: str) -> StoryCluster:
    """Minimal valid cluster for id-resolution / persistence tests."""
    return StoryCluster(
        cluster_id=cluster_id,
        cluster_centroid=[1.0, 0.0],
        cluster_category="tech",
        cluster_first_seen_utc=_NOW,
        cluster_last_seen_utc=_NOW,
    )


def _member(cluster_id: str, member_url: str, member_outlet: str | None = None) -> ClusterMember:
    return ClusterMember(
        cluster_id=cluster_id,
        member_url=member_url,
        member_outlet=member_outlet,
        member_seen_utc=_NOW,
    )


class _MintCounter:
    """Records every mint call so a test can assert it was / was NOT invoked."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.call_count = 0

    def __call__(self) -> str:
        self.call_count += 1
        return f"{self.prefix}-{self.call_count}"


class _RecordingQuery:
    """Chainable Supabase query stub mirroring test_cluster_store._RecordingQuery."""

    def __init__(self, calls: list[tuple]) -> None:
        self._calls = calls

    def upsert(self, payload, *args, **_kwargs):
        self._calls.append(("upsert", payload))
        return self

    def execute(self):
        self._calls.append(("execute", None))
        return type("_Resp", (), {"data": []})()


class _RecordingClient:
    """Supabase client stub routing ``.table(name)`` to a per-table recording query."""

    def __init__(self) -> None:
        self.calls_by_table: dict[str, list[tuple]] = {}

    def table(self, name: str):
        return _RecordingQuery(self.calls_by_table.setdefault(name, []))


def test_cluster_with_aliased_member_reuses_existing_story_id_and_does_not_mint() -> None:
    """(a) A multi-day event re-clustered today (fresh cluster_id) must KEEP its original
    story id: if any member URL already aliases to story S, reuse S — otherwise
    produce-once / don't-repeat would break and the event would re-produce as a new
    story every day. mint_story_id must NOT be called for it."""
    run = ClusterRun(
        clusters=[_cluster("clu-1")],
        members=[_member("clu-1", "https://reuters.com/markets/fed-holds")],
    )
    normalized = normalize_url("https://reuters.com/markets/fed-holds")
    mint = _MintCounter("story")

    result = resolve_cluster_story_ids(
        run,
        resolve_existing_story_ids=lambda urls: {normalized: "story-existing"},
        mint_story_id=mint,
    )

    assert result == {"clu-1": "story-existing"}
    assert mint.call_count == 0  # the load-bearing reuse: no fresh id minted


def test_cluster_with_only_unseen_urls_mints_fresh_id() -> None:
    """(b) A genuinely new cluster (no member aliases to a prior story) must mint a fresh
    id — continuity only reuses, it never invents a match."""
    run = ClusterRun(
        clusters=[_cluster("clu-1")],
        members=[_member("clu-1", "https://newsite.com/brand-new-event")],
    )
    mint = _MintCounter("story")

    result = resolve_cluster_story_ids(
        run,
        resolve_existing_story_ids=lambda urls: {},  # nothing aliased
        mint_story_id=mint,
    )

    assert result == {"clu-1": "story-1"}
    assert mint.call_count == 1


def test_multi_story_id_cluster_picks_smallest_deterministically() -> None:
    """A cluster whose members span MORE THAN ONE existing story id must resolve to ONE
    deterministically (the smallest id) — a non-deterministic pick would make the batch
    irreproducible. The tie-break encodes 'collapse to a single stable id'."""
    run = ClusterRun(
        clusters=[_cluster("clu-1")],
        members=[
            _member("clu-1", "https://a.com/one"),
            _member("clu-1", "https://b.com/two"),
        ],
    )
    url_a = normalize_url("https://a.com/one")
    url_b = normalize_url("https://b.com/two")
    mint = _MintCounter("story")

    result = resolve_cluster_story_ids(
        run,
        resolve_existing_story_ids=lambda urls: {url_a: "story-zeta", url_b: "story-alpha"},
        mint_story_id=mint,
    )

    assert result == {"clu-1": "story-alpha"}  # min() of {"story-zeta", "story-alpha"}
    assert mint.call_count == 0


def test_resolver_called_once_over_union_of_normalized_urls() -> None:
    """The resolver does its own chunked .in_() — it must be called EXACTLY ONCE over the
    union of all clusters' normalized URLs (not once per cluster), and the URLs passed in
    must already be normalized."""
    run = ClusterRun(
        clusters=[_cluster("clu-1"), _cluster("clu-2")],
        members=[
            _member("clu-1", "http://www.x.com/a/"),  # normalizes to https + no www + no slash
            _member("clu-2", "https://y.com/b"),
        ],
    )
    calls: list[list[str]] = []

    def _resolver(urls: list[str]) -> dict[str, str]:
        calls.append(urls)
        return {}

    resolve_cluster_story_ids(
        run, resolve_existing_story_ids=_resolver, mint_story_id=_MintCounter("story")
    )

    assert len(calls) == 1  # one union call, not per-cluster
    assert calls[0] == sorted({normalize_url("http://www.x.com/a/"), normalize_url("https://y.com/b")})
    assert "https://x.com/a" in calls[0]  # the normalized form, not the raw URL


def test_persist_run_upserts_each_cluster_and_adds_its_members() -> None:
    """(c) persist_run must upsert ONCE per cluster and add EACH cluster's members to
    story_cluster_members — if a cluster's members went to the wrong cluster (or were
    dropped) the persisted graph would be wrong. Mocked client: no live DB."""
    run = ClusterRun(
        clusters=[_cluster("clu-1"), _cluster("clu-2")],
        members=[
            _member("clu-1", "https://a.com/1", "a.com"),
            _member("clu-1", "https://b.com/1", "b.com"),
            _member("clu-2", "https://c.com/2", "c.com"),
        ],
    )
    client = _RecordingClient()

    persist_run(client, run)

    cluster_upserts = [p for name, p in client.calls_by_table["story_clusters"] if name == "upsert"]
    assert len(cluster_upserts) == 2  # one upsert per cluster
    assert {p["cluster_id"] for p in cluster_upserts} == {"clu-1", "clu-2"}

    member_upserts = [p for name, p in client.calls_by_table["story_cluster_members"] if name == "upsert"]
    assert len(member_upserts) == 2  # one batched add per cluster (clu-1, clu-2)
    rows_by_cluster = {batch[0]["cluster_id"]: batch for batch in member_upserts}
    assert {row["member_url"] for row in rows_by_cluster["clu-1"]} == {"https://a.com/1", "https://b.com/1"}
    assert {row["member_url"] for row in rows_by_cluster["clu-2"]} == {"https://c.com/2"}


def test_persist_run_cluster_without_members_still_upserts() -> None:
    """A cluster carrying no member rows this run still gets its row upserted (defensive)
    and add_cluster_members is a no-op (no member table call for it)."""
    run = ClusterRun(clusters=[_cluster("clu-1")], members=[])
    client = _RecordingClient()

    persist_run(client, run)

    assert len([p for name, p in client.calls_by_table["story_clusters"] if name == "upsert"]) == 1
    # add_cluster_members([]) is a no-op → the members table is never addressed.
    assert "story_cluster_members" not in client.calls_by_table


def test_normalization_matches_alias_write_path_key_form() -> None:
    """(d) THE silent-failure guard. continuity normalizes member URLs with the EXACT
    function the alias WRITE path (build_story_url_alias_rows) keys story_url_aliases on.
    If continuity normalized URLs any other way, every cross-day lookup would miss and
    each day would mint a fresh id. We assert the resolver is queried with the SAME key
    string that the write path would have stored for the same raw URL."""
    raw_url = "http://www.example.com/article/?utm_source=newsletter&id=42"

    # What the WRITE path stores as alias_normalized_url for this URL.
    story = CanonicalStory(
        canonical_story_id="cand-x",
        canonical_title="t",
        canonical_url=raw_url,
        canonical_normalized_url=normalize_url(raw_url),
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="example.com",
        member_candidate_ids=[raw_url],
    )
    alias_rows = build_story_url_alias_rows("story-7", story)
    write_keys = {row["alias_normalized_url"] for row in alias_rows}

    # What continuity queries the resolver with for a member carrying the same raw URL.
    run = ClusterRun(clusters=[_cluster("clu-1")], members=[_member("clu-1", raw_url)])
    queried: list[list[str]] = []

    def _resolver(urls: list[str]) -> dict[str, str]:
        queried.append(urls)
        # Simulate the alias row existing: the write key resolves to story-7.
        return {key: "story-7" for key in urls if key in write_keys}

    result = resolve_cluster_story_ids(
        run, resolve_existing_story_ids=_resolver, mint_story_id=_MintCounter("story")
    )

    # The queried key is exactly one the write path stored → continuity FINDS the alias.
    assert set(queried[0]) & write_keys == set(queried[0])
    assert result == {"clu-1": "story-7"}  # parity holds → cross-day id reused
