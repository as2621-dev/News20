"""Invariant tests for the offline ranking simulation (Rule 9 — encode WHY).

These run the REAL ranking + allocation code over the deterministic synthetic
world and assert the personalization guarantees that matter to the product:

  * a STRICT follower is never broadened (no fallback, no exploration),
  * a niche follower's deep stories surface (the §3.7 auto-exploration tier is
    RETIRED under phase-5a's user-set category budgets),
  * a broad follower gets diversity (40% interest-cap) + catches deep stories via
    ancestor tags + floor-1 across every followed leaf,
  * a followed entity (Nvidia, custom) lifts its story above an identical
    non-followed twin WITHIN its category, and the user's per-category budgets +
    manual sequence are honored with the source budgets rolled into topics
    (phase-5a — the CI-safe twin of the live e2e),
  * the §3.8 don't-repeat exclusion holds across days,
  * the §4 engagement loop adapts the feed WITHOUT collapsing it (over-narrowing
    guard), and
  * the world is deterministic (so the asserts above are stable, not flaky).

A test here that can't fail when the ranking business logic changes is wrong
(Rule 9): each asserts a behavior a product decision depends on.
"""

from __future__ import annotations

from agents.pipeline.sim.ranking_sim import (
    _profile_checks,
    run_drift,
    simulate_profile,
)
from agents.pipeline.categories import category_for_slug
from agents.pipeline.sim.world import (
    build_entity_boost_scenario,
    build_profiles,
    build_taxonomy,
    build_world,
)


def _world():
    """Build the taxonomy + pool once per test (cheap, pure, no DB)."""
    interest_nodes = build_taxonomy()
    stories, tags = build_world(interest_nodes)
    return interest_nodes, stories, tags


def _profile(profile_key: str):
    return next(p for p in build_profiles() if p.profile_key == profile_key)


def test_world_is_deterministic_across_builds():
    """The world must be byte-identical run-to-run, or the invariants are flaky."""
    nodes_a = build_taxonomy()
    nodes_b = build_taxonomy()
    stories_a, tags_a = build_world(nodes_a)
    stories_b, tags_b = build_world(nodes_b)

    assert [s.canonical_story_id for s in stories_a] == [
        s.canonical_story_id for s in stories_b
    ]
    assert [(s.story_outlet_count, s.canonical_published_utc) for s in stories_a] == [
        (s.story_outlet_count, s.canonical_published_utc) for s in stories_b
    ]
    assert len(tags_a) == len(tags_b)
    assert 90 <= len(stories_a) <= 110  # "~100 stories" as the plan specified


def test_strict_profile_is_never_broadened():
    """A 'cricket only' user must get ONLY cricket.india stories — no upward
    fallback to soccer/sport, and no exploration. This is the explicit owner
    directive ('if they want only cricket, give only cricket')."""
    nodes, stories, tags = _world()
    profile = _profile("A")
    slots = simulate_profile(profile, stories, tags, nodes)

    assert slots, "strict user must still get a (cricket) feed"
    assert all(s.feed_story_id.startswith("sim-sport.cricket.india-") for s in slots), (
        "strict fallback leaked a non-cricket.india story"
    )
    assert all(s.feed_slot_kind != "exploration" for s in slots)
    # All report checks for this profile must pass.
    assert all(
        _profile_checks(profile, slots, _lookup(profile, stories, tags, nodes)).values()
    )


def test_broad_profile_gets_diversity_and_depth():
    """A broad reader must (a) keep the 40% interest-fill cap so no single topic
    dominates the interest tier, (b) receive deep geopolitics/health stories via
    ancestor tags (the niche-reaches-broad mechanic), and (c) get floor-1 on every
    followed leaf with a qualifier."""
    nodes, stories, tags = _world()
    profile = _profile("B")
    slots = simulate_profile(profile, stories, tags, nodes)
    checks = _profile_checks(profile, slots, _lookup(profile, stories, tags, nodes))

    assert checks["broad: interest-fill cap ~40% holds (breaking exempt per §3.1)"]
    assert checks["broad: deep stories reach a broad follower (ancestor tags)"]
    assert checks["broad: every followed leaf with a qualifier gets ≥1 slot (floor-1)"]
    assert len(slots) == 30  # a broad profile fills the full budget


