"""Deterministic synthetic world for the offline ranking simulation.

Builds, with NO randomness and NO external calls:

  * a 3-level interest taxonomy (``dict[interest_id, InterestNode]``),
  * ~100 ``CanonicalStory`` items with varied importance (outlet count) and
    freshness (publish offset from a fixed ``SIM_NOW``),
  * the ``story_interests`` tags for every story via the REAL ancestor tagger
    (``agents.ingestion.ancestor_tagging.merge_story_tags``),
  * three user profiles that stress distinct ranking paths (strict / broad /
    niche — the §3.7 exploration tier is retired under phase-5a), and
  * a separate phase-5a entity-boost scenario (``build_entity_boost_scenario``):
    an isolated world with twin Nvidia stories + a "Build your 30" allocation so
    the sim can prove a followed entity lifts a story AND the per-category budgets
    are honored — without touching the legacy 3-profile invariants.

Everything is index-derived so two runs produce byte-identical worlds — the
simulation is reproducible and its assertions are stable (Rule 9).

Interest ids are the slugs themselves (e.g. ``"sport.cricket.india"``) — there is
no DB here, so a readable stable id beats a uuid.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from agents.ingestion.ancestor_tagging import merge_story_tags
from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.categories import CategoryAllocation
from agents.pipeline.stages.ranking import (
    FollowedEntity,
    UserProfileInterest,
    score_stories_for_interest,
)
from agents.pipeline.stages.ranking import ScoredCandidate

# Reason: a FIXED clock so freshness (an exponential decay on publish time) is
# deterministic — the sim must produce the same feed every run.
SIM_NOW: datetime = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# ── Taxonomy spec: (slug, parent_slug | None, depth_level) ────────────────────
# Three categories with sub- and sub-sub nodes — enough to exercise leaf/parent/
# grandparent DepthMatch (1.0 / 0.6 / 0.3) and strict-vs-broad following.
_TAXONOMY_SPEC: list[tuple[str, str | None, int]] = [
    ("sport", None, 0),
    ("sport.cricket", "sport", 1),
    ("sport.cricket.india", "sport.cricket", 2),
    ("sport.soccer", "sport", 1),
    ("sport.soccer.arsenal", "sport.soccer", 2),
    ("world", None, 0),
    ("world.geopolitics", "world", 1),
    ("world.health", "world", 1),
    ("tech", None, 0),
    ("tech.ai", "tech", 1),
    ("tech.ai.llms", "tech.ai", 2),
    ("tech.gadgets", "tech", 1),
    ("markets", None, 0),
    ("markets.crypto", "markets", 1),
    ("markets.stocks", "markets", 1),
]

# ── Story allocation: how many stories carry each node as their MATCHED interest.
# Sums to ~100. A story matched at a node is ancestor-tagged up to its grandparent
# by merge_story_tags, so e.g. a cricket.india story also serves cricket and sport.
_STORY_COUNTS: dict[str, int] = {
    "sport.cricket.india": 10,
    "sport.soccer.arsenal": 10,
    "sport.cricket": 5,
    "sport.soccer": 5,
    "sport": 6,
    "world.geopolitics": 10,
    "world.health": 6,
    "world": 5,
    "tech.ai.llms": 8,
    "tech.ai": 5,
    "tech.gadgets": 6,
    "tech": 4,
    "markets.crypto": 6,
    "markets.stocks": 6,
    "markets": 3,
}

# Reason: deterministic variety. Outlet count drives Importance (saturates at 12);
# the cycle includes single-outlet niche items AND many-outlet "breaking" items.
_OUTLET_CYCLE: tuple[int, ...] = (2, 4, 1, 8, 3, 13, 5, 2, 16, 6, 1, 9)
# Publish-age cycle in hours from SIM_NOW — drives Freshness (~24h half-life).
_FRESHNESS_CYCLE_HOURS: tuple[int, ...] = (1, 5, 18, 30, 3, 48, 12, 72, 6, 24, 2, 9)

# Short readable headline templates per matched node (index appended).
_HEADLINE_BY_NODE: dict[str, str] = {
    "sport.cricket.india": "India cricket",
    "sport.soccer.arsenal": "Arsenal FC",
    "sport.cricket": "Cricket world",
    "sport.soccer": "Soccer roundup",
    "sport": "Sport wire",
    "world.geopolitics": "Geopolitics",
    "world.health": "Global health",
    "world": "World desk",
    "tech.ai.llms": "LLM frontier",
    "tech.ai": "AI industry",
    "tech.gadgets": "Gadgets",
    "tech": "Tech wire",
    "markets.crypto": "Crypto markets",
    "markets.stocks": "Equities",
    "markets": "Markets wire",
}


class SimProfile(BaseModel):
    """One simulated user: a label + their followed interests + exploration seeds.

    Attributes:
        profile_key: Short stable key (e.g. ``"A"``) for report headers.
        label: Human description of who this user is.
        interests: The user's followed ``UserProfileInterest`` rows.
        exploration_interest_ids: Adjacent (NOT-followed) interest ids to seed the
            ~10% exploration slots from (empty = no exploration for this user).
        followed_entities: The user's followed entities (phase-5a EntityBonus
            source). Empty for the legacy profiles; populated for the entity-boost
            scenario so the sim can prove a followed entity lifts a story.
        category_allocation: The user's per-category slot budgets + manual sequence
            ("Build your 30"). Empty → the allocator's balanced default; populated
            for the entity-boost scenario so the sim can assert exact budgets +
            sequence (phase-5a SP4).
    """

    profile_key: str = Field(..., description="Short stable key for report headers")
    label: str = Field(..., description="Human description of the user")
    interests: list[UserProfileInterest] = Field(
        ..., description="The user's followed interests (affinity + strict)"
    )
    exploration_interest_ids: list[str] = Field(
        default_factory=list,
        description="Adjacent not-followed interest ids to seed exploration from",
    )
    followed_entities: list[FollowedEntity] = Field(
        default_factory=list,
        description="The user's followed entities (phase-5a EntityBonus source)",
    )
    category_allocation: list[CategoryAllocation] = Field(
        default_factory=list,
        description="Per-category slot budgets + manual sequence (Build your 30)",
    )


def build_taxonomy() -> dict[str, InterestNode]:
    """Build the interest taxonomy as ``{interest_id: InterestNode}``.

    Returns:
        The taxonomy map (interest_id == slug) the scorer + ancestor tagger walk.

    Example:
        >>> nodes = build_taxonomy()
        >>> nodes["sport.cricket.india"].parent_interest_id
        'sport.cricket'
    """
    nodes: dict[str, InterestNode] = {}
    for slug, parent_slug, depth in _TAXONOMY_SPEC:
        label = slug.rsplit(".", 1)[-1].replace("_", " ").title()
        nodes[slug] = InterestNode(
            interest_id=slug,
            parent_interest_id=parent_slug,
            interest_slug=slug,
            interest_label=label,
            depth_level=depth,
            interest_search_query=label,
        )
    return nodes


def build_world(
    interest_nodes: dict[str, InterestNode],
) -> tuple[list[CanonicalStory], list[StoryInterestTag]]:
    """Build the ~100-story pool and its ancestor-expanded interest tags.

    Each story is matched to exactly one node (its ``_STORY_COUNTS`` bucket) and
    then ancestor-tagged up to its grandparent by the REAL ``merge_story_tags`` —
    so a ``sport.cricket.india`` story carries (india, 0), (cricket, 1), (sport, 2)
    and reaches a broad ``sport`` follower at the lower DepthMatch.

    Args:
        interest_nodes: The taxonomy map from :func:`build_taxonomy`.

    Returns:
        ``(stories, story_interest_tags)`` — the deduped pool + all tag payloads.

    Example:
        >>> nodes = build_taxonomy()
        >>> stories, tags = build_world(nodes)
        >>> 90 <= len(stories) <= 110
        True
    """
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    global_index = 0

    for matched_node_id, count in _STORY_COUNTS.items():
        headline_stub = _HEADLINE_BY_NODE.get(matched_node_id, matched_node_id)
        for local_index in range(1, count + 1):
            story_id = f"sim-{matched_node_id}-{local_index:02d}"
            outlet_count = _OUTLET_CYCLE[global_index % len(_OUTLET_CYCLE)]
            age_hours = _FRESHNESS_CYCLE_HOURS[
                global_index % len(_FRESHNESS_CYCLE_HOURS)
            ]
            published = SIM_NOW - timedelta(hours=age_hours)
            stories.append(
                CanonicalStory(
                    canonical_story_id=story_id,
                    canonical_title=f"{headline_stub} update {local_index}",
                    canonical_url=f"https://sim.news/{story_id}",
                    canonical_normalized_url=f"https://sim.news/{story_id}",
                    canonical_published_utc=published,
                    canonical_primary_outlet_domain="sim.news",
                    canonical_primary_outlet_name="Sim Wire",
                    covering_outlets=[f"outlet{i}.news" for i in range(outlet_count)],
                    story_outlet_count=outlet_count,
                    canonical_matched_interest_ids=[matched_node_id],
                )
            )
            tags.extend(merge_story_tags(story_id, [matched_node_id], interest_nodes))
            global_index += 1

    return stories, tags


def build_profiles() -> list[SimProfile]:
    """Build the three legacy stress-test user profiles (strict / broad / niche).

    A — strict cricket.india only (proves: no upward fallback, no exploration).
    B — broad multi-interest, varied weights (proves: diversity + niche-reaches-broad
        via ancestor tags + the breaking tier hold under the balanced default).
    C — niche Arsenal + crypto (proves: a deep niche still surfaces; the §3.7
        auto-exploration tier is retired under phase-5a — no exploration slot).

    The ``exploration_interest_ids`` seed on C is now INERT: the category-budget
    allocator ignores the exploration param (the user reserves breadth via budgets).
    It is left in place to keep the legacy profile definition stable; the phase-5a
    entity-boost scenario is built separately (:func:`build_entity_boost_scenario`).

    Returns:
        The three legacy :class:`SimProfile` definitions.
    """
    return [
        SimProfile(
            profile_key="A",
            label="Strict cricket fan — follows India cricket ONLY (strict)",
            interests=[
                UserProfileInterest(
                    profile_interest_id="sport.cricket.india",
                    profile_weight=1.0,
                    profile_is_strict=True,
                ),
            ],
        ),
        SimProfile(
            profile_key="B",
            label="Broad reader — world-heavy, also sport / AI / a little stocks",
            interests=[
                UserProfileInterest(profile_interest_id="world", profile_weight=3.0),
                UserProfileInterest(profile_interest_id="sport", profile_weight=1.0),
                UserProfileInterest(profile_interest_id="tech.ai", profile_weight=2.0),
                UserProfileInterest(
                    profile_interest_id="markets.stocks", profile_weight=0.5
                ),
            ],
        ),
        SimProfile(
            profile_key="C",
            label="Niche fan — Arsenal + crypto, open to adjacent equities",
            interests=[
                UserProfileInterest(
                    profile_interest_id="sport.soccer.arsenal", profile_weight=2.0
                ),
                UserProfileInterest(
                    profile_interest_id="markets.crypto", profile_weight=1.0
                ),
            ],
            exploration_interest_ids=["markets.stocks"],
        ),
    ]


def build_exploration_candidates(
    profile: SimProfile,
    stories: list[CanonicalStory],
    story_interest_tags: list[StoryInterestTag],
    now_utc: datetime = SIM_NOW,
) -> dict[str, list[ScoredCandidate]]:
    """Pre-score adjacent (not-followed) interests to seed the exploration slots.

    Mirrors how SP4 supplies ``exploration_candidates_by_interest`` to the
    allocator: for each adjacent interest the profile is curious about, score the
    stories tagged to that node at a nominal affinity (0.5) so the allocator can
    fill the ~10% exploration reserve. Empty when the profile seeds none.

    Args:
        profile: The simulated user.
        stories: The story pool.
        story_interest_tags: All interest tags for the pool.
        now_utc: The freshness clock (defaults to :data:`SIM_NOW`).

    Returns:
        ``{adjacent_interest_id: [ScoredCandidate, ...]}`` (descending by score).
    """
    if not profile.exploration_interest_ids:
        return {}
    tags_by_story: dict[str, dict[str, int]] = {}
    for tag in story_interest_tags:
        tags_by_story.setdefault(tag.story_interest_story_id, {})[
            tag.story_interest_interest_id
        ] = tag.story_interest_match_depth

    exploration: dict[str, list[ScoredCandidate]] = {}
    for adjacent_id in profile.exploration_interest_ids:
        exploration[adjacent_id] = score_stories_for_interest(
            interest_id=adjacent_id,
            affinity=0.5,
            stories=stories,
            tags_by_story=tags_by_story,
            now_utc=now_utc,
        )
    return exploration


# ── Entity-boost scenario (phase-5a SP4) ──────────────────────────────────────
# A SECOND, isolated synthetic world built so the offline sim can prove the two
# phase-5a invariants DETERMINISTICALLY (no live DB, no network):
#
#   1. a followed entity (Nvidia, custom source) lifts a story ABOVE an otherwise
#      identical non-followed twin WITHIN its category, and
#   2. the user's per-category slot budgets + manual sequence are honored, the
#      source-category (youtube/x) budgets soft-roll into the topic categories,
#      and the feed totals exactly ``Σ budgets`` (== 30).
#
# It is kept separate from ``build_world`` so the legacy 3-profile invariants
# (strict / broad / niche) are untouched — the twin Nvidia stories only exist
# here, where the entity assertion can isolate the bonus as the sole tiebreaker.

# The DoD allocation (phase-5a SP4 (a)/(b)): topic + source budgets sum to 30, so
# the assembled feed must be exactly 30 slots with the 9 source slots (youtube 6 +
# x 3) rolled into the topic categories by sequence.
_ENTITY_SCENARIO_ALLOCATION: tuple[tuple[str, int, int], ...] = (
    # (allocation_category, allocation_slot_count, allocation_sort_order)
    ("breaking", 2, 0),
    ("world_politics", 4, 1),
    ("tech_science", 5, 2),
    ("markets", 4, 3),
    ("sport", 3, 4),
    ("culture", 3, 5),
    ("youtube", 6, 6),  # source-axis: empty today → budget rolls into topics
    ("x", 3, 7),  # source-axis: empty today → budget rolls into topics
)

# Per matched node: how many filler stories to seed (enough to fill every topic
# budget AND leave a surplus so the source soft-roll has somewhere to land).
_ENTITY_SCENARIO_FILLER_COUNTS: dict[str, int] = {
    "world.geopolitics": 10,
    "tech.ai": 10,
    "markets.stocks": 8,
    "sport.soccer.arsenal": 8,
    "world.health": 6,  # tech_science via the health→tech_science slug map
}


def build_entity_boost_scenario(
    interest_nodes: dict[str, InterestNode],
) -> tuple[list[CanonicalStory], list[StoryInterestTag], SimProfile]:
    """Build the isolated entity-boost world + the profile that follows Nvidia.

    Deterministic (no randomness, no clock dependency beyond :data:`SIM_NOW`): the
    pool holds filler stories across the topic categories PLUS a TWIN PAIR of
    ``markets.stocks`` stories that are identical in importance (outlet count) and
    freshness (publish age) — one titled with "Nvidia", one not. The profile
    follows the matching topic leaves AND a custom-source Nvidia entity, with the
    DoD per-category allocation. Because the twins are equal on every base-Score
    term, the EntityBonus is the ONLY differentiator, so a correct allocator must
    place the Nvidia story above its twin within ``markets``.

    Args:
        interest_nodes: The taxonomy map from :func:`build_taxonomy` (the scenario
            reuses the same taxonomy as :func:`build_world`).

    Returns:
        ``(stories, story_interest_tags, profile)`` — the isolated pool, its
        ancestor-expanded tags, and the entity-following :class:`SimProfile`.

    Example:
        >>> nodes = build_taxonomy()
        >>> stories, tags, profile = build_entity_boost_scenario(nodes)
        >>> profile.followed_entities[0].entity_label
        'Nvidia'
    """
    stories: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    global_index = 0

    def _add_story(
        story_id: str,
        title: str,
        matched_node_id: str,
        outlet_count: int,
        age_hours: int,
    ) -> None:
        published = SIM_NOW - timedelta(hours=age_hours)
        stories.append(
            CanonicalStory(
                canonical_story_id=story_id,
                canonical_title=title,
                canonical_url=f"https://sim.news/{story_id}",
                canonical_normalized_url=f"https://sim.news/{story_id}",
                canonical_published_utc=published,
                canonical_primary_outlet_domain="sim.news",
                canonical_primary_outlet_name="Sim Wire",
                covering_outlets=[f"outlet{i}.news" for i in range(outlet_count)],
                story_outlet_count=outlet_count,
                canonical_matched_interest_ids=[matched_node_id],
            )
        )
        tags.extend(merge_story_tags(story_id, [matched_node_id], interest_nodes))

    # Filler stories across the topic categories (index-derived importance/freshness).
    for matched_node_id, count in _ENTITY_SCENARIO_FILLER_COUNTS.items():
        headline_stub = _HEADLINE_BY_NODE.get(matched_node_id, matched_node_id)
        for local_index in range(1, count + 1):
            _add_story(
                story_id=f"ent-{matched_node_id}-{local_index:02d}",
                title=f"{headline_stub} update {local_index}",
                matched_node_id=matched_node_id,
                outlet_count=_OUTLET_CYCLE[global_index % len(_OUTLET_CYCLE)],
                age_hours=_FRESHNESS_CYCLE_HOURS[
                    global_index % len(_FRESHNESS_CYCLE_HOURS)
                ],
            )
            global_index += 1

    # The TWIN PAIR (markets.stocks): identical importance (5 outlets) + freshness
    # (3h old), so the EntityBonus is the ONLY thing separating them. The "twin"
    # title deliberately avoids the word "Nvidia"/"NVDA" so it earns no bonus.
    _add_story(
        story_id="ent-twin-nvidia",
        title="Nvidia Q3 earnings beat expectations",
        matched_node_id="markets.stocks",
        outlet_count=5,
        age_hours=3,
    )
    _add_story(
        story_id="ent-twin-plain",
        title="Chipmaker quarterly earnings beat expectations",
        matched_node_id="markets.stocks",
        outlet_count=5,
        age_hours=3,
    )

    profile = SimProfile(
        profile_key="D",
        label="Entity follower — Build-your-30 budgets + a custom Nvidia follow",
        interests=[
            UserProfileInterest(
                profile_interest_id="world.geopolitics", profile_weight=2.0
            ),
            UserProfileInterest(profile_interest_id="tech.ai", profile_weight=2.0),
            UserProfileInterest(profile_interest_id="world.health", profile_weight=1.0),
            UserProfileInterest(
                profile_interest_id="markets.stocks", profile_weight=2.0
            ),
            UserProfileInterest(
                profile_interest_id="sport.soccer.arsenal", profile_weight=1.0
            ),
        ],
        followed_entities=[
            FollowedEntity(
                entity_id="ai/ai-hardware-compute/companies-topics/nvidia",
                entity_label="Nvidia",
                entity_ticker="NVDA",
                entity_kind="company",
                # Loader-applied custom-source weight (FOLLOW_SOURCE_WEIGHT["custom"]).
                follow_weight=3.0,
                follow_path=["ai", "ai-hardware-compute", "nvidia"],
            )
        ],
        category_allocation=[
            CategoryAllocation(
                allocation_category=category,
                allocation_slot_count=slot_count,
                allocation_sort_order=sort_order,
            )
            for category, slot_count, sort_order in _ENTITY_SCENARIO_ALLOCATION
        ],
    )
    return stories, tags, profile
