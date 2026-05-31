"""Tests for the §4 profile-update job (engagement → bounded weight nudges).

These assert on the RESULTING ``profile_weight`` values — the prioritization
logic (engaged rises, ignored fades, bounds hold) — not merely that a write
happened (Rule 9). Mutation note: zeroing ``DECAY_RATE`` makes the ignored-falls
assertion fail; zeroing ``STRONG_POSITIVE_DELTA`` makes the engaged-rises
assertion fail; removing the clamp makes the floor/cap assertions fail.
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.ingestion.models import StoryInterestTag
from agents.memory.player_signals import (
    MILD_POSITIVE_DELTA,
    PLAY_MAX_POSITIVE_DELTA,
    SKIP_NEGATIVE_DELTA,
    STRONG_POSITIVE_DELTA,
    SignalEvent,
    compute_signal_delta,
)
from agents.memory.session_processor import (
    BASELINE_WEIGHT,
    DEPTH_ATTENUATION,
    MAX_DELTA_PER_RUN,
    PROFILE_WEIGHT_CEILING,
    PROFILE_WEIGHT_FLOOR,
    compute_weight_updates,
    run_profile_update_job,
)
from agents.pipeline.stages.ranking import UserProfileInterest


def _tag(story_id: str, interest_id: str, depth: int) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=depth,
    )


def _complete(story_id: str) -> SignalEvent:
    return SignalEvent(
        signal_user_id="u1", signal_story_id=story_id, event_type="complete"
    )


# ── The headline DoD: engaged rises, ignored falls, all within bounds ─────────
def test_engaged_interest_rises_and_ignored_interest_falls_within_bounds() -> None:
    """Engaged interest's weight rises (capped); un-engaged interest decays toward
    baseline (without snapping to it); both stay within [FLOOR, CEILING]."""
    engaged = UserProfileInterest(profile_interest_id="int-arsenal", profile_weight=1.0)
    ignored = UserProfileInterest(profile_interest_id="int-cricket", profile_weight=2.0)
    # Five completes on Arsenal stories; nothing on cricket.
    signals = [_complete(f"s-ars-{i}") for i in range(5)]
    tags = [_tag(f"s-ars-{i}", "int-arsenal", 0) for i in range(5)]

    updates = {
        u.interest_id: u
        for u in compute_weight_updates([engaged, ignored], signals, tags)
    }

    arsenal = updates["int-arsenal"]
    cricket = updates["int-cricket"]

    # Engaged rises; the raw delta (5 * 0.30 = 1.5) is capped to the per-run max.
    assert arsenal.new_weight > arsenal.old_weight
    assert arsenal.raw_delta == 5 * STRONG_POSITIVE_DELTA
    assert arsenal.applied_delta == MAX_DELTA_PER_RUN

    # Ignored decays toward baseline but does NOT snap to it (slow decay).
    assert cricket.new_weight < cricket.old_weight
    assert cricket.new_weight > BASELINE_WEIGHT
    assert cricket.applied_delta == 0.0

    # Both remain within bounds (no over-narrowing).
    for upd in (arsenal, cricket):
        assert PROFILE_WEIGHT_FLOOR <= upd.new_weight <= PROFILE_WEIGHT_CEILING


def test_fast_skips_lower_weight_but_never_below_floor() -> None:
    """A near-floor interest hammered with fast skips clamps at the floor."""
    near_floor = UserProfileInterest(
        profile_interest_id="int-golf", profile_weight=0.15
    )
    skips = [
        SignalEvent(
            signal_user_id="u1",
            signal_story_id=f"s-golf-{i}",
            event_type="skip",
            dwell_ms=400,
        )
        for i in range(3)
    ]
    tags = [_tag(f"s-golf-{i}", "int-golf", 0) for i in range(3)]

    (golf,) = compute_weight_updates([near_floor], skips, tags)

    assert golf.raw_delta == 3 * SKIP_NEGATIVE_DELTA  # negative aggregate
    assert golf.applied_delta == -MAX_DELTA_PER_RUN  # capped
    assert golf.new_weight == PROFILE_WEIGHT_FLOOR  # clamped, not below


def test_ancestor_attenuation_nudges_broad_follower_less_than_leaf_follower() -> None:
    """The same niche-story engagement nudges a parent-follower at the parent
    attenuation (0.5) — less than a leaf-follower gets — exercising DepthMatch."""
    niche_tags = [_tag("s-niche", "int-arsenal", 0), _tag("s-niche", "int-football", 1)]
    one_complete = [_complete("s-niche")]

    leaf_follower = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=1.0
    )
    parent_follower = UserProfileInterest(
        profile_interest_id="int-football", profile_weight=1.0
    )

    (leaf,) = compute_weight_updates([leaf_follower], one_complete, niche_tags)
    (parent,) = compute_weight_updates([parent_follower], one_complete, niche_tags)

    assert leaf.raw_delta == STRONG_POSITIVE_DELTA * DEPTH_ATTENUATION[0]
    assert parent.raw_delta == STRONG_POSITIVE_DELTA * DEPTH_ATTENUATION[1]
    assert parent.raw_delta == 0.5 * leaf.raw_delta


def test_unfollowed_interest_tags_do_not_nudge() -> None:
    """A signal on a story tagged only to interests the user does NOT follow
    leaves the user's weights to pure decay (no phantom nudges)."""
    follows = UserProfileInterest(profile_interest_id="int-arsenal", profile_weight=1.5)
    # Signal story is tagged to cricket only — user follows arsenal only.
    signals = [_complete("s-cricket")]
    tags = [_tag("s-cricket", "int-cricket", 0)]

    (arsenal,) = compute_weight_updates([follows], signals, tags)

    assert arsenal.raw_delta == 0.0
    assert arsenal.new_weight < arsenal.old_weight  # decay only


