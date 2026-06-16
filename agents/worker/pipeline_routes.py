"""Authenticated HTTP seam for the daily pipeline + single-user feed assembly.

This router exposes the two server-side pipeline operations over bearer-token-
authenticated HTTP so the onboarding first-run feed (Phase 7b) and the midnight
trigger (Phase 7c) can both drive the SAME Python functions:

  • ``POST /pipeline/daily``           → kick off a full daily run (background, 202).
  • ``POST /feed/assemble-for-user``   → build ONE user's feed from the ready pool.

This is an INTERNAL, server-to-server admin seam: it uses the service-role Supabase
client (bypasses RLS) and a shared bearer token. It is meant to be called by the
Trigger.dev midnight job (Phase 7c) and a server-side proxy for the onboarding
first-run feed (Phase 7b) — the shared secret must NEVER be shipped to a browser/app
client (see the SP3 report + the CSO note for Phase 7b). The router is mounted on the
app in ``agents/worker/main.py``.

Auth: a single shared bearer token, ``PIPELINE_TRIGGER_SECRET`` (read from the
shared :class:`~agents.shared.settings.Settings`, stored as ``SecretStr`` and
never logged). A missing or incorrect token → HTTP 401. The secret is compared
with :func:`hmac.compare_digest` to avoid leaking length/prefix via timing.
"""

from __future__ import annotations

import hmac
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("worker.pipeline")

# Reason: mirror run_live_batch.py defaults so the HTTP seam behaves identically
# to the reference runner when the caller omits these fields.
_DEFAULT_MAX_TOTAL_PRODUCTIONS = 8
_DEFAULT_LOOKBACK_DAYS = 2
_INTEREST_COLS = (
    "interest_id,parent_interest_id,interest_slug,interest_label,depth_level,"
    "interest_segment_slug,interest_search_query"
)


def _build_service_role_supabase() -> Any:
    """Construct the service-role Supabase client both pipeline handlers need.

    Reads ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` from the worker env (the
    service-role key is required to read ANY user's profile and write their
    ``daily_feeds`` rows server-side, bypassing RLS). The import is performed lazily
    by the caller's lazy-import block; this helper only wires the two values so the
    daily-run background body and the single-user assembler build the client the same
    way. Secrets are never logged.

    Returns:
        A configured Supabase client (service-role).
    """
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )


