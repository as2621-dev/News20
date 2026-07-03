"""Unit tests for the niche-section allocator (FSR slice #6).

These assert the *intent* (Rule 9) of ``build_niche_allocation``:

  - **roots-only unchanged** — a profile with no deep interest returns TODAY's coarse
    per-category allocation (NULL interest ref, NULL label, no beyond-bubble). This is
    the highest-value regression guard: a roots-only user's feed must not change, so it
    is a characterization test written to pin present behavior.
  - **named niche sections** — a deep profile becomes sections referencing the user's
    interest nodes and carrying the user's own display labels (never generic category
    names), with multiple sections allowed under one root (the cricket persona).
  - **beyond-bubble reserve** — 3–5 slots from roots the user did NOT light up on, and
    STILL a reserve when the user lit up ALL roots (the degenerate least-engaged source).
  - **source slots untouched** — youtube/x budgets are carried through exactly as passed.
  - **fail loud** — a profile referencing an unknown/deleted node raises, never a blank
    section (backing-gate rule, commit 362741b).
  - **contract for #7/#8** — every row is one of {niche, beyond-bubble, source, coarse},
    distinguishable by (interest ref, label, category) alone.
"""

from __future__ import annotations

import pytest

from agents.ingestion.models import InterestNode
from agents.pipeline.categories import DEFAULT_FEED_ALLOCATION, SOURCE_CATEGORIES
from agents.pipeline.niche_allocation import (
    BEYOND_BUBBLE_LABEL,
    BEYOND_BUBBLE_MAX,
    BEYOND_BUBBLE_MIN,
    FEED_SLOT_BUDGET,
    NicheAllocationRow,
    ProfileInterestForAllocation,
    build_niche_allocation,
    write_user_niche_allocation,
)


def _node(interest_id: str, slug: str, label: str, depth: int) -> InterestNode:
    """Build one taxonomy ``InterestNode`` for the lookup."""
    return InterestNode(
        interest_id=interest_id,
        interest_slug=slug,
        interest_label=label,
        depth_level=depth,
    )


def _profile(
    interest_id: str,
    slug: str,
    label: str | None,
    weight: float = 1.0,
) -> ProfileInterestForAllocation:
    """Build one deep-profile ⋈ node input row."""
    return ProfileInterestForAllocation(
        allocation_profile_interest_id=interest_id,
        allocation_interest_slug=slug,
        allocation_display_label=label,
        allocation_profile_weight=weight,
    )


# ── The cricket persona (three sport niches, one strict) — the canonical deep fixture ──
_CRICKET_NODES = {
    "ipl": _node("ipl", "sport.cricket.ipl", "Cricket (IPL)", 2),
    "india": _node("india", "sport.cricket.india-team", "India Team", 2),
    "wc": _node("wc", "sport.cricket.world-cup", "World Cup", 2),
}
_CRICKET_PROFILE = [
    _profile("ipl", "sport.cricket.ipl", "IPL", weight=2.0),
    _profile("india", "sport.cricket.india-team", "Team India cricket", weight=2.0),
    _profile("wc", "sport.cricket.world-cup", "Cricket World Cup", weight=2.0),
]


# ─────────────────────────── Happy path ───────────────────────────


def test_deep_profile_builds_named_niche_sections_referencing_nodes() -> None:
    """A persona's deep interests become sections referencing their nodes + labels."""
    rows = build_niche_allocation(_CRICKET_PROFILE, _CRICKET_NODES)

    niche_rows = [row for row in rows if row.allocation_interest_id is not None]
    # Three niche sections, one per micro-interest node (multiple under `sport`).
    assert {row.allocation_interest_id for row in niche_rows} == {"ipl", "india", "wc"}
    assert all(row.allocation_category == "sport" for row in niche_rows)
    # Sections are named in the USER's vocabulary, never the generic category name.
    assert {row.allocation_section_label for row in niche_rows} == {
        "IPL",
        "Team India cricket",
        "Cricket World Cup",
    }
    assert "sport" not in {row.allocation_section_label for row in niche_rows}


def test_display_label_falls_back_to_node_label_when_absent() -> None:
    """A niche row with no per-user label uses the node's own label (never blank)."""
    profile = [_profile("ipl", "sport.cricket.ipl", None, weight=1.0)]
    rows = build_niche_allocation(profile, _CRICKET_NODES)
    niche = next(row for row in rows if row.allocation_interest_id == "ipl")
    assert niche.allocation_section_label == "Cricket (IPL)"


