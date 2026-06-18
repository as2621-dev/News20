"""Daily personalized-feed pipeline runner (Phase 1d SP4) — the real executor.

Chains the five stages of the daily batch (ranking-spec §4 → §3), the substance
the Trigger.dev v4 schedule (`trigger/dailyPipeline.ts`) fires:

    A. update interest weights   → agents.memory.session_processor.run_profile_update_job
    B. ingest + tag news         → INJECTED ingest_fn (live GDELT pipeline, or a
                                    fixture pool in the live e2e)
    C. produce digests ONCE      → gate select → write fan-out → batch review →
                                    render fan-out
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
from agents.pipeline.categories import (
    CATEGORY_FLOOR,
    DEFAULT_FEED_ALLOCATION,
    CategoryAllocation,
    FeedCategory,
)
from agents.pipeline.demand import compute_pool_target
from agents.pipeline.feed_assembly import ScoredCandidate
from agents.pipeline.models import WritePhaseResult
from agents.pipeline.orchestrator import (
    DailyFeedsBatchResult,
    ActiveUserFeedInputs,
    assemble_daily_feeds,
    render_phase,
    write_phase,
)
from agents.pipeline.produce_caps import (
    cap_stories_per_category,
    compute_category_produce_caps,
    enforce_overall_ceiling,
)
from agents.pipeline.produce_dedup import dedupe_produce_shortlist
from agents.pipeline.produce_gate import select_stories_to_produce
from agents.pipeline.stages.batch_review import review_reel_pool
from agents.pipeline.stages.ranking import (
    FOLLOW_SOURCE_WEIGHT,
    FollowedEntity,
    UserProfileInterest,
)
from agents.shared.logger import get_logger
from agents.shared.settings import Settings
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


class PoolTargetCell(BaseModel):
    """One (category, subcategory) cell of the M2 shared-pool shopping list.

    A pydantic-serializable row form of a ``compute_pool_target`` entry (whose
    native form is a ``{(FeedCategory, str): int}`` dict — tuple keys don't
    serialize to JSON). M3 (targeted ingest) consumes this list off the batch
    result; M2 only computes + logs it (additive, observe-only).

    Attributes:
        cell_category: The screen :data:`FeedCategory` this cell belongs to.
        cell_subcategory: The two-segment subcategory slug (``'markets.crypto'``)
            or the ``"_all"`` sentinel (any subcategory in this category).
        cell_target_count: The unique-story target for this cell (max-over-users
            × buffer, ceil, floored).
    """

    cell_category: FeedCategory = Field(..., description="Screen feed category")
    cell_subcategory: str = Field(
        ..., description="Two-segment subcategory slug or the '_all' sentinel"
    )
    cell_target_count: int = Field(..., ge=0, description="Unique-story target")


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
        pool_target: The M2 subcategory-granular shopping list for the active user
            set (max-over-users × BUFFER, floored). Observe-only in M2 — emitted
            for M3 (targeted ingest) to consume; does NOT change which reels are
            produced this run.

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
    pool_target: list[PoolTargetCell] = Field(
        default_factory=list,
        description="M2 shared-pool shopping list (observe-only; M3 consumes it)",
    )


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


def _load_interest_nodes_by_user(
    supabase_client: Any,
    user_ids: list[str],
    interest_nodes: dict[str, InterestNode],
) -> dict[str, list[InterestNode]]:
    """Resolve every active user's followed interests to their taxonomy nodes (M2).

    ``compute_pool_target`` needs each user's followed :class:`InterestNode` list
    (the slug → subcategory split lives there), but ``user_interest_profile`` only
    carries ``profile_interest_id`` — the slug lives on ``InterestNode``. This reads
    the profile rows in ONE ``.in_()`` query (same batched style as the sibling
    loaders) and resolves each ``profile_interest_id`` through the already-in-scope
    ``interest_nodes`` taxonomy lookup — NO extra taxonomy DB query.

    An id with no matching node is skipped with a structured warning (fail-loud,
    Rule 12 — a dangling profile row should not silently miscount demand).

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids to load followed interests for.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup (the same
            one ``run_daily_pipeline`` already receives for scoring).

    Returns:
        ``{user_id: [InterestNode, ...]}`` — users with no resolvable follow are
        absent (``compute_pool_target`` treats a missing user as no follows, so
        their allocation rows route to ``"_all"`` cells).
    """
    if not user_ids:
        return {}
    rows = (
        getattr(
            supabase_client.table("user_interest_profile")
            .select("profile_user_id,profile_interest_id")
            .in_("profile_user_id", user_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    nodes_by_user: dict[str, list[InterestNode]] = {}
    missing_interest_ids: set[str] = set()
    for row in rows:
        interest_id = str(row["profile_interest_id"])
        node = interest_nodes.get(interest_id)
        if node is None:
            missing_interest_ids.add(interest_id)
            continue
        nodes_by_user.setdefault(str(row["profile_user_id"]), []).append(node)
    if missing_interest_ids:
        logger.warning(
            "interest_nodes_by_user_unresolved",
            unresolved_count=len(missing_interest_ids),
            fix_suggestion="A user_interest_profile.profile_interest_id has no "
            "interests row in the taxonomy lookup — backfill the interest node or "
            "remove the dangling profile row.",
        )
    return nodes_by_user


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
    enable_batch_review: bool = False,
    interest_segment_lookup: dict[str, str] | None = None,
    outlets_lookup: dict[str, str] | None = None,
    gdelt_adapter: Any | None = None,
) -> list[CanonicalStory]:
    """Produce each gated story into a digest, in two bounded waves (stage C).

    Split into a WRITE wave (script → verify → editorial rewrite, all in memory)
    and a RENDER wave (TTS → caption → poster → enrich → persist), with an optional
    pool-level BATCH REVIEW barrier between them. The barrier lets one showrunner
    read every reel side by side and diversify repetitive cross-reel scaffolding
    BEFORE any expensive TTS — something no per-reel pass can do, because each reel
    is otherwise written in isolation. Both waves share one ``max_concurrent``
    semaphore; the barrier fully drains the write wave before any render starts.

    Each reel carries its production-pool index into the write wave so scripting can
    rotate the opener archetype + handoff style (cross-reel diversity, Layer 2).

    Returns the subset of stories that published (a verification halt at write, or a
    render error, skips that story but never aborts the batch — the feed still
    builds from whatever produced). Order is preserved.

    The Phase 2c detail-enrichment lookups (``enable_detail_enrichment`` +
    ``interest_segment_lookup`` / ``outlets_lookup`` / ``gdelt_adapter``) are passed
    straight through to the render phase — injected so the batch is
    enrichment-capable without this module reading the DB itself.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    tags_by_story: dict[str, list[StoryInterestTag]] = {}
    for tag in story_interest_tags:
        tags_by_story.setdefault(tag.story_interest_story_id, []).append(tag)

    # ── WRITE wave — script + verify + editorial rewrite (bounded, in-memory) ──
    async def _write_one(
        story: CanonicalStory, pool_index: int
    ) -> WritePhaseResult | None:
        async with semaphore:
            try:
                return await write_phase(
                    story,
                    story_interest_tags=tags_by_story.get(story.canonical_story_id, []),
                    llm_client=llm_client,
                    story_id=story.canonical_story_id,
                    enable_editorial_rewrite=enable_editorial_rewrite,
                    interest_segment_lookup=interest_segment_lookup,
                    pool_index=pool_index,
                )
            except Exception as exc:  # noqa: BLE001 — one bad write never aborts the batch
                logger.error(
                    "produce_write_failed",
                    story_id=story.canonical_story_id,
                    error_message=str(exc),
                    fix_suggestion="Script/verify failed; skipped (feed builds from the rest).",
                )
                return None

    write_results = await asyncio.gather(
        *(_write_one(story, index) for index, story in enumerate(stories_to_produce))
    )
    survivors = [wr for wr in write_results if wr is not None]

    # ── BARRIER — pool-level cross-reel diversity pass (fail-open) ──
    if enable_batch_review and survivors:
        survivors = await review_reel_pool(survivors, llm_client)

    # ── RENDER wave — TTS + caption + poster + enrich + persist (bounded) ──
    async def _render_one(write_result: WritePhaseResult) -> str | None:
        async with semaphore:
            try:
                result = await render_phase(
                    write_result,
                    tts_client,
                    supabase_client,
                    llm_client=llm_client,
                    poster_genai_client=poster_genai_client,
                    enable_detail_enrichment=enable_detail_enrichment,
                    interest_segment_lookup=interest_segment_lookup,
                    outlets_lookup=outlets_lookup,
                    gdelt_adapter=gdelt_adapter,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "produce_render_failed",
                    story_id=write_result.canonical_story_id,
                    error_message=str(exc),
                    fix_suggestion="Story render failed; skipped (feed builds from the rest).",
                )
                return None
            return write_result.canonical_story_id if result.published else None

    rendered = await asyncio.gather(*(_render_one(wr) for wr in survivors))
    published_ids = {story_id for story_id in rendered if story_id is not None}

    # Reason: return the ORIGINAL stories (in pool order) whose render published —
    # the downstream scorer/allocator (assemble_daily_feeds) matches on
    # canonical_story_id, identical on the original and editorial views.
    return [
        wr.original_story for wr in survivors if wr.canonical_story_id in published_ids
    ]


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
    enable_produce_dedup: bool = True,
    enable_batch_review: bool = False,
    interest_segment_lookup: dict[str, str] | None = None,
    outlets_lookup: dict[str, str] | None = None,
    gdelt_adapter: Any | None = None,
    source_stories_by_user: dict[str, list[CanonicalStory]] | None = None,
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
        enable_produce_dedup: When True (default), an LLM judge drops same-event /
            near-angle duplicates from the produce shortlist BEFORE the paid
            generation fan-out (so two outlets' takes on one event are not both
            produced). Fail-open — a judge error leaves the shortlist unchanged.
        enable_batch_review: When True, a pool-level showrunner reads every written
            reel side by side BEFORE any TTS and rewrites repetitive cross-reel
            scaffolding (openers/reactions/handoffs), re-verifying any reel it
            touches. Fail-open. Defaults False so the legacy produce path is byte-
            for-byte unchanged until the pass is verified.
        interest_segment_lookup: ``{interest_id: segment_slug}`` — resolves each
            story's ``story_segment_slug`` (and the enrichment's analytic kind /
            coverage mode). Injected per batch; ``None`` → ``wildcard`` fallback.
        outlets_lookup: ``{outlet_domain: bias_lean}`` for the GDELT coverage
            census (with ``gdelt_adapter``); ``None`` skips the census.
        gdelt_adapter: The SHARED ``GdeltDocAdapter`` (honors the throttle) for the
            coverage census; ``None`` skips it.
        source_stories_by_user: ``{user_id: [followed-source stories]}`` from
            ``run_source_ingestion`` (phase-5d). Their stories are merged into the
            produce pool (exempt from the per-category caps, since they were the
            user's explicit follows), produced via the same write/render path (the
            poster stage uses their thumbnail, not Nano Banana), and the produced
            subset is handed to ``assemble_daily_feeds`` to fill each user's
            ``youtube``/``x`` source slots. ``None`` → the legacy interest-only batch.

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

    # ── Pre-generation dedup — drop same-event / near-angle duplicates the
    # ingestion clusterer (URL + 0.85 title) missed, BEFORE caps so the caps
    # backfill freed capacity with genuinely different stories. Fail-open. ──
    dedup_dropped_count = 0
    if enable_produce_dedup:
        to_produce, dedup_decisions = await dedupe_produce_shortlist(
            to_produce, llm_client
        )
        dedup_dropped_count = len(dedup_decisions)

    # ── Per-category produce cap — bound reels/category at the cross-user max ──
    # (the "Build your 30" budgets). Stops a single-topic pool (e.g. 39 markets
    # candidates) from rendering 39 markets reels and starving every other category.
    active_user_ids = _load_active_user_ids(supabase_client)
    allocation_by_user = _load_category_allocation(supabase_client, active_user_ids)
    caps = compute_category_produce_caps(
        allocation_by_user, active_user_ids, DEFAULT_FEED_ALLOCATION
    )
    to_produce = cap_stories_per_category(
        to_produce,
        _decisions,
        story_interest_tags,
        interest_nodes,
        caps,
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

    # ── M2 — shared-pool shopping list (observe-only) ─────────────────────────
    # Reason: compute the subcategory-granular demand target (max-over-users ×
    # BUFFER, floored) for the active user set and emit it for M3 (targeted
    # ingest) to consume. ADDITIVE — it does NOT touch the produce path above; it
    # reuses the already-loaded active_user_ids + allocation_by_user, resolves each
    # user's followed interests to nodes via the in-scope interest_nodes lookup
    # (no extra taxonomy query), and is surfaced on DailyPipelineResult.pool_target.
    interest_nodes_by_user = _load_interest_nodes_by_user(
        supabase_client, active_user_ids, interest_nodes
    )
    pool_target = compute_pool_target(
        allocation_by_user,
        interest_nodes_by_user,
        active_user_ids,
        DEFAULT_FEED_ALLOCATION,
        buffer=Settings().pool_buffer,
        category_floor=CATEGORY_FLOOR,
    )
    pool_target_cells = [
        PoolTargetCell(
            cell_category=cell_category,
            cell_subcategory=cell_subcategory,
            cell_target_count=cell_target_count,
        )
        for (cell_category, cell_subcategory), cell_target_count in sorted(
            pool_target.items()
        )
    ]
    logger.info(
        "daily_batch_pool_target_emitted",
        active_user_count=len(active_user_ids),
        cell_count=len(pool_target_cells),
        total_target=sum(pool_target.values()),
    )

    # ── Source-origin merge (phase-5d) — append the users' followed YouTube/X
    # stories to the produce pool, EXEMPT from the gate + per-category caps (the
    # user explicitly follows them). Dedup by story id (a source shared across users
    # is produced once), and skip any that already have a current digest. ──
    source_stories_flat: list[CanonicalStory] = []
    if source_stories_by_user:
        seen_source_ids: set[str] = set()
        for user_source_stories in source_stories_by_user.values():
            for source_story in user_source_stories:
                story_id = source_story.canonical_story_id
                if story_id in seen_source_ids:
                    continue
                seen_source_ids.add(story_id)
                source_stories_flat.append(source_story)
        already_produced = _load_has_current_digest(
            supabase_client, [s.canonical_story_id for s in source_stories_flat]
        )
        in_pool_ids = {s.canonical_story_id for s in to_produce}
        source_to_produce = [
            s
            for s in source_stories_flat
            if not already_produced.get(s.canonical_story_id)
            and s.canonical_story_id not in in_pool_ids
        ]
        to_produce = to_produce + source_to_produce
        logger.info(
            "run_daily_pipeline_source_merge",
            source_stories=len(source_stories_flat),
            source_to_produce=len(source_to_produce),
        )

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
        enable_batch_review=enable_batch_review,
        interest_segment_lookup=interest_segment_lookup,
        outlets_lookup=outlets_lookup,
        gdelt_adapter=gdelt_adapter,
    )

    # ── Stages D+E — score per user + allocate ~30-slot daily_feeds ───────────
    active_user_inputs = load_active_user_inputs(
        supabase_client, target_date, exploration_by_user
    )
    # Reason: a source slot can be filled by a source story produced THIS run or one
    # that already had a current digest (persisted, placeable). Restrict each user's
    # source pool to those placeable ids so a verification halt never leaves a
    # dangling source slot.
    produced_source_by_user: dict[str, list[CanonicalStory]] | None = None
    if source_stories_by_user:
        # Placeable = produced this run OR already carrying a current digest
        # (``already_produced`` was computed in the source-merge block above).
        placeable_source_ids = {s.canonical_story_id for s in produced_stories}
        placeable_source_ids |= {
            story_id for story_id, has in already_produced.items() if has
        }
        produced_source_by_user = {
            user_id: [
                s
                for s in user_source_stories
                if s.canonical_story_id in placeable_source_ids
            ]
            for user_id, user_source_stories in source_stories_by_user.items()
        }
    feeds = assemble_daily_feeds(
        target_date=target_date,
        active_user_inputs=active_user_inputs,
        stories=produced_stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        supabase_client=supabase_client,
        now_utc=now,
        source_stories_by_user=produced_source_by_user,
    )

    logger.info(
        "run_daily_pipeline_completed",
        feed_date=target_date.isoformat(),
        candidate_story_count=len(stories),
        produced_story_count=len(produced_stories),
        dedup_dropped_count=dedup_dropped_count,
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
        pool_target=pool_target_cells,
    )
