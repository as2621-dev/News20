"""Offline structural gate for migration 0022 (source clusters + members) — Phase FSR-M1 SP1.

WHY THIS EXISTS: the build sandbox has NO database, so the live apply of 0022 (+
its supabase/tests/0022_source_clusters_assertions.sql) is a deferred LIVE-E2E
residual. This module is the GATED OFFLINE DoD: a pure text parse of the migration
``.sql`` that fails loud when the schema contract drifts from what M6 (onboarding
bulk-select) and the SP2 resolver depend on. No DB, no network — stdlib + the one
``agents.pipeline.categories`` import (Rule 8: reuse the 8 roots, don't re-derive).

Each test name/docstring encodes the WHY the asserted behavior matters (Rule 9), so
a test that can't fail when the business rule changes would be wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents.pipeline.categories import TOPIC_CATEGORIES


def _repo_root() -> Path:
    """Walk up from this file to the repo root (the dir holding ``supabase/``).

    Robust to where pytest is invoked from — we don't assume the cwd.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "supabase" / "migrations").is_dir():
            return parent
    raise RuntimeError("could not locate repo root (no supabase/migrations above this file)")


REPO_ROOT = _repo_root()
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
MIGRATION_PATH = MIGRATIONS_DIR / "0022_source_clusters.sql"
SQL = MIGRATION_PATH.read_text()
# Lower-cased copy for case-insensitive structural matches (SQL keywords are
# case-insensitive; the migration is authored lower-case but we don't rely on that).
SQL_LOWER = SQL.lower()


def test_source_clusters_table_present_with_named_columns() -> None:
    """source_clusters must exist with the columns M6/seed write — a missing
    column silently breaks the cluster grouping the whole source-first thesis stands on."""
    assert "create table source_clusters" in SQL_LOWER
    for column in (
        "cluster_id",
        "cluster_slug",
        "cluster_label",
        "cluster_category",
        "cluster_sort_order",
        "is_curated",
        "cluster_created_at",
    ):
        assert column in SQL_LOWER, f"source_clusters missing column {column!r}"


def test_source_cluster_members_table_present_with_named_columns() -> None:
    """source_cluster_members must exist with the columns the resolver reads —
    cluster_id/source_id/personality_id/member_sort_order are the member contract."""
    assert "create table source_cluster_members" in SQL_LOWER
    for column in (
        "cluster_member_id",
        "cluster_id",
        "source_id",
        "personality_id",
        "member_sort_order",
        "member_created_at",
    ):
        assert column in SQL_LOWER, f"source_cluster_members missing column {column!r}"


def test_cluster_category_check_lists_exactly_the_eight_topic_roots() -> None:
    """The cluster_category CHECK must list EXACTLY categories.TOPIC_CATEGORIES.

    WHY (load-bearing): a cluster's category is the same closed vocabulary the
    ranking/allocation path uses (the 8 picker roots). If a root is dropped or a
    stray value (e.g. a youtube/x source axis) is added here, clusters would point
    at a category the rest of the system can't render. Set-equality against the
    imported tuple makes that drift a hard failure, in EITHER direction.
    """
    match = re.search(
        r"check\s*\(\s*cluster_category\s+in\s*\((?P<list>[^)]*)\)\s*\)",
        SQL_LOWER,
    )
    assert match is not None, "could not find the cluster_category IN (...) CHECK clause"
    literals = set(re.findall(r"'([^']+)'", match.group("list")))
    assert literals == set(TOPIC_CATEGORIES), (
        "cluster_category CHECK literals drifted from categories.TOPIC_CATEGORIES: "
        f"check={sorted(literals)} vs roots={sorted(TOPIC_CATEGORIES)}"
    )


def test_member_exactly_one_of_xor_check_present() -> None:
    """The exactly-one-of member CHECK must be present.

    WHY: a member is EITHER a content_sources row OR a personality — never both,
    never neither. The XOR `(source_id is not null) <> (personality_id is not null)`
    encodes that; without it a member could carry two refs (ambiguous render) or
    zero (a dangling row). We assert on the XOR predicate text, tolerating
    whitespace, so the constraint can't be silently dropped.
    """
    xor = re.search(
        r"\(\s*source_id\s+is\s+not\s+null\s*\)\s*<>\s*\(\s*personality_id\s+is\s+not\s+null\s*\)",
        SQL_LOWER,
    )
    assert xor is not None, "exactly-one-of XOR CHECK on source_cluster_members not found"


def test_both_tables_public_read_and_no_write_policy() -> None:
    """Both cluster tables must be RLS-enabled, public-read, and carry NO write policy.

    WHY: anon onboarding reads clusters (so public-read is required), but only the
    service-role (which bypasses RLS) may seed/curate them. A stray for-insert/
    update/delete policy would open writes to clients — a trust/security regression.
    """
    for table in ("source_clusters", "source_cluster_members"):
        assert (
            f"alter table {table} enable row level security" in SQL_LOWER
        ), f"{table} missing `enable row level security`"
        assert (
            f"create policy {table}_public_read on {table}" in SQL_LOWER
        ), f"{table} missing a public-read policy"
    # A public-read SELECT policy uses `using (true)`; assert one per table.
    assert SQL_LOWER.count("for select using (true)") == 2, (
        "expected exactly two `for select using (true)` public-read policies"
    )
    # No write policies anywhere in the migration (service-role only).
    for verb in ("for insert", "for update", "for delete", "for all"):
        assert verb not in SQL_LOWER, f"unexpected write policy `{verb}` — should be service-role only"


def test_migration_number_0022_was_unused_before_this_file() -> None:
    """0022 must be this migration's number alone, and 0021 must be the prior latest.

    WHY: migrations are forward-only and apply in monotonic number order. If 0022
    already named another file, the apply order would collide; if 0021 isn't the
    prior latest, this file was numbered against a stale tree.
    """
    matches = sorted(p.name for p in MIGRATIONS_DIR.glob("0022_*.sql"))
    assert matches == ["0022_source_clusters.sql"], (
        f"expected exactly 0022_source_clusters.sql at number 0022, found {matches}"
    )
    assert (
        MIGRATIONS_DIR / "0021_taxonomy_8_roots_backfill.sql"
    ).is_file(), "expected 0021_taxonomy_8_roots_backfill.sql to be the prior-latest migration"
