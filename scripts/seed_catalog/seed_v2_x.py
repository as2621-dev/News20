"""Seed the **908 NEW v2 X handles** as trusted cluster members (issue #27).

What this seeds
---------------
The companion to ``seed_v2`` (issue #15). #15 seeded the *feasible* half — the
120 interest nodes, 170 yt-dlp-verified YouTube channels, and the cluster
structures whose members were **already verified in prod**. It deferred the
~908 NEW X handles that could not be honestly verified keyless.

**Founder decision (2026-07-05): seed all 908 NEW X handles as TRUSTED — no
verification.** No live existence check, no paid X API, no unavatar drip, no
``is_verified`` caveat column. A dead handle that surfaces in prod is
cross-checked reactively later. This module therefore collapses to:

1. **content_sources rows** — one ``x_account`` row per NEW handle (a handle not
   already present in prod), ``is_curated=True``, ``thumbnail_url=None`` (no
   avatar probe). ``topic_tags`` = the sorted union of the roots the handle
   appears under. Upserted on ``(content_source_type, external_id)`` — reused
   verbatim from ``seed_via_pooler._flush`` (Rule 8/11).
2. **source_clusters structures** — ALL catalog clusters (including the ones #15
   deferred because every member was NEW-unverified), upserted on ``cluster_slug``.
3. **source_cluster_members** — every valid handle wired to its cluster on the
   ``(cluster_id, source_id)`` composite, ``on conflict do nothing`` → no dup
   members on re-run.

Steps 2+3 reuse ``seed_v2.seed_clusters`` unchanged: it resolves each member
handle to its ``source_id`` (case-insensitively, matching the row this module
inserted in step 1), upserts the structure, and inserts members idempotently.

Honest drops (NON-verification reasons only)
--------------------------------------------
A handle that is **structurally invalid** — empty, or failing X's own grammar
``^[A-Za-z0-9_]{1,15}$`` (e.g. a 17-char mis-remembered handle) — CANNOT resolve
to a real X account, so it is **dropped + logged, never guessed**. This is a
structural drop, not a verification check. A handle duplicated within one cluster
is collapsed to a single member (deduped, not dropped).

Safety
------
Dry-run is the DEFAULT: plan + print the full X report, write nothing. ``--live``
is required to write. content_sources upsert is one transaction; each cluster's
structure + members commit together (seed_v2.seed_clusters' per-cluster txn) so a
partial failure never orphans a structure. The write path parameterizes every
handle (asyncpg ``$n`` placeholders) — no string interpolation of handles.

Usage
-----
    # dry-run (plan + report, no writes) — ALWAYS run this first:
    .venv/bin/python -m scripts.seed_catalog.seed_v2_x

    # live seed (writes to prod via the IPv4 session pooler):
    .venv/bin/python -m scripts.seed_catalog.seed_v2_x --live

Env (from .env, never logged): ``SUPABASE_DB_URL`` (percent-encoded session pooler).
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field

from agents.shared.logger import get_logger
from scripts.seed_catalog.seed_v2 import (
    ClusterPlanRow,
    V2Catalog,
    bare_handle,
    cluster_slug,
    fetch_verified_handles,
    load_catalog,
    seed_clusters,
)
from scripts.seed_catalog.seed_via_pooler import _connect, _flush

logger = get_logger("seed_catalog.seed_v2_x")

# X's own handle grammar: 1–15 chars of [A-Za-z0-9_]. A handle failing this
# CANNOT be a real X account (X hard-caps at 15 chars), so it is a structural
# drop — never a verification decision.
X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


# ── models ────────────────────────────────────────────────────────────────────
class XHandleDrop(BaseModel):
    """A handle dropped for a structural (non-verification) reason."""

    handle: str
    cluster_slug: str
    reason: str


class XSeedPlan(BaseModel):
    """The planned X seed: new source rows, all cluster rows, drops, totals.

    Per-cluster member totals for the report are derived from ``cluster_rows``
    (``len(row.members)``) — no parallel structure, matching ``seed_v2.build_report``.
    """

    source_rows: list[dict[str, Any]] = Field(default_factory=list)
    cluster_rows: list[ClusterPlanRow] = Field(default_factory=list)
    drops: list[XHandleDrop] = Field(default_factory=list)
    new_handle_count: int = 0
    existing_handle_count: int = 0


def build_x_source_row(handle: str, topic_tags: list[str]) -> dict[str, Any]:
    """Build a trusted ``content_sources`` ``x_account`` upsert row for a handle.

    Mirrors ``seed_catalog.build_x_account_row`` (Rule 11): the bare handle is the
    ``external_id``; ``source_name`` defaults to ``@handle``; ``thumbnail_url`` and
    ``subscriber_count`` stay null (no live X API / no avatar probe — the founder's
    no-verification decision). ``is_curated=True`` marks it a trusted catalog row.

    Args:
        handle: The bare handle (no leading ``@``).
        topic_tags: The root slugs this handle appears under (sorted at build time).

    Returns:
        The column payload for an upsert on ``(content_source_type, external_id)``.
    """
    return {
        "content_source_type": "x_account",
        "external_id": handle,
        "source_name": f"@{handle}",
        "source_description": None,
        "thumbnail_url": None,
        "subscriber_count": None,
        "personas": [],
        "topic_tags": sorted(set(topic_tags)),
        "popularity_score": 50.0,
        "is_curated": True,
    }


def plan_x_seed(catalog: V2Catalog, existing_handles: set[str]) -> XSeedPlan:
    """Plan the X seed: new source rows + all cluster member rows + honest drops.

    Every cluster becomes a ``ClusterPlanRow`` carrying its valid handles (deduped
    within the cluster, order preserved). A handle already present in prod
    (``existing_handles``, lower-cased bare) is reused — no duplicate
    ``content_sources`` row is planned for it; only genuinely NEW handles yield a
    ``source_rows`` entry. Structurally invalid handles are dropped + logged.

    Args:
        catalog: The loaded v2 catalog.
        existing_handles: Lower-cased bare handles already in prod as ``x_account``
            rows — the plan-time twin of ``seed_clusters``' case-insensitive
            seed-time lookup (they must share ONE predicate).

    Returns:
        The plan: NEW ``content_sources`` rows, ALL cluster rows (with valid
        members), the drop log, and per-cluster totals.
    """
    plan = XSeedPlan()
    # Global handle → unioned topic_tags (a handle in several clusters/roots gets
    # ONE content_sources row whose topic_tags span every root it belongs to).
    global_tags: dict[str, set[str]] = {}
    global_bare: dict[str, str] = {}  # lower → first-seen original-case bare handle

    for root in catalog.roots:
        for index, cluster in enumerate(root.x_clusters):
            slug = cluster_slug(root.root_slug, cluster.cluster_name)
            members: list[str] = []
            seen: set[str] = set()
            for raw in cluster.handles:
                bare = bare_handle(raw)
                low = bare.lower()
                if not X_HANDLE_RE.match(bare):
                    plan.drops.append(
                        XHandleDrop(
                            handle=raw,
                            cluster_slug=slug,
                            reason="structurally invalid — empty or fails X grammar ^[A-Za-z0-9_]{1,15}$",
                        )
                    )
                    continue
                if low in seen:
                    continue  # dedup within cluster (a bundled handle → one member)
                seen.add(low)
                members.append(bare)
                global_bare.setdefault(low, bare)
                global_tags.setdefault(low, set()).add(root.root_slug)
            plan.cluster_rows.append(
                ClusterPlanRow(
                    cluster_slug=slug,
                    cluster_label=cluster.cluster_name,
                    cluster_category=root.root_slug,
                    cluster_sort_order=index,
                    cluster_subniche=cluster.subniche,
                    cluster_description=cluster.description,
                    members=members,
                )
            )

    for low, bare in sorted(global_bare.items()):
        if low in existing_handles:
            plan.existing_handle_count += 1
            continue
        plan.new_handle_count += 1
        plan.source_rows.append(build_x_source_row(bare, sorted(global_tags[low])))
    return plan


def build_x_report(catalog: V2Catalog, plan: XSeedPlan) -> str:
    """Render the X seed report: new/existing totals, per-root X totals, drops.

    This is the #15-report extension the slice mandates — per-cluster X member
    totals plus every structurally-dropped handle with its reason.
    """
    total_members = sum(len(c.members) for c in plan.cluster_rows)
    lines: list[str] = [
        "=== v2 X-handle seed report (trusted, no verification — issue #27) ==="
    ]
    lines.append(
        f"x_account rows: {plan.new_handle_count} NEW to seed · "
        f"{plan.existing_handle_count} already in prod (reused, no dup)"
    )
    lines.append(
        f"clusters:       {len(plan.cluster_rows)} upserted · "
        f"{total_members} member rows across them"
    )
    lines.append(f"drops:          {len(plan.drops)} structurally-invalid handles")
    lines.append("")
    lines.append("per-root X breakdown:")
    for root in catalog.roots:
        rs = root.root_slug
        rc = [c for c in plan.cluster_rows if c.cluster_category == rs]
        lines.append(
            f"  {rs:<12} clusters={len(rc):<3} members={sum(len(c.members) for c in rc)}"
        )
    lines.append("")
    lines.append("per-cluster X totals:")
    for cluster in plan.cluster_rows:
        lines.append(f"  {cluster.cluster_slug:<48} members={len(cluster.members)}")
    if plan.drops:
        lines.append("")
        lines.append(f"structural drops ({len(plan.drops)}):")
        for drop in plan.drops:
            lines.append(f"  DROP {drop.handle} [{drop.cluster_slug}] — {drop.reason}")
    return "\n".join(lines)


# ── persistence (live seed) ───────────────────────────────────────────────────
async def seed_x_sources(conn: Any, source_rows: list[dict[str, Any]]) -> int:
    """Upsert the NEW ``x_account`` rows via the shared pooler write path.

    Reuses ``seed_via_pooler._flush`` verbatim (Rule 8/11): ``insert … on conflict
    (content_source_type, external_id) do update`` with ``topic_tags`` union. One
    transaction so a mid-batch failure rolls the whole content_sources write back.

    Returns:
        The number of rows submitted (a re-run submits 0 — the plan excludes
        now-present handles).
    """
    if not source_rows:
        return 0
    async with conn.transaction():
        counts = await _flush(conn, [("content_sources", source_rows)])
    return counts.get("content_sources", 0)


async def _x_prod_counts(conn: Any) -> dict[str, int]:
    """Snapshot the prod counts this seed touches (for the double-run idempotency proof)."""
    return {
        "x_accounts": await conn.fetchval(
            "select count(*) from content_sources where content_source_type='x_account'"
        ),
        "source_clusters": await conn.fetchval("select count(*) from source_clusters"),
        "source_cluster_members": await conn.fetchval(
            "select count(*) from source_cluster_members"
        ),
    }


async def _run(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    conn = await _connect()
    try:
        existing = await fetch_verified_handles(conn)
        plan = plan_x_seed(catalog, existing)
        print(build_x_report(catalog, plan))

        if not args.live:
            logger.info(
                "dry_run_complete", message="no writes performed; pass --live to seed"
            )
            return

        before = await _x_prod_counts(conn)
        # Insert the NEW x_account rows FIRST so seed_clusters' case-insensitive
        # member lookup resolves them when it wires cluster membership.
        sources_submitted = await seed_x_sources(conn, plan.source_rows)
        clusters_upserted, members_inserted = await seed_clusters(
            conn, plan.cluster_rows
        )
        after = await _x_prod_counts(conn)
        logger.info(
            "v2_x_seed_live_complete",
            x_sources_submitted=sources_submitted,
            new_handles_planned=plan.new_handle_count,
            clusters_upserted=clusters_upserted,
            member_rows_inserted=members_inserted,
            structural_drops=len(plan.drops),
            prod_counts_before=before,
            prod_counts_after=after,
        )
    finally:
        await conn.close()


def main() -> None:
    """CLI entry point for the v2 X-handle seed (dry-run by default)."""
    parser = argparse.ArgumentParser(
        description="Seed the 908 NEW v2 X handles as trusted cluster members."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write to prod (default: dry-run report only).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
