"""Stage: per-(user, story) heuristic scoring + fallback tree (Phase 1d SP3).

ADAPTED from the TLDW donor (`agents/pipeline/stages/ranking.py`). The donor
ranked stories with ONE Gemini call that selected 5 ``RankedStory`` objects for
one user's briefing. News20's M1 ranking is a *heuristic, not ML* (<50 users, no
training data) — it is the exact formula in ``reference/ranking-spec.md`` §1–2,
**implemented, not re-derived**:

    Score(user, story) = (Affinity × DepthMatch) · 0.5
                         + Importance · 0.3
                         + Freshness · 0.2

This module owns two things from the spec:

  §1 — the per-(user, story) ``Score``: a pure function over the user's
       normalized interest affinity, the story's match depth (leaf/parent/
       grandparent), and the story's intrinsic importance + freshness.
  §2 — the fallback tree candidate generator: for each followed leaf, walk
       leaf → parent → grandparent until a story clears ``Score ≥ T``, stopping
       early at a ``strict`` interest (no upward broadening) or at the
       grandparent (depth-0 category).

The Importance and Freshness terms reuse the *same* primitives the produce-gate
already defined (``compute_importance_score`` / ``compute_freshness_score``) so a
story's importance/freshness is computed identically at gate-time and rank-time —
one source of truth, no drift (Rule 3/7).

This module is **pure** over its injected inputs (a user profile + a candidate
pool + the interest taxonomy). No DB, no clock dependency, no network — fully
unit-testable. The Supabase reads that build these inputs are the SP4
orchestrator's job; SP3 ships the scorer + generator the allocator (SP4)
consumes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import (
    DEFAULT_CATEGORY,
    FeedCategory,
    category_for_slug,
    empty_category_buckets,
)
from agents.pipeline.produce_gate import (
    compute_freshness_score,
    compute_importance_score,
)
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.ranking")

# Reason: the Score weights (reference/ranking-spec.md §1). Affinity-dominant on
# purpose (α=0.5) so a niche-but-small story surfaces for the user who follows
# it. They live here as the single config source — never hardcoded scattered.
AFFINITY_WEIGHT = 0.5
IMPORTANCE_WEIGHT = 0.3
FRESHNESS_WEIGHT = 0.2

# Reason: DepthMatch (reference/ranking-spec.md §1) — how *specifically* the
# story hits the user's node. A leaf-tagged story (match_depth 0) is the full
# 1.0; a story that only matches via the parent/grandparent ancestor tags counts
# for less. This is what lets a small Mumbai-Indians item beat a generic big
# story for a Mumbai-Indians fan.
DEPTH_MATCH_BY_DEPTH: dict[int, float] = {0: 1.0, 1: 0.6, 2: 0.3}

# Reason: the threshold T (reference/ranking-spec.md §1–2) — the minimum Score
# for a story to be "good enough" to fill a slot and to STOP the fallback climb.
# Single config constant; first-draft, confirmed at the SP4 2-user manual run.
DEFAULT_SCORE_THRESHOLD = 0.20

# Reason: EntityBonus weight (phase-5a SP2). An ADDITIVE term on the Score for a
# story whose title matches a followed entity — a Nvidia follower sees Nvidia
# stories rank up WITHIN their category. First-draft ≈ 0.3, tuned at the SP4 sim
# so an entity follow meaningfully lifts a story without drowning Affinity×Depth.
# Single config source — never hardcoded scattered (mirrors the α/β/γ weights).
ENTITY_BONUS_WEIGHT = 0.3

# The follow-source axis (mirrors the ``entity_follow_source`` Postgres enum,
# migration 0007). Drives the per-source intent weighting below.
FollowSource = Literal["seed", "more", "custom"]

# Reason: custom > more > seed intent weighting (phase-5a SP2 / 0007 design notes:
# a custom-typed follow is the highest-intent signal, a seed pick the lowest). The
# DB stores ``follow_weight = 1.0`` for ALL sources (0007), so the loader/normalizer
# encodes this differential itself — these multipliers are applied to follow_weight
# at hydration time (daily_batch.load_active_user_inputs), NOT read from the DB.
FOLLOW_SOURCE_WEIGHT: dict[str, float] = {
    "seed": 1.0,
    "more": 2.0,
    "custom": 3.0,
}

# Reason: a word-boundary matcher cache — compiling one regex per (entity, story)
# is wasteful at batch scale, so we compile once per distinct label/ticker term.
_WORD_BOUNDARY_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


class UserProfileInterest(BaseModel):
    """One row of a user's interest profile — the scorer's per-interest input.

    Maps the ``user_interest_profile`` columns the scorer needs
    (``reference/supabase-schema.md``): which interest node, how much the user
    weights it (the Affinity source), and whether it is ``strict`` (caps the
    fallback climb, ``ranking-spec.md`` §2).

    Attributes:
        profile_interest_id: FK to ``interests.interest_id`` the user follows.
        profile_weight: Raw ``profile_weight`` (normalized across the profile to
            yield the 0–1 Affinity term).
        profile_is_strict: ``profile_is_strict`` — "just give me cricket, nothing
            broader"; halts the fallback at this node (no upward broadening).

    Example:
        >>> interest = UserProfileInterest(
        ...     profile_interest_id="int-arsenal",
        ...     profile_weight=3.0,
        ...     profile_is_strict=False,
        ... )
        >>> interest.profile_weight
        3.0
    """

    profile_interest_id: str = Field(
        ..., description="FK to interests.interest_id the user follows"
    )
    profile_weight: float = Field(
        default=1.0, ge=0.0, description="Raw profile_weight (Affinity source)"
    )
    profile_is_strict: bool = Field(
        default=False,
        description="profile_is_strict — caps the fallback climb at this node",
    )


class FollowedEntity(BaseModel):
    """One entity a user follows — the EntityBonus matcher's per-entity input.

    Hydrated by the loader from ``user_entity_follows ⋈ entities`` (migration 0007):
    the user's follow row joined to the entity's identity columns. One underlying
    entity (e.g. Nvidia) may be MULTIPLE ``entities`` rows under different paths;
    the matcher dedupes on identity (label + ticker + kind) so a story is bonused
    once, not once per followed path (SP1 report §7.5).

    ``follow_weight`` carries the source-intent weighting ALREADY applied by the
    loader (``FOLLOW_SOURCE_WEIGHT``: custom > more > seed): the DB stores 1.0 for
    every source, so the differential is encoded at hydration time, not read from
    the DB. The scorer max-normalizes these per user (``normalize_entity_follow_weights``)
    exactly as ``normalize_affinities`` does for interest weights.

    Attributes:
        entity_id: The ``entities.entity_id`` (path-derived slug PK) followed.
        entity_label: Display label matched as whole words in the story title
            (e.g. "Nvidia").
        entity_ticker: Stock/asset ticker (e.g. "NVDA"); matched as a whole word
            ONLY when ``entity_kind == 'company'``. None when the entity has none.
        entity_kind: The ``entity_kind`` enum value (company | team | person | ...).
            Gates whether the ticker participates in matching.
        follow_weight: Source-weighted follow strength (loader-applied custom>more>seed;
            max-normalized per user before the bonus).
        follow_path: The ``follow_path`` array (the recursive picker path) for audit.

    Example:
        >>> entity = FollowedEntity(
        ...     entity_id="tech/semiconductors-chips/companies/nvidia",
        ...     entity_label="Nvidia",
        ...     entity_ticker="NVDA",
        ...     entity_kind="company",
        ...     follow_weight=3.0,
        ... )
        >>> entity.entity_label
        'Nvidia'
    """

    entity_id: str = Field(..., description="The entities.entity_id followed")
    entity_label: str = Field(
        ..., min_length=1, description="Display label, matched as whole words in title"
    )
    entity_ticker: str | None = Field(
        default=None,
        description="Ticker; matched as a whole word only when entity_kind=='company'",
    )
    entity_kind: str = Field(
        ..., description="entity_kind enum (company | team | person | ...)"
    )
    follow_weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Source-weighted follow strength (custom>more>seed, loader-applied)",
    )
    follow_path: list[str] = Field(
        default_factory=list, description="The recursive picker follow_path (audit)"
    )


class ScoredCandidate(BaseModel):
    """One (user, story) pair scored for the feed — the generator's output unit.

    Carries the components so the allocator (SP4) and tests can assert on the
    Score math, not just the final number.

    Attributes:
        story_id: The canonical story id scored.
        matched_interest_id: The followed interest node this score is attributed
            to (drives ``daily_feeds.feed_matched_interest_id`` in SP4).
        score: The final ``Score`` (reference/ranking-spec.md §1).
        affinity: Normalized 0–1 affinity for the matched interest.
        depth_match: The DepthMatch multiplier (1.0 / 0.6 / 0.3).
        importance: The 0–1 importance term.
        freshness: The 0–1 freshness term.
        fallback_depth: How far the fallback climbed to reach this story for this
            interest (0 = leaf, 1 = parent, 2 = grandparent).
        entity_bonus: The additive EntityBonus folded into ``score`` (phase-5a SP2);
            0.0 when the story matched no followed entity. Carried so the allocator
            / sim / tests can assert the lift came from the bonus, not the base.
        matched_entity_id: The followed ``entity_id`` whose title/ticker match
            produced ``entity_bonus`` (None when no entity matched).
        feed_category: The single best-fit screen category this story classified
            into (phase-5a SP2 ``assign_category``); None on the SP3 ranking path
            that predates classification.

    Example:
        >>> cand = ScoredCandidate(
        ...     story_id="s1", matched_interest_id="int-arsenal", score=0.7,
        ...     affinity=1.0, depth_match=1.0, importance=0.5, freshness=1.0,
        ... )
        >>> cand.score
        0.7
    """

    story_id: str = Field(..., description="The canonical story id scored")
    matched_interest_id: str = Field(
        ..., description="The followed interest node this score is attributed to"
    )
    score: float = Field(..., ge=0.0, description="The final per-user Score")
    affinity: float = Field(..., ge=0.0, le=1.0, description="Normalized 0–1 affinity")
    depth_match: float = Field(..., ge=0.0, le=1.0, description="DepthMatch multiplier")
    importance: float = Field(..., ge=0.0, le=1.0, description="0–1 importance term")
    freshness: float = Field(..., ge=0.0, le=1.0, description="0–1 freshness term")
    fallback_depth: int = Field(
        default=0, ge=0, le=2, description="How far the fallback climbed (0/1/2)"
    )
    entity_bonus: float = Field(
        default=0.0,
        ge=0.0,
        description="Additive EntityBonus folded into score (0.0 if no entity matched)",
    )
    matched_entity_id: str | None = Field(
        default=None,
        description="The followed entity_id that produced the bonus, or None",
    )
    feed_category: FeedCategory | None = Field(
        default=None,
        description="Best-fit screen category (phase-5a SP2); None on the SP3 path",
    )


def normalize_affinities(
    profile_interests: list[UserProfileInterest],
) -> dict[str, float]:
    """Normalize raw ``profile_weight`` values into 0–1 Affinity per interest.

    Affinity (reference/ranking-spec.md §1) is the user's normalized weight on a
    matched interest. We divide each interest's weight by the **max** weight in
    the profile so the most-followed interest reaches Affinity 1.0 and the rest
    scale relative to it (max-normalization keeps a single dominant interest at
    full affinity rather than diluting it across the count, which sum-norm would
    do).

    Args:
        profile_interests: The user's interest profile rows.

    Returns:
        ``{interest_id: affinity_0_to_1}``. Empty when the profile is empty or
        every weight is zero.

    Example:
        >>> affinities = normalize_affinities([
        ...     UserProfileInterest(profile_interest_id="a", profile_weight=4.0),
        ...     UserProfileInterest(profile_interest_id="b", profile_weight=1.0),
        ... ])
        >>> affinities["a"], affinities["b"]
        (1.0, 0.25)
    """
    if not profile_interests:
        return {}
    max_weight = max(interest.profile_weight for interest in profile_interests)
    if max_weight <= 0.0:
        return {interest.profile_interest_id: 0.0 for interest in profile_interests}
    return {
        interest.profile_interest_id: interest.profile_weight / max_weight
        for interest in profile_interests
    }


# Identity of a logical entity for dedup: its label (case-folded) + ticker + kind.
# Two ``entities`` rows that are the same real-world entity under different paths
# (e.g. Nvidia under AI-hardware vs Business-earnings) share this identity, so the
# matcher bonuses the underlying entity ONCE per story (SP1 report §7.5).
_EntityIdentity = tuple[str, str | None, str]


def _entity_identity(entity: FollowedEntity) -> _EntityIdentity:
    """Return the dedup identity of a followed entity (label + ticker + kind)."""
    return (entity.entity_label.casefold(), entity.entity_ticker, entity.entity_kind)


def normalize_entity_follow_weights(
    followed_entities: list[FollowedEntity],
) -> dict[str, float]:
    """Max-normalize per-user ``follow_weight`` into a 0–1 multiplier per entity.

    Mirrors :func:`normalize_affinities` exactly: divide each entity's (already
    source-weighted) ``follow_weight`` by the **max** weight in the user's follow
    set so the highest-intent follow reaches 1.0 and the rest scale to it. This is
    the ``normalized_follow_weight`` of the EntityBonus term
    (``EntityBonus = normalized_follow_weight × ENTITY_BONUS_WEIGHT``, phase-5a SP2).

    Keyed by ``entity_id`` (not identity) because two paths of the same entity can
    carry different source weights — each gets its own normalized value; the
    per-story matcher then dedupes on identity and keeps the strongest.

    Args:
        followed_entities: The user's followed entities (loader-hydrated).

    Returns:
        ``{entity_id: normalized_weight_0_to_1}``. Empty when the user follows no
        entity or every weight is zero (then all map to 0.0).

    Example:
        >>> weights = normalize_entity_follow_weights([
        ...     FollowedEntity(entity_id="a", entity_label="A", entity_kind="company",
        ...                    follow_weight=3.0),
        ...     FollowedEntity(entity_id="b", entity_label="B", entity_kind="team",
        ...                    follow_weight=1.0),
        ... ])
        >>> weights["a"], weights["b"]
        (1.0, ...)
    """
    if not followed_entities:
        return {}
    max_weight = max(entity.follow_weight for entity in followed_entities)
    if max_weight <= 0.0:
        return {entity.entity_id: 0.0 for entity in followed_entities}
    return {
        entity.entity_id: entity.follow_weight / max_weight
        for entity in followed_entities
    }


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    """Compile (and cache) a case-insensitive whole-word regex for ``term``.

    Uses ``\\b...\\b`` around the escaped term so "Meta" does NOT match
    "metabolism" and "AI" does NOT substring-match "explainable". The pattern is
    cached per distinct term — batch scoring re-uses one compiled regex across all
    stories.

    Args:
        term: The label or ticker to match as a whole word.

    Returns:
        A compiled, case-insensitive whole-word pattern.
    """
    cached = _WORD_BOUNDARY_PATTERN_CACHE.get(term)
    if cached is None:
        cached = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        _WORD_BOUNDARY_PATTERN_CACHE[term] = cached
    return cached


def _entity_matches_title(entity: FollowedEntity, title: str) -> bool:
    """True when a followed entity matches the story title (phase-5a SP2 rule).

    Match surface (owner-locked):
      - ``entity_label`` as **whole words** in the title (case-insensitive), AND
      - ``entity_ticker`` as a **whole word** — but ONLY when
        ``entity_kind == 'company'`` (a non-company ticker like a person's
        initials must not boost a generic headline; the residual short-ticker
        false-positive risk for ``AI``/``ON``/``ALL`` is documented + tested).

    Args:
        entity: The followed entity to test.
        title: The story title (already a plain string).

    Returns:
        True when the label (or company ticker) appears as a whole word in the title.
    """
    if entity.entity_label and _word_boundary_pattern(entity.entity_label).search(
        title
    ):
        return True
    # Reason: ticker match is GATED on company kind — a person/team/genre entity's
    # "ticker" (if any) must never fire on a generic headline word.
    if (
        entity.entity_kind == "company"
        and entity.entity_ticker
        and _word_boundary_pattern(entity.entity_ticker).search(title)
    ):
        return True
    return False


def entity_title_match(
    story: CanonicalStory,
    followed_entities: list[FollowedEntity],
) -> list[FollowedEntity]:
    """Return the deduped followed entities whose label/ticker hit the story title.

    Whole-word matches ``entity_label`` (and the company-gated ``entity_ticker``)
    against ``story.canonical_title``. Dedupes on identity (label + ticker + kind)
    so one logical entity followed via several paths is returned ONCE — keeping the
    best (highest ``follow_weight``) representative so the bonus reflects the
    strongest follow of that entity.

    Args:
        story: The canonical story (its title is the match surface).
        followed_entities: The user's followed entities.

    Returns:
        One :class:`FollowedEntity` per matched logical entity (deduped by identity,
        highest follow_weight kept). Empty when no entity matches.

    Example:
        >>> # See tests/agents/pipeline/test_ranking.py for the whole-word +
        >>> # company-ticker-gate behavior asserted against this matcher.
    """
    title = story.canonical_title or ""
    best_by_identity: dict[_EntityIdentity, FollowedEntity] = {}
    for entity in followed_entities:
        if not _entity_matches_title(entity, title):
            continue
        identity = _entity_identity(entity)
        existing = best_by_identity.get(identity)
        if existing is None or entity.follow_weight > existing.follow_weight:
            best_by_identity[identity] = entity
    return list(best_by_identity.values())


def compute_entity_bonus(
    story: CanonicalStory,
    followed_entities: list[FollowedEntity],
    normalized_follow_weights: dict[str, float],
) -> tuple[float, str | None]:
    """Compute the additive EntityBonus for a story + the entity that earned it.

    ``EntityBonus = normalized_follow_weight × ENTITY_BONUS_WEIGHT`` for a story
    that matches a followed entity (phase-5a SP2). When several distinct followed
    entities match one story, the **strongest** (highest normalized weight) wins —
    the bonus is a single additive lift, not a sum (a story is not multiply boosted
    for matching two unrelated follows).

    Args:
        story: The candidate story.
        followed_entities: The user's followed entities (empty → no bonus).
        normalized_follow_weights: ``{entity_id: 0-1}`` from
            :func:`normalize_entity_follow_weights`.

    Returns:
        ``(bonus, matched_entity_id)`` — ``(0.0, None)`` when nothing matched.

    Example:
        >>> # See tests/agents/pipeline/test_ranking.py for the strict-lift +
        >>> # custom>seed assertions against this function.
    """
    if not followed_entities:
        return 0.0, None
    matched = entity_title_match(story, followed_entities)
    if not matched:
        return 0.0, None
    best_entity = max(
        matched,
        key=lambda entity: normalized_follow_weights.get(entity.entity_id, 0.0),
    )
    normalized_weight = normalized_follow_weights.get(best_entity.entity_id, 0.0)
    bonus = normalized_weight * ENTITY_BONUS_WEIGHT
    return bonus, best_entity.entity_id


def compute_story_score(
    affinity: float,
    match_depth: int,
    story: CanonicalStory,
    now_utc: datetime,
) -> tuple[float, float, float, float]:
    """Compute the per-(user, story) Score and its component terms.

    Implements ``reference/ranking-spec.md`` §1 verbatim::

        Score = (Affinity × DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2

    The Importance/Freshness terms reuse the produce-gate primitives so a
    story scores identically at gate-time and rank-time.

    Args:
        affinity: The user's normalized 0–1 affinity for the matched interest.
        match_depth: The story's ``story_interest_match_depth`` against the
            matched interest (0 leaf / 1 parent / 2 grandparent) → DepthMatch.
        story: The canonical story (carries ``story_outlet_count`` and
            ``canonical_published_utc``).
        now_utc: Current time for the freshness decay (injected for tests).

    Returns:
        ``(score, depth_match, importance, freshness)``.

    Example:
        >>> from datetime import datetime, timezone
        >>> story = CanonicalStory(
        ...     canonical_story_id="s1", canonical_title="t",
        ...     canonical_url="u", canonical_normalized_url="u",
        ...     canonical_published_utc=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ...     canonical_primary_outlet_domain="d", story_outlet_count=6,
        ... )
        >>> score, dm, imp, fresh = compute_story_score(
        ...     1.0, 0, story, datetime(2026, 5, 31, tzinfo=timezone.utc),
        ... )
        >>> round(dm, 1)
        1.0
    """
    depth_match = DEPTH_MATCH_BY_DEPTH.get(match_depth, 0.0)
    importance = compute_importance_score(story.story_outlet_count)
    freshness = compute_freshness_score(story.canonical_published_utc, now_utc)
    score = (
        (affinity * depth_match) * AFFINITY_WEIGHT
        + importance * IMPORTANCE_WEIGHT
        + freshness * FRESHNESS_WEIGHT
    )
    return score, depth_match, importance, freshness


def _index_tags_by_story(
    story_interest_tags: list[StoryInterestTag],
) -> dict[str, dict[str, int]]:
    """Index ``story_interests`` tags as ``{story_id: {interest_id: match_depth}}``.

    Args:
        story_interest_tags: All ``story_interests`` tag payloads for the pool.

    Returns:
        Nested lookup of each story's tagged interests and their match depth.
    """
    index: dict[str, dict[str, int]] = {}
    for tag in story_interest_tags:
        index.setdefault(tag.story_interest_story_id, {})[
            tag.story_interest_interest_id
        ] = tag.story_interest_match_depth
    return index


def score_stories_for_interest(
    interest_id: str,
    affinity: float,
    stories: list[CanonicalStory],
    tags_by_story: dict[str, dict[str, int]],
    now_utc: datetime,
    fallback_depth: int = 0,
) -> list[ScoredCandidate]:
    """Score every story tagged to one interest node, for one user.

    A story is a candidate for ``interest_id`` only if it carries a
    ``story_interests`` tag for that exact node (the ancestor tagging at SP1
    already wrote parent/grandparent rows, so a broad node catches niche stories
    here at the lower DepthMatch).

    Args:
        interest_id: The interest node being scored against.
        affinity: The user's normalized affinity for ``interest_id``.
        stories: The candidate story pool.
        tags_by_story: ``{story_id: {interest_id: match_depth}}`` index.
        now_utc: Current time for freshness.
        fallback_depth: How far the fallback has climbed to reach this node
            (recorded on each produced candidate for SP4/audit).

    Returns:
        Scored candidates for this interest, descending by score.
    """
    scored: list[ScoredCandidate] = []
    for story in stories:
        story_tags = tags_by_story.get(story.canonical_story_id)
        if not story_tags or interest_id not in story_tags:
            continue
        match_depth = story_tags[interest_id]
        score, depth_match, importance, freshness = compute_story_score(
            affinity=affinity,
            match_depth=match_depth,
            story=story,
            now_utc=now_utc,
        )
        scored.append(
            ScoredCandidate(
                story_id=story.canonical_story_id,
                matched_interest_id=interest_id,
                score=score,
                affinity=affinity,
                depth_match=depth_match,
                importance=importance,
                freshness=freshness,
                fallback_depth=fallback_depth,
            )
        )
    scored.sort(key=lambda candidate: candidate.score, reverse=True)
    return scored


def _walk_ancestors(
    leaf_interest_id: str,
    interest_nodes: dict[str, InterestNode],
) -> list[str]:
    """Return the climb path ``[leaf, parent, grandparent]`` for a leaf node.

    Walks ``parent_interest_id`` up the taxonomy, capped at 3 levels
    (leaf → parent → grandparent) — the depth the ancestor tagging produces.

    Args:
        leaf_interest_id: The followed leaf node to climb from.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.

    Returns:
        The ordered climb path (always starts with the leaf; truncates when an
        ancestor is missing from the taxonomy or 3 levels are reached).
    """
    path: list[str] = [leaf_interest_id]
    current = interest_nodes.get(leaf_interest_id)
    while current is not None and current.parent_interest_id and len(path) < 3:
        parent_id = current.parent_interest_id
        path.append(parent_id)
        current = interest_nodes.get(parent_id)
    return path


def generate_fallback_candidates(
    followed_interest: UserProfileInterest,
    affinity: float,
    stories: list[CanonicalStory],
    tags_by_story: dict[str, dict[str, int]],
    interest_nodes: dict[str, InterestNode],
    now_utc: datetime,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> list[ScoredCandidate]:
    """Generate candidates for one followed leaf via the fallback tree (§2).

    Walks the taxonomy down→up for the followed leaf until the feed budget is
    met (``reference/ranking-spec.md`` §2):

      1. Score stories at the **leaf** node.
      2. If NO story there clears ``Score ≥ T``, fall back to the **parent**,
         then the **grandparent**.
      3. Stop when: a qualifying story (``Score ≥ T``) is found, OR the interest
         is ``strict`` (no upward broadening — halt at the leaf), OR the
         grandparent (depth-0 category) is reached.

    Args:
        followed_interest: The followed leaf interest profile row (carries the
            ``strict`` flag).
        affinity: The user's normalized affinity for this leaf.
        stories: The candidate story pool.
        tags_by_story: ``{story_id: {interest_id: match_depth}}`` index.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup (drives
            the ancestor climb).
        now_utc: Current time for freshness.
        score_threshold: ``T`` — the qualifying/stop threshold.

    Returns:
        The scored candidates from the FIRST level that produced a qualifying
        story (or the leaf-level scores if none qualifies anywhere), descending
        by score. Empty when the leaf has no tagged stories at any level.

    Example:
        >>> # See tests/agents/pipeline/test_fallback_tree.py for the climb-stop
        >>> # behavior asserted against this generator.
    """
    leaf_id = followed_interest.profile_interest_id

    # Reason: strict means "nothing broader" — score ONLY the leaf, never climb,
    # even if no leaf story qualifies (reference/ranking-spec.md §2 stop #2).
    if followed_interest.profile_is_strict:
        leaf_scored = score_stories_for_interest(
            interest_id=leaf_id,
            affinity=affinity,
            stories=stories,
            tags_by_story=tags_by_story,
            now_utc=now_utc,
            fallback_depth=0,
        )
        logger.info(
            "fallback_strict_leaf_only",
            interest_id=leaf_id,
            candidate_count=len(leaf_scored),
            qualifying=sum(1 for c in leaf_scored if c.score >= score_threshold),
        )
        return leaf_scored

    climb_path = _walk_ancestors(leaf_id, interest_nodes)
    leaf_level_scored: list[ScoredCandidate] = []

    for fallback_depth, node_id in enumerate(climb_path):
        node_scored = score_stories_for_interest(
            interest_id=node_id,
            affinity=affinity,
            stories=stories,
            tags_by_story=tags_by_story,
            now_utc=now_utc,
            fallback_depth=fallback_depth,
        )
        if fallback_depth == 0:
            leaf_level_scored = node_scored

        qualifying = [c for c in node_scored if c.score >= score_threshold]
        if qualifying:
            logger.info(
                "fallback_resolved",
                leaf_interest_id=leaf_id,
                resolved_interest_id=node_id,
                fallback_depth=fallback_depth,
                qualifying_count=len(qualifying),
            )
            return node_scored

    # Reason: nothing qualified at any climbed level — return the leaf-level
    # scores so the allocator can still consider them (the §3 floor/redistribute
    # rules decide whether to use them); never silently drop the interest.
    logger.info(
        "fallback_no_qualifier",
        leaf_interest_id=leaf_id,
        climbed_levels=len(climb_path),
        leaf_candidate_count=len(leaf_level_scored),
        fix_suggestion="No story cleared T at leaf/parent/grandparent; "
        "allocator falls back per ranking-spec §3.",
    )
    return leaf_level_scored


def score_candidates_for_user(
    profile_interests: list[UserProfileInterest],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    now_utc: datetime | None = None,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> dict[str, list[ScoredCandidate]]:
    """Generate the full candidate set for one user across all followed leaves.

    Runs the fallback tree (§2) once per followed interest and returns the scored
    candidates keyed by the FOLLOWED leaf interest id — the per-interest buckets
    the SP4 allocator (§3) fills. This is the SP3→SP4 ranking handoff.

    Args:
        profile_interests: The user's full interest profile.
        stories: The candidate story pool.
        story_interest_tags: All ``story_interests`` tag payloads for the pool.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        now_utc: Current time for freshness (defaults to ``utcnow``).
        score_threshold: ``T`` — the qualifying/stop threshold.

    Returns:
        ``{followed_leaf_interest_id: [ScoredCandidate, ...]}`` — descending by
        score within each bucket.

    Example:
        >>> # See tests/agents/pipeline/test_ranking.py for affinity-dominant
        >>> # ordering asserted against this entry point.
    """
    now = now_utc or datetime.now(timezone.utc)
    affinities = normalize_affinities(profile_interests)
    tags_by_story = _index_tags_by_story(story_interest_tags)

    candidates_by_interest: dict[str, list[ScoredCandidate]] = {}
    for followed_interest in profile_interests:
        leaf_id = followed_interest.profile_interest_id
        affinity = affinities.get(leaf_id, 0.0)
        candidates_by_interest[leaf_id] = generate_fallback_candidates(
            followed_interest=followed_interest,
            affinity=affinity,
            stories=stories,
            tags_by_story=tags_by_story,
            interest_nodes=interest_nodes,
            now_utc=now,
            score_threshold=score_threshold,
        )

    logger.info(
        "ranking_scored_candidates_completed",
        followed_interest_count=len(profile_interests),
        total_candidates=sum(len(v) for v in candidates_by_interest.values()),
    )
    return candidates_by_interest


def assign_category(
    story_id: str,
    tags_by_story: dict[str, dict[str, int]],
    interest_nodes: dict[str, InterestNode],
) -> FeedCategory:
    """Classify a story into exactly ONE best-fit screen category (phase-5a SP2).

    The single best-fit rule (clean 30-slot accounting — every story lands in
    exactly one of the 8 ``FeedCategory`` buckets, no duplicates):

      1. Among the story's ``story_interests`` tags, pick the one with the
         **lowest ``match_depth``** — leaf (0) beats parent (1) beats grandparent
         (2). A leaf-tagged interest is the most *specific* hit, so its category
         is the truest fit.
      2. Tiebreak (multiple tags at the same lowest depth): by the matched
         interest's **slug** (stable, deterministic). NOTE: the locked rule says
         "tiebreak by interest sort order", but :class:`InterestNode` does not
         carry ``interest_sort_order`` — the slug tiebreak is the deterministic
         stand-in (documented divergence; slug order is a stable proxy and the
         common case has a single lowest-depth tag).
      3. Map the winning interest's slug up to its category via
         :func:`agents.pipeline.categories.category_for_slug`.

    A story with NO resolvable tag (no tags, or none of its tags' interests are in
    the taxonomy) falls back to :data:`DEFAULT_CATEGORY` so it is never dropped.

    Args:
        story_id: The canonical story id to classify.
        tags_by_story: ``{story_id: {interest_id: match_depth}}`` index (from
            :func:`_index_tags_by_story`).
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup (resolves
            an interest id to its slug).

    Returns:
        The single best-fit :data:`FeedCategory` for the story.

    Example:
        >>> # See tests/agents/pipeline/test_ranking.py: a Nvidia earnings story
        >>> # tagged on a markets-rooted interest classifies into 'markets'.
    """
    story_tags = tags_by_story.get(story_id) or {}
    # Reason: consider only tags whose interest resolves to a slug in the taxonomy —
    # an orphan tag (interest absent from interest_nodes) cannot be categorized.
    resolvable = [
        (interest_id, match_depth, interest_nodes[interest_id].interest_slug)
        for interest_id, match_depth in story_tags.items()
        if interest_id in interest_nodes
    ]
    if not resolvable:
        return DEFAULT_CATEGORY
    # Lowest match_depth first (leaf < parent < grandparent); tiebreak by slug.
    _best_interest_id, _best_depth, best_slug = min(
        resolvable, key=lambda item: (item[1], item[2])
    )
    return category_for_slug(best_slug)


def _best_candidate_per_story(
    candidates_by_interest: dict[str, list[ScoredCandidate]],
) -> dict[str, ScoredCandidate]:
    """Collapse the per-interest candidate buckets to ONE best candidate per story.

    A story tagged to several followed interests is scored once per interest; for
    clean 30-slot accounting it must appear once, attributed to its strongest
    (highest-Score) interest. Returns the highest-Score candidate per story id.
    """
    best_by_story: dict[str, ScoredCandidate] = {}
    for candidates in candidates_by_interest.values():
        for candidate in candidates:
            existing = best_by_story.get(candidate.story_id)
            if existing is None or candidate.score > existing.score:
                best_by_story[candidate.story_id] = candidate
    return best_by_story


def score_and_classify_for_user(
    profile_interests: list[UserProfileInterest],
    followed_entities: list[FollowedEntity],
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    interest_nodes: dict[str, InterestNode],
    now_utc: datetime | None = None,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> dict[FeedCategory, list[ScoredCandidate]]:
    """Score (entity-aware) + classify a user's candidates into the 8 categories.

    The phase-5a SP2 → SP3 handoff. For one user it:

      1. Runs the existing per-interest fallback-tree scorer
         (:func:`score_candidates_for_user`) — the base
         ``Score = (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2``.
      2. Collapses to ONE best candidate per story (its strongest interest).
      3. Folds in the additive **EntityBonus**
         (``normalized_follow_weight × ENTITY_BONUS_WEIGHT``) for any story whose
         title matches a followed entity — recording the bonus + matched entity on
         the candidate. The bonus is ADDITIVE: the base α/β/γ terms are untouched.
      4. Classifies each story into its single best-fit category
         (:func:`assign_category`) and buckets the candidates by the 8 screen
         categories.

    The returned dict ALWAYS has all 8 ``FeedCategory`` keys. The five **topic**
    categories carry the classified candidates (descending by entity-aware score);
    the source categories (``youtube``/``x``) are present-but-empty (no slug maps
    to them — phase-5d), and ``breaking`` is left empty here — SP3 fills it as a
    tier (top-Importance across all categories), it is NOT a slug bucket.

    Args:
        profile_interests: The user's followed interests (Affinity + strict flags).
        followed_entities: The user's followed entities (EntityBonus source; empty
            → byte-identical to the no-entity baseline).
        stories: The shared candidate story pool.
        story_interest_tags: All ``story_interests`` tag payloads for the pool.
        interest_nodes: ``{interest_id: InterestNode}`` taxonomy lookup.
        now_utc: Current time for freshness (defaults to ``utcnow``).
        score_threshold: ``T`` — the qualifying/stop threshold for the scorer.

    Returns:
        ``{feed_category: [ScoredCandidate, ...]}`` — all 8 keys; topic buckets
        descending by (entity-aware) score.

    Example:
        >>> # See tests/agents/pipeline/test_ranking.py for the happy / false-positive
        >>> # / edge / custom>seed DoD assertions against this entry point.
    """
    now = now_utc or datetime.now(timezone.utc)
    candidates_by_interest = score_candidates_for_user(
        profile_interests=profile_interests,
        stories=stories,
        story_interest_tags=story_interest_tags,
        interest_nodes=interest_nodes,
        now_utc=now,
        score_threshold=score_threshold,
    )
    best_by_story = _best_candidate_per_story(candidates_by_interest)
    tags_by_story = _index_tags_by_story(story_interest_tags)
    normalized_follow_weights = normalize_entity_follow_weights(followed_entities)
    stories_by_id = {story.canonical_story_id: story for story in stories}

    buckets: dict[FeedCategory, list[ScoredCandidate]] = empty_category_buckets()
    entity_boosted = 0
    for story_id, candidate in best_by_story.items():
        story = stories_by_id.get(story_id)
        bonus, matched_entity_id = (
            compute_entity_bonus(story, followed_entities, normalized_follow_weights)
            if story is not None
            else (0.0, None)
        )
        if bonus > 0.0:
            entity_boosted += 1
        category = assign_category(story_id, tags_by_story, interest_nodes)
        classified = candidate.model_copy(
            update={
                "score": candidate.score + bonus,
                "entity_bonus": bonus,
                "matched_entity_id": matched_entity_id,
                "feed_category": category,
            }
        )
        buckets[category].append(classified)

    # Reason: keep each topic bucket descending by the entity-aware score so the
    # SP3 allocator fills a category's slots from its strongest candidates first.
    for category_candidates in buckets.values():
        category_candidates.sort(key=lambda c: c.score, reverse=True)

    logger.info(
        "score_and_classify_for_user_completed",
        followed_interest_count=len(profile_interests),
        followed_entity_count=len(followed_entities),
        classified_story_count=len(best_by_story),
        entity_boosted_count=entity_boosted,
    )
    return buckets
