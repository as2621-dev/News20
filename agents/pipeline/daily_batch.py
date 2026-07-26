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

from agents.ingestion.models import (
    CanonicalStory,
    InterestNode,
    SemanticRelevanceRunStamp,
    StoryInterestTag,
)
from agents.ingestion.x_theme_reel import TweetScreenshotRenderer
from agents.memory.session_processor import ProfileUpdateResult, run_profile_update_job
from agents.pipeline.categories import (
    CATEGORY_FLOOR,
    DEFAULT_FEED_ALLOCATION,
    CategoryAllocation,
    FeedCategory,
)
from agents.pipeline.clustering.reconcile import reconcile_story_ids_via_clustering
from agents.pipeline.demand import compute_pool_target
from agents.pipeline.feed_assembly import ScoredCandidate
from agents.pipeline.niche_allocation import NicheAllocationRow
from agents.pipeline.models import WritePhaseResult
from agents.pipeline.orchestrator import (
    DailyFeedsBatchResult,
    ActiveUserFeedInputs,
    assemble_daily_feeds,
    render_phase,
    write_phase,
)
from agents.pipeline.produce_caps import (
    DEFAULT_HEADROOM_MULTIPLIER,
    cap_stories_per_category,
    compute_category_produce_caps,
    enforce_overall_ceiling,
)
from agents.pipeline.notability_gate import apply_notability_gate
from agents.pipeline.produce_dedup import (
    DedupDecision,
    dedupe_produce_shortlist,
    dedupe_written_scripts,
)
from agents.pipeline.scripts_artifact import ScriptEntry, build_script_entries
from agents.pipeline.shortlist import (
    ShortlistEntry,
    build_produce_shortlist,
    warn_matched_slugs_outside_followed_set,
)
from agents.pipeline.produce_gate import select_stories_to_produce
from agents.pipeline.stages.batch_review import review_reel_pool
from agents.pipeline.stages.ranking import (
    FOLLOW_SOURCE_WEIGHT,
    FollowedEntity,
    UserProfileInterest,
    compute_category_verdicts,
)
from agents.pipeline.user_scope import (
    exclude_seed_profile_rows,
    seed_account_user_ids,
    seed_exclusion_enabled,
)
from agents.pipeline.x_theme_ladder import XThemeReelCandidate
from agents.pipeline.x_theme_production import (
    XThemeGatherResult,
    filter_placeable_theme_candidates,
    gather_x_theme_candidates,
)
from agents.shared.exceptions import HeadlineQualityError, SegmentResolutionError
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

