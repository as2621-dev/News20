"""Greenfield seeder for the **v2 root catalog** — feasible half (issue #15).

What this seeds
---------------
The v2 catalog (``data/root_catalog_v2.json`` — 8 topic roots × 15 sub-niches)
is a net-new taxonomy + source layer. This module seeds only the half that can
be **honestly verified keyless**:

1. **120 depth-1 interest nodes** (8 roots × 15 sub-niches) — minted on
   ``interest_slug`` (``{root}.{slug(subniche)}``), parented to the existing
   depth-0 root node. ``on conflict (interest_slug) do nothing`` → a slug that
   already exists (e.g. ``sport.cricket``) **converges**, never duplicates.
2. **170 YouTube channels** — each resolved via the shipped **keyless yt-dlp**
   path (``youtube_resolve``) to its ``UC…`` id. A handle that does not resolve
   (404 / ``DownloadError``) is **dropped + logged, never guessed**. Upserted on
   ``(content_source_type, external_id)``.
3. **``source_clusters`` structures** — one per X cluster, upserted on
   ``cluster_slug`` (``{root}-{slug(cluster_name)}``). A cluster's **members are
   only handles already verified in prod** (present in ``content_sources`` as
   ``x_account``). A cluster whose members would all be NEW-unverified X handles
   is **logged as deferred to issue #27**, NOT seeded empty.

DEFERRED to #27 (never touched here): the ~900 NEW X handles — they cannot be
honestly verified keyless (unavatar caps ~25/day/IP). This seeder never writes an
unverified X handle to prod.

Safety
------
Dry-run is the DEFAULT: resolve + plan + print the full report, write nothing.
``--live`` is required to write. Every write is wrapped in a transaction; a
failed cluster seed rolls back its structure AND members (no orphan). The write
path (upserts / conflict targets / jsonb+array codecs) is reused verbatim from
``seed_via_pooler`` (Rule 8/11 — read exports first).

Usage
-----
    # dry-run (resolve + report, no writes) — ALWAYS run this first:
    .venv/bin/python -m scripts.seed_catalog.seed_v2

    # live seed (writes to prod via the IPv4 session pooler):
    .venv/bin/python -m scripts.seed_catalog.seed_v2 --live

Env (from .env, never logged): ``SUPABASE_DB_URL`` (percent-encoded session pooler).
YouTube resolves KEYLESS via yt-dlp — no ``YOUTUBE_API_KEY`` needed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agents.shared.logger import get_logger
from scripts.seed_catalog import youtube_resolve
from scripts.seed_catalog.seed_via_pooler import _connect, _flush

logger = get_logger("seed_catalog.seed_v2")

# The 8 topic roots that are valid cluster categories (mirrors migration 0022's
# CHECK and agents/pipeline/categories.py::TOPIC_CATEGORIES). A root_slug outside
# this set would violate the source_clusters CHECK — fail loud rather than write.
VALID_ROOTS: frozenset[str] = frozenset(
    {
        "ai",
        "geopolitics",
        "business",
        "environment",
        "politics",
        "tech",
        "sport",
        "arts",
    }
)

DEFAULT_CATALOG_PATH = Path(__file__).parent / "data" / "root_catalog_v2.json"


# ── slug helpers (deterministic → idempotent upsert keys) ────────────────────
def _slugify(text: str) -> str:
    """Lower-case, ``&``→``and``, collapse non-alphanumerics to single hyphens.

    Deterministic so the same catalog entry maps to the same ``interest_slug`` /
    ``cluster_slug`` on every run — the property that makes the upsert idempotent.

    Example:
        >>> _slugify("Frontier labs & LLMs")
        'frontier-labs-and-llms'
    """
    lowered = re.sub(r"&", " and ", text.lower().strip())
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def subniche_interest_slug(root_slug: str, subniche: str) -> str:
    """Depth-1 interest slug for a sub-niche within a root (``{root}.{slug}``)."""
    return f"{root_slug}.{_slugify(subniche)}"


def cluster_slug(root_slug: str, cluster_name: str) -> str:
    """Stable cluster slug (``{root}-{slug}``) — the ``source_clusters`` upsert key."""
    return f"{root_slug}-{_slugify(cluster_name)}"


def bare_handle(handle: str) -> str:
    """The bare X/YT handle (leading ``@`` and whitespace stripped)."""
    return handle.lstrip("@").strip()


# ── catalog models ───────────────────────────────────────────────────────────
class V2Channel(BaseModel):
    """One YouTube channel entry in the v2 catalog."""

    channel_name: str
    youtube_handle: str
    subniche: str = ""
    why: str = ""


class V2Cluster(BaseModel):
    """One X cluster (named group of handles) in the v2 catalog."""

    cluster_name: str
    subniche: str = ""
    description: str = ""
    handles: list[str] = Field(default_factory=list)


class V2Root(BaseModel):
    """One topic root: its sub-niches, YouTube channels and X clusters."""

    root_label: str
    root_slug: str
    subniches: list[str] = Field(default_factory=list)
    youtube_channels: list[V2Channel] = Field(default_factory=list)
    x_clusters: list[V2Cluster] = Field(default_factory=list)


class V2Catalog(BaseModel):
    """The whole v2 catalog artifact (roots + provenance)."""

    source_artifact: str = ""
    roots: list[V2Root] = Field(default_factory=list)


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> V2Catalog:
    """Load + validate the v2 catalog JSON, asserting every root_slug is a valid category.

    Raises:
        ValueError: If a root carries a slug outside the 8 valid categories (would
            break the ``source_clusters`` CHECK on write).
    """
    catalog = V2Catalog.model_validate_json(path.read_text())
    bad = [r.root_slug for r in catalog.roots if r.root_slug not in VALID_ROOTS]
    if bad:
        raise ValueError(
            f"catalog roots carry invalid category slugs {bad}. "
            "fix_suggestion: root_slug must be one of the 8 TOPIC_CATEGORIES."
        )
    return catalog


# ── interest nodes ───────────────────────────────────────────────────────────
class InterestRow(BaseModel):
    """A depth-1 interest node to mint (parented at write time by ``root_slug``)."""

    interest_slug: str
    interest_label: str
    interest_sort_order: int
    root_slug: str


def build_interest_rows(catalog: V2Catalog) -> list[InterestRow]:
    """Build the 120 depth-1 interest rows (8 roots × 15 sub-niches).

    ``interest_label`` is the verbatim sub-niche display string; the slug is
    ``{root}.{slug(subniche)}``. Sort order is the sub-niche's position within its
    root (×10, leaving gaps for later inserts).
    """
    rows: list[InterestRow] = []
    for root in catalog.roots:
        for index, subniche in enumerate(root.subniches):
            rows.append(
                InterestRow(
                    interest_slug=subniche_interest_slug(root.root_slug, subniche),
                    interest_label=subniche,
                    interest_sort_order=(index + 1) * 10,
                    root_slug=root.root_slug,
                )
            )
    # Fail loud on an intra-batch slug collision: two sub-niches under one root that
    # slugify to the same interest_slug would otherwise have the second silently
    # swallowed by `on conflict do nothing` (counted as a converge, its label lost).
    slugs = [row.interest_slug for row in rows]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ValueError(
            f"sub-niches collide to duplicate interest_slug(s) {duplicates}. "
            "fix_suggestion: disambiguate the colliding sub-niche labels in the catalog."
        )
    return rows


# ── YouTube channel resolution ───────────────────────────────────────────────
class ChannelDrop(BaseModel):
    """A YouTube handle that did not resolve keyless (excluded, never guessed)."""

    youtube_handle: str
    root_slug: str
    reason: str


class ChannelResolution(BaseModel):
    """Result of resolving the catalog's YouTube handles."""

    rows: list[dict[str, Any]] = Field(default_factory=list)
    drops: list[ChannelDrop] = Field(default_factory=list)


