"""Discriminator tests for the old-shape ``story_interests`` cleanup (issue #72).

WHY these tests matter (Rule 9): the function under test decides which rows get
IRREVERSIBLY deleted from production. Every assertion below encodes a *safety*
property of that decision, not a mechanical restatement of the predicate:

  * it must catch the theme-root rows the pre-#70 writer emitted (else the slice
    does nothing and the scrambled chips stay live),
  * it must never touch a verified keyword tag (else real ranking signal is lost),
  * it must never reach past the c38092f cutover (a post-cutover depth-0 ROOT row
    is a LEGITIMATE new-shape match on a followed root interest — indistinguishable
    from a theme tag by shape alone, so the date scope is the only thing separating
    them),
  * it must never reach before the old writer existed (rows from the natural-depth
    era predating a953ef3 are also legitimate root matches).
"""

from __future__ import annotations

from datetime import datetime, timezone

from scripts.cleanup_theme_root_story_interests import (
    OLD_WRITER_WINDOW_START_UTC,
    THEME_TAG_CUTOVER_UTC,
    select_theme_root_tag_rows,
)

_ROOT_IDS = {"root-tech", "root-arts"}


def _tag_row(
    *,
    story_interest_id: str,
    interest_id: str,
    match_depth: int,
    created_at: str,
    story_id: str = "story-1",
) -> dict[str, object]:
    """Build one raw ``story_interests`` row as supabase-py returns it."""
    return {
        "story_interest_id": story_interest_id,
        "story_interest_story_id": story_id,
        "story_interest_interest_id": interest_id,
        "story_interest_match_depth": match_depth,
        "story_interest_relevance": 0.5,
        "story_interest_created_at": created_at,
    }


def test_old_shape_theme_root_row_inside_the_window_is_selected() -> None:
    """The exact shape the pre-#70 writer emitted is what the cleanup targets.

    Depth 0 + a ROOT interest + inside the window is the discriminator's whole
    claim: within that window the old writer shifted EVERY keyword tag to depth
    >= 1, so nothing else could have produced a depth-0 root row.
    """
    rows = [
        _tag_row(
            story_interest_id="si-theme",
            interest_id="root-tech",
            match_depth=0,
            created_at="2026-07-07T09:00:00+00:00",
        )
    ]
    selected = select_theme_root_tag_rows(rows, _ROOT_IDS)
    assert [row["story_interest_id"] for row in selected] == ["si-theme"]


def test_verified_keyword_tag_at_depth_one_is_never_selected() -> None:
    """A shifted keyword tag (depth >= 1) is real ranking signal — deleting it loses data.

    The issue is explicit that no inverse depth shift is attempted: the
    ``min(depth+1, 2)`` clamp made persisted depth 2 ambiguous, so keyword rows are
    left attenuated rather than guessed at.
    """
    rows = [
        _tag_row(
            story_interest_id="si-keyword",
            interest_id="leaf-cricket",
            match_depth=1,
            created_at="2026-07-07T09:00:00+00:00",
        )
    ]
    assert select_theme_root_tag_rows(rows, _ROOT_IDS) == []


def test_depth_zero_row_on_a_non_root_interest_is_never_selected() -> None:
    """Depth 0 alone is not the discriminator — the interest must be a ROOT node.

    Guards against a predicate that drops the join and deletes every depth-0 row,
    which post-#70 is exactly the *correct* leaf-match shape.
    """
    rows = [
        _tag_row(
            story_interest_id="si-leaf",
            interest_id="leaf-cricket",
            match_depth=0,
            created_at="2026-07-07T09:00:00+00:00",
        )
    ]
    assert select_theme_root_tag_rows(rows, _ROOT_IDS) == []


def test_row_created_after_the_cutover_is_never_selected() -> None:
    """HARD guardrail: a post-cutover depth-0 root row is a legitimate new-shape match.

    After c38092f a story that genuinely matches a followed ROOT interest is tagged
    depth-0-on-a-root — the same shape as an old theme tag. Only the timestamp tells
    them apart, so a predicate that ignores the cutover silently destroys good rows
    (including anything a concurrent run writes while the cleanup is in flight).
    """
    rows = [
        _tag_row(
            story_interest_id="si-new",
            interest_id="root-tech",
            match_depth=0,
            created_at="2026-07-25T19:30:00+00:00",
        )
    ]
    assert select_theme_root_tag_rows(rows, _ROOT_IDS) == []


