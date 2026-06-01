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
    FOLLOW_BOOST_DELTA,
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


# ── Follow boost (phase-3d) — persistent `follows` set raises the matched node ─
def test_following_a_story_raises_matched_interest_vs_non_following_user() -> None:
    """The §4 DoD (pure core): with NO signals at all, a user who FOLLOWS a story
    ends the run with a HIGHER matched-interest weight than an identical user who
    does not follow it — and the boosted weight stays within the bounded ceiling
    (no over-narrowing). The follow boost is sourced from `followed_story_ids`."""
    tags = [_tag("s-arsenal", "int-arsenal", 0)]

    follower = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=1.0
    )
    non_follower = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=1.0
    )

    # Identical inputs except one user follows the story; NO player_signals.
    (followed,) = compute_weight_updates(
        [follower],
        signals=[],
        story_interest_tags=tags,
        followed_story_ids=["s-arsenal"],
    )
    (not_followed,) = compute_weight_updates(
        [non_follower],
        signals=[],
        story_interest_tags=tags,
        followed_story_ids=[],
    )

    # The follow boost is the ONLY difference — and it is what raises the weight.
    assert followed.raw_delta == FOLLOW_BOOST_DELTA
    assert not_followed.raw_delta == 0.0
    assert followed.new_weight > not_followed.new_weight
    # The following user's weight rises above its starting point (boost beats decay).
    assert followed.new_weight > follower.profile_weight
    # Bounded: a single-run follow cannot exceed the per-run cap or the ceiling.
    assert followed.applied_delta <= MAX_DELTA_PER_RUN
    assert followed.new_weight <= PROFILE_WEIGHT_CEILING


def test_unfollowing_before_the_run_yields_no_boost() -> None:
    """Un-following (story absent from `followed_story_ids`) removes the boost: the
    weight then only decays toward baseline — exactly the non-following path."""
    tags = [_tag("s-arsenal", "int-arsenal", 0)]
    interest = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=1.5
    )

    (after_unfollow,) = compute_weight_updates(
        [interest], signals=[], story_interest_tags=tags, followed_story_ids=[]
    )

    assert after_unfollow.raw_delta == 0.0
    assert after_unfollow.new_weight < after_unfollow.old_weight  # decay only


def test_follow_boost_cannot_push_weight_past_the_ceiling() -> None:
    """Stacking the per-run cap onto an already-near-ceiling weight clamps at the
    ceiling — a persistent follow can never over-narrow past the bound (§3/§4)."""
    near_ceiling = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=PROFILE_WEIGHT_CEILING
    )
    # Follow many stories all tagged to the same node → aggregate exceeds the cap.
    followed_ids = [f"s-arsenal-{i}" for i in range(5)]
    tags = [_tag(sid, "int-arsenal", 0) for sid in followed_ids]

    (arsenal,) = compute_weight_updates(
        [near_ceiling],
        signals=[],
        story_interest_tags=tags,
        followed_story_ids=followed_ids,
    )

    assert arsenal.applied_delta == MAX_DELTA_PER_RUN  # aggregate capped
    assert arsenal.new_weight == PROFILE_WEIGHT_CEILING  # clamped, not above


def test_follow_attenuates_to_ancestor_followers_like_a_signal() -> None:
    """A follow on a leaf-tagged story nudges a PARENT-follower at the parent
    attenuation (0.5) — mirroring signal behavior, not a divergent path."""
    niche_tags = [
        _tag("s-niche", "int-arsenal", 0),
        _tag("s-niche", "int-football", 1),
    ]
    leaf_follower = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=1.0
    )
    parent_follower = UserProfileInterest(
        profile_interest_id="int-football", profile_weight=1.0
    )

    (leaf,) = compute_weight_updates(
        [leaf_follower],
        signals=[],
        story_interest_tags=niche_tags,
        followed_story_ids=["s-niche"],
    )
    (parent,) = compute_weight_updates(
        [parent_follower],
        signals=[],
        story_interest_tags=niche_tags,
        followed_story_ids=["s-niche"],
    )

    assert leaf.raw_delta == FOLLOW_BOOST_DELTA * DEPTH_ATTENUATION[0]
    assert parent.raw_delta == FOLLOW_BOOST_DELTA * DEPTH_ATTENUATION[1]
    assert parent.raw_delta == 0.5 * leaf.raw_delta


