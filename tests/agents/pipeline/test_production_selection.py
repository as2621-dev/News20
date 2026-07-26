"""Unit tests for the PRE-production selection + standby promotion (issue #74).

The batch used to produce the whole capped pool and only then cut each user's 30,
so ~60% of script/TTS/poster spend bought reels that never shipped. These tests pin
the inverted order (Rule 9 — each encodes WHY it matters):

  - The production set is the UNION of the users' selections, never the pool. A
    76-story pool with one 30-slot user must produce at most 30.
  - A story two users both select is produced ONCE (one reel serves both).
  - The unselected survivors stay a per-category RANKED standby list — that ranking
    is what makes a promotion the *next best* story, not an arbitrary one.
  - A production failure promotes a same-category standby (the feed stays full);
    an exhausted category promotes nothing and says so (honest short section, never
    a crash and never a silent hole).

Everything here is pure: no DB, no clock, no network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.memory.session_processor import ProfileUpdateResult
from agents.pipeline.categories import CategoryAllocation
from agents.pipeline.orchestrator import ActiveUserFeedInputs, DailyFeedsBatchResult
from agents.pipeline.production_selection import (
    build_standby_lists,
    promote_standbys,
    select_stories_for_production,
)
from agents.pipeline.stages.ranking import UserProfileInterest

_NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)

# One depth-0 interest per topic category (slug → category, per categories.py).
_CATEGORY_INTEREST: dict[str, str] = {
    "business": "int-business",
    "ai": "int-ai",
    "sport": "int-sport",
    "geopolitics": "int-world",
    "arts": "int-ent",
    "tech": "int-tech",
}
_INTEREST_SLUG = {
    "int-business": "business",
    "int-ai": "ai",
    "int-sport": "sport",
    "int-world": "world",
    "int-ent": "entertainment",
    "int-tech": "tech",
}
_INTEREST_NODES: dict[str, InterestNode] = {
    interest_id: InterestNode(
        interest_id=interest_id,
        interest_slug=slug,
        interest_label=slug.title(),
    )
    for interest_id, slug in _INTEREST_SLUG.items()
}

# Founder ground truth 2026-07-26: ash's Build-your-30 (youtube/x slots removed).
_ASH_ALLOCATION = [
    CategoryAllocation(
        allocation_category=category,
        allocation_slot_count=slot_count,
        allocation_sort_order=order,
    )
    for order, (category, slot_count) in enumerate(
        [("business", 7), ("ai", 6), ("sport", 6), ("geopolitics", 5), ("arts", 4), ("tech", 2)]
    )
]
_ALL_TOPIC_PROFILE = [
    UserProfileInterest(profile_interest_id=interest_id, profile_weight=3.0)
    for interest_id in _CATEGORY_INTEREST.values()
]


def _story(story_id: str, outlet_count: int = 4) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Story {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        covering_outlets=[f"outlet{index}.com" for index in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=0,
    )


def _pool(
    per_category_count: int,
) -> tuple[list[CanonicalStory], list[StoryInterestTag], dict[str, str]]:
    """A pool with ``per_category_count`` stories per topic category.

    Ids are ``<category>-<index>`` so a test can read a story's category off its id.
    """
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    category_by_story: dict[str, str] = {}
    for category, interest_id in _CATEGORY_INTEREST.items():
        for index in range(per_category_count):
            story_id = f"{category}-{index}"
            # Descending coverage so the importance ranking inside a category is
            # deterministic and index 0 is the strongest.
            stories.append(_story(story_id, outlet_count=20 - index))
            tags.append(_tag(story_id, interest_id))
            category_by_story[story_id] = category
    return stories, tags, category_by_story


def _ash(
    user_id: str = "ash", prior_feed_story_ids: list[str] | None = None
) -> ActiveUserFeedInputs:
    return ActiveUserFeedInputs(
        active_user_id=user_id,
        profile_interests=_ALL_TOPIC_PROFILE,
        category_allocation=_ASH_ALLOCATION,
        prior_feed_story_ids=prior_feed_story_ids or [],
    )


# ── Selection — the cut happens BEFORE production ─────────────────────────────


def test_selection_production_set_is_the_users_30_not_the_whole_pool() -> None:
    """Happy path: a 76-story pool + one 30-slot user → at most 30 to produce.

    WHY (Rule 9): this is the whole slice. If the production set tracked the pool
    instead of the selection, the batch would pay for ~46 reels that never ship —
    exactly the waste the inverted order exists to remove.
    """
    stories, tags, category_by_story = _pool(13)  # 78 candidates, 6 categories
    plan = select_stories_for_production(
        selection_pool=stories,
        standby_pool=stories,
        active_user_inputs=[_ash()],
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_by_story=category_by_story,
        now_utc=_NOW,
    )

    assert len(stories) == 78
    assert 0 < len(plan.selection_production_story_ids) <= 30
    # Every produced story is one some user actually selected.
    selected_union = {
        story_id
        for user in plan.selection_by_user
        for story_id in user.selection_story_ids
    }
    assert set(plan.selection_production_story_ids) == selected_union
    # The rest of the pool is standby, not spend.
    standby_ids = {
        story_id
        for ids in plan.selection_standby_story_ids_by_category.values()
        for story_id in ids
    }
    assert standby_ids and not (standby_ids & selected_union)


def test_selection_produces_a_shared_story_once_for_both_users() -> None:
    """Edge: two users select the same story → one reel, slotted for both.

    WHY: the union must dedup. Producing per user would double the bill for a
    story that is identical for everyone (importance is intrinsic, not per-user).
    """
    stories, tags, category_by_story = _pool(2)
    plan = select_stories_for_production(
        selection_pool=stories,
        standby_pool=stories,
        active_user_inputs=[_ash("ash"), _ash("second-user")],
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_by_story=category_by_story,
        now_utc=_NOW,
    )

    per_user = {user.selection_user_id: user.selection_story_ids for user in plan.selection_by_user}
    assert set(per_user) == {"ash", "second-user"}
    shared = set(per_user["ash"]) & set(per_user["second-user"])
    assert shared, "identical users must select overlapping stories"
    production = plan.selection_production_story_ids
    assert len(production) == len(set(production)), "the union must be deduped"
    for story_id in shared:
        assert production.count(story_id) == 1


def test_standby_lists_rank_by_importance_within_category() -> None:
    """The standby order is the promotion order — importance first, then freshness.

    WHY: a promotion is meant to be the *next best* story in that category. An
    unranked standby list would fill a failed slot with an arbitrary leftover.
    """
    stories, _tags, category_by_story = _pool(4)
    score_by_story = {
        story.canonical_story_id: (float(story.story_outlet_count), 0.5)
        for story in stories
    }
    standby = build_standby_lists(
        standby_stories=stories,
        selected_story_ids={"business-0"},
        category_by_story=category_by_story,
        score_by_story=score_by_story,
        eligible_categories={"business", "ai"},
    )

    # Selected stories are never standbys; unwanted categories are not standbys.
    assert standby["business"] == ["business-1", "business-2", "business-3"]
    assert set(standby) == {"business", "ai"}


def test_promotion_takes_the_next_standby_in_the_failed_story_category() -> None:
    """Failure path: a failed reel promotes the next same-category standby.

    WHY: the replacement must be same-category or the user's allocation silently
    shifts — a failed business reel backfilled with a sport story is a wrong feed,
    not a repaired one.
    """
    standby = {"business": ["business-1", "business-2"], "ai": ["ai-1"]}
    promoted = promote_standbys(
        failed_story_ids=["business-0"],
        standby_by_category=standby,
        category_by_story={"business-0": "business"},
        exclude_story_ids=set(),
    )

    assert promoted == ["business-1"]
    # The queue is consumed, so a second failure promotes the NEXT one, never a repeat.
    assert standby["business"] == ["business-2"]
    assert promote_standbys(
        failed_story_ids=["business-0"],
        standby_by_category=standby,
        category_by_story={"business-0": "business"},
        exclude_story_ids=set(),
    ) == ["business-2"]


def test_promotion_returns_nothing_when_the_category_standbys_are_exhausted() -> None:
    """Edge: no standby left → promote nothing (honest short section, no crash).

    WHY: the fallback ladder downstream is what makes a short section honest. This
    function must hand it a short pool rather than inventing a cross-category reel.
    """
    standby: dict[str, list[str]] = {"business": [], "ai": ["ai-1"]}
    promoted = promote_standbys(
        failed_story_ids=["business-0"],
        standby_by_category=standby,
        category_by_story={"business-0": "business"},
        exclude_story_ids=set(),
    )

    assert promoted == []
    assert standby["ai"] == ["ai-1"], "another category's standby is never raided"


def test_promotion_skips_already_attempted_stories() -> None:
    """Edge: a story already attempted this run is never re-promoted.

    WHY: without this the promotion loop can re-produce (and re-fail) the same
    story forever — a failure loop that bills every round.
    """
    standby = {"business": ["business-1", "business-2"]}
    promoted = promote_standbys(
        failed_story_ids=["business-0"],
        standby_by_category=standby,
        category_by_story={"business-0": "business"},
        exclude_story_ids={"business-1"},
    )

    assert promoted == ["business-2"]


def test_promotion_respects_the_remaining_production_budget() -> None:
    """Edge: MAX_PRODUCE bounds promotions too, not just the initial selection.

    WHY: a ceiling that only bounds the first wave is not a spend ceiling — a run
    with many failures would walk straight past it one promotion at a time.
    """
    standby = {"business": ["business-1", "business-2"], "ai": ["ai-1", "ai-2"]}
    promoted = promote_standbys(
        failed_story_ids=["business-0", "ai-0"],
        standby_by_category=standby,
        category_by_story={"business-0": "business", "ai-0": "ai"},
        exclude_story_ids=set(),
        max_promotions=1,
    )

    assert promoted == ["business-1"]


# ── Batch level — run_daily_pipeline pays only for the selection (issue #74) ───


def _batch_harness(
    monkeypatch,
    pool: list[CanonicalStory],
    tags: list[StoryInterestTag],
    users: list[ActiveUserFeedInputs],
    render_fails_for: set[str] | None = None,
) -> dict[str, Any]:
    """Wire ``run_daily_pipeline`` to in-memory fakes and record what it produced.

    Every external is mocked at its boundary (no DB, no LLM, no TTS, no posters):
    the write/render phases just record the story ids they were handed, so a test
    can assert on SPEND — which stories the run actually paid to produce.
    """
    from agents.pipeline import daily_batch

    recorded: dict[str, Any] = {"written": [], "rendered": [], "assembled": []}
    render_fails_for = render_fails_for or set()

    async def fake_ingest():
        return pool, tags

    def fake_select_to_produce(stories, _tags, _digests, **_kwargs):
        decisions = [
            SimpleNamespace(
                story_id=story.canonical_story_id,
                should_produce=True,
                importance_score=float(story.story_outlet_count),
                freshness_score=0.5,
            )
            for story in stories
        ]
        return list(stories), decisions

    async def fake_write(story, **_kwargs):
        recorded["written"].append(story.canonical_story_id)
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_args, **_kwargs):
        story_id = write_result.canonical_story_id
        recorded["rendered"].append(story_id)
        return SimpleNamespace(published=story_id not in render_fails_for)

    def fake_assemble(*, target_date, stories, **_kwargs):
        recorded["assembled"] = [story.canonical_story_id for story in stories]
        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(),
            active_user_count=len(users),
            feeds_written=len(users),
        )

    monkeypatch.setattr(
        daily_batch,
        "run_profile_update_job",
        lambda *_a, **_k: ProfileUpdateResult(users_processed=len(users)),
    )
    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select_to_produce)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    monkeypatch.setattr(daily_batch, "_load_has_current_digest", lambda *_a, **_k: {})
    monkeypatch.setattr(
        daily_batch,
        "_load_active_user_ids",
        lambda *_a, **_k: [user.active_user_id for user in users],
    )
    monkeypatch.setattr(
        daily_batch,
        "_load_category_allocation",
        lambda *_a, **_k: {
            user.active_user_id: user.category_allocation for user in users
        },
    )
    monkeypatch.setattr(daily_batch, "_load_interest_nodes_by_user", lambda *_a, **_k: {})
    monkeypatch.setattr(daily_batch, "load_active_user_inputs", lambda *_a, **_k: users)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)
    recorded["ingest_fn"] = fake_ingest
    return recorded


async def _run_batch(recorded: dict[str, Any], **kwargs: Any):
    from agents.pipeline.daily_batch import run_daily_pipeline

    return await run_daily_pipeline(
        target_date=date(2026, 7, 26),
        supabase_client=object(),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=recorded["ingest_fn"],
        interest_nodes=_INTEREST_NODES,
        now_utc=_NOW,
        enable_produce_dedup=False,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_armed_run_produces_only_the_selected_feed_not_the_whole_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance: a 78-candidate pool + one 30-slot user renders at most 30 reels.

    WHY (Rule 9): this asserts on SPEND. Under the old order the run rendered the
    whole capped pool (76 reels for a 30-slot feed) and cut afterwards. If the write
    wave ever tracks the pool again this fails, which is the only way to catch the
    regression — the resulting feed looks identical either way.
    """
    stories, tags, _categories = _pool(13)
    # Already shown to this user, so the §3.8 don't-repeat rule bars them from his
    # feed — but they are the TOP of their categories, so the caps keep them in the
    # candidate pool. Under the old order the run paid to produce all five and then
    # dropped them at assembly; that is the waste this slice removes.
    already_shown = ["business-0", "business-1", "ai-0", "ai-1", "sport-0"]
    recorded = _batch_harness(
        monkeypatch, stories, tags, [_ash(prior_feed_story_ids=already_shown)]
    )

    result = await _run_batch(recorded)

    assert len(stories) == 78
    assert len(recorded["written"]) <= 30, "the run paid for more than the feed holds"
    assert recorded["written"], "the run must still produce the selected feed"
    assert not set(recorded["written"]) & set(already_shown), (
        "produced a story no feed can show — the cut is not gating production"
    )
    assert result.selection is not None
    assert sorted(recorded["written"]) == sorted(
        result.selection.selection_production_story_ids
    )
    assert result.promoted_story_count == 0


