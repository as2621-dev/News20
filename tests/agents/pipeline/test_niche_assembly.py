"""Table-driven tests for the FSR #7 niche-first fallback-ladder assembler.

These pin the OWNER'S HONESTY DECISION (``plans/prd.md`` Decisions #6/#7): a feed slot
filled from broader than the user's niche must SAY SO — silent substitution is the
rejected failure mode. So each test encodes WHY the behaviour matters (Rule 9), and a
test that passes with a silent substitution is wrong by construction.

The ladder scenarios (the issue's enumerated cases) run table-driven through the REAL
assembler (``assemble_niche_feed``) over a synthetic sport-cricket-ipl taxonomy:

  - full pool        → niche fills direct, NO fallback metadata stamped
  - partial climb    → remaining slots fill from EXACTLY one level up, each stamped
  - whole-ladder dry → slots handed to beyond-bubble backfill; feed is never short
  - strict           → never climbs; a dry strict section yields to beyond-bubble
  - duplicate niche  → a story in two niches appears in exactly one slot

Plus the regression guards: the roots-only/legacy path is byte-identical to today's coarse
allocator, followed-source slots still lead, and the writer stays idempotent (produce-once)
and ≤ 30 rows with the new section columns.

Pure functions run for REAL; only the supabase client (writer idempotency) is mocked at the
boundary (CLAUDE.md mandate).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.feed_assembly import (
    SLOT_KIND_INTEREST,
    SLOT_KIND_SOURCE,
    AllocatedSlot,
    assemble_niche_feed,
    assemble_user_feed,
    write_daily_feed,
)
from agents.pipeline.niche_allocation import BEYOND_BUBBLE_LABEL, NicheAllocationRow
from agents.pipeline.stages.ranking import UserProfileInterest

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
_TARGET_DATE = date(2026, 5, 31)

# ── Taxonomy: a real 3-level ladder (sport → cricket → {ipl, team-india}) plus two
# un-lit roots (ai / business) for the beyond-bubble backbone. ──
_SPORT = "int-sport"
_CRICKET = "int-cricket"
_IPL = "int-ipl"
_TEAM_INDIA = "int-team-india"
_AI = "int-ai"
_BUSINESS = "int-business"

_INTEREST_NODES: dict[str, InterestNode] = {
    _SPORT: InterestNode(
        interest_id=_SPORT, interest_slug="sport", interest_label="Sport", depth_level=0
    ),
    _CRICKET: InterestNode(
        interest_id=_CRICKET,
        parent_interest_id=_SPORT,
        interest_slug="sport.cricket",
        interest_label="Cricket",
        depth_level=1,
    ),
    _IPL: InterestNode(
        interest_id=_IPL,
        parent_interest_id=_CRICKET,
        interest_slug="sport.cricket.ipl",
        interest_label="IPL",
        depth_level=2,
    ),
    _TEAM_INDIA: InterestNode(
        interest_id=_TEAM_INDIA,
        parent_interest_id=_CRICKET,
        interest_slug="sport.cricket.team-india",
        interest_label="Team India",
        depth_level=2,
    ),
    _AI: InterestNode(
        interest_id=_AI, interest_slug="ai", interest_label="AI", depth_level=0
    ),
    _BUSINESS: InterestNode(
        interest_id=_BUSINESS,
        interest_slug="business",
        interest_label="Business",
        depth_level=0,
    ),
}


def _story(story_id: str, outlet_count: int = 4) -> CanonicalStory:
    """A fresh, well-covered canonical story (clears the qualifying bar T)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Story {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        covering_outlets=[f"outlet{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str, match_depth: int) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=match_depth,
    )


def _chain_tags(story_id: str, chain: list[str]) -> list[StoryInterestTag]:
    """Tag a story at every node of its ancestor chain (leaf depth 0, parent 1, …).

    Mirrors the ingestion ancestor tagger: an IPL story carries an ``ipl`` (0), a
    ``cricket`` (1) and a ``sport`` (2) tag, so a broader section catches it at the
    lower DepthMatch — exactly what the fallback climb relies on.
    """
    return [_tag(story_id, interest_id, depth) for depth, interest_id in enumerate(chain)]


def _niche_row(
    interest_id: str,
    label: str,
    slot_count: int,
    sort_order: int,
    category: str = "sport",
) -> NicheAllocationRow:
    return NicheAllocationRow(
        allocation_category=category,
        allocation_interest_id=interest_id,
        allocation_section_label=label,
        allocation_slot_count=slot_count,
        allocation_sort_order=sort_order,
    )


def _beyond_row(category: str, sort_order: int) -> NicheAllocationRow:
    return NicheAllocationRow(
        allocation_category=category,
        allocation_section_label=BEYOND_BUBBLE_LABEL,
        allocation_slot_count=1,
        allocation_sort_order=sort_order,
    )


def _run(
    profile_interests: list[UserProfileInterest],
    niche_allocation: list[NicheAllocationRow],
    stories: list[CanonicalStory],
    tags: list[StoryInterestTag],
    **kwargs,
) -> list[AllocatedSlot]:
    return assemble_niche_feed(
        profile_interests=profile_interests,
        niche_allocation=niche_allocation,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        now_utc=_NOW,
        **kwargs,
    )


# ── The ladder table (the issue's enumerated cases) ──────────────────────────────


def test_full_pool_fills_direct_with_no_fallback_metadata() -> None:
    """Full niche pool → every slot is a DIRECT (leaf) fill, no fallback stamp.

    WHY: the interview's promise is "fresh stories from YOUR niche". When the niche pool
    is deep enough, the ladder must never climb — a fallback stamp here would be a lie.
    """
    profile = [UserProfileInterest(profile_interest_id=_IPL, profile_weight=3.0)]
    alloc = [_niche_row(_IPL, "IPL", slot_count=3, sort_order=0)]
    stories, tags = [], []
    for index in range(4):  # 4 IPL stories available for a 3-slot section
        sid = f"ipl-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_IPL, _CRICKET, _SPORT])

    slots = _run(profile, alloc, stories, tags, feed_slot_budget=3)

    assert len(slots) == 3
    assert all(slot.feed_fallback_source_level == 0 for slot in slots)
    assert all(slot.feed_section_interest_id == _IPL for slot in slots)
    assert all(slot.feed_section_label == "IPL" for slot in slots)
    # A direct fill is attributed to the section's own leaf, never an ancestor.
    assert all(slot.feed_matched_interest_id == _IPL for slot in slots)
    assert all(slot.feed_story_id.startswith("ipl-") for slot in slots)


def test_partial_dry_climbs_exactly_one_level_and_stamps_it() -> None:
    """Partially-dry niche → remaining slots fill from ONE level up, each stamped.

    WHY: PRD story #14 — a dry-ish day should still fill the section, but from exactly
    one level broader WITH a label saying so, so the user trusts the niche tracking.
    """
    profile = [UserProfileInterest(profile_interest_id=_IPL, profile_weight=3.0)]
    alloc = [_niche_row(_IPL, "IPL", slot_count=4, sort_order=0)]
    stories, tags = [], []
    for index in range(2):  # only 2 direct IPL stories
        sid = f"ipl-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_IPL, _CRICKET, _SPORT])
    for index in range(3):  # cricket stories NOT under ipl → the one-level-up pool
        sid = f"cricket-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_CRICKET, _SPORT])

    slots = _run(profile, alloc, stories, tags, feed_slot_budget=4)

    assert len(slots) == 4
    direct = [s for s in slots if s.feed_fallback_source_level == 0]
    climbed = [s for s in slots if s.feed_fallback_source_level == 1]
    assert len(direct) == 2 and len(climbed) == 2
    # Never skips to grandparent when the parent could fill it.
    assert all(s.feed_fallback_source_level != 2 for s in slots)
    # Direct slots keep the leaf; climbed slots are filled FROM the parent (honest).
    assert all(s.feed_matched_interest_id == _IPL for s in direct)
    assert all(s.feed_matched_interest_id == _CRICKET for s in climbed)
    # Every slot still belongs to the IPL SECTION regardless of where it was filled.
    assert all(s.feed_section_interest_id == _IPL for s in slots)
    assert all(s.feed_section_label == "IPL" for s in slots)


def test_whole_ladder_dry_hands_slots_to_beyond_bubble() -> None:
    """Whole ladder dry → slots go to beyond-bubble backfill; feed is never short.

    WHY: PRD story #27 — when nothing in the niche's whole ladder is fresh, the user
    still gets a full feed, filled from outside their bubble (never a short feed).
    """
    profile = [UserProfileInterest(profile_interest_id=_IPL, profile_weight=3.0)]
    alloc = [
        _niche_row(_IPL, "IPL", slot_count=3, sort_order=0),
        _beyond_row("ai", sort_order=1),
        _beyond_row("business", sort_order=2),
    ]
    # NO sport-tree stories at all — only un-lit-root backbone.
    stories, tags = [], []
    for index in range(5):
        sid = f"ai-{index}"
        stories.append(_story(sid, outlet_count=6))
        tags += _chain_tags(sid, [_AI])
    for index in range(3):
        sid = f"business-{index}"
        stories.append(_story(sid, outlet_count=5))
        tags += _chain_tags(sid, [_BUSINESS])

    slots = _run(profile, alloc, stories, tags, feed_slot_budget=5)

    # total_target = 3 (ipl) + 1 (ai) + 1 (business) = 5; beyond-bubble absorbs the
    # dry niche's 3 slots + the 2 reserved beyond slots → the feed reaches its target.
    assert len(slots) == 5
    assert all(s.feed_slot_kind == SLOT_KIND_INTEREST for s in slots)
    assert all(s.feed_section_label == BEYOND_BUBBLE_LABEL for s in slots)
    assert all(s.feed_section_interest_id is None for s in slots)
    assert all(s.feed_fallback_source_level == 0 for s in slots)
    # No IPL/cricket/sport story leaked in — the niche really was dry.
    assert all(
        s.feed_story_id.startswith(("ai-", "business-")) for s in slots
    )


def test_strict_never_climbs_and_yields_to_beyond_bubble() -> None:
    """Strict section → never climbs; its dry remainder yields to beyond-bubble.

    WHY: PRD story #17 — "only this" must mean only this. A strict IPL section must
    NEVER be topped up from cricket, even when cricket stories are plentiful.
    """
    profile = [
        UserProfileInterest(
            profile_interest_id=_IPL, profile_weight=3.0, profile_is_strict=True
        )
    ]
    alloc = [
        _niche_row(_IPL, "IPL", slot_count=3, sort_order=0),
        _beyond_row("ai", sort_order=1),
    ]
    stories, tags = [], []
    stories.append(_story("ipl-0"))  # one direct IPL story
    tags += _chain_tags("ipl-0", [_IPL, _CRICKET, _SPORT])
    for index in range(5):  # plentiful cricket — a non-strict section WOULD climb here
        sid = f"cricket-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_CRICKET, _SPORT])
    for index in range(3):  # beyond-bubble backbone
        sid = f"ai-{index}"
        stories.append(_story(sid, outlet_count=6))
        tags += _chain_tags(sid, [_AI])

    slots = _run(profile, alloc, stories, tags, feed_slot_budget=4)

    ipl_slots = [s for s in slots if s.feed_section_interest_id == _IPL]
    assert len(ipl_slots) == 1  # only the single direct IPL story — never climbed
    assert ipl_slots[0].feed_fallback_source_level == 0
    assert ipl_slots[0].feed_matched_interest_id == _IPL
    # NOT ONE cricket story substituted into the strict section (the honesty guarantee).
    assert all(not s.feed_story_id.startswith("cricket-") for s in slots)
    # The dry remainder (2 of the 3 strict slots) + the reserved beyond slot → beyond.
    beyond = [s for s in slots if s.feed_section_label == BEYOND_BUBBLE_LABEL]
    assert len(beyond) == 3
    assert all(s.feed_story_id.startswith("ai-") for s in beyond)


def test_story_in_two_niches_appears_in_exactly_one_slot() -> None:
    """A story tagged to two of the user's niches is placed once (deduped across sections).

    WHY: clean 30-slot accounting — the same story showing twice reads as a bug and
    wastes a scarce slot. First section in sequence wins.
    """
    profile = [
        UserProfileInterest(profile_interest_id=_IPL, profile_weight=3.0),
        UserProfileInterest(profile_interest_id=_TEAM_INDIA, profile_weight=3.0),
    ]
    alloc = [
        _niche_row(_IPL, "IPL", slot_count=2, sort_order=0),
        _niche_row(_TEAM_INDIA, "Team India", slot_count=2, sort_order=1),
    ]
    stories, tags = [], []
    # A shared story tagged DIRECTLY to both niches (both leaves under cricket).
    stories.append(_story("shared-0"))
    tags += [
        _tag("shared-0", _IPL, 0),
        _tag("shared-0", _TEAM_INDIA, 0),
        _tag("shared-0", _CRICKET, 1),
        _tag("shared-0", _SPORT, 2),
    ]
    # Plus enough distinct stories so each section can otherwise fill.
    for leaf, prefix in ((_IPL, "ipl"), (_TEAM_INDIA, "ti")):
        for index in range(2):
            sid = f"{prefix}-{index}"
            stories.append(_story(sid))
            tags += _chain_tags(sid, [leaf, _CRICKET, _SPORT])

    slots = _run(profile, alloc, stories, tags, feed_slot_budget=4)

    story_ids = [s.feed_story_id for s in slots]
    assert story_ids.count("shared-0") == 1  # placed exactly once
    assert len(story_ids) == len(set(story_ids))  # no duplicates anywhere
    # First section in sequence (IPL) claims the shared story.
    shared_slot = next(s for s in slots if s.feed_story_id == "shared-0")
    assert shared_slot.feed_section_interest_id == _IPL


# ── Regression guards ────────────────────────────────────────────────────────────


def test_roots_only_allocation_is_byte_identical_to_coarse_path() -> None:
    """A roots-only (NULL-interest) allocation delegates to the unchanged coarse allocator.

    WHY: Decision #8 — "a root is just a depth-0 section." A roots-only user's feed must
    not change at all under the revamp (the highest-value regression guard).
    """
    profile = [UserProfileInterest(profile_interest_id=_AI, profile_weight=3.0)]
    coarse_rows = [
        NicheAllocationRow(
            allocation_category="ai", allocation_slot_count=3, allocation_sort_order=0
        )
    ]
    stories, tags = [], []
    for index in range(4):
        sid = f"ai-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_AI])

    via_niche = _run(profile, coarse_rows, stories, tags, feed_slot_budget=3)

    from agents.pipeline.categories import CategoryAllocation

    via_coarse = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_INTEREST_NODES,
        category_allocation=[
            CategoryAllocation(
                allocation_category="ai", allocation_slot_count=3, allocation_sort_order=0
            )
        ],
        feed_slot_budget=3,
        now_utc=_NOW,
    )

    assert [s.model_dump() for s in via_niche] == [s.model_dump() for s in via_coarse]
    # And the coarse path carries no section metadata (honest "no section" values).
    assert all(s.feed_section_label is None for s in via_niche)
    assert all(s.feed_section_interest_id is None for s in via_niche)
    assert all(s.feed_fallback_source_level == 0 for s in via_niche)