def test_transient_follow_signal_is_inert_no_double_count() -> None:
    """A `player_signals` row with event_type='follow' contributes 0.0, so the
    follow boost is counted ONCE (from the persistent `follows` set only)."""
    interest = UserProfileInterest(
        profile_interest_id="int-arsenal", profile_weight=1.0
    )
    tags = [_tag("s-arsenal", "int-arsenal", 0)]
    transient_follow_signal = SignalEvent(
        signal_user_id="u1", signal_story_id="s-arsenal", event_type="follow"
    )

    # Same story both as a transient `follow` signal AND in the follows set.
    (with_both,) = compute_weight_updates(
        [interest],
        signals=[transient_follow_signal],
        story_interest_tags=tags,
        followed_story_ids=["s-arsenal"],
    )

    # Only the persistent follow contributes — the transient signal adds nothing.
    assert with_both.raw_delta == FOLLOW_BOOST_DELTA


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


def test_run_job_follow_raises_weight_vs_identical_non_following_user() -> None:
    """End-to-end DoD through the mocked Supabase client (Rule 9 — asserts on the
    PERSISTED ``profile_weight``, not the read): two identical users follow the
    SAME interest at the SAME baseline weight; only ``u_follower`` has a ``follows``
    row for a story tagged to that interest. With NO player_signals, the follower's
    persisted weight RISES above the non-follower's (which only decays), and stays
    within the ceiling (no over-narrowing)."""
    baseline = 1.0
    fake = _FakeSupabase(
        {
            "user_interest_profile": [
                {
                    "profile_user_id": "u_follower",
                    "profile_interest_id": "int-arsenal",
                    "profile_weight": baseline,
                    "profile_is_strict": False,
                },
                {
                    "profile_user_id": "u_non_follower",
                    "profile_interest_id": "int-arsenal",
                    "profile_weight": baseline,
                    "profile_is_strict": False,
                },
            ],
            "player_signals": [],  # isolate the follow boost from any signal
            "follows": [
                {"follow_user_id": "u_follower", "follow_story_id": "s-arsenal"}
            ],
            "story_interests": [
                {
                    "story_interest_story_id": "s-arsenal",
                    "story_interest_interest_id": "int-arsenal",
                    "story_interest_match_depth": 0,
                }
            ],
        }
    )

    result = run_profile_update_job(fake)

    writes_by_user = {
        w["filters"]["profile_user_id"]: w["payload"]["profile_weight"]
        for w in fake.updates
    }
    follower_weight = writes_by_user["u_follower"]
    # The non-follower at baseline==1.0 decays to exactly 1.0 (== old), so its row
    # is not written; its effective persisted weight is therefore the baseline.
    non_follower_weight = writes_by_user.get("u_non_follower", baseline)

    assert result.users_processed == 2
    # The follow boost beats decay → follower rises above baseline AND above peer.
    assert follower_weight > baseline
    assert follower_weight > non_follower_weight
    # Bounded: even with the boost the persisted weight respects the ceiling.
    assert follower_weight <= PROFILE_WEIGHT_CEILING


def test_run_job_no_follows_table_rows_is_a_no_op_for_follow_boost() -> None:
    """With an empty ``follows`` set, the job behaves exactly as the signal-only
    path — un-following before the run yields no boost (the persisted weight only
    reflects decay/signals, never a phantom follow)."""
    fake = _FakeSupabase(
        {
            "user_interest_profile": [
                {
                    "profile_user_id": "u1",
                    "profile_interest_id": "int-arsenal",
                    "profile_weight": 2.0,
                    "profile_is_strict": False,
                }
            ],
            "player_signals": [],
            "follows": [],  # user un-followed everything before the run
            "story_interests": [],
        }
    )

    run_profile_update_job(fake)

    # Only decay applied → the single write is strictly BELOW the prior weight.
    assert len(fake.updates) == 1
    assert fake.updates[0]["payload"]["profile_weight"] < 2.0
