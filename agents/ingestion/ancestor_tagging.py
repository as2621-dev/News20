"""Ancestor tagging — expand a matched interest into story_interests rows.

When a story is surfaced by an interest's search query, it is tagged to that
interest **and all of its ancestors**, each with a *relative* match depth:
0 = the matched node (leaf), 1 = its parent, 2 = its grandparent. This is what
lets a broad follower catch a niche story "for free" at a lower DepthMatch
(reference/ranking-spec.md §1–2): a story matched at ``sport.soccer.arsenal``
also carries rows for Soccer (depth 1) and Sport (depth 2).

This module is pure over an injected taxonomy (``dict[interest_id, InterestNode]``)
— the Supabase read that builds that map is the orchestrator's job (SP4). It
produces row *payloads* (StoryInterestTag); persistence is SP3.

Example:
    >>> tags = build_ancestor_tags("s1", "arsenal", interest_nodes)
    >>> [(t.story_interest_interest_id, t.story_interest_match_depth) for t in tags]
    [('arsenal', 0), ('soccer', 1), ('sport', 2)]
"""

from __future__ import annotations

from collections.abc import Iterable

from agents.ingestion.models import InterestNode, StoryInterestTag
from agents.shared.exceptions import IngestionError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

# Reason: the schema models depth as 0 leaf / 1 parent / 2 grandparent
# (story_interests.story_interest_match_depth) — cap the upward walk at the
# grandparent so DepthMatch stays in its defined 3-level range.
_MAX_ANCESTOR_DEPTH = 2


def build_ancestor_tags(
    story_id: str,
    matched_interest_id: str,
    interest_nodes: dict[str, InterestNode],
    *,
    max_depth: int = _MAX_ANCESTOR_DEPTH,
    relevance: float | None = None,
) -> list[StoryInterestTag]:
    """Walk a matched interest up to its ancestors, emitting one tag per node.

    Args:
        story_id: The (provisional) canonical story id to tag.
        matched_interest_id: The interest whose query surfaced the story (depth 0).
        interest_nodes: Taxonomy map ``interest_id -> InterestNode`` (parents must
            be resolvable through ``parent_interest_id`` for the walk to climb).
        max_depth: Maximum relative depth to emit (0=leaf .. 2=grandparent).
        relevance: Optional per-(story, interest) relevance carried onto each row.

    Returns:
        Tags ordered leaf → grandparent: ``[(matched, 0), (parent, 1), ...]``.

    Raises:
        IngestionError: If ``matched_interest_id`` is not in ``interest_nodes``
            (a story can't be tagged to an interest the taxonomy doesn't know).

    Example:
        >>> build_ancestor_tags("s1", "missing", {})
        Traceback (most recent call last):
        agents.shared.exceptions.IngestionError: ...
    """
    matched_node = interest_nodes.get(matched_interest_id)
    if matched_node is None:
        raise IngestionError(
            message=f"matched interest '{matched_interest_id}' not found in the taxonomy",
            fix_suggestion="Ensure the interests map includes every interest a search query maps to",
        )

    tags: list[StoryInterestTag] = []
    current: InterestNode | None = matched_node
    depth = 0
    while current is not None and depth <= max_depth:
        tags.append(
            StoryInterestTag(
                story_interest_story_id=story_id,
                story_interest_interest_id=current.interest_id,
                story_interest_match_depth=depth,
                story_interest_relevance=relevance,
            )
        )
        parent_id = current.parent_interest_id
        if not parent_id:
            break
        parent_node = interest_nodes.get(parent_id)
        if parent_node is None:
            # Reason: a dangling parent link is a taxonomy-integrity issue — stop
            # the climb gracefully rather than crash the whole ingest batch.
            logger.warning(
                "ancestor_tagging_dangling_parent",
                story_id=story_id,
                interest_id=current.interest_id,
                missing_parent_id=parent_id,
                fix_suggestion="Backfill the missing parent interest row so ancestors resolve",
            )
            break
        current = parent_node
        depth += 1

    return tags


def merge_story_tags(
    story_id: str,
    matched_interest_ids: Iterable[str],
    interest_nodes: dict[str, InterestNode],
    *,
    max_depth: int = _MAX_ANCESTOR_DEPTH,
) -> list[StoryInterestTag]:
    """Build ancestor tags for a story matched by multiple interests, deduped.

    A story can be surfaced by several interest queries (e.g. both "Arsenal" and
    "Soccer"). Each contributes ancestor rows, which can collide on the same
    interest at different depths. The ``story_interests`` UNIQUE(story, interest)
    constraint allows one row per interest, so we keep the **most specific**
    (minimum) match depth — a directly-matched Soccer (depth 0) beats Soccer
    reached as Arsenal's parent (depth 1).

    Args:
        story_id: The (provisional) canonical story id to tag.
        matched_interest_ids: All interests whose queries surfaced this story.
        interest_nodes: Taxonomy map ``interest_id -> InterestNode``.
        max_depth: Maximum relative depth to emit (0=leaf .. 2=grandparent).

    Returns:
        One tag per distinct interest, each at its minimum match depth, ordered by
        depth then interest id (deterministic).

    Example:
        >>> tags = merge_story_tags("s1", ["arsenal", "soccer"], interest_nodes)
        >>> next(t.story_interest_match_depth for t in tags if t.story_interest_interest_id == "soccer")
        0
    """
    best_depth_by_interest: dict[str, int] = {}
    for matched_interest_id in matched_interest_ids:
        for tag in build_ancestor_tags(
            story_id, matched_interest_id, interest_nodes, max_depth=max_depth
        ):
            interest_id = tag.story_interest_interest_id
            depth = tag.story_interest_match_depth
            if (
                interest_id not in best_depth_by_interest
                or depth < best_depth_by_interest[interest_id]
            ):
                best_depth_by_interest[interest_id] = depth

    merged = [
        StoryInterestTag(
            story_interest_story_id=story_id,
            story_interest_interest_id=interest_id,
            story_interest_match_depth=depth,
        )
        for interest_id, depth in best_depth_by_interest.items()
    ]
    merged.sort(
        key=lambda tag: (tag.story_interest_match_depth, tag.story_interest_interest_id)
    )
    return merged