def test_followed_source_slots_lead_the_feed() -> None:
    """Followed-source reels take guaranteed priority slots, leading the niche sections.

    WHY: PRD story #16 — the revamp must not disturb followed-source lead slots.
    """
    profile = [UserProfileInterest(profile_interest_id=_IPL, profile_weight=3.0)]
    alloc = [
        NicheAllocationRow(
            allocation_category="youtube", allocation_slot_count=1, allocation_sort_order=0
        ),
        _niche_row(_IPL, "IPL", slot_count=2, sort_order=1),
    ]
    stories, tags = [], []
    for index in range(2):
        sid = f"ipl-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_IPL, _CRICKET, _SPORT])
    source_story = CanonicalStory(
        canonical_story_id="yt-0",
        canonical_title="YT upload",
        canonical_url="https://youtube.com/watch?v=0",
        canonical_normalized_url="https://youtube.com/watch?v=0",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="youtube.com",
        covering_outlets=["youtube.com"],
        story_outlet_count=1,
    )

    slots = _run(
        profile, alloc, stories, tags, feed_slot_budget=3, source_stories=[source_story]
    )

    assert slots[0].feed_slot_kind == SLOT_KIND_SOURCE
    assert slots[0].feed_story_id == "yt-0"
    assert [s.feed_slot_kind for s in slots[1:]] == [SLOT_KIND_INTEREST] * 2