def test_total_slots_never_exceed_feed_budget() -> None:
    """Niche + beyond-bubble + source slots sum to at most N (30)."""
    rows = build_niche_allocation(_CRICKET_PROFILE, _CRICKET_NODES)
    assert sum(row.allocation_slot_count for row in rows) <= FEED_SLOT_BUDGET


def test_total_never_exceeds_a_shrunk_budget() -> None:
    """The beyond-bubble reserve is clamped so a small budget still totals <= N."""
    rows = build_niche_allocation(
        _CRICKET_PROFILE, _CRICKET_NODES, source_budgets={"youtube": 2, "x": 2}, feed_slot_budget=5
    )
    assert sum(row.allocation_slot_count for row in rows) <= 5


def test_depth0_pick_in_a_deep_profile_still_gets_a_section() -> None:
    """A bare-root pick alongside deep niches is a depth-0 section, not zero slots.

    Regression: a lit root with no niche row AND excluded from beyond-bubble would receive
    no coverage at all — the user picked it and must see it (Decision #8).
    """
    profile = [
        _profile("ipl", "sport.cricket.ipl", "IPL", weight=2.0),
        _profile("r-ai", "ai", "AI", weight=1.0),  # depth-0 pick
    ]
    nodes = {
        "ipl": _node("ipl", "sport.cricket.ipl", "IPL", 2),
        "r-ai": _node("r-ai", "ai", "AI", 0),
    }
    rows = build_niche_allocation(profile, nodes)
    ai_section = next(
        (row for row in rows if row.allocation_interest_id == "r-ai"), None
    )
    assert ai_section is not None
    assert ai_section.allocation_category == "ai"
    assert ai_section.allocation_slot_count >= 1
    # `ai` is lit → it is NOT also a beyond-bubble root.
    assert not any(
        row.allocation_category == "ai"
        and row.allocation_section_label == BEYOND_BUBBLE_LABEL
        for row in rows
    )


def test_weightier_niche_gets_more_slots() -> None:
    """Slot share follows profile weight (largest-remainder, floor 1)."""
    profile = [
        _profile("ipl", "sport.cricket.ipl", "IPL", weight=9.0),
        _profile("wc", "sport.cricket.world-cup", "World Cup", weight=1.0),
    ]
    rows = build_niche_allocation(profile, _CRICKET_NODES)
    by_id = {row.allocation_interest_id: row for row in rows if row.allocation_interest_id}
    assert by_id["ipl"].allocation_slot_count > by_id["wc"].allocation_slot_count
    assert by_id["wc"].allocation_slot_count >= 1  # floor 1 — never a zero-slot section


# ─────────────────────────── Beyond-bubble reserve ───────────────────────────


def test_beyond_bubble_drawn_from_unlit_roots() -> None:
    """The reserve draws 3–5 slots from roots the user did NOT light up on."""
    rows = build_niche_allocation(_CRICKET_PROFILE, _CRICKET_NODES)
    beyond = [row for row in rows if row.allocation_section_label == BEYOND_BUBBLE_LABEL]
    assert BEYOND_BUBBLE_MIN <= len(beyond) <= BEYOND_BUBBLE_MAX
    # Cricket lit only `sport` → every beyond-bubble root is un-lit, none is `sport`.
    assert all(row.allocation_category != "sport" for row in beyond)
    assert all(row.allocation_interest_id is None for row in beyond)
    assert all(row.allocation_slot_count == 1 for row in beyond)


def test_all_roots_lit_still_gets_beyond_bubble_reserve() -> None:
    """Degenerate: a user who lit up ALL 8 roots still gets a beyond-bubble reserve.

    The serendipity source falls back to the LEAST-engaged lit roots (lowest summed
    weight) — the reserve is never dropped.
    """
    all_roots_profile = [
        _profile(root, f"{root}.niche", root.title(), weight=(5.0 if root == "ai" else 1.0))
        for root in ("ai", "geopolitics", "business", "environment", "politics", "tech", "sport", "arts")
    ]
    nodes = {
        row.allocation_profile_interest_id: _node(
            row.allocation_profile_interest_id, row.allocation_interest_slug, "X", 1
        )
        for row in all_roots_profile
    }
    rows = build_niche_allocation(all_roots_profile, nodes)
    beyond = [row for row in rows if row.allocation_section_label == BEYOND_BUBBLE_LABEL]
    assert BEYOND_BUBBLE_MIN <= len(beyond) <= BEYOND_BUBBLE_MAX
    # ai is the most-engaged (weight 5) → it must NOT be a least-engaged reserve pick.
    assert "ai" not in {row.allocation_category for row in beyond}


