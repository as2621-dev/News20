"""Offline ranking simulation runner — see what surfaces per profile, and why.

Runs the synthetic world (``world.py``) through the REAL allocator
(``assemble_user_feed``) for the three legacy profiles PLUS the phase-5a
entity-boost scenario (a "Build your 30" allocation + a custom Nvidia follow), and
prints a readable feed per profile with the Score breakdown (affinity / depth /
importance / freshness) and self-validating invariant checks. ``--days N``
additionally shows the profile-update loop drifting a broad user's weights over N
days (the §4 engine).

Zero paid APIs, fully deterministic.

Run:
    python -m agents.pipeline.sim.ranking_sim
    python -m agents.pipeline.sim.ranking_sim --days 5
    python -m agents.pipeline.sim.ranking_sim --profile B
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime

from agents.memory.player_signals import SignalEvent
from agents.memory.session_processor import compute_weight_updates
from agents.shared.logger import configure_logging
from agents.pipeline.feed_assembly import AllocatedSlot, assemble_user_feed
from agents.pipeline.sim.world import (
    SIM_NOW,
    SimProfile,
    build_entity_boost_scenario,
    build_exploration_candidates,
    build_profiles,
    build_taxonomy,
    build_world,
)
from agents.pipeline.stages.ranking import (
    ScoredCandidate,
    UserProfileInterest,
    score_and_classify_for_user,
    score_candidates_for_user,
)


def simulate_profile(
    profile: SimProfile,
    stories: list,
    story_interest_tags: list,
    interest_nodes: dict,
    now_utc: datetime = SIM_NOW,
    prior_feed_story_ids: set[str] | None = None,
) -> list[AllocatedSlot]:
    """Run one profile through the REAL allocator and return its ordered feed.

    The single seam both the CLI report and the pytest invariants call, so they
    assert on exactly what the report shows. The profile's ``followed_entities``
    (phase-5a EntityBonus) and ``category_allocation`` ("Build your 30" budgets)
    are forwarded to the allocator; legacy profiles leave both empty and fall
    through to the balanced default.

    Args:
        profile: The simulated user.
        stories: The story pool.
        story_interest_tags: All interest tags for the pool.
        interest_nodes: The taxonomy map.
        now_utc: The freshness clock (defaults to :data:`SIM_NOW`).
        prior_feed_story_ids: Story ids already shown (the §3.8 don't-repeat set).

    Returns:
        The ordered allocated slots (``feed_position`` 1..N).
    """
    exploration = build_exploration_candidates(
        profile, stories, story_interest_tags, now_utc
    )
    return assemble_user_feed(
        profile_interests=profile.interests,
        stories=stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        followed_entities=profile.followed_entities or None,
        category_allocation=profile.category_allocation or None,
        prior_feed_story_ids=prior_feed_story_ids,
        exploration_candidates_by_interest=exploration or None,
        now_utc=now_utc,
    )


def _component_lookup(
    profile: SimProfile,
    stories: list,
    story_interest_tags: list,
    interest_nodes: dict,
    now_utc: datetime,
) -> dict[str, ScoredCandidate]:
    """Best ScoredCandidate per story (followed + exploration) for the breakdown.

    When the profile follows entities, the ENTITY-AWARE buckets
    (:func:`score_and_classify_for_user`) are absorbed too so the rendered Score
    (and the per-story breakdown) matches the entity-aware Score the allocator
    actually used — the report and the asserted feed never diverge (Rule 9).
    """
    by_story: dict[str, ScoredCandidate] = {}

    def _absorb(buckets: dict[str, list[ScoredCandidate]]) -> None:
        for candidates in buckets.values():
            for candidate in candidates:
                current = by_story.get(candidate.story_id)
                if current is None or candidate.score > current.score:
                    by_story[candidate.story_id] = candidate

    _absorb(
        score_candidates_for_user(
            profile_interests=profile.interests,
            stories=stories,
            story_interest_tags=story_interest_tags,
            interest_nodes=interest_nodes,
            now_utc=now_utc,
        )
    )
    if profile.followed_entities:
        _absorb(
            score_and_classify_for_user(
                profile_interests=profile.interests,
                followed_entities=profile.followed_entities,
                stories=stories,
                story_interest_tags=story_interest_tags,
                interest_nodes=interest_nodes,
                now_utc=now_utc,
            )
        )
    _absorb(
        build_exploration_candidates(profile, stories, story_interest_tags, now_utc)
    )
    return by_story


def _interest_of(slot: AllocatedSlot, by_story: dict[str, ScoredCandidate]) -> str:
    """The interest a slot is attributed to (breaking slots fall back to best cand)."""
    if slot.feed_matched_interest_id:
        return slot.feed_matched_interest_id
    candidate = by_story.get(slot.feed_story_id)
    return candidate.matched_interest_id if candidate else "(unknown)"


def render_profile(
    profile: SimProfile,
    stories: list,
    story_interest_tags: list,
    interest_nodes: dict,
    now_utc: datetime = SIM_NOW,
) -> str:
    """Render one profile's feed + Score breakdown + invariant checks as text."""
    slots = simulate_profile(
        profile, stories, story_interest_tags, interest_nodes, now_utc
    )
    by_story = _component_lookup(
        profile, stories, story_interest_tags, interest_nodes, now_utc
    )
    title_by_id = {s.canonical_story_id: s.canonical_title for s in stories}

    follows = "  ".join(
        f"{i.profile_interest_id}(w{i.profile_weight:g}"
        + (", STRICT" if i.profile_is_strict else "")
        + ")"
        for i in profile.interests
    )
    lines = [
        "",
        f"── Profile {profile.profile_key} — {profile.label} ──",
        f"   follows: {follows}",
        "   pos  kind         score | aff  depth  imp   fresh | interest                 headline",
    ]
    for slot in slots:
        c = by_story.get(slot.feed_story_id)
        comp = (
            f"{c.affinity:4.2f} {c.depth_match:4.2f}  {c.importance:4.2f}  {c.freshness:4.2f}"
            if c
            else " n/a  n/a   n/a   n/a "
        )
        lines.append(
            f"   {slot.feed_position:>2}  {slot.feed_slot_kind:<11} "
            f"{slot.feed_score:5.2f} | {comp} | "
            f"{_interest_of(slot, by_story):<23} "
            f"{title_by_id.get(slot.feed_story_id, slot.feed_story_id)}"
        )

    kind_mix = Counter(s.feed_slot_kind for s in slots)
    interest_mix = Counter(_interest_of(s, by_story) for s in slots)
    lines.append(
        f"   → {len(slots)} slots  kinds={dict(kind_mix)}  per-interest={dict(interest_mix)}"
    )
    for label, ok in _profile_checks(profile, slots, by_story).items():
        lines.append(f"     [{'PASS' if ok else 'FAIL'}] {label}")

    # Observation (not a pass/fail): surface breaking-driven concentration so the
    # >40% share a dominant interest can reach via the breaking tier is visible,
    # not hidden — it is within spec (§3.1 breaking preempts the proportional cap).
    if slots:
        top_interest, top_count = interest_mix.most_common(1)[0]
        top_share = top_count / len(slots)
        if top_share > 0.40:
            breaking_for_top = sum(
                1
                for s in slots
                if s.feed_slot_kind == "breaking"
                and _interest_of(s, by_story) == top_interest
            )
            lines.append(
                f"     · note: {top_interest} is {top_share:.0%} of the feed "
                f"({breaking_for_top} via breaking-tier preemption, exempt from the "
                f"40% cap by §3.1)"
            )
    return "\n".join(lines)


def _profile_checks(
    profile: SimProfile,
    slots: list[AllocatedSlot],
    by_story: dict[str, ScoredCandidate],
) -> dict[str, bool]:
    """The per-profile invariant checks shown in the report AND asserted in tests.

    Keyed by a human label so the report and the pytest assertions stay in lock-step
    (Rule 9 — the checks encode WHY each profile matters).
    """
    story_ids = [s.feed_story_id for s in slots]
    interests_used = [_interest_of(s, by_story) for s in slots]
    checks: dict[str, bool] = {}

    if profile.profile_key == "A":
        # Strict cricket.india: every story must be a cricket.india story, and the
        # fallback must never broaden — so no parent/grandparent leak, no exploration.
        checks["strict: every slot is a cricket.india story (no fallback)"] = bool(
            slots
        ) and all(sid.startswith("sim-sport.cricket.india-") for sid in story_ids)
        checks["strict: no exploration slots"] = all(
            s.feed_slot_kind != "exploration" for s in slots
        )
    elif profile.profile_key == "B":
        # Broad multi-interest. The §3.4 cap bounds the INTEREST-fill bucket at
        # ~40% of N; breaking preemption (§3.1) is intentionally exempt — so the
        # real invariant is on interest-kind slots, not the whole feed.
        interest_slots = [s for s in slots if s.feed_slot_kind == "interest"]
        per_interest = Counter(_interest_of(s, by_story) for s in interest_slots)
        cap = round(0.40 * 30) + 1  # +1 for floor-1 / largest-remainder slack
        checks["broad: interest-fill cap ~40% holds (breaking exempt per §3.1)"] = (
            not per_interest or max(per_interest.values()) <= cap
        )
        # world follower should receive geopolitics/health stories (ancestor tags).
        checks["broad: deep stories reach a broad follower (ancestor tags)"] = any(
            sid.startswith(("sim-world.geopolitics-", "sim-world.health-"))
            for sid in story_ids
        )
        checks["broad: every followed leaf with a qualifier gets ≥1 slot (floor-1)"] = {
            "world",
            "sport",
            "tech.ai",
            "markets.stocks",
        } <= set(interests_used)
    elif profile.profile_key == "C":
        checks["niche: a deep Arsenal story surfaced"] = any(
            sid.startswith("sim-sport.soccer.arsenal-") for sid in story_ids
        )
        # The exploration tier is RETIRED under the category-budget model (phase-5a):
        # the user reserves breadth via per-category budgets, so the niche profile
        # now broadens through the balanced default's topic split, not an
        # auto-exploration reserve. The invariant becomes "no slot is ever emitted
        # as exploration" (the kind was dropped from the allocator output).
        checks["niche: no auto-exploration slot (tier retired in phase-5a)"] = all(
            s.feed_slot_kind != "exploration" for s in slots
        )
    elif profile.profile_key == "D":
        # Entity-boost scenario (phase-5a SP4): the Nvidia follow must lift the
        # Nvidia story above its otherwise-identical twin WITHIN markets, and the
        # per-category budgets + manual sequence must be honored with the source
        # budgets (youtube/x) rolled into the topics so the feed totals 30.
        score_by_id = {s.feed_story_id: s.feed_score for s in slots}
        position_by_id = {s.feed_story_id: s.feed_position for s in slots}
        nvidia_in = "ent-twin-nvidia" in position_by_id
        twin_in = "ent-twin-plain" in position_by_id
        checks["entity: the Nvidia story is placed (within markets)"] = nvidia_in
        checks["entity: Nvidia outranks its non-followed twin (score + position)"] = (
            nvidia_in
            and twin_in
            and score_by_id["ent-twin-nvidia"] > score_by_id["ent-twin-plain"]
            and position_by_id["ent-twin-nvidia"] < position_by_id["ent-twin-plain"]
        )
        # Budget honored: feed totals Σ budgets (== 30), no duplicate story, and the
        # 2-slot breaking tier is filled (source youtube/x budgets rolled into topics).
        breaking_count = sum(1 for s in slots if s.feed_slot_kind == "breaking")
        checks["budget: feed totals 30 (source budgets rolled into topics)"] = (
            len(slots) == 30
        )
        checks["budget: no duplicate story in the feed"] = len(story_ids) == len(
            set(story_ids)
        )
        checks["budget: the 2-slot breaking tier is filled"] = breaking_count == 2
    return checks


def run_drift(
    profile: SimProfile,
    stories: list,
    story_interest_tags: list,
    interest_nodes: dict,
    days: int,
    engaged_interest: str = "tech.ai",
    now_utc: datetime = SIM_NOW,
) -> list[dict]:
    """Run the §4 profile-update loop for N days; return per-day data.

    Each simulated day: the user ENGAGES (completes) every ``engaged_interest``
    story in their current feed and IGNORES the rest. The real
    ``compute_weight_updates`` then nudges weights (bounded + slow-decay) and the
    feed is re-ranked. The engaged interest rises and an un-engaged one fades —
    without collapsing the feed (the over-narrowing guard).

    Args:
        profile: The user to drift (intended for the broad profile B).
        stories: The story pool.
        story_interest_tags: All interest tags.
        interest_nodes: The taxonomy map.
        days: Number of days to simulate.
        engaged_interest: The interest id the user keeps engaging.
        now_utc: The freshness clock.

    Returns:
        One dict per day: ``{"day", "weights": {interest_id: weight},
        "engaged_share": float}`` — the data both the report and the tests read,
        so the printed numbers and the asserted numbers cannot diverge (Rule 9).
    """
    current = [i.model_copy() for i in profile.interests]
    out: list[dict] = []

    for day in range(1, days + 1):
        day_profile = profile.model_copy(update={"interests": current})
        slots = simulate_profile(
            day_profile, stories, story_interest_tags, interest_nodes, now_utc
        )
        by_story = _component_lookup(
            day_profile, stories, story_interest_tags, interest_nodes, now_utc
        )
        engaged_slots = [
            s for s in slots if _interest_of(s, by_story) == engaged_interest
        ]
        out.append(
            {
                "day": day,
                "weights": {i.profile_interest_id: i.profile_weight for i in current},
                "engaged_share": len(engaged_slots) / len(slots) if slots else 0.0,
            }
        )

        # The user completes every engaged-interest story they were shown today.
        signals = [
            SignalEvent(
                signal_user_id=profile.profile_key,
                signal_story_id=s.feed_story_id,
                event_type="complete",
                dwell_ms=55000,
                completion_pct=1.0,
            )
            for s in engaged_slots
        ]
        updates = compute_weight_updates(
            profile_interests=current,
            signals=signals,
            story_interest_tags=story_interest_tags,
            followed_story_ids=[],
        )
        new_weight_by_id = {u.interest_id: u.new_weight for u in updates}
        current = [
            UserProfileInterest(
                profile_interest_id=i.profile_interest_id,
                profile_weight=new_weight_by_id.get(
                    i.profile_interest_id, i.profile_weight
                ),
                profile_is_strict=i.profile_is_strict,
            )
            for i in current
        ]
    return out


def render_drift(
    profile: SimProfile,
    stories: list,
    story_interest_tags: list,
    interest_nodes: dict,
    days: int,
    engaged_interest: str = "tech.ai",
) -> str:
    """Format :func:`run_drift` as a compact per-day report."""
    rows = run_drift(
        profile, stories, story_interest_tags, interest_nodes, days, engaged_interest
    )
    lines = [
        "",
        f"── Drift ({days}d) — Profile {profile.profile_key}; engages "
        f"{engaged_interest} daily ──",
    ]
    for row in rows:
        weights = "  ".join(f"{k}={v:.2f}" for k, v in row["weights"].items())
        lines.append(
            f"   day {row['day']}: {engaged_interest} share={row['engaged_share']:5.1%}"
            f"  | weights: {weights}"
        )
    return "\n".join(lines)


def main() -> None:
    """CLI entry point — print the 3-profile report (and optional drift)."""
    parser = argparse.ArgumentParser(description="Offline blip ranking simulation")
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Also simulate N days of weight drift (profile B)",
    )
    parser.add_argument(
        "--profile",
        choices=["A", "B", "C"],
        default=None,
        help="Render one profile only",
    )
    args = parser.parse_args()

    # Reason: the sim's deliverable is a readable report — silence the pipeline's
    # INFO JSON logs (the allocator/scorer narrate each step) so only the report shows.
    configure_logging("WARNING")

    interest_nodes = build_taxonomy()
    stories, story_interest_tags = build_world(interest_nodes)
    profiles = build_profiles()
    if args.profile:
        profiles = [p for p in profiles if p.profile_key == args.profile]

    print("=" * 80)
    print(
        f"blip ranking simulation — {len(stories)} stories, fixed clock "
        f"{SIM_NOW.isoformat()}"
    )
    print("=" * 80)
    for profile in profiles:
        print(render_profile(profile, stories, story_interest_tags, interest_nodes))

    # Phase-5a entity-boost scenario: its own isolated world (twin Nvidia stories +
    # the DoD allocation) so the report shows the entity lift + per-category budgets.
    if not args.profile:
        ent_stories, ent_tags, ent_profile = build_entity_boost_scenario(interest_nodes)
        print(render_profile(ent_profile, ent_stories, ent_tags, interest_nodes))

    if args.days > 0:
        broad = next((p for p in build_profiles() if p.profile_key == "B"), None)
        if broad is not None:
            print(
                render_drift(
                    broad, stories, story_interest_tags, interest_nodes, args.days
                )
            )


if __name__ == "__main__":
    main()
