"""Production gather for X theme-of-the-day reels (FSR slice #31).

The batch wiring slice #24 deliberately left open (residual R1): given the active
users, join each user's followed X sources to their clusters
(``user_content_sources → source_cluster_members → source_clusters``), read today's
SHARED ``x_cluster_sweeps.themes`` (slice #23) for those clusters, and build ONE
theme reel story per (cluster, theme) via
:func:`agents.ingestion.x_theme_reel.build_theme_reel_story` — deterministic story
id, so a re-run of the day converges on the same ``stories`` rows and produce-once
skips the re-pay. ``run_daily_pipeline`` merges the returned ``theme_stories`` into
the produce pool (exempt from the gate + caps, exactly like followed-source
stories) and, AFTER production, keeps only the candidates whose reel story is
actually placeable (:func:`filter_placeable_theme_candidates` — the residual R3 FK
guard: ``daily_feeds.feed_story_id`` references ``stories``, so an unpersisted reel
must never reach the per-user batch insert).

Eligibility is per user: ``candidates_by_user`` contains EVERY active user who
follows >= 1 X cluster (even with zero themes today — their ``x`` slots then roll
honestly to the news floor); users with no cluster follows are ABSENT and keep the
legacy source-stories x fill downstream.

DB reads are batched (one ``.in_()`` per table, grouped in memory — the same
no-N+1 style as the ``daily_batch`` loaders). The only non-DB external is the
top-tweet screenshot render, injectable via ``screenshot_renderer`` (mocked in
tests; the real Playwright renderer in production).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.cluster_sweep import load_cluster_sweeps
from agents.ingestion.models import CanonicalStory
from agents.ingestion.x_theme_reel import (
    TweetScreenshotRenderer,
    build_theme_reel_story,
)
from agents.pipeline.x_theme_ladder import XThemeAttribution, XThemeReelCandidate
from agents.shared.logger import get_logger

logger = get_logger("pipeline.x_theme_production")


class XThemeGatherResult(BaseModel):
    """What the X theme gather hands the daily batch.

    Attributes:
        theme_stories: ONE story per (cluster, theme) across ALL users (deduped by
            the deterministic story id) — merged into the produce pool so a cluster
            shared by many followers is produced exactly once.
        candidates_by_user: ``{user_id: [XThemeReelCandidate]}`` for every active
            user following >= 1 X cluster (possibly an EMPTY list on a quiet day —
            the honest ladder then rolls their x slots to the news floor). Built
            BEFORE production; must pass :func:`filter_placeable_theme_candidates`
            before reaching ``assemble_daily_feeds`` (R3 FK guard).
    """

    theme_stories: list[CanonicalStory] = Field(
        default_factory=list, description="One reel story per (cluster, theme)"
    )
    candidates_by_user: dict[str, list[XThemeReelCandidate]] = Field(
        default_factory=dict, description="Pre-FK-guard candidates per eligible user"
    )


def _load_followed_x_clusters_by_user(
    supabase_client: Any, user_ids: list[str]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Join ``user_content_sources → source_cluster_members → source_clusters``.

    Resolves each active user's followed X-account sources to the clusters those
    sources belong to, in four batched queries total (never per-user / per-source).
    Only ``content_source_type = 'x_account'`` follows count — YouTube/podcast
    follows never make a user X-theme-eligible.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids.

    Returns:
        ``({user_id: {cluster_id, ...}}, {cluster_id: cluster_label})`` — users
        with no followed X cluster are absent from the first map.
    """
    if not user_ids:
        return {}, {}
    follow_rows = (
        getattr(
            supabase_client.table("user_content_sources")
            .select("user_id,source_id")
            .in_("user_id", user_ids)
            .neq("source_priority", "off")
            .execute(),
            "data",
            None,
        )
        or []
    )
    source_ids = sorted({str(row["source_id"]) for row in follow_rows})
    if not source_ids:
        return {}, {}
    x_source_rows = (
        getattr(
            supabase_client.table("content_sources")
            .select("source_id")
            .in_("source_id", source_ids)
            .eq("content_source_type", "x_account")
            .execute(),
            "data",
            None,
        )
        or []
    )
    x_source_ids = {str(row["source_id"]) for row in x_source_rows}
    if not x_source_ids:
        return {}, {}
    member_rows = (
        getattr(
            supabase_client.table("source_cluster_members")
            .select("cluster_id,source_id")
            .in_("source_id", sorted(x_source_ids))
            .execute(),
            "data",
            None,
        )
        or []
    )
    clusters_by_source: dict[str, set[str]] = {}
    for row in member_rows:
        source_id = row.get("source_id")
        if source_id is None:
            continue
        clusters_by_source.setdefault(str(source_id), set()).add(str(row["cluster_id"]))
    clusters_by_user: dict[str, set[str]] = {}
    for row in follow_rows:
        source_id = str(row["source_id"])
        cluster_ids = clusters_by_source.get(source_id)
        if not cluster_ids:
            continue
        clusters_by_user.setdefault(str(row["user_id"]), set()).update(cluster_ids)
    all_cluster_ids = sorted(
        {cluster_id for ids in clusters_by_user.values() for cluster_id in ids}
    )
    if not all_cluster_ids:
        return {}, {}
    cluster_rows = (
        getattr(
            supabase_client.table("source_clusters")
            .select("cluster_id,cluster_label")
            .in_("cluster_id", all_cluster_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    cluster_labels = {
        str(row["cluster_id"]): str(row.get("cluster_label") or "")
        for row in cluster_rows
    }
    return clusters_by_user, cluster_labels


async def gather_x_theme_candidates(
    supabase_client: Any,
    active_user_ids: list[str],
    sweep_date: date,
    *,
    screenshot_renderer: TweetScreenshotRenderer | None = None,
) -> XThemeGatherResult:
    """Gather today's X theme reel stories + per-user candidates (slice #31).

    Reads the followed-cluster join and today's shared sweeps, then builds ONE reel
    story per (cluster, theme) — the deterministic story id means a shared cluster
    is built once no matter how many users follow it, and a same-day re-run
    converges on the same ids (produce-once then skips the paid render).

    A cluster with no sweep row today, or an empty-themes sweep (a quiet or silent
    day), contributes no candidates — its followers stay in ``candidates_by_user``
    with whatever their OTHER clusters produced (possibly nothing), so the honest
    ladder can roll their x slots to the news floor rather than fabricate a theme.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        active_user_ids: The batch's active user ids.
        sweep_date: The feed day (matches ``x_cluster_sweeps.sweep_date``).
        screenshot_renderer: Optional ``(tweet_url) -> path|None`` seam forwarded to
            :func:`build_theme_reel_story`; the real Playwright renderer when None.

    Returns:
        The :class:`XThemeGatherResult` (empty when no user follows an X cluster).
    """
    clusters_by_user, cluster_labels = _load_followed_x_clusters_by_user(
        supabase_client, active_user_ids
    )
    if not clusters_by_user:
        logger.info(
            "x_theme_gather_no_followed_clusters",
            sweep_date=sweep_date.isoformat(),
            active_user_count=len(active_user_ids),
        )
        return XThemeGatherResult()

    followed_cluster_ids = sorted(
        {cluster_id for ids in clusters_by_user.values() for cluster_id in ids}
    )
    sweeps = load_cluster_sweeps(supabase_client, followed_cluster_ids, sweep_date)

    stories_by_id: dict[str, CanonicalStory] = {}
    candidates_by_cluster: dict[str, list[XThemeReelCandidate]] = {}
    for cluster_id in followed_cluster_ids:
        sweep = sweeps.get(cluster_id)
        if sweep is None or not sweep.themes:
            # Reason: not swept / quiet cluster — honest no-op, never a faked theme.
            continue
        for theme in sweep.themes:
            story = await build_theme_reel_story(
                theme,
                cluster_id=cluster_id,
                sweep_date=sweep_date,
                outlet_name=cluster_labels.get(cluster_id) or None,
                screenshot_renderer=screenshot_renderer,
            )
            stories_by_id.setdefault(story.canonical_story_id, story)
            candidates_by_cluster.setdefault(cluster_id, []).append(
                XThemeReelCandidate(
                    reel_story_id=story.canonical_story_id,
                    cluster_id=cluster_id,
                    attribution=XThemeAttribution(
                        theme_summary=theme.theme_summary,
                        supporting_handles=list(theme.supporting_handles),
                        supporting_tweet_urls=list(theme.supporting_tweet_urls),
                    ),
                    # Reason: rank = distinct backing handles — a theme more handles
                    # converge on is louder (the ladder breaks ties deterministically).
                    reel_rank=float(len(theme.supporting_handles)),
                )
            )

    candidates_by_user = {
        user_id: [
            candidate
            for cluster_id in sorted(cluster_ids)
            for candidate in candidates_by_cluster.get(cluster_id, [])
        ]
        for user_id, cluster_ids in clusters_by_user.items()
    }
    logger.info(
        "x_theme_gather_completed",
        sweep_date=sweep_date.isoformat(),
        eligible_user_count=len(candidates_by_user),
        followed_cluster_count=len(followed_cluster_ids),
        swept_cluster_count=len(sweeps),
        theme_story_count=len(stories_by_id),
    )
    return XThemeGatherResult(
        theme_stories=list(stories_by_id.values()),
        candidates_by_user=candidates_by_user,
    )


def filter_placeable_theme_candidates(
    candidates_by_user: dict[str, list[XThemeReelCandidate]],
    placeable_story_ids: set[str],
) -> dict[str, list[XThemeReelCandidate]]:
    """Drop candidates whose reel story is NOT persisted (residual R3 FK guard).

    ``daily_feeds.feed_story_id`` is ``NOT NULL REFERENCES stories(story_id)`` and
    the per-user feed inserts as one batch — one dangling theme reel (verification
    halt, render failure) would fail the user's ENTIRE feed write. So only
    candidates whose story is placeable (produced this run, or already carrying a
    current digest) survive. Users are KEPT even when all their candidates drop —
    an eligible user with zero placeable themes still gets the honest ladder
    (x slots to the news floor), never the legacy fill.

    Args:
        candidates_by_user: The pre-production candidates per eligible user.
        placeable_story_ids: Story ids persisted + placeable this run.

    Returns:
        The FK-safe ``{user_id: [XThemeReelCandidate]}``.
    """
    filtered: dict[str, list[XThemeReelCandidate]] = {}
    dropped_story_ids: set[str] = set()
    for user_id, candidates in candidates_by_user.items():
        kept = [c for c in candidates if c.reel_story_id in placeable_story_ids]
        dropped_story_ids.update(
            c.reel_story_id
            for c in candidates
            if c.reel_story_id not in placeable_story_ids
        )
        filtered[user_id] = kept
    if dropped_story_ids:
        logger.warning(
            "x_theme_candidates_dropped_unpersisted",
            dropped_count=len(dropped_story_ids),
            dropped_story_ids=sorted(dropped_story_ids)[:10],
            fix_suggestion="A theme reel story did not persist (verification halt / "
            "render failure); its candidates were dropped BEFORE assembly so the "
            "per-user daily_feeds batch insert cannot fail on the FK. Check "
            "produce_write_failed / produce_render_failed logs for the story id.",
        )
    return filtered
