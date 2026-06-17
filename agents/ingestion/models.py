"""Pydantic models for the News20 interest-keyed ingestion pipeline (Phase 1d SP1).

Unlike the TLDW donor models (`ContentItemRecord`, which is per-user / per-source),
News20 ingests **shared** stories keyed to **interests**: one real-world story is
produced once and fanned out to every user whose followed interest it serves
(see plans/phase-1d-daily-content-pipeline.md and reference/ranking-spec.md). So
these models are News20-native — the unit is the story and its interest tags, not
a user's content item.

Pipeline data flow (SP1):
    adapter.search(query)  -> list[CandidateStory]   (one per outlet article)
    StoryClusterer         -> list[CanonicalStory]   (cross-outlet/-interest dedup)
    ancestor_tagging       -> list[StoryInterestTag] (leaf + ancestors, match_depth)

Models:
    InterestNode      -- a taxonomy node (subset of the `interests` row) for the ancestor walk
    ActiveInterest    -- a followed interest with a non-empty search query (an ingest target)
    CandidateStory    -- a single fetched article (pre-dedup), from one outlet
    CanonicalStory    -- a deduped story: one event covered by >=1 outlet (+ outlet count)
    StoryInterestTag  -- a `story_interests` row payload (built here; persisted in SP3)
    IngestionResult   -- the full SP1 batch output (canonical pool + tags + counts)

Column names that map to Supabase are transcribed verbatim from
reference/supabase-schema.md (`stories`, `story_interests`, `interests`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class InterestNode(BaseModel):
    """A single taxonomy node — the subset of the `interests` row needed to walk ancestors.

    The ancestor tagger (agents/ingestion/ancestor_tagging.py) follows
    ``parent_interest_id`` up the tree to assign relative ``match_depth`` values.

    Attributes:
        interest_id: UUID primary key (`interests.interest_id`).
        parent_interest_id: UUID of the parent node, or None at the top level (depth 0).
        interest_slug: Stable slug, e.g. 'sport', 'sport.soccer', 'sport.soccer.arsenal'.
        interest_label: Human-readable label, e.g. "Arsenal".
        depth_level: Absolute taxonomy depth (0 = category, 1 = subcategory, 2 = sub-sub).
        interest_search_query: News query the daily pipeline ingests on (may be None).

    Example:
        >>> node = InterestNode(
        ...     interest_id="a2",
        ...     parent_interest_id="a1",
        ...     interest_slug="sport.soccer.arsenal",
        ...     interest_label="Arsenal",
        ...     depth_level=2,
        ...     interest_search_query="Arsenal FC",
        ... )
        >>> node.interest_label
        'Arsenal'
    """

    interest_id: str = Field(
        ..., description="UUID primary key (interests.interest_id)"
    )
    parent_interest_id: str | None = Field(
        default=None,
        description="UUID of the parent interest node, or None at depth 0 (top level)",
    )
    interest_slug: str = Field(
        ..., description="Stable slug, e.g. 'sport.soccer.arsenal'"
    )
    interest_label: str = Field(..., description="Human-readable label, e.g. 'Arsenal'")
    depth_level: int = Field(
        default=0,
        ge=0,
        description="Absolute taxonomy depth (0 category, 1 subcategory, 2 sub-sub)",
    )
    interest_search_query: str | None = Field(
        default=None,
        description="News query the daily pipeline ingests on (reference/ranking-spec.md §2)",
    )


class ActiveInterest(BaseModel):
    """A followed interest that is an actual ingest target (has a non-empty search query).

    The active-interest set is the distinct union of all users' followed interest
    nodes that carry a search query — the unit of ingestion for the batch.

    Attributes:
        interest_id: UUID of the interest node.
        interest_slug: Stable slug of the interest node.
        interest_search_query: The non-empty news query to fetch for this interest.

    Example:
        >>> active = ActiveInterest(
        ...     interest_id="a2",
        ...     interest_slug="sport.soccer.arsenal",
        ...     interest_search_query="Arsenal FC",
        ... )
        >>> active.interest_search_query
        'Arsenal FC'
    """

    interest_id: str = Field(..., description="UUID of the interest node to ingest for")
    interest_slug: str = Field(..., description="Stable slug of the interest node")
    interest_search_query: str = Field(
        ..., min_length=1, description="Non-empty news query to fetch for this interest"
    )


class CandidateStory(BaseModel):
    """A single fetched news article (pre-dedup), from one outlet.

    Produced by a source adapter's ``search()`` and enriched by ``extract_body()``.
    The pipeline stamps ``candidate_matched_interest_id`` after fetch to record
    which interest's query surfaced the article (the adapter itself is
    interest-agnostic).

    Attributes:
        candidate_external_id: Stable per-article id (the article URL by default).
        candidate_title: Article headline as reported by the source.
        candidate_url: Canonical URL to the original article.
        candidate_outlet_domain: Publisher domain, e.g. 'cnn.com' (GDELT `domain`).
        candidate_outlet_name: Display name for the outlet (defaults to the domain).
        candidate_published_utc: Publication / first-seen timestamp in UTC.
        candidate_language: Article language as reported by the source (if any).
        candidate_source_country: Source country as reported (if any).
        candidate_social_image_url: Lead/social image URL (if any).
        candidate_body_text: Full article body text (None until extract_body runs).
        candidate_matched_interest_id: Interest whose query surfaced this article
            (set by the pipeline, not the adapter).
        candidate_matched_interest_slug: Slug of the matched interest (set by the pipeline).

    Example:
        >>> from datetime import datetime, timezone
        >>> cand = CandidateStory(
        ...     candidate_external_id="https://cnn.com/arsenal-win",
        ...     candidate_title="Arsenal win at the Emirates",
        ...     candidate_url="https://cnn.com/arsenal-win",
        ...     candidate_outlet_domain="cnn.com",
        ...     candidate_published_utc=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ... )
        >>> cand.candidate_outlet_domain
        'cnn.com'
    """

    candidate_external_id: str = Field(
        ..., description="Stable per-article id (the article URL by default)"
    )
    candidate_title: str = Field(..., description="Article headline as reported")
    candidate_url: str = Field(..., description="Canonical URL to the original article")
    candidate_outlet_domain: str = Field(
        ..., description="Publisher domain, e.g. 'cnn.com' (GDELT `domain`)"
    )
    candidate_outlet_name: str | None = Field(
        default=None, description="Display name for the outlet (defaults to the domain)"
    )
    candidate_published_utc: datetime = Field(
        ..., description="Publication / first-seen timestamp in UTC"
    )
    candidate_language: str | None = Field(
        default=None, description="Article language as reported by the source"
    )
    candidate_source_country: str | None = Field(
        default=None, description="Source country as reported by the source"
    )
    candidate_social_image_url: str | None = Field(
        default=None, description="Lead/social image URL (if any)"
    )
    candidate_body_text: str | None = Field(
        default=None,
        description="Full article body text (None until extract_body runs)",
    )
    candidate_matched_interest_id: str | None = Field(
        default=None,
        description="Interest whose query surfaced this article (set by the pipeline)",
    )
    candidate_matched_interest_slug: str | None = Field(
        default=None, description="Slug of the matched interest (set by the pipeline)"
    )
    candidate_platform_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Source-origin platform metadata (e.g. a TwitterContentMetadata "
        "dump for X posts); None for plain news candidates.",
    )


class TwitterContentMetadata(BaseModel):
    """Platform metadata for one X/Twitter post surfaced by the X account adapter (Phase 5d SP2).

    Travels on a source-origin :class:`CandidateStory` (via ``platform_metadata``)
    so the downstream pipeline can attribute the post, render the tweet card, and
    follow quote/thread references. The xAI/Grok Live Search call discovers the
    post + its canonical tweet URL; the screenshot renderer (``tweet_screenshot.py``)
    turns that URL into the reel image.

    Attributes:
        tweet_id: The numeric tweet status id parsed from the canonical tweet URL
            (e.g. "1799999999999999999"), or the URL itself when the id is not
            parseable — stable per post for dedup.
        author_handle: The post author's canonical handle WITHOUT the leading ``@``
            (e.g. "Reuters"); X handles are case-insensitive.
        tweet_url: The canonical ``https://x.com/<handle>/status/<id>`` URL.
        is_quote: True when the post quote-tweets another post.
        quoted_tweet_url: The quoted post's URL when ``is_quote`` is True, else None.
        is_thread: True when the post is part of a multi-tweet thread by the author.

    Example:
        >>> meta = TwitterContentMetadata(
        ...     tweet_id="1799999999999999999",
        ...     author_handle="Reuters",
        ...     tweet_url="https://x.com/Reuters/status/1799999999999999999",
        ... )
        >>> meta.author_handle
        'Reuters'
    """

    tweet_id: str = Field(
        ..., description="Numeric tweet status id (or the URL when not parseable)"
    )
    author_handle: str = Field(
        ..., description="Post author's canonical handle without the leading @"
    )
    tweet_url: str = Field(..., description="Canonical x.com/<handle>/status/<id> URL")
    is_quote: bool = Field(
        default=False, description="True when the post quote-tweets another post"
    )
    quoted_tweet_url: str | None = Field(
        default=None, description="The quoted post's URL when is_quote is True"
    )
    is_thread: bool = Field(
        default=False, description="True when the post is part of an author thread"
    )


class CanonicalStory(BaseModel):
    """A deduped, clustered story: one real-world event covered by one or more outlets.

    Produced by StoryClusterer from a list of CandidateStory items. The
    ``story_outlet_count`` (distinct covering outlets) is the trust/coverage
    number surfaced to the UI (reference/reuse-map.md Decision #6).

    The ``canonical_story_id`` is a *provisional* deterministic id derived from
    the representative URL; the real `stories.story_id` (slug/uuid) is assigned at
    persist time (SP3). It is text to match `stories.story_id`.

    Attributes:
        canonical_story_id: Provisional deterministic story id (stable per cluster).
        canonical_title: Representative headline (the earliest member's title).
        canonical_url: Representative article URL.
        canonical_normalized_url: Normalized representative URL (the cluster key).
        canonical_published_utc: Earliest publication time across the cluster.
        canonical_primary_outlet_domain: Domain of the representative member.
        canonical_primary_outlet_name: Display name of the representative outlet.
        canonical_social_image_url: First available social image across the cluster.
        canonical_body_text: Body text of the representative member (if extracted).
        canonical_representative_external_id: external_id of the representative member.
        covering_outlets: Distinct outlet domains covering this story (sorted).
        story_outlet_count: len(covering_outlets) — the coverage/trust number.
        canonical_matched_interest_ids: Distinct interests whose queries surfaced this story.
        member_candidate_ids: external_ids of all candidates merged into this cluster.

    Example:
        >>> story = CanonicalStory(
        ...     canonical_story_id="cand-abc123",
        ...     canonical_title="Arsenal win at the Emirates",
        ...     canonical_url="https://cnn.com/arsenal-win",
        ...     canonical_normalized_url="https://cnn.com/arsenal-win",
        ...     canonical_published_utc=__import__("datetime").datetime(2026, 5, 31),
        ...     canonical_primary_outlet_domain="cnn.com",
        ...     covering_outlets=["bbc.com", "cnn.com"],
        ...     story_outlet_count=2,
        ... )
        >>> story.story_outlet_count
        2
    """

    canonical_story_id: str = Field(
        ..., description="Provisional deterministic story id (stable per cluster)"
    )
    canonical_title: str = Field(..., description="Representative headline")
    canonical_url: str = Field(..., description="Representative article URL")
    canonical_normalized_url: str = Field(
        ..., description="Normalized representative URL (the cluster key)"
    )
    canonical_published_utc: datetime = Field(
        ..., description="Earliest publication time across the cluster"
    )
    canonical_primary_outlet_domain: str = Field(
        ..., description="Domain of the representative member"
    )
    canonical_primary_outlet_name: str | None = Field(
        default=None, description="Display name of the representative outlet"
    )
    canonical_social_image_url: str | None = Field(
        default=None, description="First available social image across the cluster"
    )
    canonical_body_text: str | None = Field(
        default=None,
        description="Body text of the representative member (if extracted)",
    )
    canonical_representative_external_id: str = Field(
        default="", description="external_id of the representative member"
    )
    covering_outlets: list[str] = Field(
        default_factory=list,
        description="Distinct outlet domains covering this story (sorted)",
    )
    story_outlet_count: int = Field(
        default=0, ge=0, description="len(covering_outlets) — coverage/trust number"
    )
    canonical_matched_interest_ids: list[str] = Field(
        default_factory=list,
        description="Distinct interests whose queries surfaced this story",
    )
    member_candidate_ids: list[str] = Field(
        default_factory=list,
        description="external_ids of all candidates merged into this cluster",
    )


class StoryInterestTag(BaseModel):
    """A `story_interests` row payload — built at SP1, persisted at SP3.

    One row per (story, interest) edge. ``story_interest_match_depth`` is the
    *relative* distance from the interest the story matched: 0 = leaf-matched,
    1 = parent, 2 = grandparent (feeds the DepthMatch score term,
    reference/ranking-spec.md §1).

    Attributes:
        story_interest_story_id: FK to `stories.story_id` (the canonical story id).
        story_interest_interest_id: FK to `interests.interest_id` (leaf or ancestor).
        story_interest_match_depth: 0 leaf / 1 parent / 2 grandparent.
        story_interest_relevance: Optional per-(story, interest) relevance score.

    Example:
        >>> tag = StoryInterestTag(
        ...     story_interest_story_id="cand-abc123",
        ...     story_interest_interest_id="a2",
        ...     story_interest_match_depth=0,
        ... )
        >>> tag.story_interest_match_depth
        0
    """

    story_interest_story_id: str = Field(
        ..., description="FK to stories.story_id (the canonical story id)"
    )
    story_interest_interest_id: str = Field(
        ..., description="FK to interests.interest_id (leaf or ancestor)"
    )
    story_interest_match_depth: int = Field(
        ..., ge=0, le=2, description="0 leaf-matched / 1 parent / 2 grandparent"
    )
    story_interest_relevance: float | None = Field(
        default=None, description="Optional per-(story, interest) relevance score"
    )


class IngestionResult(BaseModel):
    """The full output of one SP1 ingestion batch (no DB writes — payloads only).

    Attributes:
        canonical_stories: The deduped, ancestor-ready canonical story pool.
        story_interest_tags: All `story_interests` row payloads (leaf + ancestors).
        active_interests: The active-interest set that was ingested.
        total_candidates_fetched: Raw candidate count before dedup (for monitoring).

    Example:
        >>> result = IngestionResult(
        ...     canonical_stories=[],
        ...     story_interest_tags=[],
        ...     active_interests=[],
        ...     total_candidates_fetched=0,
        ... )
        >>> result.total_candidates_fetched
        0
    """

    canonical_stories: list[CanonicalStory] = Field(
        default_factory=list, description="The deduped canonical story pool"
    )
    story_interest_tags: list[StoryInterestTag] = Field(
        default_factory=list,
        description="All story_interests row payloads (leaf + ancestors)",
    )
    active_interests: list[ActiveInterest] = Field(
        default_factory=list, description="The active-interest set that was ingested"
    )
    total_candidates_fetched: int = Field(
        default=0, ge=0, description="Raw candidate count before dedup (monitoring)"
    )