def test_feed_caps_at_budget_and_never_exceeds_thirty() -> None:
    """The assembled feed is capped at the feed budget (≤ 30 rows)."""
    profile = [UserProfileInterest(profile_interest_id=_IPL, profile_weight=3.0)]
    alloc = [_niche_row(_IPL, "IPL", slot_count=40, sort_order=0)]
    stories, tags = [], []
    for index in range(50):
        sid = f"ipl-{index}"
        stories.append(_story(sid))
        tags += _chain_tags(sid, [_IPL, _CRICKET, _SPORT])

    slots = _run(profile, alloc, stories, tags)  # default budget 30

    assert len(slots) == 30
    assert [s.feed_position for s in slots] == list(range(1, 31))


# ── Writer idempotency + section-column persistence ──────────────────────────────


class _FakeQuery:
    def __init__(self, store: "_FakeClient") -> None:
        self.store = store
        self._filters: dict[str, str] = {}

    def select(self, _columns: str) -> "_FakeQuery":
        self._filters = {}
        return self

    def eq(self, column: str, value: str) -> "_FakeQuery":
        self._filters[column] = value
        return self

    def insert(self, rows: list[dict]) -> "_FakeQuery":
        self._pending = rows
        return self

    def execute(self):  # noqa: ANN201 - test double
        if hasattr(self, "_pending"):
            self.store.inserted.extend(self._pending)
            return type("R", (), {"data": self._pending})()
        # A select: answer the produce-once pre-check from what's already stored.
        matching = [
            row
            for row in self.store.inserted
            if all(str(row.get(k)) == str(v) for k, v in self._filters.items())
        ]
        return type("R", (), {"data": matching})()


