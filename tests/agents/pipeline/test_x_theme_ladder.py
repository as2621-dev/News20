"""Unit tests for the X theme-of-the-day ladder (FSR slice #24).

These pin the honest-ladder INTENT (why each behavior matters), not just shape:
  * a louder theme leads (theme-of-the-day is the top signal);
  * a quiet-cluster day is honest — the ladder fills NOTHING, so the caller rolls the
    X slots to the news floor (never a faked theme);
  * two clusters converging on one theme yield ONE reel with MERGED credit (a user
    must not see the same story twice, and credit must stay complete);
  * the roundup rung folds the day's leftover takes into a single reel.
"""

from __future__ import annotations

from agents.pipeline.x_theme_ladder import (
    RUNG_ROUNDUP,
    RUNG_SECOND_THEME,
    RUNG_THEME,
    XThemeAttribution,
    XThemeReelCandidate,
    build_x_theme_ladder,
    dedup_theme_candidates,
)


def _candidate(
    story_id: str,
    summary: str,
    handles: list[str],
    *,
    cluster_id: str = "cluster-1",
    rank: float = 1.0,
    tweet_urls: list[str] | None = None,
) -> XThemeReelCandidate:
    return XThemeReelCandidate(
        reel_story_id=story_id,
        cluster_id=cluster_id,
        reel_rank=rank,
        attribution=XThemeAttribution(
            theme_summary=summary,
            supporting_handles=handles,
            supporting_tweet_urls=tweet_urls or [f"https://x.com/{h}/status/1" for h in handles],
        ),
    )


def test_top_theme_leads_and_rungs_walk_down_in_order() -> None:
    """The loudest theme is the theme-of-the-day; the next is second_theme (PRD #29)."""
    candidates = [
        _candidate("s-quiet", "Fed rate cut takes", ["a", "b"], rank=0.4),
        _candidate("s-loud", "AI model launch reactions", ["c", "d"], rank=0.9),
    ]
    slots = build_x_theme_ladder(candidates, x_slot_budget=3, used_story_ids=set(), excluded_story_ids=set())

    assert [s.rung for s in slots] == [RUNG_THEME, RUNG_SECOND_THEME]
    # Louder theme leads.
    assert slots[0].reel_story_id == "s-loud"
    assert slots[1].reel_story_id == "s-quiet"


def test_quiet_cluster_day_fills_nothing_so_caller_rolls_to_news() -> None:
    """No themes today → ZERO ladder slots (honest news floor, never padded — PRD #30/#33)."""
    slots = build_x_theme_ladder([], x_slot_budget=3, used_story_ids=set(), excluded_story_ids=set())
    assert slots == []


def test_two_clusters_one_theme_yields_one_reel_with_merged_credit() -> None:
    """Converging clusters → ONE reel; the quieter cluster's handles merge in (dedup AC)."""
    candidates = [
        _candidate("s-x", "Election night reactions", ["alice", "bob"], cluster_id="c1", rank=0.9),
        # Same theme, different phrasing/punctuation + a different cluster's handles.
        _candidate("s-y", "election-night REACTIONS!", ["carol", "bob"], cluster_id="c2", rank=0.5),
    ]
    deduped = dedup_theme_candidates(candidates)

    assert len(deduped) == 1
    winner = deduped[0]
    assert winner.reel_story_id == "s-x"  # the louder reel wins the slot
    # Credit is complete: the union of both clusters' distinct handles.
    assert set(h.lower() for h in winner.attribution.supporting_handles) == {"alice", "bob", "carol"}


def test_third_and_beyond_themes_fold_into_one_roundup() -> None:
    """Extra themes past the first two become a single roundup-of-takes reel (spec §3)."""
    candidates = [
        _candidate("s1", "theme one", ["a", "b"], rank=0.9),
        _candidate("s2", "theme two", ["c", "d"], rank=0.8),
        _candidate("s3", "theme three", ["e", "f"], rank=0.7),
        _candidate("s4", "theme four", ["g", "h"], rank=0.6),
    ]
    slots = build_x_theme_ladder(candidates, x_slot_budget=5, used_story_ids=set(), excluded_story_ids=set())

    assert [s.rung for s in slots] == [RUNG_THEME, RUNG_SECOND_THEME, RUNG_ROUNDUP]
    roundup = slots[2]
    # The roundup credits every folded theme's handles.
    assert set(h.lower() for h in roundup.attribution.supporting_handles) == {"e", "f", "g", "h"}
    assert "theme three" in roundup.attribution.theme_summary
    assert "theme four" in roundup.attribution.theme_summary


def test_budget_of_one_places_only_the_theme_of_the_day() -> None:
    """A single X slot gets exactly the theme-of-the-day; no roundup, no second theme."""
    candidates = [
        _candidate("s1", "theme one", ["a", "b"], rank=0.9),
        _candidate("s2", "theme two", ["c", "d"], rank=0.8),
    ]
    slots = build_x_theme_ladder(candidates, x_slot_budget=1, used_story_ids=set(), excluded_story_ids=set())
    assert [s.rung for s in slots] == [RUNG_THEME]


def test_already_used_reel_is_not_double_placed() -> None:
    """A reel already placed elsewhere in the feed is skipped (feed-wide dedup, §3.8)."""
    candidates = [
        _candidate("s1", "theme one", ["a", "b"], rank=0.9),
        _candidate("s2", "theme two", ["c", "d"], rank=0.8),
    ]
    used = {"s1"}
    slots = build_x_theme_ladder(candidates, x_slot_budget=3, used_story_ids=used, excluded_story_ids=set())
    # s1 is gone; s2 becomes the theme-of-the-day.
    assert [s.reel_story_id for s in slots] == ["s2"]
    assert slots[0].rung == RUNG_THEME


def test_prior_feed_reel_is_excluded() -> None:
    """A reel shown in a prior feed is never repeated (§3.8 don't-repeat)."""
    candidates = [_candidate("s1", "theme one", ["a", "b"], rank=0.9)]
    slots = build_x_theme_ladder(candidates, x_slot_budget=3, used_story_ids=set(), excluded_story_ids={"s1"})
    assert slots == []


def test_placed_reels_are_added_to_used_set() -> None:
    """Placed reels join used_story_ids so downstream passes never re-place them."""
    candidates = [_candidate("s1", "theme one", ["a", "b"], rank=0.9)]
    used: set[str] = set()
    build_x_theme_ladder(candidates, x_slot_budget=3, used_story_ids=used, excluded_story_ids=set())
    assert "s1" in used
