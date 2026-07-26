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
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import (
    SEMANTIC_RELEVANCE_MODE_DEGRADED,
    CanonicalStory,
    InterestNode,
    StoryInterestTag,
)
from agents.pipeline.categories import FeedCategory
from agents.pipeline.production_selection import ProductionSelectionPlan
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


class ShortlistRunHeader(BaseModel):
    """The run-provenance header stamped onto the on-disk shortlist artifact (#67).

    Attributes:
        run_feed_date: ISO feed date the shortlist was selected for.
        run_semantic_relevance_mode: Which relevance mode produced this list —
            ``semantic`` / ``disabled`` / ``degraded`` (see the mode constants in
            :mod:`agents.ingestion.models`).
        run_fell_back_to_lexical: Convenience predicate — True IFF the mode is
            ``degraded``. Derived, never independently set, so it cannot drift.
        run_semantic_stories_checked: Stories the semantic key looked at.
        run_semantic_interests_checked: Distinct interests it embedded.
        run_shortlist_story_count: Entries in this artifact.
    """

    run_feed_date: str = Field(..., description="ISO feed date")
    run_semantic_relevance_mode: str = Field(
        ..., description="semantic | disabled | degraded"
    )
    run_fell_back_to_lexical: bool = Field(
        ..., description="True IFF mode == 'degraded' (embedding outage)"
    )
    run_semantic_stories_checked: int = Field(default=0, ge=0)
    run_semantic_interests_checked: int = Field(default=0, ge=0)
    run_shortlist_story_count: int = Field(default=0, ge=0)


class ShortlistArtifact(BaseModel):
    """The ``.agents/shortlists/<date>-shortlist.json`` on-disk CONTRACT (#67).

    Envelope, not a bare list: an artifact that does not say which relevance mode
    produced it makes every later audit of a bad tag unfalsifiable — a quality miss
    and an embedding outage look identical. Artifacts written before 2026-07-25 are
    bare JSON lists with no header; readers of historical files must handle both.

    Since issue #74 it also carries ``shortlist_selection`` — each user's actual
    30 plus the per-category standby (promotion) order. ``shortlist_entries`` is the
    entry list every id in that block resolves against (candidates + standbys), so
    the review reads as "here is the feed" rather than "here is a pool". Artifacts
    written before 2026-07-26 have no selection block.
    """

    shortlist_run: ShortlistRunHeader = Field(..., description="Run provenance")
    shortlist_entries: list[ShortlistEntry] = Field(
        default_factory=list, description="The would-produce review list"
    )
    shortlist_selection: ProductionSelectionPlan | None = Field(
        default=None,
        description="Per-user selected feed + standby promotion order (issue #74)",
    )


def build_shortlist_artifact(result: Any) -> ShortlistArtifact:
    """Wrap a pipeline result's shortlist in its run-provenance envelope.

    Args:
        result: A ``DailyPipelineResult`` (duck-typed to keep this module free of a
            circular import — ``daily_batch`` imports this one).

    Returns:
        The :class:`ShortlistArtifact` to serialize with ``model_dump()``.

    Example:
        >>> artifact = build_shortlist_artifact(result)  # doctest: +SKIP
        >>> artifact.shortlist_run.run_fell_back_to_lexical  # doctest: +SKIP
        False
    """
    stamp = result.semantic_relevance
    mode = stamp.semantic_relevance_mode
    return ShortlistArtifact(
        shortlist_run=ShortlistRunHeader(
            run_feed_date=result.feed_date,
            run_semantic_relevance_mode=mode,
            run_fell_back_to_lexical=mode == SEMANTIC_RELEVANCE_MODE_DEGRADED,
            run_semantic_stories_checked=stamp.semantic_relevance_stories_checked,
            run_semantic_interests_checked=stamp.semantic_relevance_interests_checked,
            run_shortlist_story_count=len(result.shortlist),
        ),
        shortlist_entries=list(result.shortlist),
        shortlist_selection=getattr(result, "selection", None),
    )


def warn_matched_slugs_outside_followed_set(
    story_interest_tags: Iterable[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    followed_interest_ids: frozenset[str] | set[str],
    story_ids: set[str] | None = None,
) -> None:
    """Warn per story about depth-0 matched slugs outside the followed universe.

    The issue #70 invariant, extracted so it runs UNCONDITIONALLY in the daily
    batch (review-panel A2: a tag-stream corruption must warn on producing runs
    too, not only under the shortlist halt) while the shortlist builder reuses
    the same single implementation for standalone callers.

    Args:
        story_interest_tags: The batch's ``story_interests`` tags.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        followed_interest_ids: The batch's followed-interest universe (union of
            the active users' ``user_interest_profile`` rows).
        story_ids: Optional scope — check only these stories (the would-produce
            pool); ``None`` checks every tagged story.
    """
    phantom_slugs_by_story: dict[str, list[str]] = {}
    for tag in story_interest_tags:
        if tag.story_interest_match_depth != 0:
            continue
        if story_ids is not None and tag.story_interest_story_id not in story_ids:
            continue
        interest_id = tag.story_interest_interest_id
        if interest_id in interest_nodes and interest_id not in followed_interest_ids:
            phantom_slugs_by_story.setdefault(tag.story_interest_story_id, []).append(
                interest_nodes[interest_id].interest_slug
            )
    for story_id, phantom_slugs in sorted(phantom_slugs_by_story.items()):
        # Reason: issue #70 invariant — a matched slug nobody follows means the
        # tag stream is corrupted again (e.g. a category signal smuggled in as an
        # interest tag). Surface it per story, never silently.
        logger.warning(
            "shortlist_matched_slug_outside_followed_set",
            story_id=story_id,
            phantom_slugs=sorted(phantom_slugs),
            followed_interest_count=len(followed_interest_ids),
            fix_suggestion=(
                "A story carries a matched interest no active user follows — the "
                "story_interests stream holds a non-interest tag. Check "
                "interest_keyed_pipeline tag emission (tags must be verified "
                "keyword matches only, issue #70) and the reconcile tag remap."
            ),
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
    story_tags = list(story_interest_tags)
    tags_by_story = _index_tags_by_story(story_tags)
    # Reason: issue #70 — the theme (aboutness) channel rides beside the tags;
    # consume the batch's map when given, derive only for standalone callers.
    if theme_category_by_story is None:
        theme_category_by_story = theme_categories_for_stories(story_pool)
    if followed_interest_ids is not None:
        warn_matched_slugs_outside_followed_set(
            story_tags,
            interest_nodes,
            followed_interest_ids,
            story_ids={story.canonical_story_id for story in story_pool},
        )
    entries: list[ShortlistEntry] = []
    for story in story_pool:
        story_id = story.canonical_story_id
        story_tag_depths = tags_by_story.get(story_id, {})
        leaf_slugs = sorted(
            interest_nodes[interest_id].interest_slug
            for interest_id, match_depth in story_tag_depths.items()
            if match_depth == 0 and interest_id in interest_nodes
        )
        category = assign_category(
            story_id,
            tags_by_story,
            interest_nodes,
            category_override_by_story=category_override_by_story,
            theme_category_by_story=theme_category_by_story,
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