def test_row_created_before_the_old_writer_shipped_is_never_selected() -> None:
    """Rows predating a953ef3 come from the natural-depth era — also legitimate roots."""
    rows = [
        _tag_row(
            story_interest_id="si-ancient",
            interest_id="root-arts",
            match_depth=0,
            created_at="2026-06-20T09:00:00+00:00",
        )
    ]
    assert select_theme_root_tag_rows(rows, _ROOT_IDS) == []


def test_rows_exactly_on_the_window_boundaries_are_selected() -> None:
    """The window is INCLUSIVE at both ends — the cutover commit's own second counts.

    c38092f landed at 2026-07-25T18:54:43Z; the last old-writer run could have
    written up to that instant, so an exclusive bound would strand rows.
    """
    rows = [
        _tag_row(
            story_interest_id="si-start",
            interest_id="root-tech",
            match_depth=0,
            created_at=OLD_WRITER_WINDOW_START_UTC.isoformat(),
        ),
        _tag_row(
            story_interest_id="si-end",
            interest_id="root-arts",
            match_depth=0,
            created_at=THEME_TAG_CUTOVER_UTC.isoformat(),
        ),
    ]
    selected = select_theme_root_tag_rows(rows, _ROOT_IDS)
    assert {row["story_interest_id"] for row in selected} == {"si-start", "si-end"}


def test_row_with_an_unparseable_timestamp_is_never_selected() -> None:
    """An unreadable ``created_at`` cannot be proven in-window, so it is KEPT.

    Fail-safe direction: a kept phantom tag is a stale chip (already the status quo);
    a wrongly-deleted row is unrecoverable outside the snapshot.
    """
    rows = [
        _tag_row(
            story_interest_id="si-broken",
            interest_id="root-tech",
            match_depth=0,
            created_at="not-a-timestamp",
        ),
        _tag_row(
            story_interest_id="si-null",
            interest_id="root-tech",
            match_depth=0,
            created_at="",
        ),
    ]
    assert select_theme_root_tag_rows(rows, _ROOT_IDS) == []


def test_row_with_a_missing_or_null_match_depth_is_never_selected() -> None:
    """An absent depth must KEEP the row, not read as 0.

    A ``depth or 0`` style read would coerce NULL/absent into the delete set —
    widening an irreversible mutation on the strength of a field that isn't there.
    """
    missing_depth = _tag_row(
        story_interest_id="si-missing-depth",
        interest_id="root-tech",
        match_depth=0,
        created_at="2026-07-07T09:00:00+00:00",
    )
    del missing_depth["story_interest_match_depth"]
    null_depth = _tag_row(
        story_interest_id="si-null-depth",
        interest_id="root-tech",
        match_depth=0,
        created_at="2026-07-07T09:00:00+00:00",
    )
    null_depth["story_interest_match_depth"] = None
    assert select_theme_root_tag_rows([missing_depth, null_depth], _ROOT_IDS) == []


def test_no_root_interests_selects_nothing() -> None:
    """An empty root set must select NOTHING, never fall through to 'match everything'.

    A failed / empty roots lookup is the most dangerous input this script can get:
    treated as a wildcard it would delete every depth-0 row in the window.
    """
    rows = [
        _tag_row(
            story_interest_id="si-theme",
            interest_id="root-tech",
            match_depth=0,
            created_at="2026-07-07T09:00:00+00:00",
        )
    ]
    assert select_theme_root_tag_rows(rows, set()) == []


def test_cutover_constant_matches_the_c38092f_commit_instant() -> None:
    """The window constants are pinned to the commits that changed the tag shape.

    Drifting either bound silently changes what gets deleted, so they are asserted
    rather than trusted to a comment.
    """
    assert THEME_TAG_CUTOVER_UTC == datetime(
        2026, 7, 25, 18, 54, 43, tzinfo=timezone.utc
    )
    assert OLD_WRITER_WINDOW_START_UTC == datetime(2026, 6, 30, tzinfo=timezone.utc)
