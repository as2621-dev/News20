"""Shared, once-daily X cluster sweep + theme extraction (Slice #23).

The **shared X data layer**. A followed cluster (``source_clusters``, migration
0022) groups several X handles within one topic root. This module sweeps a
cluster's handles **once per day** in ONE batched xAI ``x_search`` call, keeps only
**original posts** (drops retweets before any theme work), extracts cross-handle
**themes** on the shared output, and stores the sweep + themes **SHARED** — keyed by
``(cluster_id, sweep_date)`` in ``x_cluster_sweeps`` (migration 0031), NOT per user.
So N users following the same cluster share ONE sweep row and cost stays flat as
users grow (the same "produce once, fan out" model the stories pipeline uses).

What is DETERMINISTIC (code, never the model — CLAUDE.md Rule 5):
  • once-per-day dedup (the ``(cluster_id, sweep_date)`` existence check + upsert),
  • the retweet filter (drop ``is_retweet`` before theme extraction),
  • the handle cap (``MAX_SWEEP_HANDLES`` = 18, under the live-verified 20 cap),
  • attribution validation + the **multi-handle gate**: a theme is kept only when it
    traces to >= ``MIN_THEME_HANDLES`` (2) DISTINCT handles' REAL swept posts, so a
    single loud handle's thread never mints a theme.

What is JUDGMENT (an injectable LLM seam — Rule 5 permits it): grouping the original
posts into candidate themes. The model *proposes* groupings; code *verifies* each
theme's attribution against the real post set and gates on distinct-handle count.

Two injectable seams keep it fully testable without the network (mocked in tests):
  • ``post_discoverer(handles, sweep_date, max_posts) -> list[dict]`` — the batched
    xAI ``x_search`` call (defaults to the real one, reusing the X-adapter's parser).
  • ``theme_extractor(posts) -> list[dict]`` — the LLM grouping call (defaults to a
    real xAI chat call over the already-fetched post text).

Failure degradation (issue #23 error/boundary AC): a provider failure yields EMPTY
themes for the day + a loud structured log with ``fix_suggestion``, and — crucially —
does NOT persist a sweep row (no half-written / poisoned day; the seam stays intact
for a provider swap, and a later same-day retry can still succeed). A *silent* cluster
(the call succeeds but there are no original posts) is stored HONESTLY as an empty
sweep — that is a real answer, not a failure.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from agents.ingestion.adapters.xai_client import parse_xai_response, post_xai
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger(__name__)

_SWEEP_NAME = "x_cluster_sweep"
_SWEEPS_TABLE = "x_cluster_sweeps"

# Under the live-verified 20-handle x_search cap (issue #23: ~36 s / ~$0.12 per sweep).
MAX_SWEEP_HANDLES = 18
# A theme needs >= this many DISTINCT supporting handles — one loud handle's thread
# is not a theme (issue #23 multi-handle-support requirement, enforced in code).
MIN_THEME_HANDLES = 2
_DEFAULT_MAX_POSTS = 40
_DEFAULT_TIMEOUT_SECONDS = 90.0
_THEME_SUMMARY_MAX_CHARS = 400


# An async batched X discoverer: (handles, sweep_date, max_posts) -> raw post dicts.
# Injected so the xAI x_search call is fully mockable (no live call in tests).
ClusterPostDiscoverer = Callable[
    [list[str], date, int], Awaitable[list[dict[str, Any]]]
]
# An async theme grouper: original posts -> raw candidate-theme dicts. The LLM
# judgment seam; code validates + gates its output before storing.
ThemeExtractor = Callable[["list[SweptPost]"], Awaitable[list[dict[str, Any]]]]


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------


class ClusterSweepTarget(BaseModel):
    """One cluster to sweep: its id/slug + the X handles that belong to it.

    Attributes:
        cluster_id: ``source_clusters.cluster_id`` (the shared-storage key).
        cluster_slug: Stable slug (for logging / attribution).
        handles: The cluster's X handles WITHOUT a leading ``@`` (deduped + capped
            by the sweeper). May be empty — a cluster with no X handles is a no-op.
    """

    cluster_id: str = Field(..., description="source_clusters.cluster_id")
    cluster_slug: str = Field(..., description="source_clusters.cluster_slug")
    handles: list[str] = Field(
        default_factory=list, description="X handles without the leading @"
    )


class SweptPost(BaseModel):
    """One original post surfaced by the batched sweep (post-retweet-filter unit).

    Attributes:
        tweet_url: Canonical ``https://x.com/<handle>/status/<id>`` URL (dedup key).
        author_handle: The post author's handle WITHOUT ``@`` (X is case-insensitive).
        text: The full post text.
        is_retweet: True for a pure retweet — dropped before theme extraction.
        published_utc: Post timestamp (UTC-aware) when known, else None.
    """

    tweet_url: str = Field(..., description="Canonical tweet URL")
    author_handle: str = Field(..., description="Author handle without @")
    text: str = Field(..., description="Full post text")
    is_retweet: bool = Field(default=False, description="True for a pure retweet")
    published_utc: datetime | None = Field(
        default=None, description="Post timestamp (UTC-aware) or None"
    )


class ClusterTheme(BaseModel):
    """An extracted, attribution-verified theme across a cluster's handles.

    ``supporting_handles`` is recomputed from ``supporting_tweet_urls`` against the
    REAL swept posts (never trusted from the model), and always has >=
    ``MIN_THEME_HANDLES`` distinct entries (the multi-handle gate).

    Attributes:
        theme_summary: One-line summary of what several handles are converging on.
        supporting_handles: The >= 2 distinct handles whose real posts back the theme.
        supporting_tweet_urls: The real tweet URLs (from the swept set) it draws on.
    """

    theme_summary: str = Field(..., description="One-line cross-handle theme summary")
    supporting_handles: list[str] = Field(
        ..., description=">= 2 distinct handles backing the theme"
    )
    supporting_tweet_urls: list[str] = Field(
        ..., description="Real swept tweet URLs the theme draws on"
    )


class ClusterSweepResult(BaseModel):
    """The outcome of sweeping one cluster on one day (what is stored + returned).

    Attributes:
        cluster_id: The swept cluster.
        sweep_date: The calendar day of the sweep (the shared-storage key partner).
        handle_count: How many handles the batched call covered.
        raw_post_count: Posts the discoverer returned (pre-retweet-filter).
        original_post_count: Original posts kept (post-retweet-filter).
        themes: The attribution-verified, multi-handle-gated themes (may be empty).
        provider_failed: True when the xAI call failed — themes empty, NOT persisted.
        was_cached: True when an existing same-day sweep was reused (no new call).
    """

    cluster_id: str
    sweep_date: date
    handle_count: int = 0
    raw_post_count: int = 0
    original_post_count: int = 0
    themes: list[ClusterTheme] = Field(default_factory=list)
    provider_failed: bool = False
    was_cached: bool = False


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


async def sweep_followed_clusters(
    targets: list[ClusterSweepTarget],
    sweep_date: date,
    *,
    db_client: Any,
    post_discoverer: ClusterPostDiscoverer | None = None,
    theme_extractor: ThemeExtractor | None = None,
    settings: Settings | None = None,
    max_posts: int = _DEFAULT_MAX_POSTS,
) -> list[ClusterSweepResult]:
    """Sweep each distinct followed cluster once for ``sweep_date``.

    De-dups targets by ``cluster_id`` in-run (so a cluster listed twice — e.g. by two
    followers — is swept once) on TOP of the per-cluster DB once-per-day guard.

    Args:
        targets: The clusters to sweep (may contain duplicate cluster ids).
        sweep_date: The calendar day to sweep for.
        db_client: An injected Supabase client (``.table(...).select/upsert().execute()``).
        post_discoverer: Optional batched-x_search seam; real xAI call when None.
        theme_extractor: Optional LLM grouping seam; real xAI call when None.
        settings: Optional Settings (for the real seams' key); built from env if None.
        max_posts: Max posts to request in the batched call.

    Returns:
        One :class:`ClusterSweepResult` per distinct cluster, in first-seen order.
    """
    seen: set[str] = set()
    results: list[ClusterSweepResult] = []
    for target in targets:
        if target.cluster_id in seen:
            continue
        seen.add(target.cluster_id)
        results.append(
            await sweep_cluster(
                target,
                sweep_date,
                db_client=db_client,
                post_discoverer=post_discoverer,
                theme_extractor=theme_extractor,
                settings=settings,
                max_posts=max_posts,
            )
        )
    logger.info(
        "x_cluster_sweep_batch_completed",
        sweep=_SWEEP_NAME,
        sweep_date=sweep_date.isoformat(),
        clusters_swept=len(results),
    )
    return results


async def sweep_cluster(
    target: ClusterSweepTarget,
    sweep_date: date,
    *,
    db_client: Any,
    post_discoverer: ClusterPostDiscoverer | None = None,
    theme_extractor: ThemeExtractor | None = None,
    settings: Settings | None = None,
    max_posts: int = _DEFAULT_MAX_POSTS,
) -> ClusterSweepResult:
    """Sweep one cluster once for ``sweep_date`` and store its shared themes.

    The full vertical path: once-per-day dedup → ONE batched x_search call →
    original-only filter → LLM theme grouping → attribution + multi-handle gate →
    shared upsert. A provider failure degrades to empty themes + a loud log and is
    NOT persisted; a silent cluster is persisted honestly as an empty sweep.

    Args:
        target: The cluster + its handles.
        sweep_date: The calendar day to sweep for.
        db_client: An injected Supabase client.
        post_discoverer: Optional batched-x_search seam; real xAI call when None.
        theme_extractor: Optional LLM grouping seam; real xAI call when None.
        settings: Optional Settings for the real seams; built from env if None.
        max_posts: Max posts to request in the batched call.

    Returns:
        The :class:`ClusterSweepResult` (persisted unless the provider failed).
    """
    resolved_settings = settings or Settings()
    real_seams = _RealSeams(resolved_settings)

    # 1. Once-per-day dedup: reuse an existing same-day sweep (no second call).
    existing = _load_existing_sweep(db_client, target.cluster_id, sweep_date)
    if existing is not None:
        logger.info(
            "x_cluster_sweep_reused",
            sweep=_SWEEP_NAME,
            cluster_slug=target.cluster_slug,
            sweep_date=sweep_date.isoformat(),
            themes=len(existing.themes),
        )
        existing.was_cached = True
        return existing

    handles = _dedup_handles(target.handles)
    result = ClusterSweepResult(
        cluster_id=target.cluster_id,
        sweep_date=sweep_date,
        handle_count=len(handles),
    )

    # A cluster with no X handles is a clean no-op (nothing to sweep, honest empty).
    if not handles:
        logger.info(
            "x_cluster_sweep_no_handles",
            sweep=_SWEEP_NAME,
            cluster_slug=target.cluster_slug,
            sweep_date=sweep_date.isoformat(),
        )
        _store_sweep(db_client, result)
        return result

    logger.info(
        "x_cluster_sweep_started",
        sweep=_SWEEP_NAME,
        cluster_slug=target.cluster_slug,
        sweep_date=sweep_date.isoformat(),
        handle_count=len(handles),
    )

    # 2. ONE batched x_search call for the whole cluster.
    discoverer = post_discoverer or real_seams.discover_posts
    try:
        raw_posts = await discoverer(handles, sweep_date, max_posts)
    except Exception as exc:  # noqa: BLE001 — boundary: provider failure → empty, loud, unpersisted
        logger.error(
            "x_cluster_sweep_provider_failed",
            sweep=_SWEEP_NAME,
            cluster_slug=target.cluster_slug,
            sweep_date=sweep_date.isoformat(),
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            fix_suggestion="xAI x_search failed (rate-limit / no-auth / network); "
            "no sweep stored for the day so downstream falls to the news floor and a "
            "retry can still succeed. Verify XAI_API_KEY has quota and api.x.ai is reachable.",
        )
        result.provider_failed = True
        return result

    # 3. Normalize + deterministic retweet filter (BEFORE any theme work).
    all_posts = [
        p for p in (_raw_to_post(r, handles) for r in raw_posts) if p is not None
    ]
    result.raw_post_count = len(all_posts)
    original_posts = [p for p in all_posts if not p.is_retweet]
    result.original_post_count = len(original_posts)

    # 4. Silent cluster: no original posts → store an HONEST empty sweep (no fabrication).
    if not original_posts:
        logger.info(
            "x_cluster_sweep_silent",
            sweep=_SWEEP_NAME,
            cluster_slug=target.cluster_slug,
            sweep_date=sweep_date.isoformat(),
            raw_post_count=result.raw_post_count,
        )
        _store_sweep(db_client, result)
        return result

    # 5. LLM theme grouping (judgment) — a failure here degrades to empty themes,
    #    but the sweep DID find posts, so the (honest, themeless) row is still stored.
    extractor = theme_extractor or real_seams.extract_themes
    try:
        raw_themes = await extractor(original_posts)
    except Exception as exc:  # noqa: BLE001 — theme grouping failure → empty themes, still honest
        logger.error(
            "x_cluster_sweep_theme_extraction_failed",
            sweep=_SWEEP_NAME,
            cluster_slug=target.cluster_slug,
            sweep_date=sweep_date.isoformat(),
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            fix_suggestion="xAI theme grouping failed; the sweep is stored with the "
            "posts found but ZERO themes. Verify XAI_API_KEY quota; a retry next day recovers.",
        )
        raw_themes = []

    # 6. Attribution validation + multi-handle gate (deterministic — Rule 5).
    result.themes = _verify_and_gate_themes(raw_themes, original_posts)

    logger.info(
        "x_cluster_sweep_completed",
        sweep=_SWEEP_NAME,
        cluster_slug=target.cluster_slug,
        sweep_date=sweep_date.isoformat(),
        original_post_count=result.original_post_count,
        themes_kept=len(result.themes),
        themes_proposed=len(raw_themes),
    )
    _store_sweep(db_client, result)
    return result


# ----------------------------------------------------------------------
# Deterministic helpers (pure — the code that owns the rules, not the model)
# ----------------------------------------------------------------------


def _dedup_handles(handles: list[str]) -> list[str]:
    """Dedup (case-insensitive) + strip, preserve order, cap at ``MAX_SWEEP_HANDLES``.

    Args:
        handles: Raw cluster handles (may carry ``@``, dups, or blanks).

    Returns:
        Up to ``MAX_SWEEP_HANDLES`` clean handles without ``@``, first-seen order.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in handles:
        handle = str(raw or "").strip().lstrip("@").strip()
        if not handle:
            continue
        key = handle.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(handle)
        if len(cleaned) >= MAX_SWEEP_HANDLES:
            break
    return cleaned


def _raw_to_post(raw: dict[str, Any], handles: list[str]) -> SweptPost | None:
    """Map one discovered post dict into a :class:`SweptPost`, or None if unusable.

    Validates the provider payload at the boundary (never trust its shape): a usable
    post needs a URL + text, and its author must be one of the swept handles
    (case-insensitive) — a post from an unexpected handle is dropped, not attributed.

    Args:
        raw: One post dict (``tweet_url``, ``text``, ``author_handle``/``handle``,
            optional ``is_retweet`` / ``published_utc``).
        handles: The canonical swept handles (the allow-set for authorship).

    Returns:
        A :class:`SweptPost`, or None when the post is unusable / off-handle.
    """
    tweet_url = str(raw.get("tweet_url") or "").strip()
    text = str(raw.get("text") or "").strip()
    author = (
        str(raw.get("author_handle") or raw.get("handle") or "").strip().lstrip("@")
    )
    if not tweet_url or not text or not author:
        return None

    handle_by_lower = {h.lower(): h for h in handles}
    canonical = handle_by_lower.get(author.lower())
    if canonical is None:
        # Reason: x_search is scoped to allowed_x_handles, but a stray off-handle post
        # in the payload must not be silently attributed to the cluster.
        return None

    return SweptPost(
        tweet_url=tweet_url,
        author_handle=canonical,
        text=text,
        is_retweet=_coerce_retweet(raw),
        published_utc=_parse_dt(raw.get("published_utc")),
    )


def _coerce_retweet(raw: dict[str, Any]) -> bool:
    """Read a post's retweet flag from either ``is_retweet`` or a leading ``RT @``.

    The provider marks retweets via ``is_retweet``; as a backstop the classic
    ``RT @user`` text prefix is also treated as a retweet so the original-only
    guarantee does not depend on the model setting the flag.

    Args:
        raw: One discovered post dict.

    Returns:
        True when the post is a retweet.
    """
    if _coerce_bool(raw.get("is_retweet")):
        return True
    return str(raw.get("text") or "").lstrip().startswith("RT @")


def _coerce_bool(value: Any) -> bool:
    """Coerce an untrusted provider flag to a bool, honoring stringified booleans.

    ``bool("false")`` is ``True`` in Python, so a model that stringifies the flag as
    ``"false"`` / ``"0"`` must not be read as truthy. A real bool passes through; a
    string is true only when it spells an affirmative; anything else falls back to
    Python truthiness.

    Args:
        value: The raw flag (bool, str, or missing/other).

    Returns:
        The coerced boolean.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "t"}
    return bool(value)


def _verify_and_gate_themes(
    raw_themes: list[dict[str, Any]], original_posts: list[SweptPost]
) -> list[ClusterTheme]:
    """Verify each candidate theme against real posts, then apply the multi-handle gate.

    For each proposed theme, keep only the ``supporting_tweet_urls`` that exist in the
    swept post set, recompute ``supporting_handles`` from those real posts (never trust
    the model's self-reported handles), and DROP the theme unless it traces to >=
    ``MIN_THEME_HANDLES`` distinct handles. This makes attribution traceable AND
    enforces multi-handle support deterministically — a single loud handle's thread,
    however voluminous, cannot mint a theme.

    Args:
        raw_themes: The model's proposed themes (``theme_summary`` +
            ``supporting_tweet_urls``; any self-reported handles are ignored).
        original_posts: The real original posts the sweep found.

    Returns:
        The kept themes, attribution-verified and multi-handle-gated.
    """
    post_by_url = {p.tweet_url: p for p in original_posts}
    kept: list[ClusterTheme] = []
    for raw in raw_themes:
        if not isinstance(raw, dict):
            continue
        summary = str(raw.get("theme_summary") or "").strip()[:_THEME_SUMMARY_MAX_CHARS]
        if not summary:
            continue

        claimed_urls = raw.get("supporting_tweet_urls")
        if not isinstance(claimed_urls, list):
            continue

        real_urls: list[str] = []
        handles_seen: dict[str, str] = {}
        for url in claimed_urls:
            post = post_by_url.get(str(url or "").strip())
            if post is None:
                continue
            real_urls.append(post.tweet_url)
            handles_seen.setdefault(post.author_handle.lower(), post.author_handle)

        # The multi-handle gate: >= 2 DISTINCT real handles or the theme is dropped.
        if len(handles_seen) < MIN_THEME_HANDLES:
            continue

        kept.append(
            ClusterTheme(
                theme_summary=summary,
                supporting_handles=list(handles_seen.values()),
                supporting_tweet_urls=real_urls,
            )
        )
    return kept


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp into a UTC-aware datetime, or None.

    Args:
        value: The raw ``published_utc`` from a discovered post.

    Returns:
        A UTC-aware datetime, or None when missing / unparseable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# Shared storage (injected Supabase client — mocked at the boundary in tests)
# ----------------------------------------------------------------------


def load_cluster_sweeps(
    db_client: Any, cluster_ids: list[str], sweep_date: date
) -> dict[str, ClusterSweepResult]:
    """Load the stored sweeps for several clusters on one day, in ONE query.

    The batch-read companion to :func:`_load_existing_sweep` (slice #31 — the daily
    batch reads today's shared themes for every followed cluster with a single
    ``.in_()`` round-trip, no per-cluster N+1). A cluster with no stored sweep for
    ``sweep_date`` is simply absent from the result (an honest "not swept today").
    DB errors are NOT swallowed here — the caller (the batch wiring) owns the
    degrade-loudly decision.

    Args:
        db_client: The injected Supabase client.
        cluster_ids: The clusters to load sweeps for.
        sweep_date: The calendar day.

    Returns:
        ``{cluster_id: ClusterSweepResult}`` for the clusters swept on that day.
    """
    if not cluster_ids:
        return {}
    response = (
        db_client.table(_SWEEPS_TABLE)
        .select(
            "cluster_id,sweep_date,handle_count,raw_post_count,original_post_count,themes"
        )
        .in_("cluster_id", cluster_ids)
        .eq("sweep_date", sweep_date.isoformat())
        .execute()
    )
    rows = getattr(response, "data", None) or []
    results = [_row_to_result(row) for row in rows]
    return {result.cluster_id: result for result in results}


def _load_existing_sweep(
    db_client: Any, cluster_id: str, sweep_date: date
) -> ClusterSweepResult | None:
    """Read an existing same-day sweep for a cluster, or None.

    The once-per-day existence check (issue #23 dedup AC). Any DB error is swallowed
    into None (treated as "not yet swept") so a read blip cannot crash the batch — the
    unique constraint still prevents a duplicate write.

    Args:
        db_client: The injected Supabase client.
        cluster_id: The cluster to check.
        sweep_date: The calendar day.

    Returns:
        The stored :class:`ClusterSweepResult`, or None when absent / on read error.
    """
    try:
        response = (
            db_client.table(_SWEEPS_TABLE)
            .select(
                "cluster_id,sweep_date,handle_count,raw_post_count,original_post_count,themes"
            )
            .eq("cluster_id", cluster_id)
            .eq("sweep_date", sweep_date.isoformat())
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — a read blip is "not yet swept", not a crash
        logger.warning(
            "x_cluster_sweep_read_failed",
            sweep=_SWEEP_NAME,
            cluster_id=cluster_id,
            error_message=str(exc)[:200],
            fix_suggestion="Existence read failed; treated as not-yet-swept. The "
            "(cluster_id, sweep_date) unique constraint still blocks a duplicate write.",
        )
        return None

    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    return _row_to_result(rows[0])


def _store_sweep(db_client: Any, result: ClusterSweepResult) -> None:
    """Upsert a sweep row (shared, keyed by cluster_id + sweep_date).

    A single upsert on the ``(cluster_id, sweep_date)`` unique constraint — atomic, so
    there is never a half-written or duplicated sweep. Never called on a provider
    failure (an unpersisted day can be retried).

    Args:
        db_client: The injected Supabase client.
        result: The sweep outcome to persist.
    """
    row = {
        "cluster_id": result.cluster_id,
        "sweep_date": result.sweep_date.isoformat(),
        "handle_count": result.handle_count,
        "raw_post_count": result.raw_post_count,
        "original_post_count": result.original_post_count,
        "themes": [theme.model_dump() for theme in result.themes],
    }
    db_client.table(_SWEEPS_TABLE).upsert(
        row, on_conflict="cluster_id,sweep_date"
    ).execute()


def _row_to_result(row: dict[str, Any]) -> ClusterSweepResult:
    """Rehydrate a stored sweep row into a :class:`ClusterSweepResult`.

    Args:
        row: One ``x_cluster_sweeps`` row (``themes`` may be a JSON string or list).

    Returns:
        The rehydrated result (themes tolerant of a stringified jsonb column).
    """
    raw_themes = row.get("themes")
    if isinstance(raw_themes, str):
        try:
            raw_themes = json.loads(raw_themes)
        except (json.JSONDecodeError, ValueError):
            raw_themes = []
    themes: list[ClusterTheme] = []
    for stored in raw_themes or []:
        if not isinstance(stored, dict) or not stored.get("theme_summary"):
            continue
        # Reason: a partial/legacy stored theme (missing a support list) must degrade
        # to a skip, not crash the cached read — the lists default to empty.
        themes.append(
            ClusterTheme(
                theme_summary=str(stored.get("theme_summary")),
                supporting_handles=list(stored.get("supporting_handles") or []),
                supporting_tweet_urls=list(stored.get("supporting_tweet_urls") or []),
            )
        )
    return ClusterSweepResult(
        cluster_id=str(row["cluster_id"]),
        sweep_date=_coerce_date(row.get("sweep_date")),
        handle_count=int(row.get("handle_count") or 0),
        raw_post_count=int(row.get("raw_post_count") or 0),
        original_post_count=int(row.get("original_post_count") or 0),
        themes=themes,
    )


def _coerce_date(value: Any) -> date:
    """Coerce a stored ``sweep_date`` (date or ISO string) into a ``date``.

    Args:
        value: The raw ``sweep_date`` from a stored row.

    Returns:
        A ``date`` (falls back to today UTC only if the column is unexpectedly empty).
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    return datetime.now(timezone.utc).date()


# ----------------------------------------------------------------------
# Real xAI seams (default impls — never exercised in tests, which inject stubs)
# ----------------------------------------------------------------------


class _RealSeams:
    """The real xAI-backed default seams (batched x_search + theme grouping).

    Kept in a tiny holder so both defaults share one Settings/timeout without
    re-reading env. Delegates the transport + response parsing to the shared
    ``xai_client`` (attributed to ``_SWEEP_NAME`` so failures log as the sweep, not
    another adapter); the key is read only inside ``post_xai`` and never logged.
    """

    def __init__(
        self, settings: Settings, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds

    async def discover_posts(
        self, handles: list[str], sweep_date: date, max_posts: int
    ) -> list[dict[str, Any]]:
        """One batched xAI ``x_search`` call scoped to all of the cluster's handles."""
        prompt = (
            "Using x_search across these X/Twitter accounts "
            f"({', '.join('@' + h for h in handles)}), find their most recent "
            f"substantive ORIGINAL posts on and after {sweep_date.isoformat()} "
            f"(up to {max_posts} total). EXCLUDE pure retweets. Respond with ONLY a "
            "JSON array (no prose, no markdown fences); each element an object with "
            'keys: "tweet_url" (canonical https://x.com/<handle>/status/<id>), '
            '"author_handle" (the posting handle, no @), "text" (full post text), '
            '"published_utc" (ISO-8601 UTC), "is_retweet" (boolean). '
            "If none posted, respond with []."
        )
        tool: dict[str, Any] = {
            "type": "x_search",
            "allowed_x_handles": handles,
            "from_date": sweep_date.isoformat(),
        }
        body = await post_xai(
            self._settings,
            prompt,
            source_name=_SWEEP_NAME,
            tools=[tool],
            timeout_seconds=self._timeout_seconds,
        )
        return parse_xai_response(body, source_name=_SWEEP_NAME)

    async def extract_themes(self, posts: list[SweptPost]) -> list[dict[str, Any]]:
        """One xAI chat call grouping the ORIGINAL posts into cross-handle themes."""
        rendered = json.dumps(
            [
                {"tweet_url": p.tweet_url, "handle": p.author_handle, "text": p.text}
                for p in posts
            ]
        )
        prompt = (
            "You are grouping X posts from DIFFERENT accounts into shared themes. A "
            "theme is a topic that AT LEAST TWO DIFFERENT handles are posting about — "
            "one handle's thread, however long, is NOT a theme. For each theme, cite "
            "the exact tweet_url values (from the input) that support it. Respond with "
            "ONLY a JSON array (no prose, no fences); each element an object with keys: "
            '"theme_summary" (one line) and "supporting_tweet_urls" (array of tweet_url '
            f"strings taken verbatim from the input). Input posts: {rendered}"
        )
        body = await post_xai(
            self._settings,
            prompt,
            source_name=_SWEEP_NAME,
            tools=None,
            timeout_seconds=self._timeout_seconds,
        )
        return parse_xai_response(body, source_name=_SWEEP_NAME)