@pytest.mark.asyncio
async def test_render_failure_promotes_a_same_category_standby_and_produces_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance (failure path): a failed reel is replaced from its own category.

    WHY: with the cut moved before production every selected story is a slot a feed
    is counting on, so a failure is a HOLE. The replacement must come from the failed
    story's category, or the user's allocation silently shifts.
    """
    stories, tags, _categories = _pool(13)
    recorded = _batch_harness(monkeypatch, stories, tags, [_ash()])

    # Fail the strongest business reel — a story the cut is certain to have selected.
    first_pass = await _run_batch(recorded)
    doomed = next(
        story_id
        for story_id in first_pass.selection.selection_production_story_ids
        if story_id.startswith("business-")
    )

    recorded = _batch_harness(monkeypatch, stories, tags, [_ash()], {doomed})
    result = await _run_batch(recorded)

    assert result.promoted_story_count == 1
    promoted = [
        story_id for story_id in recorded["written"] if story_id.startswith("business-")
    ]
    replacement = recorded["written"][-1]
    assert replacement.startswith("business-"), "the replacement must be same-category"
    assert replacement not in first_pass.selection.selection_production_story_ids
    assert replacement in promoted
    # The feed is still full: one produced story per selected slot.
    assert len(recorded["assembled"]) == len(
        first_pass.selection.selection_production_story_ids
    )


@pytest.mark.asyncio
async def test_exhausted_standbys_run_short_loudly_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edge: nothing left to promote → a short pool, logged, never an exception.

    WHY: the honest fallback ladder downstream is what makes a short section
    truthful. Inventing a cross-category reel — or raising — would be worse than
    a section the UI can label as thin.
    """
    # Exactly one story per category: every selected reel fails, no standby exists.
    stories, tags, _categories = _pool(1)
    doomed = {story.canonical_story_id for story in stories}
    recorded = _batch_harness(monkeypatch, stories, tags, [_ash()], doomed)

    result = await _run_batch(recorded)

    assert result.produced_story_count == 0
    assert result.promoted_story_count == 0
    assert recorded["assembled"] == [], "a short pool, not a fabricated one"
    # Nothing was produced twice trying to paper over the hole.
    assert len(recorded["written"]) == len(set(recorded["written"]))