# ─────────────────────────── Source slots (regression guard) ───────────────────────────


def test_source_budgets_carried_through_unchanged() -> None:
    """youtube/x budgets pass through exactly as supplied (lead the sequence)."""
    rows = build_niche_allocation(
        _CRICKET_PROFILE, _CRICKET_NODES, source_budgets={"youtube": 3, "x": 1}
    )
    source_rows = [row for row in rows if row.allocation_category in SOURCE_CATEGORIES]
    by_cat = {row.allocation_category: row for row in source_rows}
    assert by_cat["youtube"].allocation_slot_count == 3
    assert by_cat["x"].allocation_slot_count == 1
    # Source rows carry no interest ref and no label, and lead the feed sequence.
    assert all(row.allocation_interest_id is None for row in source_rows)
    assert all(row.allocation_section_label is None for row in source_rows)
    assert max(row.allocation_sort_order for row in source_rows) < min(
        row.allocation_sort_order
        for row in rows
        if row.allocation_category not in SOURCE_CATEGORIES
    )


def test_default_source_budget_is_the_youtube_x_slice_of_the_default() -> None:
    """Omitting source_budgets uses the youtube/x slice of DEFAULT_FEED_ALLOCATION."""
    rows = build_niche_allocation(_CRICKET_PROFILE, _CRICKET_NODES)
    by_cat = {
        row.allocation_category: row.allocation_slot_count
        for row in rows
        if row.allocation_category in SOURCE_CATEGORIES
    }
    assert by_cat["youtube"] == DEFAULT_FEED_ALLOCATION["youtube"]
    assert by_cat["x"] == DEFAULT_FEED_ALLOCATION["x"]


# ─────────────────────────── Roots-only characterization (regression guard) ───────────────────────────


def test_roots_only_profile_returns_coarse_baseline_unchanged() -> None:
    """CHARACTERIZATION: a roots-only profile → today's coarse allocation, no niche/beyond.

    Pins the FSR baseline: NULL interest ref, NULL label, DEFAULT_FEED_ALLOCATION budgets,
    and NO beyond-bubble reserve — "a root is just a depth-0 section" (Decision #8).
    """
    roots_only = [
        _profile("r-sport", "sport", "Sport"),
        _profile("r-ai", "ai", "AI"),
    ]
    nodes = {
        "r-sport": _node("r-sport", "sport", "Sport", 0),
        "r-ai": _node("r-ai", "ai", "AI", 0),
    }
    rows = build_niche_allocation(roots_only, nodes)

    # No niche sections and no beyond-bubble reserve — this is the today shape.
    assert all(row.allocation_interest_id is None for row in rows)
    assert all(row.allocation_section_label is None for row in rows)
    assert not any(row.allocation_section_label == BEYOND_BUBBLE_LABEL for row in rows)
    # Lit topic roots carry their DEFAULT_FEED_ALLOCATION budget.
    topic_rows = {
        row.allocation_category: row.allocation_slot_count
        for row in rows
        if row.allocation_category not in SOURCE_CATEGORIES
    }
    assert topic_rows == {
        "sport": DEFAULT_FEED_ALLOCATION["sport"],
        "ai": DEFAULT_FEED_ALLOCATION["ai"],
    }


def test_empty_profile_returns_no_rows() -> None:
    """A user with no profile interests gets an empty allocation (caller skips them)."""
    assert build_niche_allocation([], {}) == []


# ─────────────────────────── Error / boundary (fail loud) ───────────────────────────


def test_unknown_interest_node_fails_loud() -> None:
    """A profile referencing a deleted/unknown node raises — never a blank section."""
    profile = [_profile("ghost", "sport.cricket.ipl", "IPL", weight=1.0)]
    with pytest.raises(ValueError, match="unknown interest node"):
        build_niche_allocation(profile, {})  # empty lookup → node missing


# ─────────────────────────── Persister (DB mocked at the boundary) ───────────────────────────


class _FakeQuery:
    """A chainable fake for the supabase table query (records delete/insert calls)."""

    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    def delete(self):
        self._recorder["deleted"] = True
        return self

    def eq(self, column: str, value: str):
        self._recorder["eq"] = (column, value)
        return self

    def insert(self, payload):
        self._recorder["inserted"] = payload
        return self

    def execute(self):
        return self


class _FakeClient:
    def __init__(self) -> None:
        self.recorder: dict = {}

    def table(self, name: str):
        self.recorder["table"] = name
        return _FakeQuery(self.recorder)


