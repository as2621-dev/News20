"""Structural (no-DB) gate for the editorial cluster seed (Phase FSR-M1 SP3).

These tests parse ``supabase/seed/source_clusters.sql`` as TEXT — there is no
local DB in this repo (no supabase/config.toml), so the seed's apply-time effects
are the LIVE-E2E residual. What we CAN and MUST verify offline is the seed's
*shape*, because each property below is load-bearing for the milestone:

  * a cluster's category outside the 8 topic roots can never be surfaced by the
    resolver (it filters by ``cluster_category ∈ TOPIC_CATEGORIES``) — so an
    off-root category is a silently-dead cluster;
  * a non-idempotent insert duplicates rows on every deploy (the seed re-runs each
    deploy) — the trust contract is "no randoms / no duplicates";
  * a silently-missing category is the #1 risk (catalog quality) hiding — a gap
    must be LOUD (a real cluster OR an explicit ``-- THIN CATALOG:`` admission),
    never absent-and-unremarked;
  * a hardcoded uuid in a member ref breaks across environments — members must
    resolve real catalog rows by their natural keys.

Each test name + docstring encodes the WHY (Rule 9): it fails when the business
rule is violated, not merely when the text changes shape.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents.pipeline.categories import TOPIC_CATEGORIES

# Resolve the repo root from this file: tests/seed/<this> → repo root is 3 parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = REPO_ROOT / "supabase" / "seed" / "source_clusters.sql"

_RAW_SQL = SEED_PATH.read_text()

# Strip `--` line comments before any structural parse: the header carries an
# ILLUSTRATIVE commented INSERT skeleton (the "helper shape") that is NOT a real
# statement, and the apply-time NOTICE block lists the 8 roots in prose. Parsing
# executable SQL only keeps every assertion below measuring real statements.
SEED_SQL = "\n".join(
    line.split("--", 1)[0] for line in _RAW_SQL.splitlines()
)

# One source_clusters VALUES row: ('<slug>', '<label>', '<category>', <sort_order>).
# Captures the slug (1) and the category (3) literal of every cluster row. Anchored
# to the 4-tuple shape the seed authors, so a label containing a comma (none here)
# or extra whitespace does not derail the category capture.
_CLUSTER_ROW = re.compile(
    r"\(\s*'([^']+)'\s*,\s*'(?:[^']|'')*'\s*,\s*'([^']+)'\s*,\s*\d+\s*\)"
)

# A bare uuid literal (8-4-4-4-12 hex) — must NEVER appear in a member ref.
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _cluster_rows() -> list[tuple[str, str]]:
    """Return (cluster_slug, cluster_category) for every cluster INSERT row.

    Scoped to the body of the ``insert into source_clusters ... values ...`` block
    so the apply-time ``raise notice`` array of root names (which also lists the 8
    roots) is NOT mistaken for cluster rows.
    """
    start = SEED_SQL.find("insert into source_clusters")
    assert start != -1, "seed must contain an `insert into source_clusters` block"
    end = SEED_SQL.find("on conflict", start)
    assert end != -1, "the source_clusters insert must carry an `on conflict` clause"
    block = SEED_SQL[start:end]
    return [(m.group(1), m.group(2)) for m in _CLUSTER_ROW.finditer(block)]


def test_every_cluster_category_is_one_of_the_eight_roots() -> None:
    """A cluster whose category is off-root can never be surfaced by the resolver.

    The resolver filters clusters by ``cluster_category == category`` over the 8
    ``TOPIC_CATEGORIES``; a value outside that set is a dead cluster the onboarding
    UI will never render. Assert set-membership against the imported roots so the
    test fails the instant a typo'd or invented category leaks into the seed.
    """
    rows = _cluster_rows()
    assert rows, "expected at least one cluster row in the seed"
    roots = set(TOPIC_CATEGORIES)
    offenders = sorted({cat for _slug, cat in rows if cat not in roots})
    assert not offenders, (
        f"cluster categories outside the 8 topic roots: {offenders}; "
        f"allowed = {sorted(roots)}"
    )


def test_seed_is_idempotent() -> None:
    """Re-seeding must converge, not duplicate — the seed re-runs on every deploy.

    The cluster insert must upsert (`on conflict (cluster_slug) do update`) and
    every member insert must be `on conflict do nothing` (the migration's
    partial-unique guards make a re-insert a no-op). A missing clause means a
    duplicate row accrues each deploy, breaking the no-duplicates trust contract.
    """
    cluster_inserts = re.findall(r"insert\s+into\s+source_clusters\b", SEED_SQL)
    assert cluster_inserts, "expected a source_clusters insert"
    # Each source_clusters insert is followed by an `on conflict` clause.
    assert SEED_SQL.count("on conflict (cluster_slug) do update") >= len(
        cluster_inserts
    ), "every source_clusters insert must `on conflict (cluster_slug) do update`"

    member_inserts = re.findall(
        r"insert\s+into\s+source_cluster_members\b", SEED_SQL
    )
    assert member_inserts, "expected at least one source_cluster_members insert"
    member_on_conflict = re.findall(r"on conflict do nothing", SEED_SQL)
    assert len(member_on_conflict) >= len(member_inserts), (
        f"every member insert must carry `on conflict do nothing`: "
        f"{len(member_inserts)} inserts but {len(member_on_conflict)} guards"
    )


def test_every_root_has_a_cluster_or_is_marked_thin() -> None:
    """A silently-missing category is the #1 risk (catalog quality) hiding.

    Every one of the 8 roots must EITHER carry ≥1 cluster OR be explicitly named on
    a ``-- THIN CATALOG: <category>`` comment line, so a coverage gap is loud, never
    absent-and-unremarked. Assert (roots-with-clusters ∪ roots-named-thin) == the 8.
    """
    roots = set(TOPIC_CATEGORIES)
    categories_with_clusters = {cat for _slug, cat in _cluster_rows()}

    # THIN markers live in `--` comments, so read them from the RAW (unstripped) SQL.
    thin_named = set(re.findall(r"--\s*THIN CATALOG:\s*([a-z]+)", _RAW_SQL))

    accounted = categories_with_clusters | thin_named
    missing = roots - accounted
    assert not missing, (
        f"these roots are neither seeded nor marked `-- THIN CATALOG:`: "
        f"{sorted(missing)} (a silent catalog gap — the #1 risk)"
    )
    # Guard the reverse direction: a THIN marker for a non-root is a typo we want loud.
    stray_thin = thin_named - roots
    assert not stray_thin, f"`-- THIN CATALOG:` names non-roots: {sorted(stray_thin)}"


def test_members_resolve_by_natural_key_not_raw_uuid() -> None:
    """A hardcoded uuid in a member ref breaks across environments.

    Catalog row ids differ per environment; the seed must resolve a member's
    ``source_id`` / ``personality_id`` via a sub-select on the natural key
    (``(content_source_type, external_id)`` / ``display_name``), never a literal
    uuid. Assert no uuid literal appears anywhere in the seed AND that every member
    insert pulls its ids from sub-selects.
    """
    assert not _UUID.search(SEED_SQL), (
        "a raw uuid literal appears in the seed — member refs must resolve via "
        "sub-select on the natural key, not a hardcoded uuid"
    )
    # Every member insert resolves the cluster by slug and the followable by its key.
    member_blocks = re.findall(
        r"insert\s+into\s+source_cluster_members\b.*?on conflict do nothing;",
        SEED_SQL,
        flags=re.DOTALL,
    )
    assert member_blocks, "expected member insert blocks"
    for block in member_blocks:
        assert "select cluster_id from source_clusters where cluster_slug" in block, (
            "a member insert does not resolve its cluster_id by cluster_slug"
        )
        resolves_source = (
            "select source_id from content_sources where content_source_type"
            in block
        )
        resolves_personality = (
            "select personality_id from personalities where display_name" in block
        )
        assert resolves_source or resolves_personality, (
            "a member insert resolves neither a source_id (by type+external_id) "
            "nor a personality_id (by display_name) via sub-select"
        )
        # The `where exists` guard makes a missing catalog row a no-op, not a NULL-FK.
        assert "where exists" in block, (
            "a member insert lacks the `where exists` guard that makes a missing "
            "catalog row a no-op insert rather than a NULL-FK failure"
        )