# ── Per-signal mapping (player_signals) ───────────────────────────────────────
def test_compute_signal_delta_maps_each_event_direction() -> None:
    base = {"signal_user_id": "u1", "signal_story_id": "s1"}
    assert compute_signal_delta(SignalEvent(**base, event_type="complete")) == (
        STRONG_POSITIVE_DELTA
    )
    assert compute_signal_delta(SignalEvent(**base, event_type="open_detail")) == (
        MILD_POSITIVE_DELTA
    )
    # play scales with completion_pct
    assert (
        compute_signal_delta(SignalEvent(**base, event_type="play", completion_pct=1.0))
        == PLAY_MAX_POSITIVE_DELTA
    )
    assert (
        compute_signal_delta(SignalEvent(**base, event_type="play", completion_pct=0.2))
        == PLAY_MAX_POSITIVE_DELTA * 0.2
    )
    # a 0–100 completion is normalized
    assert (
        compute_signal_delta(SignalEvent(**base, event_type="play", completion_pct=50))
        == PLAY_MAX_POSITIVE_DELTA * 0.5
    )
    # only a FAST skip is punitive
    assert (
        compute_signal_delta(SignalEvent(**base, event_type="skip", dwell_ms=400))
        == SKIP_NEGATIVE_DELTA
    )
    assert (
        compute_signal_delta(SignalEvent(**base, event_type="skip", dwell_ms=9000))
        == 0.0
    )
    # an unmapped event is inert
    assert compute_signal_delta(SignalEvent(**base, event_type="share")) == 0.0


def test_orphaned_signal_without_story_contributes_nothing() -> None:
    follows = UserProfileInterest(profile_interest_id="int-arsenal", profile_weight=1.0)
    orphan = SignalEvent(
        signal_user_id="u1", signal_story_id=None, event_type="complete"
    )
    (arsenal,) = compute_weight_updates([follows], [orphan], [])
    assert arsenal.raw_delta == 0.0


# ── The Supabase wrapper (mocked client) ──────────────────────────────────────
class _FakeBuilder:
    def __init__(self, table_name: str, store: "_FakeSupabase") -> None:
        self._table = table_name
        self._store = store
        self._mode = "select"
        self._payload: dict | None = None
        self._filters: dict[str, object] = {}

    def select(self, *_cols: str) -> "_FakeBuilder":
        self._mode = "select"
        return self

    def update(self, payload: dict) -> "_FakeBuilder":
        self._mode = "update"
        self._payload = payload
        return self

    def in_(self, _col: str, _vals: list) -> "_FakeBuilder":
        return self

    def gte(self, _col: str, _val: object) -> "_FakeBuilder":
        return self

    def eq(self, col: str, val: object) -> "_FakeBuilder":
        self._filters[col] = val
        return self

    def execute(self) -> SimpleNamespace:
        if self._mode == "update":
            self._store.updates.append(
                {
                    "table": self._table,
                    "payload": self._payload,
                    "filters": self._filters,
                }
            )
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self._store.tables.get(self._table, []))


class _FakeSupabase:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        self.updates: list[dict] = []

    def table(self, name: str) -> _FakeBuilder:
        return _FakeBuilder(name, self)


def test_run_profile_update_job_writes_only_changed_weights() -> None:
    """The wrapper writes the new weight for an engaged AND a decayed interest,
    and the persisted payloads carry the §4-computed values."""
    fake = _FakeSupabase(
        {
            "user_interest_profile": [
                {
                    "profile_user_id": "u1",
                    "profile_interest_id": "int-arsenal",
                    "profile_weight": 1.0,
                    "profile_is_strict": False,
                },
                {
                    "profile_user_id": "u1",
                    "profile_interest_id": "int-cricket",
                    "profile_weight": 2.0,
                    "profile_is_strict": False,
                },
            ],
            "player_signals": [
                {
                    "signal_user_id": "u1",
                    "signal_story_id": "s-ars",
                    "event_type": "complete",
                    "dwell_ms": None,
                    "completion_pct": None,
                }
            ],
            "story_interests": [
                {
                    "story_interest_story_id": "s-ars",
                    "story_interest_interest_id": "int-arsenal",
                    "story_interest_match_depth": 0,
                }
            ],
        }
    )

    result = run_profile_update_job(fake)

    assert result.users_processed == 1
    assert result.weights_changed == 2  # arsenal (rose) + cricket (decayed)
    by_interest = {u["filters"]["profile_interest_id"]: u for u in fake.updates}
    assert by_interest["int-arsenal"]["payload"]["profile_weight"] > 1.0
    assert by_interest["int-cricket"]["payload"]["profile_weight"] < 2.0
    # every write targets the right user and stamps profile_updated_at
    for write in fake.updates:
        assert write["filters"]["profile_user_id"] == "u1"
        assert "profile_updated_at" in write["payload"]
