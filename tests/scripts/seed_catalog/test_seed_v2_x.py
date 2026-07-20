"""Offline tests for the v2 X-handle seeder (issue #27 — trusted, no verification).

Rule 9 — every test encodes WHY the behavior matters:
  - A NEW handle yields exactly one trusted content_sources row; an already-present
    handle is reused, never re-created. WHY: a duplicate x_account row corrupts the
    catalog and breaks the (content_source_type, external_id) natural key.
  - A structurally-invalid handle (empty / >15 chars / bad chars) is DROPPED +
    logged, never guessed. WHY: it cannot be a real X account (X's own grammar), so
    seeding it = garbage in prod; AC#2 (dropped + logged, never guessed).
  - A handle duplicated within a cluster becomes ONE member. WHY: a duplicate
    (cluster_id, source_id) member row is exactly what the natural key forbids.
  - Every cluster (including ones #15 deferred as all-new) is planned. WHY: this
    slice is the half that seeds the previously-deferred clusters.
  - The chain is IDEMPOTENT — a second run inserts zero sources and zero members.
    WHY: the seeder is a re-runnable curation tool; AC#3 (no duplicated members).

All I/O is faked at its boundary (an in-memory FakeConn for the upsert chain) — NO
network, NO prod. The live double-run against prod is the #27 acceptance step.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from scripts.seed_catalog import seed_v2, seed_v2_x


def _catalog() -> seed_v2.V2Catalog:
    """A small two-root catalog: mixed verified+new, an all-new cluster, a malformed handle."""
    root_ai = seed_v2.V2Root(
        root_label="AI",
        root_slug="ai",
        subniches=["Frontier labs"],
        youtube_channels=[],
        x_clusters=[
            seed_v2.V2Cluster(
                cluster_name="Frontier-lab researchers",
                subniche="Frontier labs",
                description="Researchers posting results",
                # karpathy = already in prod (verified); brand_new_ai = NEW; dup karpathy.
                handles=["@karpathy", "@brand_new_ai", "@karpathy"],
            ),
            seed_v2.V2Cluster(
                cluster_name="All new voices",
                subniche="Fringe",
                description="Nobody verified — #15 deferred this whole cluster",
                # both NEW + one structurally-invalid (17 chars > X's 15 limit).
                handles=["@fresh_one", "@dhruva_jaishankar"],
            ),
        ],
    )
    root_tech = seed_v2.V2Root(
        root_label="Tech",
        root_slug="tech",
        subniches=["Gadgets"],
        youtube_channels=[],
        x_clusters=[
            seed_v2.V2Cluster(
                cluster_name="Cross-tagged voices",
                subniche="Gadgets",
                # brand_new_ai appears again under tech → one row, topic_tags {ai,tech}.
                handles=["@brand_new_ai", "@tech_only"],
            ),
        ],
    )
    return seed_v2.V2Catalog(source_artifact="test", roots=[root_ai, root_tech])


# ── plan tests ────────────────────────────────────────────────────────────────
def test_new_handles_become_trusted_source_rows_existing_reused() -> None:
    """WHY: only genuinely NEW handles get a row; a prod-present handle is reused."""
    plan = seed_v2_x.plan_x_seed(_catalog(), existing_handles={"karpathy"})
    by_ext = {r["external_id"]: r for r in plan.source_rows}
    # karpathy already in prod → NOT re-created.
    assert "karpathy" not in by_ext
    assert plan.existing_handle_count == 1
    # the NEW handles are all seeded, trusted, no avatar.
    assert set(by_ext) == {"brand_new_ai", "fresh_one", "tech_only"}
    row = by_ext["brand_new_ai"]
    assert row["content_source_type"] == "x_account"
    assert row["source_name"] == "@brand_new_ai"
    assert row["thumbnail_url"] is None
    assert row["subscriber_count"] is None
    assert row["is_curated"] is True
    assert plan.new_handle_count == 3


def test_cross_tagged_handle_is_one_row_with_unioned_topic_tags() -> None:
    """WHY: a handle under two roots → ONE content_sources row spanning both roots."""
    plan = seed_v2_x.plan_x_seed(_catalog(), existing_handles=set())
    rows = [r for r in plan.source_rows if r["external_id"] == "brand_new_ai"]
    assert len(rows) == 1  # appears under ai + tech → single row
    assert rows[0]["topic_tags"] == ["ai", "tech"]  # unioned + sorted


def test_malformed_handle_is_dropped_and_logged_never_seeded() -> None:
    """WHY: a >15-char handle can't be a real X account — AC#2 dropped + logged, never guessed."""
    plan = seed_v2_x.plan_x_seed(_catalog(), existing_handles=set())
    dropped = {d.handle for d in plan.drops}
    assert "@dhruva_jaishankar" in dropped  # 17 chars → structurally invalid
    # it never becomes a source row nor a cluster member.
    assert "dhruva_jaishankar" not in {r["external_id"] for r in plan.source_rows}
    all_new_cluster = next(
        c for c in plan.cluster_rows if c.cluster_slug == "ai-all-new-voices"
    )
    assert all_new_cluster.members == ["fresh_one"]  # the invalid one excluded


