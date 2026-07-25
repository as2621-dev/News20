"""Shortlist-only review artifact — the founder gate before any paid production.

Founder rule (2026-07-19, credit frugality): a pipeline run must be able to stop
at story selection and surface the would-be-produced list for human review,
spending ZERO production credits (no script LLM, no TTS, no posters). This module
builds that review artifact from the exact ``to_produce`` pool the batch would
hand to production — same stories, same tags, same category resolution
(``assign_category``, the resolver the produce caps already use; resolve-once,
never re-derive).
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import FeedCategory
from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category
from agents.pipeline.theme_category import theme_categories_for_stories
from agents.shared.logger import get_logger

logger = get_logger("pipeline.shortlist")


class ShortlistEntry(BaseModel):
    """One would-be-produced story, in founder-review form.

    Attributes:
        shortlist_story_id: The canonical story id (stable per cluster).
        shortlist_headline: The representative headline the reel would carry.
        shortlist_primary_outlet: Display name (or domain) of the primary outlet.
        shortlist_outlet_count: Distinct covering outlets (the trust number).
        shortlist_category: The single best-fit screen category —
            resolved via ``assign_category``, identical to the produce caps.
        shortlist_matched_interest_slugs: Slugs of the LEAF-matched (depth-0)
            interests whose queries surfaced this story; empty when untagged.
            Post-#70 the tag stream holds only verified interest matches, so
            depth 0 is the fetching leaf again — never a theme-derived root.
    """

    shortlist_story_id: str = Field(..., description="Canonical story id")
    shortlist_headline: str = Field(..., description="Representative headline")
    shortlist_primary_outlet: str = Field(
        default="", description="Primary outlet display name or domain"
    )
    shortlist_outlet_count: int = Field(
        default=0, ge=0, description="Distinct covering outlets"
    )
    shortlist_category: str = Field(
        ..., description="Best-fit screen category (assign_category)"
    )
    shortlist_matched_interest_slugs: list[str] = Field(
        default_factory=list,
        description="Leaf-matched interest slugs (empty when untagged)",
    )


def build_produce_shortlist(
    stories: Iterable[CanonicalStory],
    story_interest_tags: Iterable[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    category_override_by_story: dict[str, FeedCategory] | None = None,
    followed_interest_ids: frozenset[str] | set[str] | None = None,
    theme_category_by_story: dict[str, FeedCategory] | None = None,
) -> list[ShortlistEntry]:
    """Build the founder-review shortlist from the would-produce story pool.

    Args:
        stories: The final ``to_produce`` pool (post notability gate, produce-once
            gate, dedup and caps) — exactly what production would receive.
        story_interest_tags: The batch's full SP1 tag list (indexed per story).
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        category_override_by_story: The reconcile stage's cross-category pins,
            passed through to ``assign_category`` (same precedence as the caps).
        followed_interest_ids: The batch's followed-interest universe (union of the
            active users' ``user_interest_profile`` rows). When given, every matched
            slug outside it fires a structured
            ``shortlist_matched_slug_outside_followed_set`` WARNING — the issue #70
            invariant (matched slugs ⊆ followed set) failing loud, never silent.
            ``None`` skips the check (pure fixture callers without a profile).
        theme_category_by_story: The batch's already-resolved theme (aboutness)
            categories, forwarded to ``assign_category`` as tiebreak/fallback
            (issue #70). ``None`` → derived here from ``stories`` (standalone
            callers); the daily batch passes its own map so the theme resolution
            — and its miss logging — happens ONCE per run (resolve-once).

    Returns:
        One :class:`ShortlistEntry` per story, in pool order. A story with no
        resolvable tag is still listed (empty slugs, default category) — the
        review must see everything that would be produced, never a subset.

    Example:
        >>> entries = build_produce_shortlist(to_produce, tags, nodes, None)
        >>> entries[0].shortlist_category
        'ai'
    """
    story_pool = list(stories)
    tags_by_story = _index_tags_by_story(list(story_interest_tags))
    # Reason: issue #70 — the theme (aboutness) channel rides beside the tags;
    # consume the batch's map when given, derive only for standalone callers.
    if theme_category_by_story is None:
        theme_category_by_story = theme_categories_for_stories(story_pool)
    entries: list[ShortlistEntry] = []
    for story in story_pool:
        story_id = story.canonical_story_id
        story_tag_depths = tags_by_story.get(story_id, {})
        leaf_interest_ids = [
            interest_id
            for interest_id, match_depth in story_tag_depths.items()
            if match_depth == 0 and interest_id in interest_nodes
        ]
        leaf_slugs = sorted(
            interest_nodes[interest_id].interest_slug
            for interest_id in leaf_interest_ids
        )
        if followed_interest_ids is not None:
            phantom_slugs = sorted(
                interest_nodes[interest_id].interest_slug
                for interest_id in leaf_interest_ids
                if interest_id not in followed_interest_ids
            )
            if phantom_slugs:
                # Reason: issue #70 invariant — a matched slug nobody follows means
                # the tag stream is corrupted again (e.g. a category signal smuggled
                # in as an interest tag). Surface it per story, never silently.
                logger.warning(
                    "shortlist_matched_slug_outside_followed_set",
                    story_id=story_id,
                    phantom_slugs=phantom_slugs,
                    followed_interest_count=len(followed_interest_ids),
                    fix_suggestion=(
                        "A shortlist row carries a matched interest no active user "
                        "follows — the story_interests stream holds a non-interest "
                        "tag. Check interest_keyed_pipeline tag emission (tags must "
                        "be verified keyword matches only, issue #70) and the "
                        "reconcile tag remap."
                    ),
                )
        category = assign_category(
            story_id,
            tags_by_story,
            interest_nodes,
            category_override_by_story,
            theme_category_by_story,
        )
        entries.append(
            ShortlistEntry(
                shortlist_story_id=story_id,
                shortlist_headline=story.canonical_title,
                shortlist_primary_outlet=(
                    story.canonical_primary_outlet_name
                    or story.canonical_primary_outlet_domain
                ),
                shortlist_outlet_count=story.story_outlet_count,
                shortlist_category=str(category),
                shortlist_matched_interest_slugs=leaf_slugs,
            )
        )
    logger.info(
        "produce_shortlist_built",
        shortlist_count=len(entries),
        untagged_count=sum(
            1 for e in entries if not e.shortlist_matched_interest_slugs
        ),
    )
    return entries
