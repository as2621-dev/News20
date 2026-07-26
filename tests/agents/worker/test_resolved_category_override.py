"""The persisted batch verdict steers on-demand assembly (issue #73).

WHY this file exists (Rule 9). ``_load_ready_story_pool`` is the live loader behind
``POST /feed/assemble-for-user`` and ``/feed/assemble-mine``. It rebuilds
``CanonicalStory`` objects from the ``stories`` table WITHOUT ``canonical_themes``, so
``assign_category`` on that path can never see the theme (aboutness) side-channel the
nightly batch classifies with. Two concrete divergences follow: an equal-depth
cross-root tie falls back to alphabetical slug order, and a story with no
taxonomy-resolvable tag falls to ``DEFAULT_CATEGORY`` (arts). Either one lets the same
story bucket differently here than it did in the batch — i.e. differently from the chip
the founder approved on the shortlist.

The contract under test is therefore **"the on-demand path returns the BATCH's verdict,
not its own"**, plus its safety twin: **a story with no persisted verdict classifies
EXACTLY as it did before the column existed**. The divergent-fixture half is asserted
explicitly — a fixture where the two resolvers happened to agree would go green while
proving nothing.

Zero network, zero Gemini, zero DB: driven by the same in-memory fake client the rest
of the worker tests use.
"""

from __future__ import annotations

from agents.ingestion.models import InterestNode
from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category
from agents.worker import pipeline_routes
from tests.agents.worker.test_pipeline_routes import _FakeSupabase

_STORY_ID = "story-resolved-category"
_ROOT_ARTS_INTEREST_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_LEAF_CRICKET_INTEREST_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

# Reason: 'arts' sorts before 'sport.cricket', so on this equal-depth cross-root tie
# the SLUG-ORDER resolver returns 'arts'. The batch — which also reads the story's
# theme (aboutness) — returned 'sport'. That divergence is the whole point of the
# slice, so the fixture is built to guarantee it rather than to hope for it.
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
        parent_interest_id=None,
        interest_slug="sport.cricket",
        interest_label="Cricket",
        depth_level=0,
        interest_search_query="cricket",
    ),
}

_TIED_TAG_ROWS: list[dict[str, object]] = [
    {
        "story_interest_story_id": _STORY_ID,
        "story_interest_interest_id": _ROOT_ARTS_INTEREST_ID,
        "story_interest_match_depth": 0,
        "story_interest_relevance": None,
    },
    {
        "story_interest_story_id": _STORY_ID,
        "story_interest_interest_id": _LEAF_CRICKET_INTEREST_ID,
        "story_interest_match_depth": 0,
        "story_interest_relevance": None,
    },
]

_BASE_STORY_ROW: dict[str, object] = {
    "story_id": _STORY_ID,
    "story_headline": "India seal the series with a last-over win",
    "story_primary_outlet_name": "Reuters",
    "story_outlet_count": 7,
    "story_first_reported_utc": "2026-07-07T08:00:00+00:00",
}


def _pool_client(story_row: dict[str, object]) -> _FakeSupabase:
    """A ready-pool client whose single story is ``story_row`` with the tied tags."""
    return _FakeSupabase(
        {
            "digests": [
                {
                    "digest_story_id": story_row["story_id"],
                    "digest_audio_url": "https://cdn.test/a.mp3",
                    "digest_ambient_poster_url": "https://cdn.test/p.png",
                }
            ],
            "stories": [story_row],
            "story_interests": _TIED_TAG_ROWS,
            "story_sources": [],
        }
    )


def _category_through_worker_read_path(story_row: dict[str, object]) -> str:
    """Load the pool the way the assemble routes do, then classify the story.

    Deliberately goes through the REAL loader + the REAL classifier with the REAL
    override seam, because the bug lives in what the loader does or does not hand the
    classifier — not in either one alone.
    """
    _stories, tags, overrides = pipeline_routes._load_ready_story_pool(
        _pool_client(story_row)
    )
    return assign_category(
        story_id=_STORY_ID,
        tags_by_story=_index_tags_by_story(tags),
        interest_nodes=_INTEREST_NODES,
        category_override_by_story=overrides,
    )


def test_fixture_really_diverges_so_the_regression_can_fail() -> None:
    """Without an override the read path answers 'arts' — the slug-order verdict.

    Asserted on its own so the regression below cannot pass by coincidence: if the
    tiebreak ever stops picking 'arts' here, this fails first and says why.
    """
    assert _category_through_worker_read_path(_BASE_STORY_ROW) == "arts"


def test_persisted_batch_verdict_beats_the_slug_order_verdict() -> None:
    """AC3 — the on-demand path returns the BATCH's category, not its own.

    Same story, same tags, same tie. The only difference is the persisted verdict, and
    it must win: the founder approved a 'sport' chip on the shortlist, so the feed must
    not bucket the reel under 'arts'.
    """
    row = {**_BASE_STORY_ROW, "story_resolved_category": "sport"}
    assert _category_through_worker_read_path(row) == "sport"


def test_null_resolved_category_preserves_todays_behaviour() -> None:
    """AC4 — a NULL column classifies EXACTLY as before the column existed.

    Both the pre-migration shape (no key at all) and the post-migration NULL shape must
    land on the same answer as the no-override fixture above: this migration is only
    safe if it is a no-op for every row nobody has written a verdict to.
    """
    absent = _category_through_worker_read_path(_BASE_STORY_ROW)
    explicit_null = _category_through_worker_read_path(
        {**_BASE_STORY_ROW, "story_resolved_category": None}
    )
    assert absent == explicit_null == "arts"


def test_null_resolved_category_contributes_no_override_entry() -> None:
    """A NULL verdict must not even appear in the map — absent, not falsy.

    An entry carrying ``None`` would reach ``assign_category`` as an override and
    could be read as "categorize as nothing"; the safe default is that the story is
    simply not in the map.
    """
    _stories, _tags, overrides = pipeline_routes._load_ready_story_pool(
        _pool_client({**_BASE_STORY_ROW, "story_resolved_category": None})
    )
    assert overrides == {}


def test_persisted_value_outside_the_python_literal_is_ignored() -> None:
    """Edge: the Postgres enum is WIDER than the Python ``FeedCategory`` Literal.

    ``feed_category`` still carries 'podcasts' (migration 0010), which no allocator
    bucket accepts. A value the Literal does not know must be dropped — degrading to
    today's tag-based classification — rather than steering assembly into a bucket
    that cannot be filled.
    """
    row = {**_BASE_STORY_ROW, "story_resolved_category": "podcasts"}
    _stories, _tags, overrides = pipeline_routes._load_ready_story_pool(
        _pool_client(row)
    )
    assert overrides == {}
    assert _category_through_worker_read_path(row) == "arts"


def test_story_dropped_by_the_headline_gate_contributes_no_override() -> None:
    """A story the loader skips must not steer assembly from outside the pool.

    The override map is keyed by story id and handed to a classifier that runs over the
    RETURNED pool; an entry for a story the gate rejected would be a verdict for a reel
    that can never be placed.
    """
    masthead_row = {
        **_BASE_STORY_ROW,
        "story_id": "story-masthead-73",
        "story_headline": "Language Magazine",
        "story_primary_outlet_name": "Language Magazine",
        "story_resolved_category": "sport",
    }
    stories, _tags, overrides = pipeline_routes._load_ready_story_pool(
        _pool_client(masthead_row)
    )
    assert stories == []
    assert overrides == {}
