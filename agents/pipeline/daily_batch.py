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
from agents.pipeline.feed_assembly import ScoredCandidate
from agents.pipeline.orchestrator import (
    DailyFeedsBatchResult,
    ActiveUserFeedInputs,
    assemble_daily_feeds,
    orchestrate_story,
)
from agents.pipeline.produce_gate import select_stories_to_produce
from agents.pipeline.stages.ranking import UserProfileInterest
from agents.shared.logger import get_logger
from agents.voice.gemini_tts import GeminiTTSClient

logger = get_logger("pipeline.daily_batch")

# Reason: a paid per-story render is heavy (TTS + image + 2 LLM passes); bound the
# concurrent fan-out so a large pool does not stampede the LLM/TTS quotas.
DEFAULT_MAX_CONCURRENT_PRODUCTIONS = 4

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
        feeds: The per-user allocation summary (stages D+E).

    Example:
        >>> # See tests/agents/pipeline/test_daily_batch.py for the staged asserts.
    """

    feed_date: str = Field(..., description="ISO feed date written")
    profile_update: ProfileUpdateResult = Field(default_factory=ProfileUpdateResult)
    candidate_story_count: int = Field(default=0, ge=0)
    produced_story_count: int = Field(default=0, ge=0)
    skipped_by_gate_count: int = Field(default=0, ge=0)
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
    rows = (
        getattr(
            supabase_client.table("digests")
            .select("digest_story_id")
            .in_("digest_story_id", story_ids)
            .eq("digest_is_current", True)
            .execute(),
            "data",
            None,
        )
        or []
    )
    return {str(row["digest_story_id"]): True for row in rows}


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

    inputs: list[ActiveUserFeedInputs] = []
    for user_id, profile_interests in interests_by_user.items():
        prior_rows = (
            getattr(
                supabase_client.table("daily_feeds")
                .select("feed_story_id")
                .eq("feed_user_id", user_id)
                .lt("feed_date", target_date.isoformat())
                .execute(),
                "data",
                None,
            )
            or []
        )
        prior_story_ids = [str(row["feed_story_id"]) for row in prior_rows]
        inputs.append(
            ActiveUserFeedInputs(
                active_user_id=user_id,
                profile_interests=profile_interests,
                prior_feed_story_ids=prior_story_ids,
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
) -> list[CanonicalStory]:
    """Produce each gated story into a digest, bounded-concurrently (stage C).

    Returns the subset of stories that published (a verification-halt or a render
    error skips that story but never aborts the batch — the feed still builds from
    whatever produced).
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
    produced_stories = await _produce_story_pool(
        stories_to_produce=to_produce,
        story_interest_tags=story_interest_tags,
        llm_client=llm_client,
        tts_client=tts_client,
        supabase_client=supabase_client,
        poster_genai_client=poster_genai_client,
        max_concurrent=max_concurrent_productions,
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
        feeds_written=feeds.feeds_written,
    )
    return DailyPipelineResult(
        feed_date=target_date.isoformat(),
        profile_update=profile_update,
        candidate_story_count=len(stories),
        produced_story_count=len(produced_stories),
        skipped_by_gate_count=len(stories) - len(to_produce),
        feeds=feeds,
    )