def require_pipeline_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI auth dependency: require a correct ``Bearer`` pipeline token.

    Reads the expected secret from the shared :class:`Settings`
    (``PIPELINE_TRIGGER_SECRET``, a ``SecretStr`` resolved at request time, never
    logged) and compares it to the request's ``Authorization`` header. A missing
    header, a non-``Bearer`` scheme, or a token mismatch raises HTTP 401. The token
    comparison uses :func:`hmac.compare_digest` so a wrong guess cannot be probed
    by timing.

    Args:
        authorization: The raw ``Authorization`` request header, if present.

    Raises:
        HTTPException: HTTP 401 when the bearer token is missing or incorrect, or
            HTTP 500 when the worker has no ``PIPELINE_TRIGGER_SECRET`` configured
            (an unconfigured guard is a deploy error, not an auth failure — fail loud).
    """
    expected_token = Settings().pipeline_trigger_secret.get_secret_value()
    if not expected_token:
        # Reason: an empty configured secret would let ANY token through — refuse to
        # serve rather than silently disable the guard (Rule 12 — fail loud).
        logger.error(
            "pipeline_trigger_secret_missing",
            error_code="pipeline_trigger_secret_missing",
            error_message="PIPELINE_TRIGGER_SECRET is not configured on the worker",
            fix_suggestion="Set PIPELINE_TRIGGER_SECRET in the worker env to enable the pipeline endpoints.",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pipeline endpoints are not configured",
        )

    presented_token = ""
    if authorization and authorization.startswith("Bearer "):
        presented_token = authorization[len("Bearer ") :].strip()

    if not presented_token or not hmac.compare_digest(presented_token, expected_token):
        logger.warning(
            "pipeline_auth_rejected",
            error_code="pipeline_auth_rejected",
            error_message="Missing or incorrect bearer token on a pipeline endpoint",
            fix_suggestion="Send 'Authorization: Bearer <PIPELINE_TRIGGER_SECRET>'.",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing pipeline bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Reason: the guard is applied router-wide so EVERY current and future pipeline
# route inherits auth by default (fail-closed) — no route can be added unguarded
# by omission.
router = APIRouter(dependencies=[Depends(require_pipeline_token)])


class DailyRunRequest(BaseModel):
    """Body for ``POST /pipeline/daily`` — drive a full daily pipeline run.

    The run parameters come from the request (the caller's intent), never the
    worker clock, so a backfill or a re-run targets the date the caller asks for.

    Attributes:
        target_date: The feed date the daily run should produce.
        max_total_productions: Optional overall ceiling on productions for the run;
            ``None`` uses the pipeline default.
        lookback_days: Optional ingestion lookback window in days; ``None`` uses the
            pipeline default.
    """

    target_date: date = Field(
        ..., description="Feed date the daily run should produce."
    )
    max_total_productions: int | None = Field(
        default=None,
        ge=0,
        description="Optional overall ceiling on productions for this run (None = pipeline default).",
    )
    lookback_days: int | None = Field(
        default=None,
        ge=0,
        description="Optional ingestion lookback window in days (None = pipeline default).",
    )


class DailyRunResponse(BaseModel):
    """Response for ``POST /pipeline/daily`` — the accepted run's identity.

    Attributes:
        run_id: Opaque identifier for the scheduled run (for log correlation).
        accepted: Whether the run was accepted and scheduled.
    """

    run_id: str = Field(..., description="Opaque identifier for the scheduled run.")
    accepted: bool = Field(
        ..., description="Whether the run was accepted and scheduled."
    )


class AssembleFeedRequest(BaseModel):
    """Body for ``POST /feed/assemble-for-user`` — build ONE user's feed on demand.

    Attributes:
        user_id: The user whose feed should be assembled and written.
        feed_date: The feed date to assemble for.
    """

    user_id: str = Field(..., min_length=1, description="User whose feed to assemble.")
    feed_date: date = Field(..., description="Feed date to assemble for.")


class AssembleFeedResponse(BaseModel):
    """Response for ``POST /feed/assemble-for-user`` — what was written.

    Attributes:
        allocated_count: Number of stories allocated/written to the user's feed.
        feed_total: The target feed size (slot budget) for the assembly.
    """

    allocated_count: int = Field(
        ..., ge=0, description="Stories allocated/written to the feed."
    )
    feed_total: int = Field(..., ge=0, description="Target feed size (slot budget).")


async def _run_daily(
    target_date: date,
    max_total_productions: int,
    lookback_days: int,
    run_id: str,
) -> None:
    """Build the live clients and await one full ``run_daily_pipeline`` run.

    This is the background body for ``POST /pipeline/daily``. It constructs the SAME
    clients/args that :mod:`scripts.run_live_batch` wires (service-role Supabase, the
    Gemini text + TTS clients, the poster client, the taxonomy lookups, the GDELT
    ingest adapter + ``ingest_fn``) and runs the pipeline with detail enrichment and
    editorial rewrite ON. All heavy imports are performed LAZILY here so the worker
    can cold-start without dragging the whole pipeline into the import graph (this
    also keeps ``/healthz`` and SP4's boot test cheap).

    A run takes minutes; the HTTP handler schedules this on a background task and has
    already returned ``202`` by the time this executes. Any failure is logged with a
    ``fix_suggestion`` (Rule 12 — a background failure must surface, never be silently
    swallowed) and re-raised so the task runner records it.

    Args:
        target_date: The ``daily_feeds.feed_date`` to produce (from the request body).
        max_total_productions: Overall production ceiling (``<=0`` → uncapped).
        lookback_days: GDELT ingestion lookback window in days.
        run_id: The opaque id returned to the caller, for log correlation.
    """
    logger.info(
        "pipeline_daily_run_started",
        run_id=run_id,
        target_date=target_date.isoformat(),
        max_total_productions=max_total_productions,
        lookback_days=lookback_days,
    )
    try:
        # Reason: lazy imports — keep the worker's cold-start import graph small.
        from google import genai

        from agents.ingestion.adapters.gdelt_doc import GdeltDocAdapter
        from agents.ingestion.interest_keyed_pipeline import ingest_active_interests
        from agents.ingestion.models import InterestNode
        from agents.pipeline.daily_batch import (
            build_story_id_resolver,
            run_daily_pipeline,
        )
        from agents.pipeline.llm_clients import LLMClient
        from agents.pipeline.persist_helpers import load_outlets_lookup
        from agents.voice.gemini_tts import GeminiTTSClient

        supabase = _build_service_role_supabase()

        interest_rows = (
            supabase.table("interests").select(_INTEREST_COLS).execute().data or []
        )
        interest_nodes = {
            str(row["interest_id"]): InterestNode(
                interest_id=str(row["interest_id"]),
                parent_interest_id=(
                    str(row["parent_interest_id"])
                    if row.get("parent_interest_id")
                    else None
                ),
                interest_slug=str(row["interest_slug"]),
                interest_label=str(row.get("interest_label") or row["interest_slug"]),
                depth_level=int(row["depth_level"]),
                interest_search_query=row.get("interest_search_query"),
            )
            for row in interest_rows
        }
        interest_segment_lookup = _build_interest_segment_lookup(interest_rows)

        profile_rows = (
            supabase.table("user_interest_profile")
            .select("profile_user_id,profile_interest_id")
            .execute()
            .data
            or []
        )
        followed_ids = sorted({str(r["profile_interest_id"]) for r in profile_rows})

        outlets_lookup = load_outlets_lookup(supabase)
        gdelt_adapter = GdeltDocAdapter()
        resolver = build_story_id_resolver(supabase)
        since_utc = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        async def ingest_fn():  # type: ignore[no-untyped-def]
            result = await ingest_active_interests(
                followed_interest_ids=followed_ids,
                interest_nodes=interest_nodes,
                adapter=gdelt_adapter,
                since_utc=since_utc,
                resolve_existing_story_ids=resolver,
            )
            return result.canonical_stories, result.story_interest_tags

        llm_client = LLMClient()
        tts_client = GeminiTTSClient()
        poster_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

        result = await run_daily_pipeline(
            target_date=target_date,
            supabase_client=supabase,
            llm_client=llm_client,
            tts_client=tts_client,
            ingest_fn=ingest_fn,
            interest_nodes=interest_nodes,
            poster_genai_client=poster_client,
            max_total_productions=max_total_productions,
            enable_detail_enrichment=True,
            enable_editorial_rewrite=True,
            interest_segment_lookup=interest_segment_lookup,
            outlets_lookup=outlets_lookup,
            gdelt_adapter=gdelt_adapter,
        )
        logger.info(
            "pipeline_daily_run_completed",
            run_id=run_id,
            target_date=target_date.isoformat(),
            produced_story_count=result.produced_story_count,
            feeds_written=result.feeds.feeds_written if result.feeds else 0,
        )
    except Exception as exc:  # noqa: BLE001 — log loudly, then re-raise.
        # Reason: a background failure must be surfaced, not swallowed (Rule 12).
        logger.error(
            "pipeline_daily_run_failed",
            run_id=run_id,
            target_date=target_date.isoformat(),
            error_message=str(exc),
            fix_suggestion=(
                "Check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / GEMINI_API_KEY env "
                "on the worker and that interests + user_interest_profile are seeded."
            ),
        )
        raise


def _build_interest_segment_lookup(rows: list[dict]) -> dict[str, str]:
    """Build ``{interest_id: segment_slug}`` by walking each node to its nearest
    ancestor that carries an ``interest_segment_slug`` (mirrors run_live_batch).

    Args:
        rows: All ``interests`` rows (with parent + ``interest_segment_slug``).

    Returns:
        ``{interest_id: segment_slug}`` for every interest that resolves to one.
    """
    by_id = {str(r["interest_id"]): r for r in rows}

    def nearest_segment(start_id: str) -> str | None:
        seen: set[str] = set()
        cursor: str | None = start_id
        while cursor and cursor not in seen:
            seen.add(cursor)
            row = by_id.get(cursor)
            if not row:
                return None
            segment = row.get("interest_segment_slug")
            if segment:
                return str(segment)
            parent = row.get("parent_interest_id")
            cursor = str(parent) if parent else None
        return None

    lookup: dict[str, str] = {}
    for interest_id in by_id:
        segment = nearest_segment(interest_id)
        if segment:
            lookup[interest_id] = segment
    return lookup


def _load_interest_nodes(supabase_client: Any) -> dict[str, Any]:
    """Load the full ``{interest_id: InterestNode}`` taxonomy lookup (scoring input).

    Mirrors the loader in :func:`_run_daily` so single-user scoring uses the SAME
    taxonomy (depth + ancestry) the daily batch scores against.

    Args:
        supabase_client: Service-role Supabase client.

    Returns:
        ``{interest_id: InterestNode}`` for every interest row.
    """
    from agents.ingestion.models import InterestNode

    interest_rows = (
        supabase_client.table("interests").select(_INTEREST_COLS).execute().data or []
    )
    return {
        str(row["interest_id"]): InterestNode(
            interest_id=str(row["interest_id"]),
            parent_interest_id=(
                str(row["parent_interest_id"])
                if row.get("parent_interest_id")
                else None
            ),
            interest_slug=str(row["interest_slug"]),
            interest_label=str(row.get("interest_label") or row["interest_slug"]),
            depth_level=int(row["depth_level"]),
            interest_search_query=row.get("interest_search_query"),
        )
        for row in interest_rows
    }


def _load_ready_story_pool(
    supabase_client: Any,
) -> tuple[list[Any], list[Any]]:
    """Load the GLOBAL ready-story pool + its interest tags from Supabase.

    A "ready" story is one that has a CURRENT digest with both audio and a poster —
    i.e. fully produced and renderable in the reel. The single-user assembler scores
    only over this already-produced pool (no generation happens here), so a partial
    pool (fewer than 30 ready) yields a shorter feed rather than inventing slots.

    The :func:`assemble_user_feed` scorer reads only a few ``CanonicalStory`` fields
    (``canonical_story_id`` for identity/dedup, ``canonical_title`` for entity
    matching, ``story_outlet_count`` for Importance, ``canonical_published_utc`` for
    Freshness), so this loader reconstructs lightweight ``CanonicalStory`` objects
    from ``stories`` rather than re-clustering — the heavy body/source fields are not
    needed for allocation.

    Args:
        supabase_client: Service-role Supabase client.

    Returns:
        ``(stories, story_interest_tags)`` — the ready ``CanonicalStory`` pool and
        every ``story_interests`` edge for those stories. Empty when no story is
        ready (the caller returns ``allocated_count=0`` — not an error).
    """
    from agents.ingestion.models import CanonicalStory, StoryInterestTag

    # Reason: a story is ready ONLY when its current digest carries audio AND a
    # poster — the reel can render it. The partial unique index guarantees at most
    # one current digest per story, so this is one row per ready story.
    digest_rows = (
        getattr(
            supabase_client.table("digests")
            .select("digest_story_id,digest_audio_url,digest_ambient_poster_url")
            .eq("digest_is_current", True)
            .execute(),
            "data",
            None,
        )
        or []
    )
    ready_story_ids = [
        str(row["digest_story_id"])
        for row in digest_rows
        if row.get("digest_audio_url") and row.get("digest_ambient_poster_url")
    ]
    if not ready_story_ids:
        return [], []

    story_rows = (
        getattr(
            supabase_client.table("stories")
            .select(
                "story_id,story_headline,story_primary_outlet_name,"
                "story_outlet_count,story_first_reported_utc"
            )
            .in_("story_id", ready_story_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )

    stories: list[CanonicalStory] = []
    for row in story_rows:
        story_id = str(row["story_id"])
        published_raw = str(row.get("story_first_reported_utc") or "")
        try:
            published_utc = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            # Reason: a malformed/absent timestamp must not drop a ready story —
            # fall back to "now" so it still scores (lowest-impact Freshness default).
            published_utc = datetime.now(timezone.utc)
        outlet_name = row.get("story_primary_outlet_name")
        synthetic_url = f"https://news20.app/{story_id}"
        stories.append(
            CanonicalStory(
                canonical_story_id=story_id,
                canonical_title=str(row.get("story_headline") or story_id),
                canonical_url=synthetic_url,
                canonical_normalized_url=synthetic_url,
                canonical_published_utc=published_utc,
                canonical_primary_outlet_domain=str(outlet_name or "unknown"),
                canonical_primary_outlet_name=(
                    str(outlet_name) if outlet_name else None
                ),
                story_outlet_count=int(row.get("story_outlet_count") or 0),
                member_candidate_ids=[story_id],
            )
        )

    tag_rows = (
        getattr(
            supabase_client.table("story_interests")
            .select(
                "story_interest_story_id,story_interest_interest_id,"
                "story_interest_match_depth,story_interest_relevance"
            )
            .in_("story_interest_story_id", ready_story_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    story_interest_tags = [
        StoryInterestTag(
            story_interest_story_id=str(row["story_interest_story_id"]),
            story_interest_interest_id=str(row["story_interest_interest_id"]),
            story_interest_match_depth=int(row["story_interest_match_depth"]),
            story_interest_relevance=(
                float(row["story_interest_relevance"])
                if row.get("story_interest_relevance") is not None
                else None
            ),
        )
        for row in tag_rows
    ]
    return stories, story_interest_tags


def _load_single_user_inputs(
    supabase_client: Any,
    user_id: str,
    feed_date: date,
) -> Any | None:
    """Load ONE user's allocation inputs (interests / allocation / follows / prior feed).

    Scopes the daily batch's per-user loaders to a single user so the same inputs
    drive single-user assembly without re-running ranking. Returns ``None`` when the
    user has NO ``user_interest_profile`` rows (an unknown / un-onboarded user) so the
    handler can answer 404 — distinct from a known user with an empty ready pool.

    ``prior_feed_story_ids`` is loaded for repeat-day correctness even for a brand-new
    user (it is simply empty for one with no prior feed).

    Args:
        supabase_client: Service-role Supabase client.
        user_id: The user to assemble for.
        feed_date: The feed date (prior feeds are those strictly before it).

    Returns:
        An ``ActiveUserFeedInputs`` for the user, or ``None`` if the user is unknown.
    """
    from agents.pipeline.daily_batch import (
        _load_category_allocation,
        _load_followed_entities,
        _load_prior_feed_story_ids,
    )
    from agents.pipeline.orchestrator import ActiveUserFeedInputs
    from agents.pipeline.stages.ranking import UserProfileInterest

    profile_rows = (
        getattr(
            supabase_client.table("user_interest_profile")
            .select(
                "profile_user_id,profile_interest_id,profile_weight,profile_is_strict"
            )
            .eq("profile_user_id", user_id)
            .execute(),
            "data",
            None,
        )
        or []
    )
    if not profile_rows:
        return None

    profile_interests = [
        UserProfileInterest(
            profile_interest_id=str(row["profile_interest_id"]),
            profile_weight=float(row["profile_weight"]),
            profile_is_strict=bool(row["profile_is_strict"]),
        )
        for row in profile_rows
    ]
    user_ids = [user_id]
    entities_by_user = _load_followed_entities(supabase_client, user_ids)
    allocation_by_user = _load_category_allocation(supabase_client, user_ids)
    prior_by_user = _load_prior_feed_story_ids(supabase_client, user_ids, feed_date)

    return ActiveUserFeedInputs(
        active_user_id=user_id,
        profile_interests=profile_interests,
        followed_entities=entities_by_user.get(user_id, []),
        category_allocation=allocation_by_user.get(user_id, []),
        prior_feed_story_ids=prior_by_user.get(user_id, []),
        exploration_candidates_by_interest={},
    )


async def _assemble_for_user(user_id: str, feed_date: date) -> tuple[int, int]:
    """Build + persist ONE user's feed from the global ready pool. Returns counts.

    Synchronous-fast: a single-user allocation over an ALREADY-produced pool (no
    generation), so the HTTP handler awaits it inline and returns 200. Steps:

      1. Build the service-role Supabase client (same wiring as the daily run).
      2. Load the user's inputs; ``None`` → unknown user (handler answers 404).
      3. Load the global ready-story pool + interest tags + the taxonomy.
      4. ``assemble_user_feed`` (pure ranking/allocation) → ordered slots.
      5. ``write_daily_feed`` (idempotent produce-once on ``(user, date)``).

    An empty ready pool yields zero slots → ``allocated_count=0`` (NOT an error). A
    re-call hits ``write_daily_feed``'s produce-once gate and writes no duplicate
    rows; the count returned then reflects the already-present feed length so the
    caller sees a stable, non-zero answer on repeat.

    Args:
        user_id: The user whose feed to assemble.
        feed_date: The feed date to assemble for.

    Returns:
        ``(allocated_count, feed_total)`` — stories written/already-present and the
        target feed size (N = 30).

    Raises:
        LookupError: When ``user_id`` has no interest profile (unknown user → 404).
    """
    from agents.pipeline.feed_assembly import (
        FEED_SLOT_BUDGET,
        assemble_user_feed,
        write_daily_feed,
    )

    logger.info(
        "feed_assemble_for_user_started",
        user_id=user_id,
        feed_date=feed_date.isoformat(),
    )
    supabase = _build_service_role_supabase()

    user_inputs = _load_single_user_inputs(supabase, user_id, feed_date)
    if user_inputs is None:
        logger.warning(
            "feed_assemble_for_user_unknown_user",
            user_id=user_id,
            feed_date=feed_date.isoformat(),
            error_code="feed_assemble_unknown_user",
            fix_suggestion="User has no user_interest_profile rows; onboard the user "
            "(pick interests) before assembling a feed.",
        )
        raise LookupError(f"No interest profile for user_id={user_id}")

    stories, story_interest_tags = _load_ready_story_pool(supabase)
    interest_nodes = _load_interest_nodes(supabase)

    slots = assemble_user_feed(
        profile_interests=user_inputs.profile_interests,
        stories=stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        followed_entities=user_inputs.followed_entities,
        category_allocation=user_inputs.category_allocation,
        prior_feed_story_ids=set(user_inputs.prior_feed_story_ids),
    )

    write_result = write_daily_feed(
        supabase_client=supabase,
        feed_user_id=user_id,
        feed_date=feed_date,
        slots=slots,
    )
    # Reason: on a fresh write, the count is the rows inserted; on a produce-once
    # skip (re-call), no rows are written but the user already has len(slots) — so
    # surface len(slots) so a repeat call returns the same non-zero count (idempotent
    # answer), never 0 for an already-populated feed.
    allocated_count = (
        len(slots) if write_result.already_present else write_result.slots_written
    )

    logger.info(
        "feed_assemble_for_user_completed",
        user_id=user_id,
        feed_date=feed_date.isoformat(),
        allocated_count=allocated_count,
        ready_pool_size=len(stories),
        already_present=write_result.already_present,
    )
    return allocated_count, FEED_SLOT_BUDGET


@router.post(
    "/pipeline/daily",
    response_model=DailyRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_pipeline_daily(
    request: DailyRunRequest, background_tasks: BackgroundTasks
) -> DailyRunResponse:
    """Schedule a daily pipeline run for ``request.target_date`` and return ``202``.

    A full daily run takes minutes, so this handler does NOT block on it: it generates
    a ``run_id``, enqueues :func:`_run_daily` on a FastAPI background task (the heavy
    pipeline imports happen lazily inside the runner), and returns immediately. The run
    parameters come from the request body (the caller's intent), never the worker
    clock. The auth guard is applied at the router level (see
    :func:`require_pipeline_token`).

    Args:
        request: The :class:`DailyRunRequest` body (target date + optional limits).
        background_tasks: FastAPI's per-request background-task collector.

    Returns:
        A :class:`DailyRunResponse` acknowledging the scheduled run (HTTP 202).
    """
    run_id = uuid.uuid4().hex
    max_total_productions = (
        request.max_total_productions
        if request.max_total_productions is not None
        else _DEFAULT_MAX_TOTAL_PRODUCTIONS
    )
    lookback_days = (
        request.lookback_days
        if request.lookback_days is not None
        else _DEFAULT_LOOKBACK_DAYS
    )

    background_tasks.add_task(
        _run_daily,
        target_date=request.target_date,
        max_total_productions=max_total_productions,
        lookback_days=lookback_days,
        run_id=run_id,
    )

    logger.info(
        "pipeline_daily_run_scheduled",
        run_id=run_id,
        target_date=request.target_date.isoformat(),
        max_total_productions=max_total_productions,
        lookback_days=lookback_days,
    )
    return DailyRunResponse(run_id=run_id, accepted=True)


@router.post(
    "/feed/assemble-for-user",
    response_model=AssembleFeedResponse,
    status_code=status.HTTP_200_OK,
)
async def post_feed_assemble_for_user(
    request: AssembleFeedRequest,
) -> AssembleFeedResponse:
    """Assemble + write ONE user's feed from the global ready story pool (SP3).

    Loads the user's interests / allocation / follows and the global ready-story pool
    (stories with a current digest carrying audio + poster), calls
    :func:`assemble_user_feed` then :func:`write_daily_feed` (idempotent on
    ``(feed_user_id, feed_date)``), and returns the written count. Runs SYNCHRONOUSLY
    — a single-user allocation over an already-produced pool is fast (no generation),
    so the handler awaits it inline and returns 200. The auth guard is applied at the
    router level (see :func:`require_pipeline_token`).

    Partial-friendly: fewer than 30 ready stories → fewer slots, never invented. An
    empty ready pool → ``allocated_count=0`` (a valid 200, not an error). An unknown
    user (no interest profile) → 404.

    Args:
        request: The :class:`AssembleFeedRequest` body (user id + feed date).

    Returns:
        An :class:`AssembleFeedResponse` with the allocated count + target feed size.

    Raises:
        HTTPException: 404 when ``user_id`` has no interest profile; 500 on an
            unexpected assembly/persistence failure (logged with a fix_suggestion).
    """
    try:
        allocated_count, feed_total = await _assemble_for_user(
            request.user_id, request.feed_date
        )
    except LookupError as exc:
        # Reason: an unknown user is a 404, not a 500 — the caller asked for a feed
        # for a user that does not exist / has not onboarded.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interest profile for the requested user_id",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — log loudly, then surface as 500.
        # Reason: a failure here must surface (Rule 12), never be swallowed into a
        # misleading 200 with an empty feed.
        logger.error(
            "feed_assemble_for_user_failed",
            user_id=request.user_id,
            feed_date=request.feed_date.isoformat(),
            error_message=str(exc),
            fix_suggestion=(
                "Check SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY on the worker and that "
                "stories/digests/story_interests are seeded for the ready pool."
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assemble the user's feed",
        ) from exc

    return AssembleFeedResponse(allocated_count=allocated_count, feed_total=feed_total)