# Type of the injected ingest stage: returns the deduped, ancestor-tagged pool,
# OPTIONALLY followed by the semantic-relevance run stamp (issue #67 — the live
# path supplies it so the shortlist artifact carries its provenance; fixture
# ingests return the two-element form and keep the default DISABLED stamp).
IngestFn = Callable[
    [],
    Awaitable[
        tuple[list[CanonicalStory], list[StoryInterestTag]]
        | tuple[list[CanonicalStory], list[StoryInterestTag], SemanticRelevanceRunStamp]
    ],
]


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
        shortlist: The would-be-produced review list (founder rule 2026-07-19,
            shortlist-first). Populated ONLY when the run halts with
            ``shortlist_only=True``; empty on a producing run.
        semantic_relevance: Which relevance mode Stage B actually ran (issue #67) —
            the provenance stamped onto the on-disk shortlist artifact so an audit
            can tell a semantic run from an embedding-outage one.
        scripts: The written reel scripts up for founder review (issue #68).
            Populated ONLY when the run halts with ``scripts_only=True``; empty on
            an armed (full-produce) run and on a shortlist halt.
        script_dedup_drops: Near-duplicate scripts the similarity gate dropped
            before the reel stage, each with the twin it was dropped in favour of.
        script_dedup_enabled: Whether that gate ran at all — zero drops means
            something different when the gate was off.

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
    shortlist: list[ShortlistEntry] = Field(
        default_factory=list,
        description="Would-produce review list (set only when shortlist_only halts)",
    )
    semantic_relevance: SemanticRelevanceRunStamp = Field(
        default_factory=SemanticRelevanceRunStamp,
        description="Stage B relevance mode + counts (issue #67 artifact provenance)",
    )
    scripts: list[ScriptEntry] = Field(
        default_factory=list,
        description="Written scripts for review (set only when scripts_only halts)",
    )
    script_dedup_drops: list[DedupDecision] = Field(
        default_factory=list,
        description="Near-duplicate scripts the similarity gate dropped (#68)",
    )
    script_dedup_enabled: bool = Field(
        default=False, description="Whether the script similarity gate ran"
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
    # Reason: founder directive 2026-07-26 — seed/test accounts (M4 personas, demo
    # users) never count as active: their allocations must not inflate the produce
    # caps. Excluded unless INCLUDE_SEED_USERS=1 (agents/pipeline/user_scope.py).
    if seed_exclusion_enabled():
        rows = exclude_seed_profile_rows(rows, seed_account_user_ids(supabase_client))
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


def _load_mute_terms(supabase_client: Any, user_ids: list[str]) -> dict[str, list[str]]:
    """Load every active user's ``user_mute_terms`` in ONE query (FSR #17).

    The SKIP-TUNE mute terms the assembler hard-filters on (migration 0029). One ``.in_()``
    over all active users, grouped in memory (no N+1). Category is not needed downstream —
    a mute is a global hard filter for that user — so only the terms are collected.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids to load mute terms for.

    Returns:
        ``{user_id: [mute_term, ...]}`` (users with no mutes are absent → no filter).
    """
    if not user_ids:
        return {}
    rows = (
        getattr(
            supabase_client.table("user_mute_terms")
            .select("mute_user_id,mute_term")
            .in_("mute_user_id", user_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    mutes_by_user: dict[str, list[str]] = {}
    for row in rows:
        term = str(row.get("mute_term") or "").strip()
        if term:
            mutes_by_user.setdefault(str(row["mute_user_id"]), []).append(term)
    return mutes_by_user


def _load_niche_allocation(
    supabase_client: Any, user_ids: list[str]
) -> dict[str, list[NicheAllocationRow]]:
    """Load every active user's ``user_feed_allocation`` rows WITH section metadata (FSR #7).

    The niche-aware sibling of :func:`_load_category_allocation`: it additionally selects the
    slice-#6 columns (``allocation_interest_id`` + ``allocation_section_label``, migration
    0026) so :func:`agents.pipeline.feed_assembly.assemble_niche_feed` can fill each named
    niche section leaf-first and stamp climbed slots. One ``.in_()`` over all active users,
    grouped in memory. A user with only coarse rows (NULL interest ref) hydrates as coarse
    :class:`NicheAllocationRow`s — the assembler then delegates to the unchanged coarse path.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: The active user ids to load allocations for.

    Returns:
        ``{user_id: [NicheAllocationRow, ...]}`` (users with no allocation are absent —
        the assembler applies a balanced default for them via the coarse delegate).
    """
    if not user_ids:
        return {}
    rows = (
        getattr(
            supabase_client.table("user_feed_allocation")
            .select(
                "follow_user_id,allocation_category,allocation_interest_id,"
                "allocation_section_label,allocation_slot_count,allocation_sort_order"
            )
            .in_("follow_user_id", user_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    allocation_by_user: dict[str, list[NicheAllocationRow]] = {}
    for row in rows:
        interest_id = row.get("allocation_interest_id")
        section_label = row.get("allocation_section_label")
        allocation_by_user.setdefault(str(row["follow_user_id"]), []).append(
            NicheAllocationRow(
                allocation_category=str(row["allocation_category"]),
                allocation_interest_id=(
                    str(interest_id) if interest_id is not None else None
                ),
                allocation_section_label=(
                    str(section_label) if section_label is not None else None
                ),
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
    # Reason: founder directive 2026-07-26 — no daily_feeds are built for seed/test
    # accounts (M4 personas, demo users) unless INCLUDE_SEED_USERS=1. Same single
    # contract as _load_active_user_ids (agents/pipeline/user_scope.py).
    if seed_exclusion_enabled():
        profile_rows = exclude_seed_profile_rows(
            profile_rows, seed_account_user_ids(supabase_client)
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
    # FSR #7: the niche-aware section plan (with interest-node refs + user-vocab labels)
    # the fallback-ladder assembler fills niche-first. A user with only coarse rows
    # hydrates as coarse NicheAllocationRows → the assembler delegates to the coarse path.
    niche_allocation_by_user = _load_niche_allocation(supabase_client, active_user_ids)
    # FSR #17: per-user SKIP-TUNE mute terms — hard-filtered at assembly (never ingestion).
    mute_terms_by_user = _load_mute_terms(supabase_client, active_user_ids)

    inputs: list[ActiveUserFeedInputs] = []
    for user_id, profile_interests in interests_by_user.items():
        inputs.append(
            ActiveUserFeedInputs(
                active_user_id=user_id,
                profile_interests=profile_interests,
                followed_entities=entities_by_user.get(user_id, []),
                category_allocation=allocation_by_user.get(user_id, []),
                niche_allocation=niche_allocation_by_user.get(user_id, []),
                prior_feed_story_ids=prior_story_ids_by_user.get(user_id, []),
                mute_terms=mute_terms_by_user.get(user_id, []),
                exploration_candidates_by_interest=exploration_by_user.get(user_id, {}),
            )
        )
    logger.info("load_active_user_inputs_completed", active_user_count=len(inputs))
    return inputs


async def _write_script_pool(
    stories_to_produce: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    llm_client: Any,
    max_concurrent: int,
    enable_editorial_rewrite: bool = False,
    enable_batch_review: bool = False,
    interest_segment_lookup: dict[str, str] | None = None,
    pinned_segment_by_story: dict[str, str] | None = None,
) -> list[WritePhaseResult]:
    """Run the WRITE wave — script → verify → editorial rewrite (stage C, first half).

    Everything here is in-memory text on ``gemini-3.5-flash``: pennies per story, and
    NOTHING in this function touches TTS, posters, storage, or ``daily_feeds``. That
    is what makes the issue #68 script halt possible — the founder can review real
    scripts before the expensive tail is armed (see :func:`_render_story_pool`).

    The optional pool-level BATCH REVIEW barrier closes this wave: one showrunner
    reads every reel side by side and diversifies repetitive cross-reel scaffolding
    BEFORE any expensive TTS — something no per-reel pass can do, because each reel
    is otherwise written in isolation.

    Each reel carries its production-pool index into the write wave so scripting can
    rotate the opener archetype + handoff style (cross-reel diversity, Layer 2).

    Args:
        stories_to_produce: The gated produce pool, in order.
        story_interest_tags: The batch's SP1 tags (indexed per story here).
        llm_client: Gemini text client (scripting + verification + review).
        max_concurrent: Bounded fan-out width.
        enable_editorial_rewrite: Per-reel editorial headline/body rewrite.
        enable_batch_review: Run the cross-reel diversity barrier (fail-open).
        interest_segment_lookup: ``{interest_id: segment_slug}`` — the WRITE phase
            resolves the segment ONCE onto ``WritePhaseResult.segment_slug``; render
            consumes that rather than re-resolving (issue #61).
        pinned_segment_by_story: The batch's resolve-once category verdicts (#70).

    Returns:
        The written reels, in pool order. A story whose script/verify failed is
        absent (logged, never fatal) — the run continues for the rest.
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
                    # Reason: issue #70 — the persisted segment must be the SAME
                    # resolve-once verdict the chip and the cap bucket used.
                    pinned_segment_slug=(pinned_segment_by_story or {}).get(
                        story.canonical_story_id
                    ),
                )
            except SegmentResolutionError as exc:
                # Reason: the nightly batch calls write_phase DIRECTLY (never
                # orchestrate_story), so without this arm a segment rejection is
                # swallowed by the generic handler below and reported as a
                # script/verify failure — the exact conflation this guard exists to
                # prevent. Distinct event, and the resolver's own fix_suggestion.
                logger.error(
                    "produce_write_segment_unresolved",
                    story_id=story.canonical_story_id,
                    error_message=str(exc),
                    fix_suggestion=exc.fix_suggestion,
                )
                return None
            except HeadlineQualityError as exc:
                # Reason: same reason as the segment arm above — the batch calls
                # write_phase directly, so without this a dropped masthead is
                # reported as a script/verify failure and the real, actionable
                # signal (how many stories the headline gate cost us) is lost.
                logger.error(
                    "produce_write_headline_rejected",
                    story_id=story.canonical_story_id,
                    rejection_reason=exc.rejection_reason,
                    error_message=str(exc),
                    fix_suggestion=exc.fix_suggestion,
                )
                return None
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

    return survivors


async def _render_story_pool(
    write_results: list[WritePhaseResult],
    tts_client: GeminiTTSClient,
    supabase_client: Any,
    llm_client: Any,
    poster_genai_client: Any | None,
    max_concurrent: int,
    enable_detail_enrichment: bool = False,
    outlets_lookup: dict[str, str] | None = None,
    gdelt_adapter: Any | None = None,
) -> list[CanonicalStory]:
    """Run the RENDER wave — TTS → caption → poster → enrich → persist (stage C, 2nd half).

    THIS is the expensive tail (issue #68): every call below bills TTS audio and/or
    Nano-Banana poster generation and writes a real digest. Callers must therefore
    reach it only on an ARMED run — ``run_daily_pipeline`` gates it behind the
    script halt, and the entry points behind ``run_flags.resolve_run_stage``.

    Args:
        write_results: The written reels (WRITE wave output, post similarity gate).
        tts_client: Gemini multi-speaker TTS client.
        supabase_client: Service-role client (persist).
        llm_client: Gemini text client (caption/enrichment helpers).
        poster_genai_client: Optional poster client (``None`` skips posters).
        max_concurrent: Bounded fan-out width. A fresh semaphore is correct here:
            the barrier fully drains the write wave before any render starts, so the
            two waves never overlap and never share concurrency.
        enable_detail_enrichment: Phase 2c grounded detail + coverage census.
        outlets_lookup: ``{outlet_domain: bias_lean}`` for the census.
        gdelt_adapter: The SHARED throttled GDELT adapter for the census.

    Returns:
        The ORIGINAL stories (in pool order) whose render published — a render error
        skips that story but never aborts the batch.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    survivors = write_results

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
                    outlets_lookup=outlets_lookup,
                    gdelt_adapter=gdelt_adapter,
                )
            except HeadlineQualityError as exc:
                # Reason: persist re-gates the headline, so a rejection can land in
                # the RENDER half too. Without this arm it reads as a render failure
                # and the headline-gate cost of the batch is undercounted.
                logger.error(
                    "produce_render_headline_rejected",
                    story_id=write_result.canonical_story_id,
                    rejection_reason=exc.rejection_reason,
                    error_message=str(exc),
                    fix_suggestion=exc.fix_suggestion,
                )
                return None
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
    pinned_segment_by_story: dict[str, str] | None = None,
) -> list[CanonicalStory]:
    """Produce each gated story into a digest, in two bounded waves (stage C).

    The unsplit stage: WRITE wave (:func:`_write_script_pool`) then RENDER wave
    (:func:`_render_story_pool`), exactly as before issue #68 split them so a run
    could stop between the two. ``run_daily_pipeline`` drives the halves directly
    (it needs the written scripts at the seam); this composition remains the single
    call for any caller that just wants a fully produced pool.

    Returns the subset of stories that published (a verification halt at write, or a
    render error, skips that story but never aborts the batch — the feed still
    builds from whatever produced). Order is preserved.
    """
    write_results = await _write_script_pool(
        stories_to_produce=stories_to_produce,
        story_interest_tags=story_interest_tags,
        llm_client=llm_client,
        max_concurrent=max_concurrent,
        enable_editorial_rewrite=enable_editorial_rewrite,
        enable_batch_review=enable_batch_review,
        interest_segment_lookup=interest_segment_lookup,
        pinned_segment_by_story=pinned_segment_by_story,
    )
    return await _render_story_pool(
        write_results=write_results,
        tts_client=tts_client,
        supabase_client=supabase_client,
        llm_client=llm_client,
        poster_genai_client=poster_genai_client,
        max_concurrent=max_concurrent,
        enable_detail_enrichment=enable_detail_enrichment,
        outlets_lookup=outlets_lookup,
        gdelt_adapter=gdelt_adapter,
    )


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
    produce_cap_headroom: float = DEFAULT_HEADROOM_MULTIPLIER,
    enable_detail_enrichment: bool = False,
    enable_editorial_rewrite: bool = False,
    enable_produce_dedup: bool = True,
    enable_batch_review: bool = False,
    enable_semantic_clustering: bool = False,
    enable_notability_gate: bool = False,
    interest_segment_lookup: dict[str, str] | None = None,
    outlets_lookup: dict[str, str] | None = None,
    gdelt_adapter: Any | None = None,
    source_stories_by_user: dict[str, list[CanonicalStory]] | None = None,
    enable_x_theme_reels: bool = False,
    tweet_screenshot_renderer: TweetScreenshotRenderer | None = None,
    shortlist_only: bool = False,
    scripts_only: bool = False,
    enable_script_dedup: bool = False,
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
        produce_cap_headroom: Over-provision factor for the per-category produce
            caps (``PRODUCE_CAP_HEADROOM``). Defaults to
            :data:`agents.pipeline.produce_caps.DEFAULT_HEADROOM_MULTIPLIER` (``1.5``,
            FSR-M6b SP3 — sized for the source-led mix); ``1.0`` renders 1× demand;
            ``2.0`` doubles the render pool so downstream quality-gate rejections
            still leave enough survivors to fill each category's real feed budget.
            The feed itself is still capped at the user's true allocation by
            ``feed_assembly``. Note: a low ``max_total_productions`` ceiling applied
            AFTER the caps can negate this — set ``MAX_PRODUCE=0`` to let the
            headroomed caps bind.
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
        enable_semantic_clustering: When True, the deduped candidate pool is run
            through the M3a/M3b online semantic clusterer BEFORE the produce gate so
            two candidates about the SAME real-world event (that ingestion's
            URL+0.85-title dedup missed — e.g. two reworded headlines for one match)
            collapse onto one shared ``story_id``. Downstream produce-once + the
            ``feed_assembly`` exact-id dedup then guarantee one reel per event, and the
            clusters' E1 importance feeds the assembler's Importance term. Adds one paid
            Gemini ``gemini-embedding-001`` call per near-dup representative. Defaults
            False so the legacy path costs nothing and is byte-for-byte unchanged until
            a caller opts in (production entry points default it ON via
            ``ENABLE_SEMANTIC_CLUSTERING``, issue #34). A reconcile failure mid-run
            falls back to the un-reconciled pool for the WHOLE run — loud, never a
            half-reconciled feed.
        enable_notability_gate: When True (issue #48), the deduped/reconciled pool passes
            the notability hard cut (``notability_gate.apply_notability_gate``) BEFORE the
            produce-once gate: a candidate reaches production only with ≥2 distinct
            editorial outlets OR an authority-tier outlet — a syndication burst of one
            wire item across many distributors counts as zero corroborating outlets and is
            dropped. A thin leaf niche relaxes to the single-outlet rule (stamped
            ``relaxed``) rather than starving. Followed-source (YouTube/X) stories are
            exempt. The batch emits candidates-surviving-per-niche as structured JSON.
            Fails LOUD if the authority config is unavailable (never a silent open gate).
            Defaults False so the legacy path is byte-for-byte unchanged until a caller
            opts in (the live entry point defaults it ON via ``ENABLE_NOTABILITY_GATE``).
        interest_segment_lookup: ``{interest_id: segment_slug}`` — resolves each
            story's ``story_segment_slug`` (and the enrichment's analytic kind /
            coverage mode). Injected per batch; a story that resolves to no
            canonical segment root is rejected and skipped, never defaulted.
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
        enable_x_theme_reels: Slice #31 gate (``RUN_X_THEMES`` at the entry points).
            When True, each active user's followed X clusters are joined
            (``user_content_sources → source_cluster_members → source_clusters``),
            today's shared ``x_cluster_sweeps.themes`` are read, ONE theme reel story
            per (cluster, theme) is produced IN THIS RUN (deterministic id — a re-run
            reuses the same ``stories`` row via the produce-once digest gate), and the
            FK-guarded ``x_theme_candidates_by_user`` is handed to
            ``assemble_daily_feeds`` so eligible users' ``x`` slots fill via the
            honest theme ladder (rung + attribution stamped). Users following no X
            cluster keep the legacy x fill. A gather failure degrades LOUDLY to the
            legacy fill for the whole run. Defaults False — zero behaviour change.
        tweet_screenshot_renderer: Optional ``(tweet_url) -> path|None`` seam for the
            theme reels' top-tweet screenshot (mocked in tests; the real Playwright
            renderer when ``None``). Only read when ``enable_x_theme_reels`` is True.
        shortlist_only: Founder rule 2026-07-19 (shortlist-first, credit frugality).
            When True the run HALTS after the full selection pipeline (ingest →
            gates → dedup → caps → merges) and returns the would-produce list on
            ``result.shortlist`` — the paid write/render phases and the feed
            assembly never run, so zero production credits are spent and no
            ``daily_feeds`` rows are written. Defaults False (producing run).
        scripts_only: Founder decision 2026-07-25 (staged production, issue #68) —
            the SECOND rung of the halt ladder, below ``shortlist_only``. When True
            the run writes the scripts (cheap text), runs the script similarity gate
            if enabled, returns them on ``result.scripts``, and HALTS before the
            render wave: no TTS, no poster, no persist, no ``daily_feeds``. Ignored
            when ``shortlist_only`` is True (that halt is higher and returns first).
            Defaults False — the library's neutral value; the PRODUCT policy (an
            un-armed run stops here) lives in ``run_flags.resolve_run_stage``.
        enable_script_dedup: When True, an LLM judge reads the FINAL scripts side by
            side and drops near-duplicate reels before the render wave
            (``produce_dedup.dedupe_written_scripts`` — the same judge/parse/keep
            machinery as the story-level dedup, one extra cheap call). Fail-open.
            Defaults False so the legacy produce path is byte-for-byte unchanged
            until a caller opts in; the live entry points default it ON.

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
    # Issue #67: an ingest_fn MAY return a third element — the semantic-relevance
    # run stamp — so the shortlist artifact can say which mode produced it. A
    # two-element return (every fixture ingest) leaves the default DISABLED stamp,
    # which is the truth for a fixture: no semantic key ran.
    ingested = await ingest_fn()
    stories, story_interest_tags = ingested[0], ingested[1]
    semantic_relevance = (
        ingested[2] if len(ingested) > 2 else SemanticRelevanceRunStamp()
    )

    # ── Stage B.5 — semantic same-event reconciliation (FSR-M3, gated) ────────
    # Collapse candidates about the SAME real-world event that ingestion's
    # URL+0.85-title dedup left as separate ``story_id`` s (the "two reels, one
    # event" bug) onto ONE shared id, BEFORE the gate/caps/assembly all key on it.
    # Also produces the shared cluster-importance map (closes the FSR-M3 residual).
    # Gated OFF by default — no Gemini spend, no behaviour change — until a caller
    # opts in (see ``enable_semantic_clustering``).
    cluster_importance_by_story: dict[str, float] | None = None
    category_override_by_story: dict[str, FeedCategory] | None = None
    if enable_semantic_clustering:
        try:
            reconciled = await reconcile_story_ids_via_clustering(
                stories,
                story_interest_tags,
                supabase_client=supabase_client,
                llm_client=llm_client,
                interest_nodes=interest_nodes,
                resolve_existing_story_ids=build_story_id_resolver(supabase_client),
                now_utc=now,
            )
        except Exception as reconcile_error:
            # Reason: a mid-batch embedding/DB failure must degrade the WHOLE run to
            # the legacy un-clustered pool — reconcile's outputs are all-or-nothing, so
            # a half-embedded batch never leaks a half-reconciled feed. The paid dedup
            # upgrade is lost for this run; the nightly batch itself must not be.
            logger.error(
                "semantic_reconcile_failed_run_fallback",
                error_type=type(reconcile_error).__name__,
                error_message=str(reconcile_error),
                candidate_count=len(stories),
                fix_suggestion=(
                    "Semantic reconcile failed mid-run; the batch fell back to the "
                    "legacy un-clustered pool (raw outlet-count importance). The "
                    "same-event collapse is PERMANENTLY skipped for this batch's "
                    "candidates — once they are produced un-merged, produce-once "
                    "keeps the duplicates. Fix Gemini embedding availability/quota "
                    "(gemini-embedding-001) or story_clusters DB access BEFORE the "
                    "next batch."
                ),
            )
        else:
            stories = reconciled.reconciled_stories
            story_interest_tags = reconciled.reconciled_tags
            cluster_importance_by_story = reconciled.cluster_importance_by_story

    # ── Stage B.9 — notability hard cut (issue #48, gated) ────────────────────
    # Reason (PRD RC2): nothing reaches production unless it is plausibly news —
    # ≥2 distinct editorial outlets OR an authority-tier outlet, with a thin-niche
    # relaxation (stamped) so a legitimately thin leaf niche does not starve. A
    # syndication burst of one wire item counts as zero corroborating outlets and is
    # dropped. Runs BEFORE the produce-once gate so cost is only ever spent on news.
    # ``stories`` is left intact (candidate_story_count = the full ingested pool);
    # only the produce path narrows to the notable subset. Fails LOUD on missing config.
    producible_stories = stories
    if enable_notability_gate:
        notability_result = apply_notability_gate(stories, story_interest_tags)
        notable_ids = set(notability_result.notable_story_ids)
        producible_stories = [
            story for story in stories if story.canonical_story_id in notable_ids
        ]
        logger.info(
            "notability_gate_applied",
            candidate_pool=len(stories),
            notable=len(producible_stories),
            rejected=len(stories) - len(producible_stories),
            relaxed_niche_count=len(notability_result.relaxed_niche_ids),
        )

    # ── Stage C — produce-once gate, then bounded paid fan-out ────────────────
    has_current_digest = _load_has_current_digest(
        supabase_client, [s.canonical_story_id for s in producible_stories]
    )
    to_produce, _decisions = select_stories_to_produce(
        producible_stories,
        story_interest_tags,
        has_current_digest,
        now_utc=now,
        interest_nodes=interest_nodes,
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
        allocation_by_user,
        active_user_ids,
        DEFAULT_FEED_ALLOCATION,
        headroom_multiplier=produce_cap_headroom,
    )
    # Reason: issue #70 (review-panel HIGH) — resolve ONE category verdict per pool
    # story (tags + theme tiebreak/fallback through assign_category, ONCE) and ride
    # it as the override map to EVERY consumer: caps, ceiling, founder shortlist,
    # feed-assembly buckets, and the persisted story_segment_slug. Before this map
    # the produce path resolved with the theme tiebreak while the feed path did
    # not, so one story could chip "sport" on the shortlist but bucket to "arts"
    # at assembly. Subsumes the former reconcile merge pin (identical resolver
    # over the reconciled pool).
    category_override_by_story = compute_category_verdicts(
        stories, story_interest_tags, interest_nodes
    )
    to_produce = cap_stories_per_category(
        to_produce,
        _decisions,
        story_interest_tags,
        interest_nodes,
        caps,
        default_cap=DEFAULT_PER_CATEGORY_CAP,
        category_override_by_story=category_override_by_story,
    )
    if max_total_productions and max_total_productions > 0:
        to_produce = enforce_overall_ceiling(
            to_produce,
            _decisions,
            story_interest_tags,
            interest_nodes,
            max_total_productions,
            category_override_by_story=category_override_by_story,
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
    # Reason: issue #70 invariant, UNCONDITIONAL (review-panel A2) — a matched slug
    # outside the batch's followed universe means the tag stream is corrupted again;
    # it must warn on producing runs too, not only under the shortlist halt.
    batch_followed_interest_ids = frozenset(
        node.interest_id
        for user_nodes in interest_nodes_by_user.values()
        for node in user_nodes
    )
    warn_matched_slugs_outside_followed_set(
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        followed_interest_ids=batch_followed_interest_ids,
        story_ids={story.canonical_story_id for story in to_produce},
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

    # ── X theme-of-the-day merge (slice #31) — join each active user's followed X
    # clusters, read today's SHARED x_cluster_sweeps.themes, and merge ONE reel
    # story per (cluster, theme) into the produce pool, EXEMPT from the gate + caps
    # (mirrors the source-origin merge above; deterministic ids + the current-digest
    # skip keep a same-day re-run from re-paying). The theme stories are produced IN
    # THIS RUN because daily_feeds only fills from same-run stories. ──
    x_theme_gather: XThemeGatherResult | None = None
    x_theme_already_produced: dict[str, bool] = {}
    if enable_x_theme_reels:
        try:
            x_theme_gather = await gather_x_theme_candidates(
                supabase_client,
                active_user_ids,
                target_date,
                screenshot_renderer=tweet_screenshot_renderer,
            )
        except Exception as gather_error:  # noqa: BLE001 — never kill the nightly batch
            # Reason: a cluster-join/sweep-read failure must not abort the whole
            # nightly batch — degrade to the legacy x fill (candidates stay None so
            # NO user is switched into ladder mode with an empty list) and log loud.
            logger.error(
                "x_theme_gather_failed_run_fallback",
                error_type=type(gather_error).__name__,
                error_message=str(gather_error),
                fix_suggestion="X theme gather failed; this run falls back to the "
                "legacy source-stories x fill (no theme ladder). Check "
                "user_content_sources/source_cluster_members/source_clusters/"
                "x_cluster_sweeps access before the next batch.",
            )
            x_theme_gather = None
        if x_theme_gather is not None and x_theme_gather.theme_stories:
            x_theme_already_produced = _load_has_current_digest(
                supabase_client,
                [s.canonical_story_id for s in x_theme_gather.theme_stories],
            )
            pool_ids = {s.canonical_story_id for s in to_produce}
            theme_to_produce = [
                s
                for s in x_theme_gather.theme_stories
                if not x_theme_already_produced.get(s.canonical_story_id)
                and s.canonical_story_id not in pool_ids
            ]
            to_produce = to_produce + theme_to_produce
            logger.info(
                "run_daily_pipeline_x_theme_merge",
                theme_stories=len(x_theme_gather.theme_stories),
                theme_to_produce=len(theme_to_produce),
                eligible_user_count=len(x_theme_gather.candidates_by_user),
            )

    # ── SHORTLIST-ONLY halt (founder rule 2026-07-19, shortlist-first) ────────
    # Reason: production (script LLM → TTS → poster) is the expensive tail of the
    # batch. Halting HERE — after every gate, dedup, cap and merge — surfaces the
    # exact would-produce pool for founder review at zero production cost. The
    # review list must be exactly what production would receive, so this sits
    # immediately above _produce_story_pool and nothing may slip between them.
    if shortlist_only:
        # Reason: the followed-set invariant already ran unconditionally above; the
        # override map carries the batch's resolve-once verdicts (issue #70).
        shortlist_entries = build_produce_shortlist(
            to_produce,
            story_interest_tags,
            interest_nodes,
            category_override_by_story,
        )
        logger.info(
            "shortlist_only_halt",
            feed_date=target_date.isoformat(),
            candidate_story_count=len(stories),
            shortlist_count=len(shortlist_entries),
            semantic_relevance_mode=semantic_relevance.semantic_relevance_mode,
            skipped_by_gate_count=len(stories) - gated_count,
            capped_count=capped_count,
        )
        return DailyPipelineResult(
            feed_date=target_date.isoformat(),
            profile_update=profile_update,
            candidate_story_count=len(stories),
            produced_story_count=0,
            skipped_by_gate_count=len(stories) - gated_count,
            capped_count=capped_count,
            feeds=None,
            pool_target=pool_target_cells,
            shortlist=shortlist_entries,
            semantic_relevance=semantic_relevance,
        )

    # ── WRITE wave — scripts only, no media (stage C, first half) ─────────────
    write_results = await _write_script_pool(
        stories_to_produce=to_produce,
        story_interest_tags=story_interest_tags,
        llm_client=llm_client,
        max_concurrent=max_concurrent_productions,
        enable_editorial_rewrite=enable_editorial_rewrite,
        enable_batch_review=enable_batch_review,
        interest_segment_lookup=interest_segment_lookup,
        pinned_segment_by_story=category_override_by_story,
    )

    # ── SCRIPT SIMILARITY GATE (issue #68) ────────────────────────────────────
    # Reason: the story-level judge ran on headlines + leads, before anything was
    # written; two reels can clear it and still play as the same reel twice. This
    # second pass reads the FINAL scripts and drops the redundant twin BEFORE the
    # paid render wave — so a duplicate costs one cheap judge call, not a TTS run
    # plus a poster. Fail-open (a judge error keeps every script, never blocks).
    script_dedup_drops: list[DedupDecision] = []
    if enable_script_dedup:
        write_results, script_dedup_drops = await dedupe_written_scripts(
            write_results, llm_client
        )

    # ── SCRIPTS-ONLY halt (founder decision 2026-07-25, staged production) ────
    # Reason: TTS + posters are the expensive tail (the 2026-07-19 $24 burn was 58
    # reels' media); the scripts above are gemini-3.5-flash pennies. Halting HERE
    # gives the founder the REAL scripts to approve while nothing downstream has
    # billed. This sits immediately above _render_story_pool — the first line of
    # the run that spends media credits — and nothing may slip between them.
    if scripts_only:
        script_entries = build_script_entries(write_results)
        logger.info(
            "scripts_only_halt",
            feed_date=target_date.isoformat(),
            candidate_story_count=len(stories),
            to_produce_count=len(to_produce),
            written_script_count=len(script_entries),
            script_dedup_enabled=enable_script_dedup,
            script_dedup_dropped_count=len(script_dedup_drops),
            semantic_relevance_mode=semantic_relevance.semantic_relevance_mode,
        )
        return DailyPipelineResult(
            feed_date=target_date.isoformat(),
            profile_update=profile_update,
            candidate_story_count=len(stories),
            produced_story_count=0,
            skipped_by_gate_count=len(stories) - gated_count,
            capped_count=capped_count,
            feeds=None,
            pool_target=pool_target_cells,
            semantic_relevance=semantic_relevance,
            scripts=script_entries,
            script_dedup_drops=script_dedup_drops,
            script_dedup_enabled=enable_script_dedup,
        )

    # ── RENDER wave — the ARMED, paid half (TTS → poster → persist) ───────────
    produced_stories = await _render_story_pool(
        write_results=write_results,
        tts_client=tts_client,
        supabase_client=supabase_client,
        llm_client=llm_client,
        poster_genai_client=poster_genai_client,
        max_concurrent=max_concurrent_productions,
        enable_detail_enrichment=enable_detail_enrichment,
        outlets_lookup=outlets_lookup,
        gdelt_adapter=gdelt_adapter,
    )

    # ── Stages D+E — score per user + allocate ~30-slot daily_feeds ───────────
    active_user_inputs = load_active_user_inputs(
        supabase_client, target_date, exploration_by_user
    )
    # ── FSR-M3 cluster-importance bridge ──────────────────────────────────────
    # The assembly seam threads a SHARED ``cluster_importance_by_story`` map through
    # ``assemble_daily_feeds`` → ``assemble_user_feed`` → ``score_and_classify_for_user`` →
    # ``compute_story_score(cluster_importance=…)`` so a clustered story's Importance term
    # is its authority-weighted, within-category-normalized E1 score
    # (``agents/pipeline/importance/story_importance.score_clusters``), falling back to the
    # raw outlet count for un-clustered stories (Rule 3, additive).
    #
    # The SOURCE of that map is produced upstream in Stage B.5 by
    # ``reconcile_story_ids_via_clustering`` when ``enable_semantic_clustering`` is on
    # (``cluster_importance_by_story`` was set there). When the flag is off it stays
    # ``None`` and the un-clustered raw-importance fallback is used (no fake).
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
    # ── X theme FK guard (slice #31, residual R3) — keep only candidates whose reel
    # story is placeable (produced this run, or already carrying a current digest) so
    # a verification/render halt can never fail a user's batched daily_feeds insert.
    # Eligible users are kept even at zero candidates (honest ladder → news floor).
    x_theme_candidates_by_user: dict[str, list[XThemeReelCandidate]] | None = None
    if x_theme_gather is not None:
        placeable_theme_ids = {s.canonical_story_id for s in produced_stories}
        placeable_theme_ids |= {
            story_id for story_id, has in x_theme_already_produced.items() if has
        }
        x_theme_candidates_by_user = filter_placeable_theme_candidates(
            x_theme_gather.candidates_by_user, placeable_theme_ids
        )
    feeds = assemble_daily_feeds(
        target_date=target_date,
        active_user_inputs=active_user_inputs,
        stories=produced_stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        supabase_client=supabase_client,
        now_utc=now,
        source_stories_by_user=produced_source_by_user,
        x_theme_candidates_by_user=x_theme_candidates_by_user,
        cluster_importance_by_story=cluster_importance_by_story,
        category_override_by_story=category_override_by_story,
    )

    logger.info(
        "run_daily_pipeline_completed",
        feed_date=target_date.isoformat(),
        candidate_story_count=len(stories),
        produced_story_count=len(produced_stories),
        dedup_dropped_count=dedup_dropped_count,
        capped_count=capped_count,
        feeds_written=feeds.feeds_written,
        # Reason (issue #67): positive log evidence of the run mode on PRODUCING runs
        # too — an absent error line is not proof the semantic gate ran.
        semantic_relevance_mode=semantic_relevance.semantic_relevance_mode,
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
        semantic_relevance=semantic_relevance,
    )
