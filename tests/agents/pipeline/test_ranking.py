"""Unit tests for the per-(user, story) heuristic scorer (Phase 1d SP3).

DoD (phase file SP3 / Rule 9): assert AFFINITY-DOMINANT ordering — a small
niche-followed story OUTSCORES a generic broad one on the Score *math*, not a
stub. These tests encode WHY the formula is affinity-dominant (α=0.5 × the
leaf-vs-grandparent DepthMatch gap) so they fail if the weights or DepthMatch
ladder change. All inputs are pure data — no DB, no clock, no network.

Score = (Affinity × DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2
        (reference/ranking-spec.md §1)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline import daily_batch
from agents.pipeline.stages.ranking import (
    AFFINITY_WEIGHT,
    DEPTH_MATCH_BY_DEPTH,
    ENTITY_BONUS_WEIGHT,
    FRESHNESS_WEIGHT,
    IMPORTANCE_WEIGHT,
    FollowedEntity,
    UserProfileInterest,
    assign_category,
    compute_story_score,
    entity_title_match,
    normalize_affinities,
    normalize_entity_follow_weights,
    score_and_classify_for_user,
    score_candidates_for_user,
)

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _story(
    story_id: str,
    outlet_count: int,
    published: datetime = _NOW,
    title: str | None = None,
) -> CanonicalStory:
    """Build a minimal canonical story with a given importance/freshness/title."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title if title is not None else f"Headline {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain="example.com",
        covering_outlets=[f"o{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


class TestNormalizeAffinities:
    """Affinity normalization — the 0–1 weight per interest (ranking-spec §1)."""

    def test_max_normalization_scales_top_interest_to_one(self) -> None:
        """The most-followed interest reaches affinity 1.0; others scale to it."""
        affinities = normalize_affinities(
            [
                UserProfileInterest(profile_interest_id="a", profile_weight=4.0),
                UserProfileInterest(profile_interest_id="b", profile_weight=1.0),
            ]
        )
        assert affinities["a"] == 1.0
        assert affinities["b"] == 0.25

    def test_empty_profile_returns_empty(self) -> None:
        """An empty profile yields no affinities (sparse-profile safe)."""
        assert normalize_affinities([]) == {}

    def test_all_zero_weights_do_not_divide_by_zero(self) -> None:
        """Edge: all-zero weights resolve to 0.0 affinity, not a ZeroDivisionError."""
        affinities = normalize_affinities(
            [UserProfileInterest(profile_interest_id="a", profile_weight=0.0)]
        )
        assert affinities == {"a": 0.0}


class TestComputeStoryScore:
    """The Score formula — exact weight application (ranking-spec §1)."""

    def test_score_equals_weighted_sum_of_terms(self) -> None:
        """Score is exactly (Affinity×DepthMatch)·0.5 + Importance·0.3 + Freshness·0.2."""
        # 6 outlets / 12 saturation = 0.5 importance; reported now = 1.0 freshness.
        story = _story("s1", outlet_count=6, published=_NOW)
        score, depth_match, importance, freshness = compute_story_score(
            affinity=1.0, match_depth=0, story=story, now_utc=_NOW
        )
        assert depth_match == DEPTH_MATCH_BY_DEPTH[0] == 1.0
        assert importance == pytest.approx(0.5)
        assert freshness == pytest.approx(1.0)
        expected = (
            (1.0 * 1.0) * AFFINITY_WEIGHT
            + 0.5 * IMPORTANCE_WEIGHT
            + 1.0 * FRESHNESS_WEIGHT
        )
        assert score == pytest.approx(expected)

    def test_depth_match_ladder_penalizes_ancestor_matches(self) -> None:
        """Failure-mode guard: a parent/grandparent match scores below a leaf match."""
        story = _story("s1", outlet_count=6)
        leaf_score, _, _, _ = compute_story_score(1.0, 0, story, _NOW)
        parent_score, _, _, _ = compute_story_score(1.0, 1, story, _NOW)
        grandparent_score, _, _, _ = compute_story_score(1.0, 2, story, _NOW)
        assert leaf_score > parent_score > grandparent_score

    def test_stale_story_loses_freshness(self) -> None:
        """Edge: a 24h-old story halves its freshness term vs a now-dated one."""
        fresh = _story("fresh", outlet_count=6, published=_NOW)
        stale = _story("stale", outlet_count=6, published=_NOW - timedelta(hours=24))
        fresh_score, _, _, fresh_freshness = compute_story_score(1.0, 0, fresh, _NOW)
        _, _, _, stale_freshness = compute_story_score(1.0, 0, stale, _NOW)
        assert stale_freshness == pytest.approx(0.5, abs=0.01)
        assert fresh_freshness > stale_freshness


class TestAffinityDominantOrdering:
    """THE DoD test — a niche-followed small story beats a generic big one."""

    def test_niche_small_story_outscores_generic_big_story(self) -> None:
        """A small niche-followed story outscores a generic big one (Rule 9).

        Mirrors the ranking-spec rationale: a Mumbai-Indians fan should see a
        small Mumbai-Indians item above a generic blockbuster they only catch
        via a low-affinity grandparent. Tests the *math*, not a stub:
          - niche story: 1 outlet (importance ~0.083), leaf-matched (depth 0)
            on the user's TOP interest (affinity 1.0).
          - generic story: 12 outlets (importance 1.0), but only matched via the
            user's grandparent node (depth 2) on a LOW-affinity interest.
        """
        user = [
            UserProfileInterest(profile_interest_id="int-mumbai", profile_weight=5.0),
            UserProfileInterest(profile_interest_id="int-world", profile_weight=1.0),
        ]
        niche = _story("niche", outlet_count=1, published=_NOW)
        generic = _story("generic", outlet_count=12, published=_NOW)
        tags = [
            # niche: leaf-matched on the top interest.
            StoryInterestTag(
                story_interest_story_id="niche",
                story_interest_interest_id="int-mumbai",
                story_interest_match_depth=0,
            ),
            # generic: grandparent-matched on the low-affinity interest.
            StoryInterestTag(
                story_interest_story_id="generic",
                story_interest_interest_id="int-world",
                story_interest_match_depth=2,
            ),
        ]
        nodes = {
            "int-mumbai": InterestNode(
                interest_id="int-mumbai",
                parent_interest_id=None,
                interest_slug="sport.cricket.mumbai",
                interest_label="Mumbai Indians",
            ),
            "int-world": InterestNode(
                interest_id="int-world",
                parent_interest_id=None,
                interest_slug="world",
                interest_label="World",
            ),
        }

        candidates = score_candidates_for_user(
            profile_interests=user,
            stories=[niche, generic],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )

        niche_score = candidates["int-mumbai"][0].score
        generic_score = candidates["int-world"][0].score
        assert niche_score > generic_score, (
            f"niche {niche_score} should beat generic {generic_score} — "
            "affinity×depth dominance failed"
        )

    def test_bucket_keyed_by_followed_leaf_and_sorted_desc(self) -> None:
        """Candidates come back per followed leaf, sorted descending by score."""
        user = [
            UserProfileInterest(profile_interest_id="int-a", profile_weight=2.0),
        ]
        story_hi = _story("hi", outlet_count=12, published=_NOW)
        story_lo = _story("lo", outlet_count=1, published=_NOW - timedelta(hours=48))
        tags = [
            StoryInterestTag(
                story_interest_story_id="hi",
                story_interest_interest_id="int-a",
                story_interest_match_depth=0,
            ),
            StoryInterestTag(
                story_interest_story_id="lo",
                story_interest_interest_id="int-a",
                story_interest_match_depth=0,
            ),
        ]
        nodes = {
            "int-a": InterestNode(
                interest_id="int-a",
                parent_interest_id=None,
                interest_slug="a",
                interest_label="A",
            )
        }
        candidates = score_candidates_for_user(
            profile_interests=user,
            stories=[story_lo, story_hi],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        bucket = candidates["int-a"]
        assert [c.story_id for c in bucket] == ["hi", "lo"]
        assert bucket[0].score >= bucket[1].score


# ── phase-5a SP2: entity-aware Score + story→category classifier ──────────────

# The real seeded Semiconductors leaf (interests.sql) — a Nvidia earnings story is
# leaf-tagged here. Its slug root 'business' maps up to the 'business' screen
# category (categories.SLUG_TO_CATEGORY), so the happy-path DoD lands in business.
_SEMIS_INTEREST_ID = "int-semis"
_SEMIS_NODE = InterestNode(
    interest_id=_SEMIS_INTEREST_ID,
    parent_interest_id=None,
    interest_slug="business.equities.semis",
    interest_label="Semiconductors",
    depth_level=2,
)

# The real seeded Nvidia entity identity (entities.sql): a 'company' with NVDA.
_NVIDIA = FollowedEntity(
    entity_id="tech/semiconductors-chips/companies/nvidia",
    entity_label="Nvidia",
    entity_ticker="NVDA",
    entity_kind="company",
    follow_weight=1.0,
)


def _semis_tag(story_id: str, depth: int = 0) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=_SEMIS_INTEREST_ID,
        story_interest_match_depth=depth,
    )


class TestEntityTitleMatch:
    """Whole-word entity matching — the false-positive guard (Rule 9).

    WHY: the entity signal must be a *precise* lift, not a substring scattergun.
    If matching degraded to substring containment, "Meta" would fire on
    "metabolism" and a 2-letter ticker would fire on any headline — drowning the
    Affinity×Depth signal the spec is built to protect. These tests fail the
    moment the matcher drops its word boundaries or the company-ticker gate.
    """

    def test_label_matches_as_whole_word(self) -> None:
        """A followed label hits when it appears as a whole word in the title."""
        story = _story("s", 6, title="Nvidia Q3 earnings beat expectations")
        matched = entity_title_match(story, [_NVIDIA])
        assert [e.entity_id for e in matched] == [_NVIDIA.entity_id]

    def test_label_does_not_match_substring(self) -> None:
        """'Meta' must NOT fire on 'metabolism' — the \\b boundaries are load-bearing."""
        meta = FollowedEntity(
            entity_id="e-meta",
            entity_label="Meta",
            entity_kind="company",
            entity_ticker="META",
        )
        story = _story("s", 6, title="New study on human metabolism and diet")
        assert entity_title_match(story, [meta]) == []

    def test_company_ticker_matches_as_whole_word(self) -> None:
        """A company ticker (NVDA) matches as a whole word even without the label."""
        story = _story("s", 6, title="Chip rally lifts NVDA to a record high")
        matched = entity_title_match(story, [_NVIDIA])
        assert [e.entity_id for e in matched] == [_NVIDIA.entity_id]

    def test_noncompany_ticker_is_ignored(self) -> None:
        """A NON-company entity's ticker must never match — the kind gate (DoD).

        WHY: a person/genre entity carrying the 'ticker' AI would otherwise boost
        every generic 'AI' headline. The gate restricts ticker matching to
        entity_kind == 'company'.
        """
        ai_person = FollowedEntity(
            entity_id="e-ai-person",
            entity_label="Some Analyst",
            entity_kind="person",
            entity_ticker="AI",
        )
        story = _story("s", 6, title="AI adoption accelerates across industries")
        assert entity_title_match(story, [ai_person]) == []

    def test_company_ticker_ai_still_word_bounded(self) -> None:
        """Even a company ticker 'AI' is whole-word — it must not hit 'explainable'.

        WHY: residual short-ticker risk (AI/ON/ALL) is bounded by \\b, not removed.
        'AI' as a standalone word still fires; embedded letters do not.
        """
        c3 = FollowedEntity(
            entity_id="e-c3ai",
            entity_label="C3 dot ai",  # label that won't appear verbatim
            entity_kind="company",
            entity_ticker="AI",
        )
        embedded = _story("s1", 6, title="The most explainable model yet")
        standalone = _story("s2", 6, title="Shares of AI jump on the deal")
        assert entity_title_match(embedded, [c3]) == []
        assert [e.entity_id for e in entity_title_match(standalone, [c3])] == ["e-c3ai"]

    def test_dedupes_same_entity_followed_via_multiple_paths(self) -> None:
        """One logical entity followed via N paths bonuses ONCE (identity dedup).

        WHY: Nvidia is 3 entities rows (AI / business / tech paths) sharing
        label+ticker+kind. Without identity dedup a story would be double-counted;
        the matcher must collapse them to one, keeping the strongest follow_weight.
        """
        nvidia_ai = _NVIDIA.model_copy(
            update={"entity_id": "ai/.../nvidia", "follow_weight": 1.0}
        )
        nvidia_biz = _NVIDIA.model_copy(
            update={"entity_id": "business/.../nvidia", "follow_weight": 3.0}
        )
        story = _story("s", 6, title="Nvidia unveils its next GPU")
        matched = entity_title_match(story, [nvidia_ai, nvidia_biz])
        assert len(matched) == 1
        # The higher-weight path is the kept representative.
        assert matched[0].follow_weight == 3.0


class TestEntityBonusScore:
    """The additive EntityBonus on the Score — the happy + edge + custom>seed DoD."""

    def _profile_and_nodes(self):
        user = [
            UserProfileInterest(
                profile_interest_id=_SEMIS_INTEREST_ID, profile_weight=3.0
            )
        ]
        nodes = {_SEMIS_INTEREST_ID: _SEMIS_NODE}
        return user, nodes

    def test_nvidia_follower_scores_strictly_higher_and_lands_in_business(self) -> None:
        """HAPPY DoD: a Nvidia follower's 'Nvidia Q3 earnings beat' scores strictly
        higher than the same story WITHOUT the entity bonus, AND classifies into
        'business'.

        WHY: this is the whole point of the phase — a followed entity must
        measurably lift its story within the user's category. If the bonus were
        non-additive (or zero), the two scores would tie and the test fails.
        """
        user, nodes = self._profile_and_nodes()
        story = _story("nvda-earnings", 6, title="Nvidia Q3 earnings beat estimates")
        tags = [_semis_tag("nvda-earnings")]

        with_entity = score_and_classify_for_user(
            profile_interests=user,
            followed_entities=[_NVIDIA],
            stories=[story],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        baseline = score_and_classify_for_user(
            profile_interests=user,
            followed_entities=[],  # no entities → no bonus
            stories=[story],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )

        boosted = with_entity["business"][0]
        plain = baseline["business"][0]
        # Best-fit category is business (business-rooted leaf tag).
        assert boosted.feed_category == "business"
        assert boosted.matched_entity_id == _NVIDIA.entity_id
        # Strictly higher, and the lift is exactly the entity bonus term.
        assert boosted.score > plain.score
        assert boosted.entity_bonus == pytest.approx(ENTITY_BONUS_WEIGHT)
        assert boosted.score == pytest.approx(plain.score + ENTITY_BONUS_WEIGHT)
        # The base α/β/γ terms are untouched (only the additive term differs).
        assert boosted.affinity == plain.affinity
        assert boosted.importance == plain.importance
        assert boosted.freshness == plain.freshness

    def test_no_matching_entity_is_byte_identical_to_baseline(self) -> None:
        """EDGE DoD: a follower whose entities match NO story gets identical scores.

        WHY: the bonus must be inert when nothing matches — an entity follow can
        never *lower* or perturb a non-matching story's score. The two candidate
        sets must be equal field-for-field.
        """
        user, nodes = self._profile_and_nodes()
        story = _story("amd-news", 6, title="AMD reveals a new server chip")
        tags = [_semis_tag("amd-news")]

        with_entity = score_and_classify_for_user(
            profile_interests=user,
            followed_entities=[_NVIDIA],  # Nvidia does not appear in the title
            stories=[story],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        baseline = score_and_classify_for_user(
            profile_interests=user,
            followed_entities=[],
            stories=[story],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        boosted = with_entity["business"][0]
        plain = baseline["business"][0]
        assert boosted.entity_bonus == 0.0
        assert boosted.matched_entity_id is None
        assert boosted.score == pytest.approx(plain.score)
        assert boosted.model_dump() == plain.model_dump()

    def test_custom_source_follow_beats_seed_source_follow(self) -> None:
        """CUSTOM>SEED DoD: a custom-source follow yields a LARGER bonus than a
        seed-source follow on the same story.

        WHY: custom is the highest-intent signal (0007 design). The DB stores
        follow_weight=1.0 for both, so the loader encodes the differential — here
        the FollowedEntity weights stand in for that loader output (custom weighted
        3.0 vs seed 1.0). Max-normalization then makes the custom story reach the
        full ENTITY_BONUS_WEIGHT while the seed story gets a fraction of it.

        A user follows TWO entities — a custom-weighted one and a seed-weighted one
        — and we compare the bonus each earns on its own matching story.
        """
        user = [
            UserProfileInterest(
                profile_interest_id=_SEMIS_INTEREST_ID, profile_weight=3.0
            )
        ]
        nodes = {_SEMIS_INTEREST_ID: _SEMIS_NODE}
        custom_entity = FollowedEntity(
            entity_id="e-custom",
            entity_label="Nvidia",
            entity_ticker="NVDA",
            entity_kind="company",
            follow_weight=3.0,  # loader-applied custom multiplier
        )
        seed_entity = FollowedEntity(
            entity_id="e-seed",
            entity_label="Intel",
            entity_ticker="INTC",
            entity_kind="company",
            follow_weight=1.0,  # loader-applied seed multiplier
        )
        custom_story = _story("c", 6, title="Nvidia ships record GPUs")
        seed_story = _story("s", 6, title="Intel delays its next node")
        tags = [_semis_tag("c"), _semis_tag("s")]

        buckets = score_and_classify_for_user(
            profile_interests=user,
            followed_entities=[custom_entity, seed_entity],
            stories=[custom_story, seed_story],
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        by_story = {c.story_id: c for c in buckets["business"]}
        # Custom follow (normalized weight 1.0) earns the full bonus; seed (1/3) less.
        assert by_story["c"].entity_bonus == pytest.approx(ENTITY_BONUS_WEIGHT)
        assert by_story["s"].entity_bonus == pytest.approx(ENTITY_BONUS_WEIGHT / 3.0)
        assert by_story["c"].entity_bonus > by_story["s"].entity_bonus

    def test_strongest_matching_entity_wins_not_the_sum(self) -> None:
        """Two distinct follows matching one story → single (max) bonus, not a sum.

        WHY: a story is one lift, not multiply-boosted for unrelated follows — else
        a story matching several follows could leapfrog the whole feed.
        """
        a = FollowedEntity(
            entity_id="e-a",
            entity_label="Apple",
            entity_kind="company",
            follow_weight=1.0,
        )
        b = FollowedEntity(
            entity_id="e-b",
            entity_label="Google",
            entity_kind="company",
            follow_weight=1.0,
        )
        story = _story("s", 6, title="Apple and Google announce a partnership")
        weights = normalize_entity_follow_weights([a, b])
        # Both normalize to 1.0; the bonus is one ENTITY_BONUS_WEIGHT, not double.
        from agents.pipeline.stages.ranking import compute_entity_bonus

        bonus, matched_id = compute_entity_bonus(story, [a, b], weights)
        assert bonus == pytest.approx(ENTITY_BONUS_WEIGHT)
        assert matched_id in {"e-a", "e-b"}


class TestNormalizeEntityFollowWeights:
    """Max-normalization mirrors normalize_affinities exactly (Rule 9)."""

    def test_max_normalization_scales_top_follow_to_one(self) -> None:
        """The strongest follow reaches 1.0; the rest scale relative to it."""
        weights = normalize_entity_follow_weights(
            [
                FollowedEntity(
                    entity_id="a",
                    entity_label="A",
                    entity_kind="company",
                    follow_weight=3.0,
                ),
                FollowedEntity(
                    entity_id="b",
                    entity_label="B",
                    entity_kind="team",
                    follow_weight=1.0,
                ),
            ]
        )
        assert weights["a"] == 1.0
        assert weights["b"] == pytest.approx(1.0 / 3.0)

    def test_empty_follows_return_empty(self) -> None:
        """No follows → no weights (the no-entity baseline path)."""
        assert normalize_entity_follow_weights([]) == {}


class TestAssignCategory:
    """Single best-fit classification — lowest match_depth wins (Rule 9).

    WHY: clean 30-slot accounting requires EXACTLY one category per story. The
    lowest-match_depth tag is the most specific hit, so its category is the truest
    fit — a story leaf-tagged in sport but grandparent-tagged in world must land in
    sport, not world. If the rule used the wrong extreme (highest depth), the test
    flips and fails.
    """

    def test_lowest_depth_tag_decides_category(self) -> None:
        """A leaf (depth 0) sport tag beats a grandparent (depth 2) world tag."""
        nodes = {
            "int-cricket": InterestNode(
                interest_id="int-cricket",
                parent_interest_id=None,
                interest_slug="sport.cricket.india",
                interest_label="India",
            ),
            "int-world": InterestNode(
                interest_id="int-world",
                parent_interest_id=None,
                interest_slug="world",
                interest_label="World",
            ),
        }
        tags_by_story = {
            "s1": {"int-cricket": 0, "int-world": 2},  # leaf sport vs grandparent world
        }
        assert assign_category("s1", tags_by_story, nodes) == "sport"

    def test_unknown_root_slug_falls_back_to_arts(self) -> None:
        """A slug whose root is not mapped falls back to the arts catch-all."""
        nodes = {
            "int-x": InterestNode(
                interest_id="int-x",
                parent_interest_id=None,
                interest_slug="obscure.thing",
                interest_label="Obscure",
            )
        }
        assert assign_category("s1", {"s1": {"int-x": 0}}, nodes) == "arts"

    def test_no_tags_falls_back_to_arts_not_crash(self) -> None:
        """Edge: an untagged story classifies to the default, never raising."""
        assert assign_category("missing", {}, {}) == "arts"


class TestScoreAndClassifyReturnsAllTenKeys:
    """The SP3 handoff contract — all 10 keys, source buckets empty (no breaking)."""

    def test_returns_all_ten_category_keys(self) -> None:
        """WHY: SP3 reads every budgeted category; a missing key would KeyError the
        allocator. The SP3 taxonomy unification is 10 keys (8 topic roots + 2
        source axes); the source categories must be present-but-empty here."""
        user = [
            UserProfileInterest(
                profile_interest_id=_SEMIS_INTEREST_ID, profile_weight=1.0
            )
        ]
        nodes = {_SEMIS_INTEREST_ID: _SEMIS_NODE}
        story = _story("s", 6, title="Chip demand surges")
        buckets = score_and_classify_for_user(
            profile_interests=user,
            followed_entities=[],
            stories=[story],
            story_interest_tags=[_semis_tag("s")],
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        assert set(buckets.keys()) == {
            "ai",
            "geopolitics",
            "business",
            "environment",
            "politics",
            "tech",
            "sport",
            "arts",
            "youtube",
            "x",
        }
        assert "breaking" not in buckets
        # The story classified into business; source axes are empty.
        assert [c.story_id for c in buckets["business"]] == ["s"]
        assert buckets["youtube"] == []
        assert buckets["x"] == []


class _FakeQuery:
    """Chainable Supabase query stub (mirrors test_daily_batch): builders return
    self; execute returns the seeded rows and bumps an optional call counter."""

    def __init__(self, data: list[dict], on_execute=None) -> None:
        self._data = data
        self._on_execute = on_execute

    def select(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def lt(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        if self._on_execute is not None:
            self._on_execute()
        return SimpleNamespace(data=self._data)


class TestLoaderHydratesEntitiesAndAllocation:
    """DB hydration against a MOCKED client (CLAUDE.md boundary mandate, Rule 9).

    WHY: the loader is the only place the custom>more>seed weighting is encoded
    (the DB stores 1.0 for every source). If the loader stopped applying
    FOLLOW_SOURCE_WEIGHT, a custom follow would normalize identically to a seed
    follow and the custom>seed DoD would silently break upstream. These tests pin
    the join shape, the source weighting, and the one-query batching.
    """

    def test_hydrates_followed_entities_with_source_weighting(self) -> None:
        """user_entity_follows ⋈ entities → FollowedEntity, custom weighted > seed."""
        profile_rows = [
            {
                "profile_user_id": "u1",
                "profile_interest_id": "int-a",
                "profile_weight": 1.0,
                "profile_is_strict": False,
            }
        ]
        follow_rows = [
            {
                "follow_user_id": "u1",
                "entity_id": "tech/.../nvidia",
                "follow_source": "custom",
                "follow_weight": 1.0,  # DB stores 1.0 for ALL sources
                "follow_path": ["tech", "semis", "nvidia"],
                "entities": {
                    "entity_label": "Nvidia",
                    "entity_ticker": "NVDA",
                    "entity_kind": "company",
                },
            },
            {
                "follow_user_id": "u1",
                "entity_id": "sport/.../arsenal",
                "follow_source": "seed",
                "follow_weight": 1.0,
                "follow_path": ["sport", "soccer", "arsenal"],
                "entities": {
                    "entity_label": "Arsenal",
                    "entity_ticker": None,
                    "entity_kind": "team",
                },
            },
        ]
        follow_query_count: list[int] = []

        class _Client:
            def table(self, name: str):
                if name == "user_interest_profile":
                    return _FakeQuery(profile_rows)
                if name == "user_entity_follows":
                    return _FakeQuery(
                        follow_rows,
                        on_execute=lambda: follow_query_count.append(1),
                    )
                return _FakeQuery([])

        inputs = daily_batch.load_active_user_inputs(_Client(), date(2026, 6, 1))
        # ONE query for all users' follows (no N+1).
        assert len(follow_query_count) == 1
        entities = {e.entity_id: e for e in inputs[0].followed_entities}
        # custom (×3) outweighs seed (×1) after the loader applies FOLLOW_SOURCE_WEIGHT.
        assert entities["tech/.../nvidia"].follow_weight == 3.0
        assert entities["sport/.../arsenal"].follow_weight == 1.0
        # Join carried the identity columns through.
        nvidia = entities["tech/.../nvidia"]
        assert nvidia.entity_label == "Nvidia"
        assert nvidia.entity_ticker == "NVDA"
        assert nvidia.entity_kind == "company"
        # A team with no ticker hydrates ticker=None.
        assert entities["sport/.../arsenal"].entity_ticker is None

    def test_hydrates_category_allocation_rows(self) -> None:
        """user_feed_allocation rows → CategoryAllocation, grouped per user."""
        profile_rows = [
            {
                "profile_user_id": "u1",
                "profile_interest_id": "int-a",
                "profile_weight": 1.0,
                "profile_is_strict": False,
            }
        ]
        allocation_rows = [
            {
                "follow_user_id": "u1",
                "allocation_category": "business",
                "allocation_slot_count": 5,
                "allocation_sort_order": 2,
            },
            {
                "follow_user_id": "u1",
                "allocation_category": "geopolitics",
                "allocation_slot_count": 4,
                "allocation_sort_order": 0,
            },
        ]

        class _Client:
            def table(self, name: str):
                if name == "user_interest_profile":
                    return _FakeQuery(profile_rows)
                if name == "user_feed_allocation":
                    return _FakeQuery(allocation_rows)
                return _FakeQuery([])

        inputs = daily_batch.load_active_user_inputs(_Client(), date(2026, 6, 1))
        allocs = {a.allocation_category: a for a in inputs[0].category_allocation}
        assert allocs["business"].allocation_slot_count == 5
        assert allocs["business"].allocation_sort_order == 2
        assert allocs["geopolitics"].allocation_slot_count == 4

    def test_orphan_follow_without_joined_entity_is_skipped(self) -> None:
        """A follow whose joined entities row is missing is skipped (no labelless entity)."""
        profile_rows = [
            {
                "profile_user_id": "u1",
                "profile_interest_id": "int-a",
                "profile_weight": 1.0,
                "profile_is_strict": False,
            }
        ]
        follow_rows = [
            {
                "follow_user_id": "u1",
                "entity_id": "orphan",
                "follow_source": "seed",
                "follow_weight": 1.0,
                "follow_path": [],
                "entities": None,  # orphaned FK / no embed
            }
        ]

        class _Client:
            def table(self, name: str):
                if name == "user_interest_profile":
                    return _FakeQuery(profile_rows)
                if name == "user_entity_follows":
                    return _FakeQuery(follow_rows)
                return _FakeQuery([])

        inputs = daily_batch.load_active_user_inputs(_Client(), date(2026, 6, 1))
        assert inputs[0].followed_entities == []


class TestImportanceWeightFlip:
    """SP4 DoD (Rule 9) — the big-story-beats-minor reordering at the raised β.

    The diagnosed bug (PRD Problem #4 / US6, US7): at the OLD β=0.3 a well-matched MINOR
    story outranks a genuinely BIG one. The fix raises β so the big story's (now
    authority-weighted E1) Importance lifts it back above the minor. This test PINS the
    flip so β cannot silently drift back below the value that fixes the bug.

    The fixture pair (scored via ``compute_story_score`` with an injected E1
    ``cluster_importance``):
      - BIG: a genuinely big story — high E1 importance (0.95), but only a MODEST
        affinity×depth (parent match on a half-affinity interest → 0.5×0.6 = 0.30).
      - MINOR: a well-matched minor story — high affinity×depth (leaf match on the top
        interest → 1.0×1.0 = 1.0), but low E1 importance (0.10).
    Both equally fresh, so freshness is neutral to the ordering.

    Closed form (freshness=1.0 both):
        big(β)   = 0.5·0.30 + β·0.95 + 0.2 = 0.35 + 0.95β
        minor(β) = 0.5·1.00 + β·0.10 + 0.2 = 0.70 + 0.10β
        big > minor  ⇔  0.85β > 0.35  ⇔  β > ~0.4118
    So the ordering flips between the old 0.3 (minor wins) and the new 0.45 (big wins);
    any β ≤ ~0.41 (including the old 0.3) leaves the bug unfixed.
    """

    # E1 importances injected as cluster_importance (authority-weighted, normalized).
    _BIG_E1 = 0.95
    _MINOR_E1 = 0.10
    _BIG_AFFINITY, _BIG_DEPTH = 0.5, 1  # parent match (DepthMatch 0.6)
    _MINOR_AFFINITY, _MINOR_DEPTH = 1.0, 0  # leaf match (DepthMatch 1.0)

    def _score_at_beta(self, terms: tuple[float, float, float, float], beta: float) -> float:
        """Reconstruct the Score at an arbitrary β from the (score, depth, imp, fresh)."""
        _score, depth_match, importance, freshness = terms
        affinity = self._affinity  # set by the caller for the term being rebuilt
        return (
            (affinity * depth_match) * AFFINITY_WEIGHT
            + importance * beta
            + freshness * FRESHNESS_WEIGHT
        )

    def _terms(self, affinity: float, depth: int, e1: float):
        story = _story("x", outlet_count=1, published=_NOW)
        self._affinity = affinity
        return affinity, compute_story_score(
            affinity=affinity, match_depth=depth, story=story, now_utc=_NOW,
            cluster_importance=e1,
        )

    def test_minor_wins_at_old_beta_big_wins_at_new_beta(self) -> None:
        """The known minor-vs-major ordering flips between β=0.3 (old) and β=0.45 (new)."""
        big_aff, big_terms = self._terms(self._BIG_AFFINITY, self._BIG_DEPTH, self._BIG_E1)
        minor_aff, minor_terms = self._terms(
            self._MINOR_AFFINITY, self._MINOR_DEPTH, self._MINOR_E1
        )

        # OLD β=0.3: the well-matched MINOR story wins — reproduces the diagnosed bug.
        self._affinity = big_aff
        big_old = self._score_at_beta(big_terms, 0.3)
        self._affinity = minor_aff
        minor_old = self._score_at_beta(minor_terms, 0.3)
        assert minor_old > big_old, "at old β=0.3 the minor story should still win (bug)"

        # NEW β=0.45: the genuinely BIG story wins — the bug is fixed.
        self._affinity = big_aff
        big_new = self._score_at_beta(big_terms, 0.45)
        self._affinity = minor_aff
        minor_new = self._score_at_beta(minor_terms, 0.45)
        assert big_new > minor_new, "at new β=0.45 the big story should win (fix)"

    def test_live_importance_weight_satisfies_the_flip(self) -> None:
        """The shipped ``IMPORTANCE_WEIGHT`` is on the big-story-wins side of the flip.

        Pins the live constant: if someone drifts β back to ≤~0.41 (e.g. the old 0.3),
        this fails — the big story would no longer outrank the minor one.
        """
        assert IMPORTANCE_WEIGHT >= 0.45, (
            f"IMPORTANCE_WEIGHT={IMPORTANCE_WEIGHT} regressed below the pinned 0.45 — "
            "the big-story-beats-minor flip would break"
        )
        big_aff, big_terms = self._terms(self._BIG_AFFINITY, self._BIG_DEPTH, self._BIG_E1)
        minor_aff, minor_terms = self._terms(
            self._MINOR_AFFINITY, self._MINOR_DEPTH, self._MINOR_E1
        )
        self._affinity = big_aff
        big = self._score_at_beta(big_terms, IMPORTANCE_WEIGHT)
        self._affinity = minor_aff
        minor = self._score_at_beta(minor_terms, IMPORTANCE_WEIGHT)
        assert big > minor

    def test_e1_cluster_importance_overrides_raw_outlet_count(self) -> None:
        """When ``cluster_importance`` is supplied it REPLACES the raw outlet-count term.

        WHY: the raised β must lift the authority-weighted E1 importance, not the raw,
        gameable ``min(1, outlet_count/12)``. A 1-outlet story with a high E1 importance
        must score its E1 value, not the ~0.083 its outlet count would give.
        """
        story = _story("x", outlet_count=1, published=_NOW)
        _s, _dm, raw_importance, _f = compute_story_score(1.0, 0, story, _NOW)
        _s2, _dm2, e1_importance, _f2 = compute_story_score(
            1.0, 0, story, _NOW, cluster_importance=0.9
        )
        assert raw_importance < 0.1  # 1/12
        assert e1_importance == pytest.approx(0.9)

    def test_unclustered_story_unchanged_falls_back_to_raw(self) -> None:
        """An un-clustered story (no E1) keeps the raw outlet-count importance (Rule 3)."""
        story = _story("x", outlet_count=6, published=_NOW)
        _s, _dm, importance, _f = compute_story_score(1.0, 0, story, _NOW)
        assert importance == pytest.approx(0.5)  # 6/12, the preserved un-clustered path
