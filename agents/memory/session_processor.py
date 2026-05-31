"""Daily profile-update job (ranking-spec §4) — the engine that makes the feed
feel personal over time.

Runs FIRST in the daily batch (before scoring), so today's feed reflects
yesterday's behavior. It aggregates ``player_signals`` per followed interest and
applies **bounded, slow-decay** nudges to ``user_interest_profile.profile_weight``:

  1. attenuate each signal by how specifically its story hit the interest
     (leaf 1.0 / parent 0.5 / grandparent 0.25),
  2. cap the aggregate per-run delta (``MAX_DELTA_PER_RUN``),
  3. decay every followed weight slowly toward baseline (an ignored interest
     fades, it does not snap to zero),
  4. clamp the result to ``[FLOOR, CEILING]``.

These guards (cap + decay + clamp), together with the allocator's floor-1 / 40%
cap / 10% exploration invariants (§3), are what stop the feed collapsing onto one
topic even if weights drift — the brief's explicit over-narrowing caution.

The pure core (``compute_weight_updates``) is asserted in tests on the resulting
weights (Rule 9 — prioritization logic, not just the write). ``run_profile_update_job``
is the thin Supabase read/write wrapper (client injected; mocked in tests, real in
the live e2e).

NOTE (deferred, M1): exploration→follow conversion (creating new
``profile_source='signal'`` rows when a user engages an interest they don't yet
follow) is NOT implemented here — this job only nudges EXISTING followed weights.
Documented extension, not in M1 scope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import StoryInterestTag
from agents.memory.player_signals import SignalEvent, compute_signal_delta
from agents.pipeline.stages.ranking import UserProfileInterest
from agents.shared.logger import get_logger

logger = get_logger("memory.session_processor")

# ── Bounds + decay (tunable config; ranking-spec §4 guards) ───────────────────
BASELINE_WEIGHT: float = 1.0  # the resting weight a new 'typed' follow starts at
PROFILE_WEIGHT_FLOOR: float = 0.1  # never zero — an ignored interest fades, not dies
PROFILE_WEIGHT_CEILING: float = 5.0  # a loved interest saturates (caps affinity)
MAX_DELTA_PER_RUN: float = 0.5  # one run cannot swing a weight more than this
DECAY_RATE: float = 0.1  # 10% of the gap to baseline closes each run (slow)

# DepthMatch attenuation: how much a signal on an ANCESTOR-tagged story nudges the
# followed node (index = story_interests.match_depth: leaf / parent / grandparent).
DEPTH_ATTENUATION: tuple[float, float, float] = (1.0, 0.5, 0.25)


class InterestWeightUpdate(BaseModel):
    """The computed weight change for one followed interest in one run.

    Carries the components so tests assert on the §4 math (decay + bounded delta),
    not just the final number.

    Attributes:
        interest_id: ``user_interest_profile.profile_interest_id`` updated.
        old_weight: ``profile_weight`` before the run.
        new_weight: ``profile_weight`` after decay + bounded delta + clamp.
        raw_delta: Aggregated, depth-attenuated signal delta BEFORE the per-run cap.
        applied_delta: ``raw_delta`` after the per-run cap (what was added post-decay).

    Example:
        >>> upd = InterestWeightUpdate(
        ...     interest_id="int-arsenal", old_weight=1.0, new_weight=1.5,
        ...     raw_delta=0.9, applied_delta=0.5)
        >>> upd.new_weight
        1.5
    """

    interest_id: str = Field(..., description="The followed interest updated")
    old_weight: float = Field(..., description="profile_weight before the run")
    new_weight: float = Field(..., description="profile_weight after the run")
    raw_delta: float = Field(..., description="Aggregated attenuated delta pre-cap")
    applied_delta: float = Field(..., description="Delta after the per-run cap")


class ProfileUpdateResult(BaseModel):
    """Summary of one ``run_profile_update_job`` execution.

    Attributes:
        users_processed: How many users had their profile evaluated.
        weights_changed: How many ``user_interest_profile`` rows were written.
        updates: Per-(user, interest) audit records.

    Example:
        >>> ProfileUpdateResult(users_processed=2, weights_changed=3).weights_changed
        3
    """

    users_processed: int = Field(default=0, ge=0)
    weights_changed: int = Field(default=0, ge=0)
    updates: dict[str, list[InterestWeightUpdate]] = Field(default_factory=dict)


def _index_tags_by_story(
    story_interest_tags: list[StoryInterestTag],
) -> dict[str, list[tuple[str, int]]]:
    """Map ``story_id -> [(interest_id, match_depth), ...]`` for attenuation."""
    index: dict[str, list[tuple[str, int]]] = {}
    for tag in story_interest_tags:
        index.setdefault(tag.story_interest_story_id, []).append(
            (tag.story_interest_interest_id, tag.story_interest_match_depth)
        )
    return index


def compute_weight_updates(
    profile_interests: list[UserProfileInterest],
    signals: list[SignalEvent],
    story_interest_tags: list[StoryInterestTag],
) -> list[InterestWeightUpdate]:
    """Compute one run's bounded, slow-decay weight updates for ONE user (§4).

    For every followed interest it (1) sums the depth-attenuated deltas of the
    user's signals whose story is tagged to that interest, (2) caps the aggregate
    at ``±MAX_DELTA_PER_RUN``, (3) decays the old weight slowly toward
    ``BASELINE_WEIGHT``, and (4) clamps to ``[FLOOR, CEILING]``. EVERY followed
    interest gets an update record — even one with no signal — so neglect-driven
    decay (the "ignored interest fades" guard) is applied and auditable.

    Args:
        profile_interests: The user's followed interests (current weights).
        signals: The user's ``player_signals`` since the last run.
        story_interest_tags: ``story_interests`` rows for the signalled stories
            (provides each story's interest + ancestor ``match_depth``).

    Returns:
        One :class:`InterestWeightUpdate` per followed interest.

    Example:
        >>> updates = compute_weight_updates([], [], [])
        >>> updates
        []
    """
    tags_by_story = _index_tags_by_story(story_interest_tags)
    followed_ids = {pi.profile_interest_id for pi in profile_interests}

    # 1. Aggregate depth-attenuated signal deltas onto followed interests.
    raw_delta_by_interest: dict[str, float] = {iid: 0.0 for iid in followed_ids}
    for signal in signals:
        if signal.signal_story_id is None:
            continue
        base_delta = compute_signal_delta(signal)
        if base_delta == 0.0:
            continue
        for interest_id, match_depth in tags_by_story.get(signal.signal_story_id, []):
            if interest_id not in followed_ids:
                continue  # only nudge interests the user actually follows (M1)
            attenuation = DEPTH_ATTENUATION[
                min(match_depth, len(DEPTH_ATTENUATION) - 1)
            ]
            raw_delta_by_interest[interest_id] += base_delta * attenuation

    # 2–4. Cap, decay toward baseline, clamp — per followed interest.
    updates: list[InterestWeightUpdate] = []
    for interest in profile_interests:
        old_weight = interest.profile_weight
        raw_delta = raw_delta_by_interest.get(interest.profile_interest_id, 0.0)
        applied_delta = max(-MAX_DELTA_PER_RUN, min(MAX_DELTA_PER_RUN, raw_delta))
        # Reason: slow pull toward baseline so an un-engaged interest fades instead
        # of snapping to zero; an engaged interest's positive delta outweighs it.
        decayed = old_weight + (BASELINE_WEIGHT - old_weight) * DECAY_RATE
        new_weight = max(
            PROFILE_WEIGHT_FLOOR, min(PROFILE_WEIGHT_CEILING, decayed + applied_delta)
        )
        updates.append(
            InterestWeightUpdate(
                interest_id=interest.profile_interest_id,
                old_weight=old_weight,
                new_weight=new_weight,
                raw_delta=raw_delta,
                applied_delta=applied_delta,
            )
        )
    return updates


def _load_user_profiles(
    supabase_client: Any, user_ids: list[str] | None
) -> dict[str, list[UserProfileInterest]]:
    """Read ``user_interest_profile`` rows, grouped by user (injected client)."""
    query = supabase_client.table("user_interest_profile").select(
        "profile_user_id,profile_interest_id,profile_weight,profile_is_strict"
    )
    if user_ids:
        query = query.in_("profile_user_id", user_ids)
    rows = getattr(query.execute(), "data", None) or []
    profiles: dict[str, list[UserProfileInterest]] = {}
    for row in rows:
        profiles.setdefault(str(row["profile_user_id"]), []).append(
            UserProfileInterest(
                profile_interest_id=str(row["profile_interest_id"]),
                profile_weight=float(row["profile_weight"]),
                profile_is_strict=bool(row["profile_is_strict"]),
            )
        )
    return profiles


def _load_signals(
    supabase_client: Any, user_ids: list[str] | None, since_utc: datetime | None
) -> dict[str, list[SignalEvent]]:
    """Read ``player_signals`` since ``since_utc``, grouped by user."""
    query = supabase_client.table("player_signals").select(
        "signal_user_id,signal_story_id,event_type,dwell_ms,completion_pct"
    )
    if user_ids:
        query = query.in_("signal_user_id", user_ids)
    if since_utc is not None:
        query = query.gte("occurred_at", since_utc.isoformat())
    rows = getattr(query.execute(), "data", None) or []
    signals: dict[str, list[SignalEvent]] = {}
    for row in rows:
        signals.setdefault(str(row["signal_user_id"]), []).append(
            SignalEvent(
                signal_user_id=str(row["signal_user_id"]),
                signal_story_id=(
                    str(row["signal_story_id"]) if row.get("signal_story_id") else None
                ),
                event_type=str(row["event_type"]),
                dwell_ms=row.get("dwell_ms"),
                completion_pct=row.get("completion_pct"),
            )
        )
    return signals


def _load_story_interests(
    supabase_client: Any, story_ids: list[str]
) -> list[StoryInterestTag]:
    """Read ``story_interests`` rows for the signalled stories (for attenuation)."""
    if not story_ids:
        return []
    rows = (
        getattr(
            supabase_client.table("story_interests")
            .select(
                "story_interest_story_id,story_interest_interest_id,story_interest_match_depth"
            )
            .in_("story_interest_story_id", story_ids)
            .execute(),
            "data",
            None,
        )
        or []
    )
    return [
        StoryInterestTag(
            story_interest_story_id=str(row["story_interest_story_id"]),
            story_interest_interest_id=str(row["story_interest_interest_id"]),
            story_interest_match_depth=int(row["story_interest_match_depth"]),
        )
        for row in rows
    ]


def run_profile_update_job(
    supabase_client: Any,
    user_ids: list[str] | None = None,
    since_utc: datetime | None = None,
    now_utc: datetime | None = None,
) -> ProfileUpdateResult:
    """Aggregate signals and write bounded, slow-decay weight nudges (§4).

    The daily batch's FIRST stage. Reads each user's profile + recent signals +
    the signalled stories' interest tags, computes the updates
    (``compute_weight_updates``), and writes the changed ``profile_weight`` rows.
    A weight equal to its prior value is not written.

    Args:
        supabase_client: Service-role client (injected; mocked in tests).
        user_ids: Restrict to these users (None = all users with a profile).
        since_utc: Only aggregate signals at/after this time (None = all signals).
        now_utc: Timestamp for ``profile_updated_at`` (defaults to ``utcnow``).

    Returns:
        A :class:`ProfileUpdateResult` summarizing users processed + rows written.

    Example:
        >>> # See tests/agents/memory/test_session_processor.py for the seeded
        >>> # engaged-rises / ignored-falls / within-bounds assertions.
    """
    now = now_utc or datetime.now(timezone.utc)
    profiles = _load_user_profiles(supabase_client, user_ids)
    signals_by_user = _load_signals(supabase_client, user_ids, since_utc)

    all_story_ids = sorted(
        {
            s.signal_story_id
            for user_signals in signals_by_user.values()
            for s in user_signals
            if s.signal_story_id is not None
        }
    )
    story_interest_tags = _load_story_interests(supabase_client, all_story_ids)

    logger.info(
        "run_profile_update_job_started",
        user_count=len(profiles),
        signalled_story_count=len(all_story_ids),
        since_utc=since_utc.isoformat() if since_utc else None,
    )

    result = ProfileUpdateResult(users_processed=len(profiles))
    for user_id, profile_interests in profiles.items():
        updates = compute_weight_updates(
            profile_interests=profile_interests,
            signals=signals_by_user.get(user_id, []),
            story_interest_tags=story_interest_tags,
        )
        written: list[InterestWeightUpdate] = []
        for update in updates:
            if update.new_weight == update.old_weight:
                continue
            (
                supabase_client.table("user_interest_profile")
                .update(
                    {
                        "profile_weight": update.new_weight,
                        "profile_updated_at": now.isoformat(),
                    }
                )
                .eq("profile_user_id", user_id)
                .eq("profile_interest_id", update.interest_id)
                .execute()
            )
            written.append(update)
        if written:
            result.updates[user_id] = written
            result.weights_changed += len(written)

    logger.info(
        "run_profile_update_job_completed",
        users_processed=result.users_processed,
        weights_changed=result.weights_changed,
    )
    return result
