"""DoD gate for migration 0023 (root interest nodes + leaf re-parent) — Phase FSR-M2R.

Two layers, both runnable in the build sandbox (no Supabase, GDELT 403):

  * STRUCTURAL parse tests — a pure text parse of the migration ``.sql`` (stdlib +
    the one ``agents.pipeline.categories`` import, Rule 8: reuse the 8 roots, never
    re-derive). These pin the contract M2-SP3 ingest tagging depends on and fail loud
    if it drifts. They always run.

  * EPHEMERAL-PG apply test — when ``pg_virtualenv`` (PG16) is on PATH, actually
    applies 0023 against a throw-away cluster via the SQL harness
    ``_0023_ephemeral_apply.sql`` (stub parent tables + the real seed rows), proving
    the live DoD: all 8 roots exist exactly once, every picker leaf re-parented to
    its true root, idempotent re-apply is a no-op, no orphans, FK integrity holds,
    and an existing ``user_interest_profile`` FK still resolves. If ``pg_virtualenv``
    is absent the test SKIPS with a LIVE-E2E-deferred marker — it is NEVER faked.

Each test name/docstring encodes WHY the behavior matters (Rule 9): a test that
can't fail when the taxonomy-foundation rule changes would be worthless.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agents.pipeline.categories import (
    SOURCE_CATEGORIES,
    TOPIC_CATEGORIES,
    category_for_slug,
    root_interest_slug_for_category,
)


def _repo_root() -> Path:
    """Walk up to the repo root (the dir holding ``supabase/migrations``)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "supabase" / "migrations").is_dir():
            return parent
    raise RuntimeError("could not locate repo root (no supabase/migrations above this file)")


REPO_ROOT = _repo_root()
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
MIGRATION_PATH = MIGRATIONS_DIR / "0023_root_interest_nodes.sql"
EPHEMERAL_SQL = Path(__file__).resolve().parent / "_0023_ephemeral_apply.sql"
SQL = MIGRATION_PATH.read_text()