def _unique_channel_entries(catalog: V2Catalog) -> list[dict[str, Any]]:
    """Collapse the catalog's YouTube channels to one entry per handle (no-dup bundling).

    A handle that appears under several roots is resolved ONCE; its ``root_slugs``
    accumulate (unioned) so ``topic_tags`` spans every root it belongs to.
    """
    by_handle: dict[str, dict[str, Any]] = {}
    for root in catalog.roots:
        for channel in root.youtube_channels:
            key = bare_handle(channel.youtube_handle).lower()
            if not key:
                continue
            entry = by_handle.setdefault(
                key,
                {
                    "youtube_handle": channel.youtube_handle,
                    "channel_name": channel.channel_name,
                    "subniche": channel.subniche,
                    "why": channel.why,
                    "root_slugs": [],
                },
            )
            if root.root_slug not in entry["root_slugs"]:
                entry["root_slugs"].append(root.root_slug)
    return list(by_handle.values())


async def resolve_channels(
    catalog: V2Catalog,
    *,
    extractor: youtube_resolve.ChannelInfoExtractor | None = None,
    concurrency: int = youtube_resolve.RESOLVE_CONCURRENCY,
) -> ChannelResolution:
    """Resolve every unique YouTube handle keyless; build upsert rows + drop log.

    Args:
        catalog: The loaded v2 catalog.
        extractor: The yt-dlp extraction seam (tests inject a fake; None → real).
        concurrency: Max simultaneous channel-page probes.

    Returns:
        Resolved ``content_sources`` rows (deduped by ``UC…`` id, ``topic_tags``
        unioned across roots) plus the drop log for unresolved handles.
    """
    entries = _unique_channel_entries(catalog)
    resolved = await youtube_resolve.resolve_many(
        [{"youtube_handle": e["youtube_handle"]} for e in entries],
        extractor=extractor,
        concurrency=concurrency,
    )

    drops: list[ChannelDrop] = []
    # Dedup by resolved channel id: two handles could map to one channel (renames).
    rows_by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        # resolve_many keys by the RAW handle lower-cased (WITHOUT stripping '@').
        key = entry["youtube_handle"].lower()
        meta = resolved.get(key)
        if meta is None:
            drops.append(
                ChannelDrop(
                    youtube_handle=entry["youtube_handle"],
                    root_slug=(entry["root_slugs"] or ["?"])[0],
                    reason="unresolved keyless (404 / DownloadError / no channel_id)",
                )
            )
            continue
        row = rows_by_id.get(meta.channel_id)
        if row is None:
            rows_by_id[meta.channel_id] = {
                "content_source_type": "youtube_channel",
                "external_id": meta.channel_id,
                "source_name": meta.title or entry["channel_name"],
                "source_description": meta.description,
                "thumbnail_url": meta.thumbnail_url,
                "subscriber_count": meta.subscriber_count,
                "platform_metadata": {
                    "youtube_handle": bare_handle(entry["youtube_handle"]),
                    "subniche": entry["subniche"],
                    "why": entry["why"],
                },
                "topic_tags": sorted(set(entry["root_slugs"])),
                "personas": [],
                "popularity_score": 50.0,
                "is_curated": True,
            }
        else:
            row["topic_tags"] = sorted(
                set(row["topic_tags"]) | set(entry["root_slugs"])
            )

    return ChannelResolution(rows=list(rows_by_id.values()), drops=drops)