def test_write_user_niche_allocation_replaces_and_omits_surrogate_pk() -> None:
    """The persister deletes the user's rows then inserts the new plan (surrogate PK omitted)."""
    client = _FakeClient()
    rows = [
        NicheAllocationRow(
            allocation_category="sport",
            allocation_interest_id="ipl",
            allocation_section_label="IPL",
            allocation_slot_count=4,
            allocation_sort_order=0,
        )
    ]
    written = write_user_niche_allocation(client, "user-1", rows)

    assert written == 1
    assert client.recorder["deleted"] is True
    assert client.recorder["eq"] == ("follow_user_id", "user-1")
    payload = client.recorder["inserted"]
    assert payload[0]["follow_user_id"] == "user-1"
    assert payload[0]["allocation_interest_id"] == "ipl"
    assert payload[0]["allocation_section_label"] == "IPL"
    # The surrogate PK is never sent — the DB default mints it.
    assert "allocation_id" not in payload[0]


def test_write_empty_rows_is_a_noop_never_wipes() -> None:
    """An empty plan writes nothing AND deletes nothing (never wipe a user to zero rows)."""
    client = _FakeClient()
    written = write_user_niche_allocation(client, "user-1", [])
    assert written == 0
    assert "deleted" not in client.recorder
    assert "inserted" not in client.recorder


def test_build_then_persist_seam_matches_rows() -> None:
    """Allocator → persister seam: every built row is written field-for-field."""
    client = _FakeClient()
    rows = build_niche_allocation(_CRICKET_PROFILE, _CRICKET_NODES)
    written = write_user_niche_allocation(client, "user-1", rows)

    assert written == len(rows)
    payload = client.recorder["inserted"]
    assert len(payload) == len(rows)
    for sent, row in zip(payload, rows):
        assert sent["allocation_category"] == row.allocation_category
        assert sent["allocation_interest_id"] == row.allocation_interest_id
        assert sent["allocation_section_label"] == row.allocation_section_label
        assert sent["allocation_slot_count"] == row.allocation_slot_count


# ─────────────────────────── System-wide: old assembler on new rows ───────────────────────────


def test_old_category_assembler_consumes_niche_rows_without_breaking() -> None:
    """The pre-#7 assembler still works when the allocation table holds NICHE rows.

    daily_batch._load_category_allocation reads only (category, slot_count, sort_order),
    so it maps every niche row to a CategoryAllocation. assemble_user_feed then treats the
    three `sport` niche rows as summed `sport` budget — graceful degradation (expand/
    contract), not a crash — until slice #7 makes assembly node-aware. This guards that
    landing the schema does not break today's feed for a deep-profile user.
    """
    from datetime import datetime, timezone

    from agents.ingestion.models import CanonicalStory, StoryInterestTag
    from agents.pipeline.categories import CategoryAllocation
    from agents.pipeline.feed_assembly import assemble_user_feed
    from agents.pipeline.stages.ranking import UserProfileInterest

    now = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
    rows = build_niche_allocation(_CRICKET_PROFILE, _CRICKET_NODES)

    # Exactly the daily_batch loader mapping: niche rows → per-category budgets.
    category_allocation = [
        CategoryAllocation(
            allocation_category=row.allocation_category,
            allocation_slot_count=row.allocation_slot_count,
            allocation_sort_order=row.allocation_sort_order,
        )
        for row in rows
    ]
    profile = [
        UserProfileInterest(profile_interest_id=node_id, profile_weight=2.0)
        for node_id in _CRICKET_NODES
    ]
    stories = [
        CanonicalStory(
            canonical_story_id=f"s-{i}",
            canonical_title=f"Cricket story {i}",
            canonical_url=f"https://example.com/{i}",
            canonical_normalized_url=f"https://example.com/{i}",
            canonical_published_utc=now,
            canonical_primary_outlet_domain="bbc.com",
            covering_outlets=[f"o{j}.com" for j in range(4)],
            story_outlet_count=4,
        )
        for i in range(12)
    ]
    tags = [
        StoryInterestTag(
            story_interest_story_id=f"s-{i}",
            story_interest_interest_id=list(_CRICKET_NODES)[i % 3],
            story_interest_match_depth=0,
        )
        for i in range(12)
    ]

    slots = assemble_user_feed(
        profile_interests=profile,
        stories=stories,
        story_interest_tags=tags,
        interest_nodes=_CRICKET_NODES,
        category_allocation=category_allocation,
        now_utc=now,
    )
    # It produces a valid, bounded feed (no crash, no overshoot) on the new-schema rows.
    assert 0 < len(slots) <= FEED_SLOT_BUDGET
