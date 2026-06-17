"""Daily personalized-feed pipeline runner (Phase 1d SP4) — the real executor.

Chains the five stages of the daily batch (ranking-spec §4 → §3), the substance
the Trigger.dev v4 schedule (`trigger/dailyPipeline.ts`) fires:

    A. update interest weights   → agents.memory.session_processor.run_profile_update_job
    B. ingest + tag news         → INJECTED ingest_fn (live GDELT pipeline, or a
                                    fixture pool in the live e2e)
    C. produce digests ONCE      → produce-gate select + orchestrate_story fan-out
    D. score per user            ┐ both inside
    E. allocate ~30-slot feed    ┘ assemble_daily_feeds → daily_feeds

Ingest is injected (not hardcoded) so production uses the live interest-keyed
pipeline while the e2e injects a deterministic, ancestor-tagged fixture pool —
one runner, both paths (CLAUDE.md injection mandate). All heavy clients are
injected too, so the stages are unit-testable with mocks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.memory.session_processor import ProfileUpdateResult, run_profile_update_job
from agents.pipeline.categories import DEFAULT_FEED_ALLOCATION, CategoryAllocation
from agents.pipeline.feed_assembly import ScoredCandidate
from agents.pipeline.orchestrator import (
    DailyFeedsBatchResult,
    ActiveUserFeedInputs,
    assemble_daily_feeds,
    orchestrate_story,
)
from agents.pipeline.produce_caps import (
    cap_stories_per_category,
    compute_category_produce_caps,
    enforce_overall_ceiling,
)
from agents.pipeline.produce_gate import select_stories_to_produce
from agents.pipeline.stages.ranking import (
    FOLLOW_SOURCE_WEIGHT,
    FollowedEntity,
    UserProfileInterest,
)
from agents.shared.logger import get_logger
from agents.voice.gemini_tts import GeminiTTSClient

logger = get_logger("pipeline.daily_batch")

# Reason: a paid per-story render is heavy (TTS + image + 2 LLM passes); bound the
# concurrent fan-out so a large pool does not stampede the LLM/TTS quotas.
DEFAULT_MAX_CONCURRENT_PRODUCTIONS = 4

# Reason: per-category produce cap used ONLY when no active user has any explicit
# user_feed_allocation row (decision: explicit budgets drive the caps; this is the
# safe fallback so a freshly-seeded DB without allocations still stays balanced).
DEFAULT_PER_CATEGORY_CAP = 8

# Type of the injected ingest stage: returns the deduped, ancestor-tagged pool.
IngestFn = Callable[[], Awaitable[tuple[list[CanonicalStory], list[StoryInterestTag]]]]


class DailyPipelineResult(BaseModel):
    """Outcome of one ``run_daily_pipeline`` execution (audit + e2e assertions).

    Attributes:
        feed_date: ISO feed date written.
        profile_update: The §4 weight-update summary (stage A).
        candidate_story_count: Stories in the ingested pool (stage B).
        produced_story_count: Stories produced into digests this run (stage C).
        skipped_by_gate_count: Stories the produce-once gate rejected.
        capped_count: Gate-passed stories the per-category cap then dropped (stage C).
        feeds: The per-user allocation summary (stages D+E).

    Example:
        >>> # See tests/agents/pipeline/test_daily_batch.py for the staged asserts.
    """

    feed_date: str = Field(..., description="ISO feed date written")
    profile_update: ProfileUpdateResult = Field(default_factory=ProfileUpdateResult)
    candidate_story_count: int = Field(default=0, ge=0)
    produced_story_count: int = Field(default=0, ge=0)
    skipped_by_gate_count: int = Field(default=0, ge=0)
    capped_count: int = Field(default=0, ge=0)
    feeds: DailyFeedsBatchResult | None = Field(default=None)


def _load_has_current_digest(
    supabase_client: Any, story_ids: list[str]
) -> dict[str, bool]:
    """Map ``story_id -> True`` for stories that already have a current digest.

    The produce-once economics: a story with a ``digest_is_current = true`` row is
    not re-produced. Missing ids default to False in the gate.
    """
    if not story_ids:
        return {}
    # Reason: chunk the .in_() so a large candidate pool (BigQuery ingest emits
    # ~1000s of canonical stories) doesn't overflow the request URL length.
    chunk_size = 150
    has_digest: dict[str, bool] = {}
    for start in range(0, len(story_ids), chunk_size):
        chunk = story_ids[start : start + chunk_size]
        rows = (
            getattr(
                supabase_client.table("digests")
                .select("digest_story_id")
                .in_("digest_story_id", chunk)
                .eq("digest_is_current", True)
                .execute(),
                "data",
                None,
            )
            or []
        )
        for row in rows:
            has_digest[str(row["digest_story_id"])] = True
    return has_digest


def _load_prior_feed_story_ids(
    supabase_client: Any, user_ids: list[str], target_date: date
) -> dict[str, list[str]]:
    """Load every active user's prior-feed story ids in ONE query (§3.8).

    Replaces a per-user ``daily_feeds`` read (an N+1) with a single ``.in_()``
    over all active users, grouped in memory. ``feed_date < target_date`` so only
    earlier days count as "already shown".

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids to load prior feeds for.
        target_date: The feed date being built (prior = strictly before it).

    Returns:
        ``{user_id: [prior feed_story_id, ...]}`` (users with none are absent).
    """
    if not user_ids:
        return {}
    rows = (
        getattr(
            supabase_client.table("daily_feeds")
            .select("feed_user_id,feed_story_id")
            .in_("feed_user_id", user_ids)
            .lt("feed_date", target_date.isoformat())
            .execute(),
            "data",
            None,
        )
        or []
    )
    prior_by_user: dict[str, list[str]] = {}
    for row in rows:
        prior_by_user.setdefault(str(row["feed_user_id"]), []).append(
            str(row["feed_story_id"])
        )
    return prior_by_user


def _load_followed_entities(
    supabase_client: Any, user_ids: list[str]
) -> dict[str, list[FollowedEntity]]:
    """Hydrate every active user's followed entities in ONE join query (phase-5a SP2).

    Joins ``user_entity_follows`` to ``entities`` (migration 0007) so each follow
    carries the entity's identity (label / ticker / kind) the EntityBonus matcher
    needs. PostgREST embeds the joined ``entities`` row under the FK relationship.
    Encodes the **custom > more > seed** source weighting HERE (the DB stores
    ``follow_weight = 1.0`` for every source — SP1 report §7.3): each follow's
    weight is multiplied by ``FOLLOW_SOURCE_WEIGHT[follow_source]`` so a custom
    follow normalizes higher than a seed follow downstream.

    A single ``.in_()`` over all active users (grouped in memory) — one round-trip,
    not one per user (avoids the N+1 the prior-feed loader already avoids).

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids to load entity follows for.

    Returns:
        ``{user_id: [FollowedEntity, ...]}`` (users with no follows are absent).
    """
    if not user_ids:
        return {}
    rows = (
        getattr(
            supabase_client.table("user_entity_follows")
            .select(
                "follow_user_id,entity_id,follow_source,follow_weight,follow_path,"
                "entities(entity_label,entity_ticker,entity_kind)"
            )
            .in_("follow_user_id", user_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    entities_by_user: dict[str, list[FollowedEntity]] = {}
    for row in rows:
        entity = row.get("entities") or {}
        # Reason: a follow whose joined entity row is missing (orphan FK) cannot be
        # matched (no label) — skip it rather than fabricate an empty-label entity.
        entity_label = entity.get("entity_label")
        if not entity_label:
            logger.warning(
                "load_followed_entities_orphan_skipped",
                follow_user_id=row.get("follow_user_id"),
                entity_id=row.get("entity_id"),
                fix_suggestion="user_entity_follows row has no joined entities row; "
                "skipped (cannot match a labelless entity).",
            )
            continue
        # Reason: encode custom>more>seed by multiplying the DB's flat 1.0 weight by
        # the source multiplier (default 1.0 for an unknown source — fail safe).
        source = str(row.get("follow_source") or "seed")
        base_weight = float(row.get("follow_weight") or 1.0)
        source_weight = base_weight * FOLLOW_SOURCE_WEIGHT.get(source, 1.0)
        entities_by_user.setdefault(str(row["follow_user_id"]), []).append(
            FollowedEntity(
                entity_id=str(row["entity_id"]),
                entity_label=str(entity_label),
                entity_ticker=(
                    str(entity["entity_ticker"])
                    if entity.get("entity_ticker")
                    else None
                ),
                entity_kind=str(entity.get("entity_kind") or "org"),
                follow_weight=source_weight,
                follow_path=[str(p) for p in (row.get("follow_path") or [])],
            )
        )
    return entities_by_user


def _load_active_user_ids(supabase_client: Any) -> list[str]:
    """Load the distinct active user ids (those with ≥1 interest profile row).

    The per-category produce cap needs every active user's allocation BEFORE the
    produce gate runs (Stage C), so it loads the active-user set here rather than
    waiting for the Stage D :func:`load_active_user_inputs`. An active user = one
    with at least one ``user_interest_profile`` row (same definition Stage D uses).

    Args:
        supabase_client: Service-role client (injected; mocked in tests).

    Returns:
        The distinct active user ids (deterministic order: sorted).
    """
    rows = (
        getattr(
            supabase_client.table("user_interest_profile")
            .select("profile_user_id")
            .execute(),
            "data",
            None,
        )
        or []
    )
    return sorted({str(row["profile_user_id"]) for row in rows})


def _load_category_allocation(
    supabase_client: Any, user_ids: list[str]
) -> dict[str, list[CategoryAllocation]]:
    """Load every active user's ``user_feed_allocation`` rows in ONE query (phase-5a).

    The Layer-1 per-category slot budgets + manual sequence (migration 0008) the
    SP3 allocator reads. One ``.in_()`` over all active users, grouped in memory.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids to load allocations for.

    Returns:
        ``{user_id: [CategoryAllocation, ...]}`` (users with no allocation are
        absent — SP3 applies a balanced default for them).
    """
    if not user_ids:
        return {}
    rows = (
        getattr(
            supabase_client.table("user_feed_allocation")
            .select(
                "follow_user_id,allocation_category,allocation_slot_count,"
                "allocation_sort_order"
            )
            .in_("follow_user_id", user_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    allocation_by_user: dict[str, list[CategoryAllocation]] = {}
    for row in rows:
        allocation_by_user.setdefault(str(row["follow_user_id"]), []).append(
            CategoryAllocation(
                allocation_category=str(row["allocation_category"]),
                allocation_slot_count=int(row["allocation_slot_count"]),
                allocation_sort_order=int(row["allocation_sort_order"]),
            )
        )
    return allocation_by_user


def build_story_id_resolver(
    supabase_client: Any,
) -> Callable[[list[str]], dict[str, str]]:
    """Build the cross-day story-id resolver the ingest batch injects (0006).

    Returns a callable that, given normalized URLs, returns the subset already
    aliased to an existing ``stories.story_id`` (one ``.in_()`` query against
    ``story_url_aliases``). Wire this into
    ``ingest_active_interests(resolve_existing_story_ids=...)`` in production so a
    re-clustered multi-day event reuses its original id — keeping produce-once and
    don't-repeat correct across days.

    Args:
        supabase_client: Service-role client (bypasses RLS to read aliases).

    Returns:
        ``(normalized_urls) -> {normalized_url: existing_story_id}``.
    """

    # Reason: a single .in_() of every candidate URL overflows the request URL
    # length once the pool is large (BigQuery ingest returns ~1000s of candidates,
    # vs the DOC API's 250 cap). Chunk the lookup so the GET query string stays
    # well under server/proxy URL limits while preserving produce-once semantics.
    _URL_CHUNK = 150

    def _resolve(normalized_urls: list[str]) -> dict[str, str]:
        if not normalized_urls:
            return {}
        resolved: dict[str, str] = {}
        for start in range(0, len(normalized_urls), _URL_CHUNK):
            chunk = normalized_urls[start : start + _URL_CHUNK]
            rows = (
                getattr(
                    supabase_client.table("story_url_aliases")
                    .select("alias_normalized_url,alias_story_id")
                    .in_("alias_normalized_url", chunk)
                    .execute(),
                    "data",
                    None,
                )
                or []
            )
            for row in rows:
                resolved[str(row["alias_normalized_url"])] = str(row["alias_story_id"])
        return resolved

    return _resolve


def load_active_user_inputs(
    supabase_client: Any,
    target_date: date,
    exploration_by_user: dict[str, dict[str, list[ScoredCandidate]]] | None = None,
) -> list[ActiveUserFeedInputs]:
    """Build one ``ActiveUserFeedInputs`` per active user from Supabase.

    The loader the SP4 allocator was written to consume (it was deferred as "the
    loader's job"). An active user = one with at least one ``user_interest_profile``
    row. Reads each user's followed interests + the story ids already shown to them
    in prior ``daily_feeds`` (the §3.8 don't-repeat exclusion), and attaches any
    pre-built exploration candidates.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        target_date: The feed date being built (prior feeds are those before it).
        exploration_by_user: Optional ``{user_id: {interest_id: [ScoredCandidate]}}``
            adjacent-interest candidates for the ~10% exploration slots.

    Returns:
        One :class:`ActiveUserFeedInputs` per active user.
    """
    exploration_by_user = exploration_by_user or {}
    profile_rows = (
        getattr(
            supabase_client.table("user_interest_profile")
            .select(
                "profile_user_id,profile_interest_id,profile_weight,profile_is_strict"
            )
            .execute(),
            "data",
            None,
        )
        or []
    )
    interests_by_user: dict[str, list[UserProfileInterest]] = {}
    for row in profile_rows:
        interests_by_user.setdefault(str(row["profile_user_id"]), []).append(
            UserProfileInterest(
                profile_interest_id=str(row["profile_interest_id"]),
                profile_weight=float(row["profile_weight"]),
                profile_is_strict=bool(row["profile_is_strict"]),
            )
        )

    # Reason: load EVERY active user's prior-feed story ids in ONE query (the §3.8
    # don't-repeat exclusion), grouped in memory — not one query per user. At 100
    # users this is 1 round-trip instead of 100 (the old per-user loop was an N+1).
    active_user_ids = list(interests_by_user.keys())
    prior_story_ids_by_user = _load_prior_feed_story_ids(
        supabase_client, active_user_ids, target_date
    )
    # phase-5a: hydrate the entity follows (⋈ entities) + per-category allocations,
    # each in ONE batched query keyed by the same active-user set.
    entities_by_user = _load_followed_entities(supabase_client, active_user_ids)
    allocation_by_user = _load_category_allocation(supabase_client, active_user_ids)

    inputs: list[ActiveUserFeedInputs] = []
    for user_id, profile_interests in interests_by_user.items():
        inputs.append(
            ActiveUserFeedInputs(
                active_user_id=user_id,
                profile_interests=profile_interests,
                followed_entities=entities_by_user.get(user_id, []),
                category_allocation=allocation_by_user.get(user_id, []),
                prior_feed_story_ids=prior_story_ids_by_user.get(user_id, []),
                exploration_candidates_by_interest=exploration_by_user.get(user_id, {}),
            )
        )
    logger.info("load_active_user_inputs_completed", active_user_count=len(inputs))
    return inputs


async def _produce_story_pool(
    stories_to_produce: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    llm_client: Any,
    tts_client: GeminiTTSClient,
    supabase_client: Any,
    poster_genai_client: Any | None,
    max_concurrent: int,
    enable_detail_enrichment: bool = False,
    enable_editorial_rewrite: bool = False,
    interest_segment_lookup: dict[str, str] | None = None,
    outlets_lookup: dict[str, str] | None = None,
    gdelt_adapter: Any | None = None,
) -> list[CanonicalStory]:
    """Produce each gated story into a digest, bounded-concurrently (stage C).

    Returns the subset of stories that published (a verification-halt or a render
    error skips that story but never aborts the batch — the feed still builds from
    whatever produced).

    The Phase 2c detail-enrichment lookups (``enable_detail_enrichment`` +
    ``interest_segment_lookup`` / ``outlets_lookup`` / ``gdelt_adapter``) are passed
    straight through to ``orchestrate_story`` — injected so the batch is
    enrichment-capable without this module reading the DB itself.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    tags_by_story: dict[str, list[StoryInterestTag]] = {}
    for tag in story_interest_tags:
        tags_by_story.setdefault(tag.story_interest_story_id, []).append(tag)

    async def _produce_one(story: CanonicalStory) -> CanonicalStory | None:
        async with semaphore:
            try:
                result = await orchestrate_story(
                    story=story,
                    story_interest_tags=tags_by_story.get(story.canonical_story_id, []),
                    llm_client=llm_client,
                    tts_client=tts_client,
                    supabase_client=supabase_client,
                    poster_genai_client=poster_genai_client,
                    story_id=story.canonical_story_id,
                    enable_detail_enrichment=enable_detail_enrichment,
                    enable_editorial_rewrite=enable_editorial_rewrite,
                    interest_segment_lookup=interest_segment_lookup,
                    outlets_lookup=outlets_lookup,
                    gdelt_adapter=gdelt_adapter,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "produce_story_failed",
                    story_id=story.canonical_story_id,
                    error_message=str(exc),
                    fix_suggestion="Story render failed; skipped (feed builds from the rest).",
                )
                return None
            return story if result.published else None

    produced = await asyncio.gather(*(_produce_one(s) for s in stories_to_produce))
    return [story for story in produced if story is not None]


async def run_daily_pipeline(
    target_date: date,
    supabase_client: Any,
    llm_client: Any,
    tts_client: GeminiTTSClient,
    ingest_fn: IngestFn,
    interest_nodes: dict[str, InterestNode],
    poster_genai_client: Any | None = None,
    exploration_by_user: dict[str, dict[str, list[ScoredCandidate]]] | None = None,
    now_utc: datetime | None = None,
    since_utc: datetime | None = None,
    max_concurrent_productions: int = DEFAULT_MAX_CONCURRENT_PRODUCTIONS,
    max_total_productions: int | None = None,
    enable_detail_enrichment: bool = False,
    enable_editorial_rewrite: bool = False,
    interest_segment_lookup: dict[str, str] | None = None,
    outlets_lookup: dict[str, str] | None = None,
    gdelt_adapter: Any | None = None,
) -> DailyPipelineResult:
    """Run the full daily personalized-feed batch end-to-end (stages A–E).

    Idempotent at the edges: stage A writes only changed weights, stage C skips
    stories that already have a current digest, and stage E (``assemble_daily_feeds``)
    is produce-once per (user, date). A second run with the same pool does not
    duplicate feeds.

    Args:
        target_date: The ``daily_feeds.feed_date`` to write.
        supabase_client: Service-role client (injected).
        llm_client: Gemini text client (scripting + verification).
        tts_client: Gemini multi-speaker TTS client.
        ingest_fn: Stage B — async ``() -> (stories, tags)``. Injected so the live
            pipeline and the fixture e2e share this runner.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup (scoring).
        poster_genai_client: Optional poster client (None skips posters).
        exploration_by_user: Optional per-user exploration candidates (§3.7).
        now_utc: Time for freshness + ``profile_updated_at`` (defaults to utcnow).
        since_utc: Only aggregate signals at/after this time (stage A).
        max_concurrent_productions: Bounded fan-out width for stage C.
        max_total_productions: Optional overall ceiling on the produced pool, applied
            AFTER the per-category caps and trimmed round-robin across categories so
            balance is preserved (the re-purposed ``MAX_PRODUCE``). ``None``/``<=0``
            leaves the per-category caps as the only bound.
        enable_detail_enrichment: Phase 2c gate — when True, each produced story
            also gets grounded detail enrichment + the GDELT coverage census.
            Defaults False (the M1 produce path) until the production wiring passes
            the lookups below.
        interest_segment_lookup: ``{interest_id: segment_slug}`` — resolves each
            story's ``story_segment_slug`` (and the enrichment's analytic kind /
            coverage mode). Injected per batch; ``None`` → ``wildcard`` fallback.
        outlets_lookup: ``{outlet_domain: bias_lean}`` for the GDELT coverage
            census (with ``gdelt_adapter``); ``None`` skips the census.
        gdelt_adapter: The SHARED ``GdeltDocAdapter`` (honors the throttle) for the
            coverage census; ``None`` skips it.

    Returns:
        A :class:`DailyPipelineResult` summarizing every stage.
    """
    now = now_utc or datetime.now(timezone.utc)
    logger.info("run_daily_pipeline_started", feed_date=target_date.isoformat())

    # ── Stage A — update interest weights FIRST (today reflects yesterday) ─────
    profile_update = run_profile_update_job(
        supabase_client, since_utc=since_utc, now_utc=now
    )

    # ── Stage B — ingest + dedup + ancestor-tag (injected) ────────────────────
    stories, story_interest_tags = await ingest_fn()

    # ── Stage C — produce-once gate, then bounded paid fan-out ────────────────
    has_current_digest = _load_has_current_digest(
        supabase_client, [s.canonical_story_id for s in stories]
    )
    to_produce, _decisions = select_stories_to_produce(
        stories, story_interest_tags, has_current_digest, now_utc=now
    )
    gated_count = len(to_produce)

    # ── Per-category produce cap — bound reels/category at the cross-user max ──
    # (the "Build your 30" budgets). Stops a single-topic pool (e.g. 39 markets
    # candidates) from rendering 39 markets reels and starving every other category.
    active_user_ids = _load_active_user_ids(supabase_client)
    allocation_by_user = _load_category_allocation(supabase_client, active_user_ids)
    caps, breaking_headroom = compute_category_produce_caps(
        allocation_by_user, active_user_ids, DEFAULT_FEED_ALLOCATION
    )
    to_produce = cap_stories_per_category(
        to_produce,
        _decisions,
        story_interest_tags,
        interest_nodes,
        caps,
        breaking_headroom,
        default_cap=DEFAULT_PER_CATEGORY_CAP,
    )
    if max_total_productions and max_total_productions > 0:
        to_produce = enforce_overall_ceiling(
            to_produce,
            _decisions,
            story_interest_tags,
            interest_nodes,
            max_total_productions,
        )
    capped_count = gated_count - len(to_produce)

    produced_stories = await _produce_story_pool(
        stories_to_produce=to_produce,
        story_interest_tags=story_interest_tags,
        llm_client=llm_client,
        tts_client=tts_client,
        supabase_client=supabase_client,
        poster_genai_client=poster_genai_client,
        max_concurrent=max_concurrent_productions,
        enable_detail_enrichment=enable_detail_enrichment,
        enable_editorial_rewrite=enable_editorial_rewrite,
        interest_segment_lookup=interest_segment_lookup,
        outlets_lookup=outlets_lookup,
        gdelt_adapter=gdelt_adapter,
    )

    # ── Stages D+E — score per user + allocate ~30-slot daily_feeds ───────────
    active_user_inputs = load_active_user_inputs(
        supabase_client, target_date, exploration_by_user
    )
    feeds = assemble_daily_feeds(
        target_date=target_date,
        active_user_inputs=active_user_inputs,
        stories=produced_stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        supabase_client=supabase_client,
        now_utc=now,
    )

    logger.info(
        "run_daily_pipeline_completed",
        feed_date=target_date.isoformat(),
        candidate_story_count=len(stories),
        produced_story_count=len(produced_stories),
        capped_count=capped_count,
        feeds_written=feeds.feeds_written,
    )
    return DailyPipelineResult(
        feed_date=target_date.isoformat(),
        profile_update=profile_update,
        candidate_story_count=len(stories),
        produced_story_count=len(produced_stories),
        skipped_by_gate_count=len(stories) - gated_count,
        capped_count=capped_count,
        feeds=feeds,
    )