# ── cluster planning (verified-member gating) ────────────────────────────────
class ClusterPlanRow(BaseModel):
    """A feasible cluster structure + its verified member handles (ordered)."""

    cluster_slug: str
    cluster_label: str
    cluster_category: str
    cluster_sort_order: int
    cluster_subniche: str
    cluster_description: str
    members: list[str] = Field(default_factory=list)


class ClusterDeferral(BaseModel):
    """A cluster whose members are all NEW-unverified X handles → deferred to #27."""

    cluster_slug: str
    root_slug: str
    cluster_name: str
    reason: str = "all members are NEW-unverified X handles — deferred to #27"


class ClusterPlan(BaseModel):
    """Split of the catalog's clusters into feasible (seed) vs deferred (#27)."""

    feasible: list[ClusterPlanRow] = Field(default_factory=list)
    deferred: list[ClusterDeferral] = Field(default_factory=list)


def plan_clusters(catalog: V2Catalog, verified_handles: set[str]) -> ClusterPlan:
    """Split clusters into feasible (≥1 verified member) vs deferred (#27).

    Args:
        catalog: The loaded v2 catalog.
        verified_handles: Lower-cased bare handles already present in prod as
            ``content_sources`` ``x_account`` rows (the only handles we may attach —
            we never write a NEW-unverified handle).

    Returns:
        The feasible clusters (with their deduped, order-preserved verified member
        handles) and the deferred clusters.
    """
    plan = ClusterPlan()
    for root in catalog.roots:
        for index, cluster in enumerate(root.x_clusters):
            members: list[str] = []
            seen: set[str] = set()
            for handle in cluster.handles:
                bare = bare_handle(handle)
                low = bare.lower()
                # verified-only + no-dup within the cluster (edge: bundled handle → once)
                if low in verified_handles and low not in seen:
                    members.append(bare)
                    seen.add(low)
            slug = cluster_slug(root.root_slug, cluster.cluster_name)
            if members:
                plan.feasible.append(
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
            else:
                plan.deferred.append(
                    ClusterDeferral(
                        cluster_slug=slug,
                        root_slug=root.root_slug,
                        cluster_name=cluster.cluster_name,
                    )
                )
    return plan


# ── seed report ──────────────────────────────────────────────────────────────
def build_report(
    catalog: V2Catalog,
    interest_rows: list[InterestRow],
    channels: ChannelResolution,
    clusters: ClusterPlan,
) -> str:
    """Render the full seed report: per-root/per-type totals + every drop/deferral."""
    lines: list[str] = ["=== v2 seed report (feasible half — issue #15) ==="]
    lines.append(
        f"interests: {len(interest_rows)} depth-1 nodes "
        f"(mint-or-converge on interest_slug)"
    )
    lines.append(
        f"youtube:   {len(channels.rows)} resolved rows "
        f"({len(channels.drops)} dropped) of "
        f"{sum(len(r.youtube_channels) for r in catalog.roots)} handles"
    )
    lines.append(
        f"clusters:  {len(clusters.feasible)} feasible (seeded) · "
        f"{len(clusters.deferred)} deferred to #27 · "
        f"{sum(len(c.members) for c in clusters.feasible)} verified member rows"
    )
    lines.append("")
    lines.append("per-root breakdown:")
    for root in catalog.roots:
        rs = root.root_slug
        feas = [c for c in clusters.feasible if c.cluster_category == rs]
        defr = [c for c in clusters.deferred if c.root_slug == rs]
        lines.append(
            f"  {rs:<12} subniches={len(root.subniches):<3} "
            f"clusters_feasible={len(feas):<3} clusters_deferred={len(defr):<3} "
            f"members={sum(len(c.members) for c in feas)}"
        )
    if channels.drops:
        lines.append("")
        lines.append(f"YouTube drops ({len(channels.drops)}):")
        for drop in channels.drops:
            lines.append(
                f"  DROP {drop.youtube_handle} [{drop.root_slug}] — {drop.reason}"
            )
    if clusters.deferred:
        lines.append("")
        lines.append(f"clusters deferred to #27 ({len(clusters.deferred)}):")
        for deferral in clusters.deferred:
            lines.append(f"  DEFER {deferral.cluster_slug} — {deferral.reason}")
    return "\n".join(lines)


# ── persistence (live seed) ──────────────────────────────────────────────────
async def fetch_verified_handles(conn: Any) -> set[str]:
    """Lower-cased bare handles already present in prod as ``x_account`` rows."""
    rows = await conn.fetch(
        "select external_id from content_sources where content_source_type='x_account'"
    )
    return {bare_handle(r["external_id"]).lower() for r in rows}


async def seed_interests(conn: Any, interest_rows: list[InterestRow]) -> int:
    """Mint depth-1 interest nodes; converge (do nothing) on an existing slug.

    Each node is parented to its depth-0 root via a slug-join in the INSERT, so no
    root uuids are hardcoded. A missing root would silently drop its sub-niches —
    guarded by an explicit pre-check that fails loud instead.

    Returns:
        The number of NEW nodes minted (existing slugs converge, counted as 0).
    """
    needed_roots = sorted({r.root_slug for r in interest_rows})
    present = {
        r["interest_slug"]
        for r in await conn.fetch(
            "select interest_slug from interests where depth_level=0 and interest_slug = any($1::text[])",
            needed_roots,
        )
    }
    missing = [r for r in needed_roots if r not in present]
    if missing:
        raise RuntimeError(
            f"depth-0 root interest nodes missing for {missing} — cannot parent sub-niches. "
            "fix_suggestion: apply migration 0023 (root interest nodes) first."
        )

    minted = 0
    query = (
        "insert into interests "
        "(interest_slug, interest_label, depth_level, parent_interest_id, interest_sort_order) "
        "select $1, $2, 1, r.interest_id, $3 from interests r "
        "where r.interest_slug = $4 and r.depth_level = 0 "
        "on conflict (interest_slug) do nothing returning interest_id"
    )
    async with conn.transaction():
        for row in interest_rows:
            got = await conn.fetchval(
                query,
                row.interest_slug,
                row.interest_label,
                row.interest_sort_order,
                row.root_slug,
            )
            if got is not None:
                minted += 1
    return minted


async def seed_channels(conn: Any, channel_rows: list[dict[str, Any]]) -> int:
    """Upsert resolved YouTube channels via the shared pooler write path.

    Reuses ``seed_via_pooler._flush`` verbatim (Rule 8/11): ``insert … on conflict
    (content_source_type, external_id) do update`` with ``topic_tags`` union +
    jsonb/array/numeric codecs. Returns the number of rows submitted.
    """
    if not channel_rows:
        return 0
    # _flush takes the [(table, rows)] batch directly; the conflict target is fixed
    # per table in _CONFLICT_TARGETS, so no supabase-client shim is needed here.
    async with conn.transaction():
        counts = await _flush(conn, [("content_sources", channel_rows)])
    return counts.get("content_sources", 0)


async def seed_clusters(conn: Any, feasible: list[ClusterPlanRow]) -> tuple[int, int]:
    """Upsert feasible cluster structures + their verified members, per-cluster txn.

    Each cluster's structure AND members commit together (or roll back together) so
    a partial failure never leaves an orphaned structure with no members.

    Returns:
        ``(clusters_upserted, member_rows_inserted)`` — members counts NEW inserts
        only (a re-run's ``on conflict do nothing`` adds zero).
    """
    clusters_upserted = 0
    members_inserted = 0
    upsert_cluster = (
        "insert into source_clusters "
        "(cluster_slug, cluster_label, cluster_category, cluster_sort_order, "
        "cluster_subniche, cluster_description) values ($1,$2,$3,$4,$5,$6) "
        "on conflict (cluster_slug) do update set "
        "cluster_label = excluded.cluster_label, "
        "cluster_category = excluded.cluster_category, "
        "cluster_sort_order = excluded.cluster_sort_order, "
        "cluster_subniche = excluded.cluster_subniche, "
        "cluster_description = excluded.cluster_description "
        "returning cluster_id"
    )
    # ltrim('@') mirrors fetch_verified_handles' bare-handle normalization so the
    # plan-time gate and this seed-time lookup share ONE membership predicate — an
    # x_account row stored with a stray leading '@' still matches.
    find_source = (
        "select source_id from content_sources "
        "where content_source_type='x_account' and lower(ltrim(external_id, '@'))=lower($1)"
    )
    insert_member = (
        "insert into source_cluster_members "
        "(cluster_id, source_id, member_sort_order, member_created_at) "
        "values ($1,$2,$3, now()) "
        "on conflict (cluster_id, source_id) where source_id is not null do nothing"
    )
    for cluster in feasible:
        # Resolve members BEFORE upserting the structure so a cluster whose members
        # vanished between plan and seed is skipped, never committed empty (the
        # "not seeded empty" contract, enforced at the write boundary too).
        source_ids = [
            source_id
            for handle in cluster.members
            if (source_id := await conn.fetchval(find_source, handle)) is not None
        ]
        if not source_ids:
            logger.warning(
                "cluster_skipped_no_live_members",
                cluster_slug=cluster.cluster_slug,
                fix_suggestion="members vanished between plan and seed — re-run after verifying x_account rows.",
            )
            continue
        async with conn.transaction():
            cluster_id = await conn.fetchval(
                upsert_cluster,
                cluster.cluster_slug,
                cluster.cluster_label,
                cluster.cluster_category,
                cluster.cluster_sort_order,
                cluster.cluster_subniche or None,
                cluster.cluster_description or None,
            )
            clusters_upserted += 1
            for order, source_id in enumerate(source_ids):
                status = await conn.execute(insert_member, cluster_id, source_id, order)
                if status.endswith(" 1"):
                    members_inserted += 1
    return clusters_upserted, members_inserted


async def _prod_counts(conn: Any, interest_slugs: list[str]) -> dict[str, int]:
    """Snapshot the prod row counts this seed touches (for the idempotency proof)."""
    return {
        "interests_v2": await conn.fetchval(
            "select count(*) from interests where interest_slug = any($1::text[])",
            interest_slugs,
        ),
        "youtube_channels": await conn.fetchval(
            "select count(*) from content_sources where content_source_type='youtube_channel'"
        ),
        "source_clusters": await conn.fetchval("select count(*) from source_clusters"),
        "source_cluster_members": await conn.fetchval(
            "select count(*) from source_cluster_members"
        ),
    }


DROP_GATE_THRESHOLD = 0.20


def exceeds_drop_gate(
    catalog: V2Catalog,
    channels: ChannelResolution,
    threshold: float = DROP_GATE_THRESHOLD,
) -> bool:
    """True when too many YouTube handles failed to resolve to trust the seed.

    Denominator = UNIQUE handles attempted (deduped, matching the likewise-deduped
    ``channels.drops`` numerator) so a cross-root duplicate can't inflate the
    denominator and silently weaken the gate before a prod write. A ratio over the
    threshold smells like IP-throttle (transient) rather than genuine per-handle
    deaths — the caller aborts instead of seeding a throttle-starved catalog.
    """
    attempted = len(_unique_channel_entries(catalog))
    if not attempted:
        return False
    return (len(channels.drops) / attempted) > threshold


async def _run(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    interest_rows = build_interest_rows(catalog)

    # Resolve YouTube keyless (dry-run resolves too — that is the point of the report).
    channels = await resolve_channels(catalog, concurrency=args.concurrency)

    conn = await _connect()
    try:
        verified = await fetch_verified_handles(conn)
        clusters = plan_clusters(catalog, verified)
        report = build_report(catalog, interest_rows, channels, clusters)
        print(report)

        if exceeds_drop_gate(catalog, channels):
            attempted = len(_unique_channel_entries(catalog))
            logger.error(
                "youtube_resolution_mass_failure",
                dropped=len(channels.drops),
                total=attempted,
                drop_ratio=round(len(channels.drops) / attempted, 3),
                fix_suggestion="Likely IP-throttle, not genuine deaths — retry later; do NOT seed.",
            )
            raise SystemExit(
                "aborting: >20% of YouTube handles failed to resolve (probable throttle)"
            )

        if not args.live:
            logger.info(
                "dry_run_complete", message="no writes performed; pass --live to seed"
            )
            return

        interest_slugs = [r.interest_slug for r in interest_rows]
        before = await _prod_counts(conn, interest_slugs)
        minted = await seed_interests(conn, interest_rows)
        submitted = await seed_channels(conn, channels.rows)
        clusters_upserted, members_inserted = await seed_clusters(
            conn, clusters.feasible
        )
        after = await _prod_counts(conn, interest_slugs)
        logger.info(
            "v2_seed_live_complete",
            interests_minted=minted,
            channels_submitted=submitted,
            clusters_upserted=clusters_upserted,
            member_rows_inserted=members_inserted,
            clusters_deferred_to_27=len(clusters.deferred),
            prod_counts_before=before,
            prod_counts_after=after,
        )
    finally:
        await conn.close()


def main() -> None:
    """CLI entry point for the v2 catalog seed (dry-run by default)."""
    parser = argparse.ArgumentParser(
        description="Seed the feasible half of the v2 root catalog."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write to prod (default: dry-run report only).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=youtube_resolve.RESOLVE_CONCURRENCY,
        help="Max simultaneous YouTube channel-page probes.",
    )
    args = parser.parse_args()
    if os.environ.get("YOUTUBE_API_KEY"):
        logger.info(
            "youtube_api_key_ignored", message="v2 seeder resolves keyless via yt-dlp"
        )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