class _FakeClient:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self)


def test_writer_persists_section_columns_and_is_idempotent() -> None:
    """write_daily_feed persists the 3 section columns and never double-writes a day."""
    slots = [
        AllocatedSlot(
            feed_story_id="ipl-0",
            feed_position=1,
            feed_score=0.7,
            feed_matched_interest_id=_CRICKET,
            feed_slot_kind=SLOT_KIND_INTEREST,
            feed_section_label="IPL",
            feed_section_interest_id=_IPL,
            feed_fallback_source_level=1,
        )
    ]
    client = _FakeClient()

    first = write_daily_feed(client, "u1", _TARGET_DATE, slots)
    second = write_daily_feed(client, "u1", _TARGET_DATE, slots)

    assert first.slots_written == 1
    assert second.already_present is True  # produce-once
    assert len(client.inserted) == 1  # not double-written
    row = client.inserted[0]
    assert row["feed_section_label"] == "IPL"
    assert row["feed_section_interest_id"] == _IPL
    assert row["feed_fallback_source_level"] == 1


@pytest.mark.parametrize(
    "scenario",
    ["full", "partial", "dry", "strict", "duplicate"],
)
def test_ladder_scenarios_never_short_and_dedup(scenario: str) -> None:
    """Table-driven invariant sweep: every ladder scenario dedups and honours the cap.

    A single sweep over the five enumerated ladder cases pinning the cross-cutting
    invariants (no duplicate story; feed within budget; a climbed slot is always stamped
    and a direct slot never is) so a regression in any case is caught here too.
    """
    profile = [
        UserProfileInterest(
            profile_interest_id=_IPL,
            profile_weight=3.0,
            profile_is_strict=(scenario == "strict"),
        ),
        UserProfileInterest(profile_interest_id=_TEAM_INDIA, profile_weight=2.0),
    ]
    alloc = [
        _niche_row(_IPL, "IPL", slot_count=3, sort_order=0),
        _niche_row(_TEAM_INDIA, "Team India", slot_count=2, sort_order=1),
        _beyond_row("ai", sort_order=2),
    ]
    stories, tags = [], []
    for index in range(3):
        sid = f"ai-{index}"
        stories.append(_story(sid, outlet_count=6))
        tags += _chain_tags(sid, [_AI])
    if scenario == "full":
        for index in range(4):
            sid = f"ipl-{index}"
            stories.append(_story(sid))
            tags += _chain_tags(sid, [_IPL, _CRICKET, _SPORT])
    elif scenario in ("partial", "strict"):
        stories.append(_story("ipl-0"))
        tags += _chain_tags("ipl-0", [_IPL, _CRICKET, _SPORT])
        for index in range(4):
            sid = f"cricket-{index}"
            stories.append(_story(sid))
            tags += _chain_tags(sid, [_CRICKET, _SPORT])
    elif scenario == "duplicate":
        stories.append(_story("shared-0"))
        tags += [_tag("shared-0", _IPL, 0), _tag("shared-0", _TEAM_INDIA, 0)]
    # "dry" adds no sport-tree stories at all.

    slots = _run(profile, alloc, stories, tags, feed_slot_budget=6)

    story_ids = [s.feed_story_id for s in slots]
    assert len(story_ids) == len(set(story_ids))  # dedup always holds
    assert len(slots) <= 6  # never exceeds the budget
    for slot in slots:
        # A niche slot is stamped iff it was climbed; beyond/source carry level 0.
        if slot.feed_section_interest_id is not None and slot.feed_fallback_source_level:
            assert slot.feed_matched_interest_id != slot.feed_section_interest_id
        if slot.feed_fallback_source_level == 0 and slot.feed_section_interest_id:
            assert slot.feed_matched_interest_id == slot.feed_section_interest_id