def test_duplicate_within_cluster_becomes_one_member() -> None:
    """WHY: a duplicate (cluster_id, source_id) member is exactly the natural key violation."""
    plan = seed_v2_x.plan_x_seed(_catalog(), existing_handles={"karpathy"})
    researchers = next(
        c for c in plan.cluster_rows if c.cluster_slug == "ai-frontier-lab-researchers"
    )
    assert researchers.members == ["karpathy", "brand_new_ai"]  # dup karpathy collapsed


def test_all_clusters_planned_including_previously_deferred() -> None:
    """WHY: this slice seeds the all-new clusters #15 deferred, not just mixed ones."""
    plan = seed_v2_x.plan_x_seed(_catalog(), existing_handles={"karpathy"})
    slugs = {c.cluster_slug for c in plan.cluster_rows}
    assert "ai-all-new-voices" in slugs  # was deferred by #15, seeded here


def test_report_lists_per_cluster_totals_and_drops() -> None:
    """WHY: the report is the #15-extension the slice mandates — X totals + drop reasons."""
    catalog = _catalog()
    plan = seed_v2_x.plan_x_seed(catalog, existing_handles={"karpathy"})
    report = seed_v2_x.build_x_report(catalog, plan)
    assert "per-cluster X totals" in report
    assert "ai-frontier-lab-researchers" in report
    assert "@dhruva_jaishankar" in report  # the drop surfaced with a reason
    assert "structural drops" in report


def test_x_handle_grammar_rejects_over_15_and_bad_chars() -> None:
    """WHY: the grammar is the ONLY drop gate — must match X's real handle rules."""
    ok = seed_v2_x.X_HANDLE_RE
    assert ok.match("karpathy")
    assert ok.match("a_b_1")
    assert not ok.match("")  # empty
    assert not ok.match("dhruva_jaishankar")  # 17 chars
    assert not ok.match("has space")  # invalid char
    assert not ok.match("has-dash")  # hyphen not allowed


# ── in-memory upsert-chain integration (fake DB only) ─────────────────────────
class FakeConn:
    """In-memory asyncpg stand-in enforcing the real conflict-key semantics.

    Implements exactly the surface the x-seed chain calls: ``fetch`` (existing
    x_account handles), ``executemany`` (the ``_flush`` content_sources upsert),
    and ``fetchval`` / ``execute`` for ``seed_clusters``. A second run must add
    zero rows — the property AC#3 requires.
    """

    def __init__(self, existing: dict[str, str]) -> None:
        # lower-bare handle → source_id (x_account rows already in "prod").
        self._sources = dict(existing)
        self.clusters: dict[str, str] = {}  # cluster_slug → cluster_id
        self.members: set[tuple[str, str]] = set()  # (cluster_id, source_id)
        self._next_id = 0

    def transaction(self) -> "FakeConn":
        return self

    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "from content_sources where content_source_type='x_account'" in query:
            return [{"external_id": h} for h in self._sources]
        return []

    async def executemany(self, query: str, args_iter: Any) -> None:
        # Parse the content_sources upsert column list to find external_id's slot.
        if not query.startswith("insert into content_sources"):
            return
        cols = re.search(r"\(([^)]*)\)", query).group(1).replace(" ", "").split(",")
        ext_idx = cols.index("external_id")
        for row in args_iter:
            ext = row[ext_idx]
            low = ext.lower()
            # on conflict do update → no NEW row for an already-present handle.
            self._sources.setdefault(low, f"SRC_{ext}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        if query.startswith("insert into source_clusters"):
            slug = args[0]
            if slug not in self.clusters:
                self._next_id += 1
                self.clusters[slug] = f"CID_{self._next_id}"
            return self.clusters[slug]
        if query.startswith("select source_id from content_sources"):
            return self._sources.get(args[0].lower())
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if query.startswith("insert into source_cluster_members"):
            cluster_id, source_id, _order = args
            key = (cluster_id, source_id)
            if key in self.members:
                return "INSERT 0 0"
            self.members.add(key)
            return "INSERT 0 1"
        return "INSERT 0 0"


def test_seed_chain_is_idempotent_on_fake_db() -> None:
    """WHY: proves sources + clusters + members all no-op on the second run (AC#3)."""
    catalog = _catalog()
    conn = FakeConn(existing={"karpathy": "SRC_karpathy"})

    async def run_once() -> tuple[int, int, int]:
        existing = await seed_v2.fetch_verified_handles(conn)
        plan = seed_v2_x.plan_x_seed(catalog, existing)
        submitted = await seed_v2_x.seed_x_sources(conn, plan.source_rows)
        clusters, members = await seed_v2.seed_clusters(conn, plan.cluster_rows)
        return submitted, members, len(plan.source_rows)

    first = asyncio.run(run_once())
    members_after_first = len(conn.members)
    second = asyncio.run(run_once())

    # First run seeds the 3 NEW handles + wires every member.
    assert first[2] == 3  # 3 NEW source rows planned
    assert first[1] > 0  # members inserted
    # Second run: every handle now present → plan yields 0 new sources, 0 new members.
    assert second[2] == 0  # plan_x_seed excludes now-present handles
    assert second[0] == 0  # nothing submitted
    assert second[1] == 0  # no duplicate members
    assert len(conn.members) == members_after_first  # membership stable — no dup rows