@pytest.mark.asyncio
async def test_a_story_two_users_selected_is_produced_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance (multi-user overlap): one reel serves everyone who selected it.

    WHY: importance is intrinsic, so two users routinely want the same story.
    Producing per user would double the bill for an identical reel.
    """
    stories, tags, _categories = _pool(6)
    # Each user has already seen a different story, so neither can be produced FOR
    # THEM — but the other user still wants it. The union is what gets produced.
    users = [
        _ash("ash", prior_feed_story_ids=["business-0"]),
        _ash("second-user", prior_feed_story_ids=["ai-0"]),
    ]
    recorded = _batch_harness(monkeypatch, stories, tags, users)

    result = await _run_batch(recorded)

    per_user = {
        user.selection_user_id: set(user.selection_story_ids)
        for user in result.selection.selection_by_user
    }
    shared = per_user["ash"] & per_user["second-user"]
    assert shared, "two identically-configured users must overlap"
    assert set(recorded["written"]) == per_user["ash"] | per_user["second-user"]
    assert len(recorded["written"]) == len(set(recorded["written"]))
    for story_id in shared:
        assert recorded["written"].count(story_id) == 1
    # A story only ONE user selected is still produced (the union, not the overlap).
    assert "business-0" in recorded["written"] and "ai-0" in recorded["written"]


@pytest.mark.asyncio
async def test_shortlist_halt_carries_the_selection_and_spends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance: the default halt still halts, and now shows the real feed.

    WHY: the founder review is only useful if it names the stories that will ship
    and the order replacements would arrive in. It must still cost zero — the halt
    ladder's defaults (#68) are unchanged by this slice.
    """
    stories, tags, _categories = _pool(13)
    recorded = _batch_harness(monkeypatch, stories, tags, [_ash()])

    result = await _run_batch(recorded, shortlist_only=True)

    assert recorded["written"] == [] and recorded["rendered"] == []
    assert result.produced_story_count == 0 and result.feeds is None
    assert result.selection is not None
    selected = result.selection.selection_by_user[0].selection_story_ids
    assert 0 < len(selected) <= 30
    standby = result.selection.selection_standby_story_ids_by_category
    assert standby, "the standby (promotion) order must be reviewable too"
    # Every id the artifact names resolves to a reviewable entry.
    entry_ids = {entry.shortlist_story_id for entry in result.shortlist}
    assert set(selected) <= entry_ids
    for story_ids in standby.values():
        assert set(story_ids) <= entry_ids
    # And the on-disk envelope carries it, not just the in-memory result — the JSON
    # file IS the review artifact the founder reads.
    from agents.pipeline.shortlist import build_shortlist_artifact

    artifact = build_shortlist_artifact(result)
    assert artifact.shortlist_selection is not None
    assert artifact.shortlist_selection.selection_by_user[0].selection_story_ids == selected
    assert artifact.model_dump()["shortlist_selection"][
        "selection_standby_story_ids_by_category"
    ], "the standby order must survive serialization"

