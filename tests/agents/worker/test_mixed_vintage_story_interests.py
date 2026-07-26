"""Mixed-vintage ``story_interests`` regression through the worker read path (issue #72).

WHY this file exists (Rule 9). #70 (c38092f) fixed the *writer*: theme-derived
categories stopped being emitted as depth-0 ROOT tags. It did NOT fix the rows the
old writer already persisted. `_load_ready_story_pool` is the live loader behind
``POST /feed/assemble-for-user`` / ``/feed/assemble-mine``; it has no date scoping
and no theme map, so a pre-cutover phantom root tag reaches ``assign_category``
sitting at the SAME lowest depth as the story's real verified leaf. That is a
cross-root tie the phantom can win on slug order alone — the live scrambled-chip
bug (cricket → arts).

So the contract under test is not "the loader calls a predicate" but: **after the
#72 cleanup, a story carrying BOTH vintages categorizes by its verified leaf.** The
"before" half is asserted alongside it deliberately — without it the test would
still pass on a fixture where the phantom happened to lose the tiebreak, and would
therefore prove nothing about why the deletion was necessary.

Zero network, zero Gemini: the pool loader is driven by the same in-memory fake
client the rest of the worker tests use.
"""

from __future__ import annotations

from agents.ingestion.models import InterestNode
from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category
from agents.worker import pipeline_routes
from scripts.cleanup_theme_root_story_interests import select_theme_root_tag_rows
from tests.agents.worker.test_pipeline_routes import _FakeSupabase

# Reason: 'arts' sorts before 'sport.cricket', so on an equal-depth tie the phantom
# ROOT wins the slug tiebreak. A fixture where the leaf won alphabetically would
# make the test green for the wrong reason.
_ROOT_ARTS_INTEREST_ID = "11111111-1111-1111-1111-111111111111"
_LEAF_CRICKET_INTEREST_ID = "22222222-2222-2222-2222-222222222222"
_STORY_ID = "story-mixed-vintage"

_INTEREST_NODES: dict[str, InterestNode] = {
    _ROOT_ARTS_INTEREST_ID: InterestNode(
        interest_id=_ROOT_ARTS_INTEREST_ID,
        parent_interest_id=None,
        interest_slug="arts",
        interest_label="Arts & Culture",
        depth_level=0,
        interest_search_query=None,
    ),
    _LEAF_CRICKET_INTEREST_ID: InterestNode(
        interest_id=_LEAF_CRICKET_INTEREST_ID,
        parent_interest_id="33333333-3333-3333-3333-333333333333",
        interest_slug="sport.cricket",
        interest_label="Cricket",
        depth_level=1,
        interest_search_query="cricket",
    ),
}

# The OLD writer's output: the theme-derived category as a depth-0 ROOT tag, inside
# the a953ef3 → c38092f window.
_OLD_SHAPE_THEME_ROOT_ROW: dict[str, object] = {
    "story_interest_id": "si-old-theme-root",
    "story_interest_story_id": _STORY_ID,
    "story_interest_interest_id": _ROOT_ARTS_INTEREST_ID,
    "story_interest_match_depth": 0,
    "story_interest_relevance": None,
    "story_interest_created_at": "2026-07-07T09:00:00+00:00",
}
# The NEW writer's output for the same story: the verified keyword match at its
# natural depth, written after the cutover when the story was re-produced.
_NEW_SHAPE_LEAF_ROW: dict[str, object] = {
    "story_interest_id": "si-new-leaf",
    "story_interest_story_id": _STORY_ID,
    "story_interest_interest_id": _LEAF_CRICKET_INTEREST_ID,
    "story_interest_match_depth": 0,
    "story_interest_relevance": 0.82,
    "story_interest_created_at": "2026-07-26T09:00:00+00:00",
}

_STORY_ROW: dict[str, object] = {
    "story_id": _STORY_ID,
    "story_headline": "India seal the series with a last-over win",
    "story_primary_outlet_name": "Reuters",
    "story_outlet_count": 7,
    "story_first_reported_utc": "2026-07-07T08:00:00+00:00",
}


