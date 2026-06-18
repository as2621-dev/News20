"""Followed-source ingestion pipeline (Phase 5d SP3) — sources → per-user pool.

The source-axis twin of ``interest_keyed_pipeline.py``. Where that pipeline fans
out a NEWS query per active interest, this one polls a user's *followed sources*
(YouTube channels / X accounts) and promotes their fresh content into the same
deduped story pool the existing produce pipeline (``produce_gate`` →
``orchestrate_story``) consumes — so a followed channel's new upload becomes a
reel sitting alongside topic-driven news.

Flow (``run_source_ingestion``):
  1. Take the user's active followed sources (loaded by the SP4 orchestrator from
     ``user_content_sources ⋈ content_sources`` and INJECTED here, so the pipeline
     stays pure / unit-testable — mirroring ``interest_keyed_pipeline`` taking the
     followed-interest ids, not reading Supabase itself).
  2. **Cadence-filter** each source via :class:`CadenceScheduler` (YouTube 6h / X
     6h) against its ``last_fetched_at`` — a source polled within its window is
     skipped (no re-fetch).
  3. **Dispatch the right adapter** by ``content_source_type``
     (``youtube_channel`` → :class:`YouTubeAdapter`, ``x_account`` →
     :class:`XAccountAdapter`) via the source-keyed ``fetch_new_items`` entry point.
  4. **Dedup** the fetched items (drop already-ingested + intra-batch repeats) via
     :func:`dedup_source_items`.
  5. **Promote** substantive items into a deduped :class:`CanonicalStory` pool,
     each tagged to ``user_id`` (so the gate/orchestrator treat them like news
     candidates) and carrying its source image so the poster stage skips Nano
     Banana Pro generation.

Source-origin marking: a promoted story is recognised downstream purely by its
outlet domain (``youtube.com`` / ``x.com``, set by the adapters) — see
``agents/ingestion/dedup.is_source_origin_domain``. No extra model flag is needed.

The function is pure over its injected inputs (the followed-source list, the
adapters, the scheduler, the dedup key set, the clock) — no DB, no network — so it
is fully unit-testable with mocked adapters (CLAUDE.md mandate).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agents.ingestion.adapters.x_account import XAccountAdapter
from agents.ingestion.adapters.youtube import YouTubeAdapter
from agents.ingestion.dedup import (
    StoryClusterer,
    dedup_source_items,
    is_source_origin_domain,
    normalize_url,
    source_item_dedup_key,
)
from agents.ingestion.models import CandidateStory, CanonicalStory
from agents.ingestion.scheduler import CadenceScheduler
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("ingestion.source_pipeline")

# Reason: the content_sources.content_source_type enum values (migration 0009) the
# two shipped adapters handle. 'podcast' / 'personality' are out of scope this
# phase (plans/phase-5d-source-ingestion.md) — a followed source of those types is
# skipped (logged) rather than dispatched, never crashing the user's batch.
SOURCE_TYPE_YOUTUBE = "youtube_channel"
SOURCE_TYPE_X = "x_account"

# Reason: a YouTube transcript / X post must carry at least this many characters of
# body text to be "substantive" enough to produce a digest from (plans Open Q2 —
# the floor that keeps trivial / empty-caption items out of the pool). Conservative
# first draft; the control surface (5e) tunes "only their big stuff" on top of this.
_MIN_SUBSTANTIVE_BODY_CHARS = 80


class FollowedSource(BaseModel):
    """One of a user's active followed sources — the unit this pipeline polls.

    Built by the SP4 orchestrator from a ``user_content_sources ⋈ content_sources``
    read and injected into :func:`run_source_ingestion` (so this module never
    touches Supabase). Carries exactly the fields the cadence + dispatch need.

    Attributes:
        source_id: ``content_sources.source_id`` (the catalog row id).
        content_source_type: ``content_sources.content_source_type`` — drives which
            adapter is dispatched (``youtube_channel`` / ``x_account``).
        external_id: ``content_sources.external_id`` — the channel id (YouTube) or
            handle (X) the adapter's ``fetch_new_items`` is keyed on.
        source_name: ``content_sources.source_name`` (for logging / attribution).
        last_fetched_at: ``content_sources.last_fetched_at`` — when this source was
            last polled (None = never), the cadence gate's input.

    Example:
        >>> src = FollowedSource(
        ...     source_id="s1",
        ...     content_source_type="youtube_channel",
        ...     external_id="UCabc",
        ...     source_name="Some Channel",
        ... )
        >>> src.external_id
        'UCabc'
    """

    source_id: str = Field(..., description="content_sources.source_id")
    content_source_type: str = Field(
        ...,
        description="content_sources.content_source_type (youtube_channel / x_account)",
    )
    external_id: str = Field(
        ..., description="content_sources.external_id (channel id or handle)"
    )
    source_name: str = Field(default="", description="content_sources.source_name")
    last_fetched_at: datetime | None = Field(
        default=None,
        description="content_sources.last_fetched_at (None = never polled)",
    )


class PromotedSourceStory(BaseModel):
    """A deduped source-origin story promoted into the per-user pool.

    Pairs the :class:`CanonicalStory` (the produce-pipeline unit) with the source
    it came from and the user it is tagged to — the association the SP4 orchestrator
    persists (``content_source_items`` + the user→story tag) and that produce_gate /
    orchestrate_story consume as a candidate.

    Attributes:
        story: The deduped canonical story (outlet domain marks it source-origin).
        user_id: The user this story is tagged to (the follower of ``source_id``).
        source_id: The ``content_sources.source_id`` it was ingested from.
        source_image_url: The video thumbnail / tweet-screenshot path the poster
            stage uses INSTEAD of generating a Nano Banana poster (may be None).

    Example:
        >>> # built internally by run_source_ingestion
    """

    story: CanonicalStory = Field(..., description="The deduped canonical story")
    user_id: str = Field(..., description="The user this story is tagged to")
    source_id: str = Field(..., description="The content_sources.source_id of origin")
    source_image_url: str | None = Field(
        default=None,
        description="Thumbnail / tweet-screenshot the poster stage uses (skip generation)",
    )


class SourceIngestionResult(BaseModel):
    """The output of one user's followed-source ingestion run (no DB writes).

    Attributes:
        user_id: The user the run was for.
        promoted_stories: Deduped source-origin stories tagged to the user.
        polled_source_ids: Sources that were actually polled (cadence-due).
        skipped_not_due_source_ids: Sources skipped because cadence had not elapsed.
        failed_source_ids: Sources whose adapter fetch failed (skipped, not fatal).
        items_fetched: Raw items fetched across all polled sources (pre-dedup).
        items_dropped_dedup: Items dropped as already-ingested / intra-batch repeats.
        items_dropped_not_substantive: Items dropped for too-short body text.

    Example:
        >>> result = SourceIngestionResult(user_id="u1")
        >>> result.items_fetched
        0
    """

    user_id: str = Field(..., description="The user this ingestion run was for")
    promoted_stories: list[PromotedSourceStory] = Field(
        default_factory=list, description="Deduped source stories tagged to the user"
    )
    polled_source_ids: list[str] = Field(
        default_factory=list, description="Sources actually polled (cadence-due)"
    )
    skipped_not_due_source_ids: list[str] = Field(
        default_factory=list, description="Sources skipped (cadence window not elapsed)"
    )
    failed_source_ids: list[str] = Field(
        default_factory=list, description="Sources whose adapter fetch failed"
    )
    items_fetched: int = Field(
        default=0, ge=0, description="Raw items fetched (pre-dedup)"
    )
    items_dropped_dedup: int = Field(
        default=0, ge=0, description="Items dropped as already-ingested / repeats"
    )
    items_dropped_not_substantive: int = Field(
        default=0, ge=0, description="Items dropped for too-short body text"
    )


def _is_substantive(candidate: CandidateStory) -> bool:
    """Return True when a fetched item has enough body text to produce a digest.

    A YouTube transcript / X post under :data:`_MIN_SUBSTANTIVE_BODY_CHARS` is
    treated as not worth producing (an empty-caption video the adapter could not
    skip, or a one-word tweet). Logged when dropped (no silent cap).

    Args:
        candidate: A fetched source-origin candidate.

    Returns:
        True when ``candidate_body_text`` clears the substance floor.
    """
    body = (candidate.candidate_body_text or "").strip()
    return len(body) >= _MIN_SUBSTANTIVE_BODY_CHARS


async def run_source_ingestion(
    user_id: str,
    followed_sources: list[FollowedSource],
    *,
    youtube_adapter: YouTubeAdapter | None = None,
    x_adapter: XAccountAdapter | None = None,
    scheduler: CadenceScheduler | None = None,
    clusterer: StoryClusterer | None = None,
    already_ingested_keys: set[str] | None = None,
    now_utc: datetime | None = None,
    mark_source_polled: Callable[[str, datetime], Awaitable[None]] | None = None,
) -> SourceIngestionResult:
    """Poll a user's followed sources and promote fresh items into the story pool.

    For each cadence-due source it dispatches the type's adapter
    (``youtube_channel`` → YouTube RSS+yt-dlp, ``x_account`` → xAI+screenshot) via
    ``fetch_new_items(external_id, since)``, where ``since`` is the source's
    ``last_fetched_at`` (or a bounded cold-start lookback for a never-polled
    source). Fetched items are deduped (already-ingested + intra-batch), filtered to
    substantive ones, clustered into :class:`CanonicalStory` units, and returned
    tagged to ``user_id`` — ready for ``produce_gate`` (which auto-exempts
    source-origin stories) and ``orchestrate_story`` (whose poster stage uses the
    supplied image). One source's adapter failure is logged + skipped, never fatal.

    Args:
        user_id: The user whose followed sources are polled (stories tag to them).
        followed_sources: The user's active followed sources (injected; the SP4
            orchestrator builds these from ``user_content_sources ⋈ content_sources``).
        youtube_adapter: Optional injected :class:`YouTubeAdapter` (tests mock it);
            a default is built when YouTube sources are present and none is given.
        x_adapter: Optional injected :class:`XAccountAdapter` (tests mock it); a
            default is built when X sources are present and none is given.
        scheduler: Optional :class:`CadenceScheduler` (defaults to YouTube 6h / X 6h).
        clusterer: Optional :class:`StoryClusterer` for promotion (defaults standard).
        already_ingested_keys: Dedup keys of items ingested on a prior run
            (``content_source_items`` / ``story_url_aliases``); None → pure path.
        now_utc: Current time for cadence + cold-start (injected; defaults to now).
        mark_source_polled: Optional async callback invoked as
            ``await mark_source_polled(source_id, now)`` immediately after a source
            is successfully polled (its adapter fetch returned without raising). The
            SP4 orchestrator injects a callback that stamps
            ``content_sources.last_fetched_at = now`` so the :class:`CadenceScheduler`
            throttle actually engages on the next run; None keeps this pipeline pure
            (no DB) for unit tests. A failed poll does NOT mark the source polled (so
            a transient failure does not suppress the next attempt).

    Returns:
        A :class:`SourceIngestionResult` — the promoted per-user pool + run counts.

    Example:
        >>> # result = await run_source_ingestion("u1", sources, youtube_adapter=mock)
    """
    now = now_utc or datetime.now(timezone.utc)
    scheduler = scheduler or CadenceScheduler()
    clusterer = clusterer or StoryClusterer()

    result = SourceIngestionResult(user_id=user_id)
    logger.info(
        "source_ingestion_started",
        user_id=user_id,
        followed_source_count=len(followed_sources),
    )

    fetched: list[CandidateStory] = []
    # Reason: track which source each fetched item came from so the promoted story
    # can be paired back to its source_id (the user→story tag the orchestrator writes).
    source_id_by_item_key: dict[str, str] = {}

    for source in followed_sources:
        if not scheduler.is_source_due(
            source.content_source_type, source.last_fetched_at, now
        ):
            result.skipped_not_due_source_ids.append(source.source_id)
            logger.info(
                "source_skipped_not_due",
                user_id=user_id,
                source_id=source.source_id,
                content_source_type=source.content_source_type,
                last_fetched_at=(
                    source.last_fetched_at.isoformat()
                    if source.last_fetched_at
                    else None
                ),
            )
            continue

        adapter = _resolve_adapter(
            source.content_source_type, youtube_adapter, x_adapter
        )
        if adapter is None:
            # Out-of-scope type (podcast / personality) — skip, do not crash.
            logger.warning(
                "source_unsupported_type",
                user_id=user_id,
                source_id=source.source_id,
                content_source_type=source.content_source_type,
                fix_suggestion="Only youtube_channel + x_account are ingested this "
                "phase; podcast/personality are out of scope (plans/phase-5d).",
            )
            continue
        # Lazily build the default adapter for this type if the caller injected none.
        if adapter is _BUILD_YOUTUBE:
            # Reason: the real (non-test) path picks up cookie + self-pacing config
            # from env via from_settings so cloud runs survive YouTube IP-throttling
            # (the "1/7 channels" failure). Tests inject a mock adapter and never hit
            # this branch, so the pipeline stays pure for them.
            youtube_adapter = youtube_adapter or YouTubeAdapter.from_settings(
                Settings()
            )
            adapter = youtube_adapter
        elif adapter is _BUILD_X:
            x_adapter = x_adapter or XAccountAdapter()
            adapter = x_adapter

        since = scheduler.fetch_since(
            source.content_source_type, source.last_fetched_at, now
        )
        try:
            items = await adapter.fetch_new_items(source.external_id, since)
        except AdapterFetchError as exc:
            # Reason: one source's fetch failure must not abort the user's batch
            # (mirrors interest_keyed_pipeline's per-interest skip). Fail loud.
            result.failed_source_ids.append(source.source_id)
            logger.warning(
                "source_fetch_failed",
                user_id=user_id,
                source_id=source.source_id,
                content_source_type=source.content_source_type,
                error_message=str(exc)[:300],
                fix_suggestion="Source fetch failed; this source is skipped this run.",
            )
            continue

        result.polled_source_ids.append(source.source_id)
        # Reason (Phase 5d SP4): stamp content_sources.last_fetched_at = now right
        # after a successful poll so the CadenceScheduler window engages next run
        # (without this write-back, every run re-fetches — cadence cannot throttle
        # in production). Only fires on success; a failed fetch (handled above) does
        # NOT mark the source polled, so the source is retried next run. The actual
        # DB write is the injected callback's job — this module stays DB-free.
        if mark_source_polled is not None:
            await mark_source_polled(source.source_id, now)
        for item in items:
            source_id_by_item_key.setdefault(
                source_item_dedup_key(item), source.source_id
            )
        fetched.extend(items)
        logger.info(
            "source_fetched",
            user_id=user_id,
            source_id=source.source_id,
            content_source_type=source.content_source_type,
            items_fetched=len(items),
        )

    result.items_fetched = len(fetched)

    # --- Dedup (already-ingested + intra-batch repeats) ---
    deduped = dedup_source_items(fetched, already_ingested_keys=already_ingested_keys)
    result.items_dropped_dedup = len(fetched) - len(deduped)

    # --- Substance filter (drop empty-caption / one-word items) ---
    substantive = [c for c in deduped if _is_substantive(c)]
    result.items_dropped_not_substantive = len(deduped) - len(substantive)
    if result.items_dropped_not_substantive:
        logger.info(
            "source_items_dropped_not_substantive",
            user_id=user_id,
            dropped=result.items_dropped_not_substantive,
            min_body_chars=_MIN_SUBSTANTIVE_BODY_CHARS,
        )

    # --- Promote: cluster into canonical stories tagged to the user ---
    # Reason: reuse the SAME clusterer the news pool uses so promoted stories are
    # the identical CanonicalStory shape produce_gate/orchestrator already consume
    # (Rule 5 — wiring, not a reimplementation). Source items rarely cluster (one
    # upload/tweet is distinct), so this is mostly 1:1, but it normalizes the shape.
    canonical_stories = clusterer.cluster_candidates(substantive)

    promoted: list[PromotedSourceStory] = []
    for story in canonical_stories:
        # Defensive: every promoted story must be recognised as source-origin
        # downstream (it drives the produce-gate exemption + poster skip).
        if not is_source_origin_domain(story.canonical_primary_outlet_domain):
            logger.warning(
                "promoted_story_not_source_origin",
                user_id=user_id,
                story_id=story.canonical_story_id,
                outlet_domain=story.canonical_primary_outlet_domain,
                fix_suggestion="A promoted source story has a non-source outlet "
                "domain; check the adapter sets youtube.com / x.com.",
            )
        # Resolve the source_id from the representative member's dedup key.
        rep_key = _normalized_key(story.canonical_representative_external_id)
        source_id = source_id_by_item_key.get(rep_key, "")
        promoted.append(
            PromotedSourceStory(
                story=story,
                user_id=user_id,
                source_id=source_id,
                source_image_url=story.canonical_social_image_url,
            )
        )

    result.promoted_stories = promoted
    logger.info(
        "source_ingestion_completed",
        user_id=user_id,
        polled_sources=len(result.polled_source_ids),
        skipped_not_due=len(result.skipped_not_due_source_ids),
        failed_sources=len(result.failed_source_ids),
        items_fetched=result.items_fetched,
        items_dropped_dedup=result.items_dropped_dedup,
        items_dropped_not_substantive=result.items_dropped_not_substantive,
        promoted_stories=len(promoted),
    )
    return result


# Sentinels: "this type maps to an adapter, build the default lazily if not injected".
_BUILD_YOUTUBE = object()
_BUILD_X = object()


def _resolve_adapter(
    content_source_type: str,
    youtube_adapter: YouTubeAdapter | None,
    x_adapter: XAccountAdapter | None,
) -> object | None:
    """Return the adapter (or a build-sentinel) for a source type, or None.

    Returns the injected adapter when present, else a sentinel telling the caller
    to lazily build the default for that type, else None for an out-of-scope type.

    Args:
        content_source_type: The source type to dispatch.
        youtube_adapter: An injected YouTube adapter, or None.
        x_adapter: An injected X adapter, or None.

    Returns:
        The adapter instance, a build sentinel, or None (unsupported type).
    """
    if content_source_type == SOURCE_TYPE_YOUTUBE:
        return youtube_adapter or _BUILD_YOUTUBE
    if content_source_type == SOURCE_TYPE_X:
        return x_adapter or _BUILD_X
    return None


def _normalized_key(external_id: str) -> str:
    """Normalize a representative external_id to its source-item dedup key."""
    return normalize_url(external_id) or external_id
