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

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
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