def _pool_client(tag_rows: list[dict[str, object]]) -> _FakeSupabase:
    """A ready-pool client whose single story carries exactly ``tag_rows``."""
    return _FakeSupabase(
        {
            "digests": [
                {
                    "digest_story_id": _STORY_ID,
                    "digest_audio_url": "https://cdn.test/a.mp3",
                    "digest_ambient_poster_url": "https://cdn.test/p.png",
                }
            ],
            "stories": [_STORY_ROW],
            "story_interests": tag_rows,
            "story_sources": [],
        }
    )


def _category_through_worker_read_path(tag_rows: list[dict[str, object]]) -> str:
    """Load the pool the way the assemble routes do, then classify the story.

    Deliberately goes through the REAL ``_load_ready_story_pool`` rather than
    hand-building ``StoryInterestTag`` objects: the loader is where the missing date
    scoping lives, and it is the seam that hands mixed-vintage rows to the ranker.
    """
    _stories, story_interest_tags, _overrides = pipeline_routes._load_ready_story_pool(
        _pool_client(tag_rows)
    )
    return assign_category(
        story_id=_STORY_ID,
        tags_by_story=_index_tags_by_story(story_interest_tags),
        interest_nodes=_INTEREST_NODES,
    )


def test_mixed_vintage_story_categorizes_by_its_verified_leaf_after_cleanup() -> None:
    """The cleanup is what makes the live feed path agree with the verified match.

    Both halves are asserted in one test on purpose — the "after" verdict is only
    meaningful next to the "before" verdict it changes. The post-state is derived by
    running the REAL discriminator over the mixed rows (not hand-written), so this
    test fails if the script's predicate ever stops removing the phantom.
    """
    mixed_vintage_rows = [_OLD_SHAPE_THEME_ROOT_ROW, _NEW_SHAPE_LEAF_ROW]

    # Before: the phantom depth-0 root ties with the real leaf and wins on slug order.
    assert _category_through_worker_read_path(mixed_vintage_rows) == "arts"

    deleted_rows = select_theme_root_tag_rows(
        mixed_vintage_rows, {_ROOT_ARTS_INTEREST_ID}
    )
    deleted_ids = {row["story_interest_id"] for row in deleted_rows}
    assert deleted_ids == {"si-old-theme-root"}

    surviving_rows = [
        row for row in mixed_vintage_rows if row["story_interest_id"] not in deleted_ids
    ]
    assert _category_through_worker_read_path(surviving_rows) == "sport"


def test_cleanup_leaves_a_story_that_only_ever_had_new_shape_rows_untouched() -> None:
    """A story produced entirely after the cutover must classify identically pre/post.

    The cleanup has to be a no-op on the healthy majority; a predicate that also ate
    new-shape rows would show up here as a changed verdict or an empty tag set.
    """
    new_shape_only = [_NEW_SHAPE_LEAF_ROW]
    assert select_theme_root_tag_rows(new_shape_only, {_ROOT_ARTS_INTEREST_ID}) == []
    assert _category_through_worker_read_path(new_shape_only) == "sport"


def test_a_post_cutover_root_match_survives_and_still_categorizes_the_story() -> None:
    """A LEGITIMATE root match written after the cutover is kept, not swept.

    Post-#70 a story matching a followed depth-0 root is tagged depth-0-on-a-root —
    byte-identical in shape to an old theme tag. If the date scope were dropped this
    story would lose its only tag and fall back to the loud DEFAULT_CATEGORY instead
    of the root the user actually follows.
    """
    legitimate_root_match = dict(_OLD_SHAPE_THEME_ROOT_ROW)
    legitimate_root_match["story_interest_id"] = "si-post-cutover-root"
    legitimate_root_match["story_interest_created_at"] = "2026-07-26T09:00:00+00:00"

    rows = [legitimate_root_match]
    assert select_theme_root_tag_rows(rows, {_ROOT_ARTS_INTEREST_ID}) == []
    assert _category_through_worker_read_path(rows) == "arts"