def _strip_sql_comments(sql: str) -> str:
    """Drop ``-- ...`` line comments so structural matches see only executable SQL.

    The migration documents its rollback/down plan and verification queries in
    comments (which legitimately contain ``delete from interests`` /
    ``set parent_interest_id`` example text). Structural assertions must match the
    EXECUTABLE statements, not prose — so we strip comment tails before lowercasing.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


# Executable-only, lower-cased view for structural matches.
SQL_LOWER = _strip_sql_comments(SQL).lower()


# ── Structural parse tests ──────────────────────────────────────────────────────


def test_migration_number_0023_unused_before_and_0022_is_prior_latest() -> None:
    """0023 must be this migration's number alone, with 0022 the prior latest.

    WHY: migrations are forward-only, applied in monotonic order. M1 used 0022;
    a collision at 0023 or a stale prior-latest means this file was numbered against
    the wrong tree and would apply out of order.
    """
    matches = sorted(p.name for p in MIGRATIONS_DIR.glob("0023_*.sql"))
    assert matches == ["0023_root_interest_nodes.sql"], (
        f"expected exactly 0023_root_interest_nodes.sql at number 0023, found {matches}"
    )
    assert (MIGRATIONS_DIR / "0022_source_clusters.sql").is_file(), (
        "expected 0022_source_clusters.sql to be the prior-latest migration"
    )


def test_mints_exactly_the_eight_topic_roots_as_depth_zero() -> None:
    """The root INSERT must mint EXACTLY categories.TOPIC_CATEGORIES at depth 0.

    WHY (load-bearing): M2's assign_category drops a tag whose interest_id is not a
    known node. The whole phase exists to make all 8 topic roots resolvable interest
    nodes. If a root is missing here, theme tags for it still collapse to
    DEFAULT_CATEGORY (the bug); if a stray slug (e.g. a youtube/x source axis, which
    has no interest node) is minted, we create a node nothing should tag. Set-equality
    against the imported tuple makes drift a hard failure in EITHER direction.
    """
    insert = re.search(
        r"insert\s+into\s+interests.*?on\s+conflict\s*\(\s*interest_slug\s*\)\s*do\s+nothing",
        SQL_LOWER,
        re.DOTALL,
    )
    assert insert is not None, "root INSERT … on conflict (interest_slug) do nothing not found"
    block = insert.group(0)
    # The minted slugs are the first quoted literal of each VALUES tuple `('slug',`.
    minted = set(re.findall(r"\(\s*'([a-z]+)'\s*,", block))
    assert minted == set(TOPIC_CATEGORIES), (
        "minted root slugs drifted from categories.TOPIC_CATEGORIES: "
        f"minted={sorted(minted)} vs roots={sorted(TOPIC_CATEGORIES)}"
    )
    # Minted at depth 0 with NULL parent (satisfies ck_interest_depth).
    assert re.search(r"0\s*,\s*null\s*,", block), "roots must be inserted depth_level=0, parent NULL"


def test_root_insert_is_idempotent_on_conflict_do_nothing() -> None:
    """The root INSERT must use `on conflict (interest_slug) do nothing`.

    WHY: re-applying must be safe AND the 3 pre-existing roots (business/tech/sport,
    seeded in Phase 1e) must be RECONCILED — not duplicated and not overwritten. `do
    nothing` (vs `do update`) preserves their existing label/segment/sort while still
    minting the 5 missing roots. A `do update` here would silently rewrite the
    pre-existing roots' Phase-1e identity.
    """
    assert "on conflict (interest_slug) do nothing" in SQL_LOWER, (
        "root INSERT must be `on conflict (interest_slug) do nothing` (idempotent, non-clobbering)"
    )
    # Guard against an accidental do-update that would clobber existing roots.
    assert "on conflict (interest_slug) do update" not in SQL_LOWER, (
        "root INSERT must NOT `do update` — that would overwrite the 3 pre-existing roots"
    )


def test_reparent_is_slug_root_driven_and_scoped_to_depth_one() -> None:
    """The re-parent UPDATE must key on the slug's ROOT segment and touch only depth-1.

    WHY: the re-parenting rule MUST equal categories.category_for_slug's root-segment
    logic (parent = the depth-0 node whose slug == split_part(slug,'.',1)). Scoping to
    depth_level=1 leaves the depth-2 leaves (sport.cricket.india etc.) under their
    depth-1 parent — re-homing those to a root would flatten the tree. The
    `split_part(... '.' ... 1)` join is what makes one statement re-home every root's
    leaves without per-root branches.
    """
    update = re.search(r"update\s+interests.*?;", SQL_LOWER, re.DOTALL)
    assert update is not None, "re-parent UPDATE statement not found"
    stmt = update.group(0)
    assert "split_part" in stmt, "re-parent must derive the root from the slug via split_part"
    assert "leaf.depth_level = 1" in stmt, "re-parent must be scoped to depth_level=1 leaves only"
    # Sets parent_interest_id (the only column it may change — never slug/id/depth).
    assert "set parent_interest_id" in stmt, "re-parent must set parent_interest_id"
    for forbidden in ("set interest_slug", "set interest_id", "set depth_level"):
        assert forbidden not in stmt, f"re-parent must NOT change {forbidden.split()[1]} (breaks FKs/identity)"


def test_reparent_has_idempotency_guard() -> None:
    """The re-parent UPDATE must guard so a re-apply is a no-op.

    WHY: applying twice must not thrash rows. The `is distinct from` guard makes the
    UPDATE match zero rows once every leaf is already under its root — idempotency we
    also prove live in the ephemeral test. Without it, re-apply would needlessly
    re-write every leaf (and the idempotency DoD would be unprovable by diff).
    """
    assert "is distinct from root.interest_id" in SQL_LOWER, (
        "re-parent UPDATE must carry an `is distinct from root.interest_id` idempotency guard"
    )


def test_migration_does_not_delete_or_rename_interests() -> None:
    """The migration must be additive: no DELETE of interests, no slug rename.

    WHY (constraint): the phase is additive + non-destructive. Deleting a legacy root
    or renaming a slug would break existing user_interest_profile / story_interests
    rows (and the picker's findCanonicalInterest slug lookups). Only minting + parent
    re-pointing is allowed.
    """
    assert "delete from interests" not in SQL_LOWER, "migration must not DELETE interests (additive only)"
    assert "drop table" not in SQL_LOWER, "migration must not DROP tables"
    # No UPDATE that rewrites interest_slug (rename).
    assert not re.search(r"set\s+interest_slug\s*=", SQL_LOWER), "migration must not rename any interest_slug"


# ── category→root-interest accessor contract (the M2-SP3 mapping) ─────────────────


def test_root_accessor_is_identity_for_topic_roots() -> None:
    """root_interest_slug_for_category must return the same slug 0023 mints per topic root.

    WHY (load-bearing): M2-SP3 calls this to know which depth-0 interest node to tag a
    story's category onto. The migration mints one root per TOPIC_CATEGORIES with
    interest_slug == the category key, so the accessor MUST return that exact slug —
    and it must round-trip with category_for_slug (the inverse on a bare root slug).
    If they disagree, SP3 tags a node that doesn't exist and assign_category drops it.
    """
    for category in TOPIC_CATEGORIES:
        root_slug = root_interest_slug_for_category(category)
        assert root_slug == category, f"accessor must be identity for topic root {category!r}, got {root_slug!r}"
        # Inverse contract: category_for_slug(root_slug) == category.
        assert category_for_slug(root_slug) == category, (
            f"round-trip broke: category_for_slug({root_slug!r}) != {category!r}"
        )


def test_root_accessor_returns_none_for_source_axes() -> None:
    """root_interest_slug_for_category must return None for youtube/x.

    WHY: the source-axis categories are follow-gated — no interest slug maps to them
    and 0023 mints no interest node for them. Returning a slug would point SP3 at a
    non-existent node. None is the explicit "nothing to tag" signal.
    """
    for category in SOURCE_CATEGORIES:
        assert root_interest_slug_for_category(category) is None, (
            f"source-axis category {category!r} has no interest node — accessor must return None"
        )


# ── Ephemeral-PG apply (live DoD when pg_virtualenv is present) ───────────────────


@pytest.mark.skipif(
    shutil.which("pg_virtualenv") is None,
    reason="LIVE-E2E (deferred): pg_virtualenv not on PATH — ephemeral apply unavailable in this env",
)
def test_ephemeral_apply_idempotency_and_dod() -> None:
    """Apply 0023 twice on a throw-away PG16 cluster; assert the full DoD in-SQL.

    WHY: the structural tests prove the migration's TEXT is right; this proves it
    actually APPLIES and yields the invariants the phase promises (8 roots once each,
    every picker leaf under its true root, re-apply is a byte-for-byte no-op, no
    orphans, FK integrity, existing user_interest_profile FK still resolves). The SQL
    harness raises on any failed `assert`; ON_ERROR_STOP makes psql exit non-zero,
    which fails this test loud (Rule 12) — never faked.
    """
    assert EPHEMERAL_SQL.is_file(), f"missing ephemeral harness {EPHEMERAL_SQL}"
    proc = subprocess.run(
        [
            "pg_virtualenv",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"migration={MIGRATION_PATH}",
            "-f",
            str(EPHEMERAL_SQL),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = proc.stdout + "\n" + proc.stderr
    assert proc.returncode == 0, f"ephemeral apply failed (rc={proc.returncode}):\n{combined}"
    assert "OK: migration 0023 ephemeral apply + idempotency + DoD all passed" in proc.stdout, (
        f"expected DoD success sentinel not found:\n{combined}"
    )
