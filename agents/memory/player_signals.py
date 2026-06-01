"""Per-signal engagement → weight-delta mapping (ranking-spec §4).

Pure, side-effect-free scoring of one ``player_signals`` row into a raw weight
delta. The aggregation + bounded application lives in ``session_processor``; this
module only answers "how much, and which direction, does THIS event nudge?".

Signal → effect (ranking-spec §4 table):
  complete / save / ask / voice → strong +
  open_detail                   → mild +
  play (partial)                → small +, scaled by completion_pct
  skip (fast, low dwell_ms)     → −

``follow`` is intentionally NOT a transient signal here. As of phase-3d, a follow
persists in the ``follows`` table and is re-applied as a boost on EVERY daily run
while followed — the persistent ``follows`` set is the single source of truth for
the follow contribution (``session_processor`` reads it directly). So a transient
``player_signals`` row with ``event_type='follow'`` is INERT here (delta 0.0) to
avoid double-counting the same follow. See ``FOLLOW_BOOST_DELTA`` below.

Constants are FIRST-DRAFT and tunable (confirm at the 2-user manual run, phase
Open Q4); do not scatter copies — import from here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.shared.logger import get_logger

logger = get_logger("memory.player_signals")

# ── Per-event base deltas (tunable config; ranking-spec §4) ───────────────────
# Reason: affinity-dominant, bounded nudges. A single strong positive is ~⅔ of
# the per-run cap (session_processor.MAX_DELTA_PER_RUN=0.5), so it takes a few
# consistent strong signals — not one tap — to move an interest a full step.
STRONG_POSITIVE_DELTA: float = 0.30  # complete, save, ask, voice
MILD_POSITIVE_DELTA: float = 0.10  # open_detail
PLAY_MAX_POSITIVE_DELTA: float = 0.15  # play, multiplied by completion_pct (0–1)
SKIP_NEGATIVE_DELTA: float = -0.20  # a fast skip = explicit "not for me"

# Reason (phase-3d): a *persistent* follow is a deliberate, durable "more of this
# subniche" — stronger than a single transient strong signal, but still BELOW the
# per-run cap (session_processor.MAX_DELTA_PER_RUN=0.5) so that on its own a follow
# nudges hard yet cannot, in one run, jump a weight a full step toward the ceiling
# (over-narrowing guard). It is re-applied every run while the follow persists, so
# the cumulative pull is the decay-balanced equilibrium, not a single jump. Sourced
# from the `follows` table (NOT a player_signals row) to avoid double-counting.
FOLLOW_BOOST_DELTA: float = 0.40  # one followed story → its matched node, per run

# A skip is only punitive if the user bounced fast; a skip after watching most of
# a reel is not a rejection of the topic.
SKIP_FAST_DWELL_MS: int = 2000

# Default completion for a `play` row missing completion_pct (a partial watch we
# can't size) — modest positive, not a full strong signal.
PLAY_DEFAULT_COMPLETION: float = 0.3

# Reason: 'follow' is deliberately EXCLUDED — its contribution comes from the
# persistent `follows` set (FOLLOW_BOOST_DELTA, applied in session_processor), not
# from a transient signal row, so a `follow` event scores 0.0 here (no double-count).
_STRONG_POSITIVE_EVENTS: frozenset[str] = frozenset(
    {"complete", "save", "ask", "voice"}
)


class SignalEvent(BaseModel):
    """One ``player_signals`` row, as the weight job consumes it.

    Mirrors the ``player_signals`` columns the §4 job reads (migration 0003).

    Attributes:
        signal_user_id: ``signal_user_id`` — the user the event belongs to.
        signal_story_id: ``signal_story_id`` — the story engaged with (maps to
            interests via ``story_interests``). May be ``None`` if the story was
            deleted (``on delete set null``); such rows contribute nothing.
        event_type: ``event_type`` (``player_signal_event`` enum).
        dwell_ms: ``dwell_ms`` — milliseconds on the reel (drives the fast-skip
            test); ``None`` when not recorded.
        completion_pct: ``completion_pct`` — fraction watched (0–1; a 0–100 value
            is normalized). Scales a ``play`` nudge.

    Example:
        >>> ev = SignalEvent(
        ...     signal_user_id="u1", signal_story_id="s1", event_type="complete"
        ... )
        >>> ev.event_type
        'complete'
    """

    signal_user_id: str = Field(..., description="The user the event belongs to")
    signal_story_id: str | None = Field(
        default=None, description="The engaged story id (maps to interests)"
    )
    event_type: str = Field(..., description="player_signal_event value")
    dwell_ms: int | None = Field(default=None, description="Milliseconds on the reel")
    completion_pct: float | None = Field(
        default=None, description="Fraction watched (0–1; 0–100 normalized)"
    )


def _normalized_completion(completion_pct: float | None) -> float:
    """Coerce a raw completion_pct into a 0–1 fraction.

    Accepts either a 0–1 fraction or a 0–100 percentage (values > 1 are treated
    as a percentage and divided by 100), then clamps to ``[0, 1]``.
    """
    if completion_pct is None:
        return PLAY_DEFAULT_COMPLETION
    pct = completion_pct / 100.0 if completion_pct > 1.0 else completion_pct
    return max(0.0, min(1.0, pct))


def compute_signal_delta(event: SignalEvent) -> float:
    """Map one engagement event to its raw, un-attenuated weight delta.

    Direction and magnitude per ranking-spec §4. The result is the delta BEFORE
    depth attenuation (``session_processor`` scales it by how specifically the
    story hit the interest) and BEFORE the per-run cap.

    Args:
        event: The engagement event to score.

    Returns:
        A signed delta: ``+`` for engagement, ``−`` for a fast skip, ``0`` for a
        slow skip or an unscorable/orphaned event.

    Example:
        >>> compute_signal_delta(SignalEvent(
        ...     signal_user_id="u1", signal_story_id="s1", event_type="complete"))
        0.3
        >>> compute_signal_delta(SignalEvent(
        ...     signal_user_id="u1", signal_story_id="s1",
        ...     event_type="skip", dwell_ms=500))
        -0.2
    """
    event_type = event.event_type
    if event_type in _STRONG_POSITIVE_EVENTS:
        return STRONG_POSITIVE_DELTA
    if event_type == "open_detail":
        return MILD_POSITIVE_DELTA
    if event_type == "play":
        return PLAY_MAX_POSITIVE_DELTA * _normalized_completion(event.completion_pct)
    if event_type == "skip":
        # Reason: only a fast bounce is a rejection. dwell unknown ⇒ treat as fast
        # (no dwell recorded = the user did not engage).
        is_fast = event.dwell_ms is None or event.dwell_ms < SKIP_FAST_DWELL_MS
        return SKIP_NEGATIVE_DELTA if is_fast else 0.0
    if event_type == "follow":
        # Reason: a transient follow event is INERT here — the follow boost is
        # sourced from the persistent `follows` set (FOLLOW_BOOST_DELTA, applied in
        # session_processor) to avoid double-counting (phase-3d). Not "unhandled".
        return 0.0
    logger.info(
        "compute_signal_delta_unhandled_event",
        event_type=event_type,
        fix_suggestion="Add a §4 mapping if this player_signal_event should nudge.",
    )
    return 0.0
