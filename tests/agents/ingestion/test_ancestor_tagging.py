"""Unit tests for ancestor tagging (Phase 1d SP1).

The SP1 DoD example: a story matched at leaf sport.soccer.arsenal must produce
story_interests rows for Arsenal (depth 0), Soccer (depth 1), and Sport (depth 2)
with the correct relative match_depth. Also covers matching at non-leaf nodes,
the grandparent depth cap, missing/dangling nodes, and the multi-interest merge.

    >>> pytest tests/agents/ingestion/test_ancestor_tagging.py -v
"""

from __future__ import annotations

import pytest

from agents.ingestion.ancestor_tagging import build_ancestor_tags, merge_story_tags
from agents.ingestion.models import InterestNode
from agents.shared.exceptions import IngestionError


def _tag_pairs(tags) -> list[tuple[str, int]]:
    """(interest_id, match_depth) pairs for compact assertions."""
    return [(t.story_interest_interest_id, t.story_interest_match_depth) for t in tags]


class TestBuildAncestorTags:
    """The leaf → parent → grandparent walk with relative match_depth."""

    def test_leaf_match_tags_self_parent_grandparent(
        self, interest_nodes, interest_ids
    ) -> None:
        """DoD: leaf Arsenal → Arsenal(0), Soccer(1), Sport(2)."""
        tags = build_ancestor_tags("s1", interest_ids["arsenal"], interest_nodes)
        assert _tag_pairs(tags) == [
            (interest_ids["arsenal"], 0),
            (interest_ids["soccer"], 1),
            (interest_ids["sport"], 2),
        ]

    def test_parent_match_starts_depth_zero(self, interest_nodes, interest_ids) -> None:
        """A story matched directly at Soccer → Soccer(0), Sport(1) — no Arsenal."""
        tags = build_ancestor_tags("s1", interest_ids["soccer"], interest_nodes)
        assert _tag_pairs(tags) == [
            (interest_ids["soccer"], 0),
            (interest_ids["sport"], 1),
        ]

    def test_top_level_match_is_single_tag(self, interest_nodes, interest_ids) -> None:
        """A depth-0 match (Sport) produces only its own tag."""
        tags = build_ancestor_tags("s1", interest_ids["sport"], interest_nodes)
        assert _tag_pairs(tags) == [(interest_ids["sport"], 0)]

    def test_depth_capped_at_grandparent(self) -> None:
        """A 4-level chain stops at relative depth 2 (grandparent), not deeper."""
        nodes = {
            "l3": InterestNode(
                interest_id="l3",
                parent_interest_id="l2",
                interest_slug="a.b.c.d",
                interest_label="L3",
                depth_level=3,
            ),
            "l2": InterestNode(
                interest_id="l2",
                parent_interest_id="l1",
                interest_slug="a.b.c",
                interest_label="L2",
                depth_level=2,
            ),
            "l1": InterestNode(
                interest_id="l1",
                parent_interest_id="l0",
                interest_slug="a.b",
                interest_label="L1",
                depth_level=1,
            ),
            "l0": InterestNode(
                interest_id="l0",
                parent_interest_id=None,
                interest_slug="a",
                interest_label="L0",
                depth_level=0,
            ),
        }
        tags = build_ancestor_tags("s1", "l3", nodes)
        assert _tag_pairs(tags) == [
            ("l3", 0),
            ("l2", 1),
            ("l1", 2),
        ]  # l0 not reached (cap)

    def test_missing_matched_node_raises(self) -> None:
        """Tagging against an unknown interest fails loud (Rule 12)."""
        with pytest.raises(IngestionError):
            build_ancestor_tags("s1", "ghost", {})

    def test_dangling_parent_stops_gracefully(self) -> None:
        """A parent id not in the map stops the climb without crashing."""
        nodes = {
            "leaf": InterestNode(
                interest_id="leaf",
                parent_interest_id="gone",
                interest_slug="x.y",
                interest_label="Leaf",
                depth_level=1,
            ),
        }
        tags = build_ancestor_tags("s1", "leaf", nodes)
        assert _tag_pairs(tags) == [("leaf", 0)]  # climb halts at the missing parent


class TestMergeStoryTags:
    """Multi-interest merge keeps the most-specific (minimum) depth per interest."""

    def test_single_interest_expands_to_ancestors(
        self, interest_nodes, interest_ids
    ) -> None:
        tags = merge_story_tags("s1", [interest_ids["arsenal"]], interest_nodes)
        assert _tag_pairs(tags) == [
            (interest_ids["arsenal"], 0),
            (interest_ids["soccer"], 1),
            (interest_ids["sport"], 2),
        ]

    def test_direct_match_beats_ancestor_depth(
        self, interest_nodes, interest_ids
    ) -> None:
        """Matched by both Arsenal and Soccer → Soccer kept at depth 0, not 1."""
        tags = merge_story_tags(
            "s1", [interest_ids["arsenal"], interest_ids["soccer"]], interest_nodes
        )
        depth_by_id = {
            t.story_interest_interest_id: t.story_interest_match_depth for t in tags
        }
        assert (
            depth_by_id[interest_ids["soccer"]] == 0
        )  # direct match wins over Arsenal's parent
        assert depth_by_id[interest_ids["arsenal"]] == 0
        assert depth_by_id[interest_ids["sport"]] == 1
        # One row per interest (UNIQUE(story, interest)) — no duplicates.
        assert len(tags) == 3

    def test_tags_ordered_by_depth_then_id(self, interest_nodes, interest_ids) -> None:
        tags = merge_story_tags("s1", [interest_ids["arsenal"]], interest_nodes)
        depths = [t.story_interest_match_depth for t in tags]
        assert depths == sorted(depths)