def test_niche_profile_surfaces_depth_without_exploration_tier():
    """A deep Arsenal niche must still surface (affinity-dominant ranking), but the
    auto-exploration tier is RETIRED under the category-budget model (phase-5a):
    the user now reserves breadth via per-category budgets, so NO slot is ever
    emitted with the obsolete ``exploration`` kind.

    WHY (Rule 9): this guards the phase-5a behavioral change (the surfaced Rule-7
    conflict — the user-set allocation supersedes the affinity-proportional split +
    retires §3.7 auto-exploration). It fails if the allocator ever re-introduces an
    exploration slot, and it fails if the niche's deep stories stop surfacing."""
    nodes, stories, tags = _world()
    profile = _profile("C")
    slots = simulate_profile(profile, stories, tags, nodes)

    assert any(s.feed_story_id.startswith("sim-sport.soccer.arsenal-") for s in slots)
    assert all(s.feed_slot_kind != "exploration" for s in slots), (
        "the auto-exploration tier is retired in phase-5a; no slot may carry it"
    )
    # The report checks for this profile must all pass (kept in lock-step, Rule 9).
    assert all(
        _profile_checks(profile, slots, _lookup(profile, stories, tags, nodes)).values()
    )


def test_entity_follow_lifts_story_above_twin_within_category():
    """phase-5a entity invariant (the CI-safe twin of the live e2e): a followed
    entity (Nvidia, custom source) must lift its story ABOVE an otherwise-identical
    non-followed twin WITHIN the markets category.

    The twin pair is equal on every base-Score term (same outlet count → Importance,
    same publish age → Freshness, same interest → Affinity×DepthMatch), so the
    EntityBonus is the ONLY differentiator. This is deterministic — no live DB, no
    network, fixed clock.

    WHY (Rule 9): this fails if the allocator stops threading ``followed_entities``
    into the entity-aware scorer, if the EntityBonus is dropped, or if a category is
    sorted by something other than the entity-aware Score. It mirrors the live e2e's
    central assertion so CI proves it without credentials."""
    nodes = build_taxonomy()
    stories, tags, profile = build_entity_boost_scenario(nodes)
    slots = simulate_profile(profile, stories, tags, nodes)

    by_id = {s.feed_story_id: s for s in slots}
    assert "ent-twin-nvidia" in by_id, "the Nvidia story must be placed in the feed"
    assert "ent-twin-plain" in by_id, "the non-followed twin must be placed too"

    nvidia, twin = by_id["ent-twin-nvidia"], by_id["ent-twin-plain"]
    # Both must classify into markets (same interest) — the lift is WITHIN a category.
    assert category_for_slug(nvidia.feed_matched_interest_id) == "markets"
    assert category_for_slug(twin.feed_matched_interest_id) == "markets"
    # The entity bonus lifts the Nvidia story's Score and its position above the twin.
    assert nvidia.feed_score > twin.feed_score, (
        "the EntityBonus must lift the Nvidia story's Score above its twin's"
    )
    assert nvidia.feed_position < twin.feed_position, (
        "the Nvidia story must be ordered above its non-followed twin"
    )


def test_entity_scenario_honors_category_budgets_and_sequence():
    """phase-5a budget invariant (CI-safe): the entity-boost feed must honor the
    per-category slot budgets + manual sequence, roll the source-category (youtube/x)
    budgets into the topics, and total exactly Σ budgets (== 30) with no duplicate.

    WHY (Rule 9): this fails if the allocator ignores the user's
    ``category_allocation`` (would fall to the balanced default), if breaking is not
    user-budgeted (here 2, not the default 4), if the source budgets are not rolled
    (feed would be 21), or if a story is placed twice. The markets category is
    asserted at EXACTLY its budget (4) because it sits late enough in the sequence
    that the roll-over fills earlier categories first — so its count is controlled."""
    nodes = build_taxonomy()
    stories, tags, profile = build_entity_boost_scenario(nodes)
    slots = simulate_profile(profile, stories, tags, nodes)

    story_ids = [s.feed_story_id for s in slots]
    assert len(slots) == 30, (
        "source budgets must roll into topics so the feed totals 30"
    )
    assert len(story_ids) == len(set(story_ids)), "no story may appear twice"

    breaking = [s for s in slots if s.feed_slot_kind == "breaking"]
    assert len(breaking) == 2, "breaking is user-budgeted to 2 (not the default 4)"

    # markets is budgeted to 4 and sits after world_politics/tech_science in the
    # sequence, so the roll-over does not inflate it — it holds exactly its budget.
    markets_slots = [
        s
        for s in slots
        if s.feed_slot_kind != "breaking"
        and category_for_slug(s.feed_matched_interest_id) == "markets"
    ]
    assert len(markets_slots) == 4, "markets must hold exactly its 4-slot budget"

    # Sequence: breaking first, then the topic categories in allocation_sort_order.
    category_sequence: list[str] = []
    for slot in slots:
        category = (
            "breaking"
            if slot.feed_slot_kind == "breaking"
            else category_for_slug(slot.feed_matched_interest_id)
        )
        if category not in category_sequence:
            category_sequence.append(category)
    assert category_sequence[0] == "breaking"
    # The topic categories present must appear in the user's sort_order
    # (world_politics < tech_science < markets < sport — culture has no stories).
    topic_order = [c for c in category_sequence if c != "breaking"]
    expected_order = ["world_politics", "tech_science", "markets", "sport"]
    assert topic_order == [c for c in expected_order if c in topic_order]


def test_niche_feed_is_shorter_than_budget():
    """A narrow follower has fewer than 30 matching stories, so the feed is SHORT —
    this documents that '~30 in ~30 min' is not guaranteed for niche users (a
    product gap surfaced by the sim, not a bug)."""
    nodes, stories, tags = _world()
    slots = simulate_profile(_profile("C"), stories, tags, nodes)
    assert 0 < len(slots) < 30


def test_dont_repeat_excludes_prior_days_stories():
    """§3.8: a story shown yesterday must not reappear today. Without this the
    finite/completable promise breaks (the user re-sees the same news)."""
    nodes, stories, tags = _world()
    profile = _profile("B")

    day1 = simulate_profile(profile, stories, tags, nodes)
    day1_ids = {s.feed_story_id for s in day1}
    day2 = simulate_profile(
        profile, stories, tags, nodes, prior_feed_story_ids=day1_ids
    )
    day2_ids = {s.feed_story_id for s in day2}

    assert day2, "day-2 must still build a feed from the remaining pool"
    assert day1_ids.isdisjoint(day2_ids), "a prior-day story was repeated"


def test_engagement_raises_weight_without_collapsing_feed():
    """The §4 loop: engaging tech.ai daily must RAISE its weight and feed share,
    DECAY an un-engaged interest toward (not to) baseline, and PLATEAU the share —
    the feed adapts but never collapses onto one topic (the over-narrowing guard)."""
    nodes, stories, tags = _world()
    rows = run_drift(_profile("B"), stories, tags, nodes, days=6)

    first, last = rows[0], rows[-1]
    # Engaged interest rises in weight and in share.
    assert last["weights"]["tech.ai"] > first["weights"]["tech.ai"]
    assert last["engaged_share"] > first["engaged_share"]
    # An un-engaged interest decays toward baseline but does not die (floor 0.1).
    assert first["weights"]["world"] > last["weights"]["world"] > 1.0
    # The feed does not collapse: even after sustained engagement, the engaged
    # interest stays well under a majority (cap + breaking diversity hold).
    assert last["engaged_share"] <= 0.50


def _lookup(profile, stories, tags, nodes):
    """Component lookup helper mirroring the report (kept local to the tests)."""
    from agents.pipeline.sim.ranking_sim import _component_lookup
    from agents.pipeline.sim.world import SIM_NOW

    return _component_lookup(profile, stories, tags, nodes, SIM_NOW)
