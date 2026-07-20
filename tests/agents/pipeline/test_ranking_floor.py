"""Unit tests for the ranking floor (feed-quality reset WS-B, PRD RC2 second half).

DoD (Rule 9 — each test fails if the weighting reverts to admitting affinity alone):
  AC1. A candidate with MAXIMAL affinity but FLOOR importance does NOT clear the gate.
  AC2. Inverse: a genuinely important story with only MODEST affinity still admits.
  AC3. Within-category importance normalization makes importance COMPARABLE across
       categories of very different pool sizes (a 3-story vs a 60-story category).
  AC4. A single-candidate category → normalization neither divides by zero nor
       auto-inflates the lone story to top importance.

These encode WHY the floor exists: the Score is affinity-dominant by design (α=0.5), so a
top-weight interest match reaches (Affinity 1.0 × DepthMatch 1.0)·0.5 = 0.5 — past the 0.20
threshold — on affinity alone, with ZERO importance corroboration. That is exactly how
single-outlet local notices reached the founder's AI slots (RC2). The floor requires
importance AS WELL, so affinity alone can no longer fill a slot. All inputs are pure data —
no DB, no clock, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.stages.ranking import (
    DEFAULT_SCORE_THRESHOLD,
    MIN_ADMISSION_IMPORTANCE,
    ScoredCandidate,
    UserProfileInterest,
    candidate_clears_ranking_floor,
    generate_fallback_candidates,
    normalize_candidate_importance_within_category,
    score_and_classify_for_user,
)

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _story(
    story_id: str, outlet_count: int, published: datetime = _NOW
) -> CanonicalStory:
    """Build a minimal canonical story with a given importance (outlet count)/freshness."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Headline {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain="example.com",
        covering_outlets=[f"o{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


def _candidate(
    story_id: str, category: str, importance: float, score: float = 0.9
) -> ScoredCandidate:
    """A minimal scored candidate carrying a category + importance for normalization tests."""
    return ScoredCandidate(
        story_id=story_id,
        matched_interest_id="int-x",
        score=score,
        affinity=1.0,
        depth_match=1.0,
        importance=importance,
        freshness=1.0,
        feed_category=category,
    )


class TestRankingFloorPredicate:
    """AC1 + AC2 — the pure admission predicate: importance is REQUIRED, not affinity alone."""

    def test_maximal_affinity_floor_importance_does_not_clear(self) -> None:
        """AC1: a maximal-affinity Score (0.5, past T=0.20) with floor importance is REJECTED.

        This is the RC2 leak in miniature — affinity 1.0 × depth 1.0 × α 0.5 = 0.5 clears
        the 0.20 threshold with NO importance. The floor must veto it.
        """
        maximal_affinity_score = 0.5  # (Affinity 1.0 × DepthMatch 1.0) · α 0.5
        assert (
            maximal_affinity_score >= DEFAULT_SCORE_THRESHOLD
        )  # clears T on affinity alone
        below_floor = MIN_ADMISSION_IMPORTANCE - 0.01
        assert (
            candidate_clears_ranking_floor(maximal_affinity_score, below_floor) is False
        ), (
            "affinity alone (Score 0.5) with floor importance must NOT clear the ranking floor"
        )

    def test_floor_is_a_real_flip_not_decoration(self) -> None:
        """Rule 9: crossing the importance floor flips admission, holding Score fixed.

        Same maximal-affinity Score both times; only the importance moves from just-below
        to just-at the floor. If the floor regressed to accepting affinity alone, the
        below-floor case would wrongly clear and this fails.
        """
        score = 0.5
        assert (
            candidate_clears_ranking_floor(score, MIN_ADMISSION_IMPORTANCE - 1e-6)
            is False
        )
        assert candidate_clears_ranking_floor(score, MIN_ADMISSION_IMPORTANCE) is True

    def test_important_story_with_modest_affinity_still_admits(self) -> None:
        """AC2: a genuinely important story (high importance) with only modest affinity admits.

        Modest affinity×depth contributes little to the Score, but a high importance term
        both lifts the Score past T and clears the importance floor — so real news is never
        starved by the floor that stops affinity-only junk.
        """
        # Modest affinity (0.2) via a parent match (depth_match 0.6) + strong importance (0.9).
        modest_affinity_term = (0.2 * 0.6) * 0.5
        important_score = modest_affinity_term + 0.9 * 0.45 + 1.0 * 0.2
        assert important_score >= DEFAULT_SCORE_THRESHOLD
        assert candidate_clears_ranking_floor(important_score, 0.9) is True

    def test_below_threshold_never_clears_regardless_of_importance(self) -> None:
        """A candidate below the Score threshold is out even with maximal importance."""
        assert (
            candidate_clears_ranking_floor(DEFAULT_SCORE_THRESHOLD - 0.01, 1.0) is False
        )


class TestWithinCategoryImportanceNormalization:
    """AC3 + AC4 — importance made comparable across categories, degenerate cases handled."""

    def test_normalization_is_comparable_across_pool_sizes(self) -> None:
        """AC3: a 3-story category and a 60-story category normalize to the SAME 0–1 scale.

        Category "small" (3 stories) has raw importances on a NARROW absolute band
        (0.10–0.30); category "big" (60 stories) spans 0.10–0.90. A single fixed floor on
        RAW importance would treat "small" harshly (its top 0.30 looks weak next to big's
        0.90). After within-category normalization each category's leader reaches 1.0 and
        its tail reaches 0.0 INDEPENDENTLY — so importance is comparable and pool-size does
        not decide admission. Encodes WHY normalization matters (Rule 9).
        """
        small = [
            _candidate("small-lo", "sport", 0.10),
            _candidate("small-mid", "sport", 0.20),
            _candidate("small-hi", "sport", 0.30),
        ]
        big = [
            _candidate(f"big-{i}", "business", 0.10 + (0.80 * i / 59))
            for i in range(60)
        ]
        normalized = normalize_candidate_importance_within_category(
            {"sport": small, "business": big}
        )

        # Each category's leader → ~1.0, tail → ~0.0, INDEPENDENTLY of absolute scale/size.
        assert normalized["small-hi"] == 1.0
        assert normalized["big-59"] == 1.0
        assert normalized["small-lo"] == 0.0
        assert normalized["big-0"] == 0.0
        # The small category's raw-0.30 leader is NOT suppressed below the big category's
        # raw-0.30 member just because big's pool is larger / spans higher.
        assert normalized["small-hi"] > normalized["big-0"]
        # A mid-rank story in each pool lands mid-scale (comparable band, not pool-decided).
        assert 0.4 < normalized["small-mid"] < 0.6

    def test_single_candidate_category_is_neutral_not_inflated(self) -> None:
        """AC4: a lone story in its category gets a NEUTRAL importance, not auto-1.0.

        No divide-by-zero (min == max), and no spurious inflation to top importance — a
        one-story category must not claim "maximally important". A documented neutral mid.
        """
        lone = [_candidate("lone", "arts", 0.42)]
        normalized = normalize_candidate_importance_within_category({"arts": lone})
        assert normalized["lone"] != 1.0, (
            "lone story must NOT be auto-inflated to top importance"
        )
        assert 0.0 < normalized["lone"] < 1.0, (
            "lone story gets a neutral mid, not 0.0 or 1.0"
        )

    def test_empty_buckets_do_not_crash(self) -> None:
        """Edge: empty/absent categories normalize to an empty map, never an exception."""
        assert normalize_candidate_importance_within_category({}) == {}
        assert normalize_candidate_importance_within_category({"sport": []}) == {}


class TestRankingFloorWiredIntoScoring:
    """The floor is CALLED in the live scoring path — not a built-but-uncalled function (RC2)."""

    def _one_category_pool(self):
        """A pool where an affinity-junk story OUTSCORES a real-news story in one category.

        Both classify into 'sport'. The junk story is leaf-tagged to the user's TOP interest
        (affinity 1.0) but is a single outlet (importance ≈ 0.083); the real story matches a
        low-affinity interest at parent depth but is broadly covered (importance 1.0). The
        affinity-dominant Score puts the JUNK story slightly ahead — so only the importance
        floor can demote it below the real news.
        """
        profile = [
            UserProfileInterest(profile_interest_id="int-arsenal", profile_weight=5.0),
            UserProfileInterest(profile_interest_id="int-cricket", profile_weight=1.0),
        ]
        junk = _story("affinity-junk", outlet_count=1)  # importance 1/12 ≈ 0.083
        real = _story("real-news", outlet_count=12)  # importance 1.0
        tags = [
            StoryInterestTag(
                story_interest_story_id="affinity-junk",
                story_interest_interest_id="int-arsenal",
                story_interest_match_depth=0,  # leaf on the TOP interest → affinity 1.0
            ),
            StoryInterestTag(
                story_interest_story_id="real-news",
                story_interest_interest_id="int-cricket",
                story_interest_match_depth=1,  # parent depth on a low-affinity interest
            ),
        ]
        nodes = {
            "int-arsenal": InterestNode(
                interest_id="int-arsenal",
                parent_interest_id=None,
                interest_slug="sport.soccer.arsenal",
                interest_label="Arsenal",
                depth_level=2,
            ),
            "int-cricket": InterestNode(
                interest_id="int-cricket",
                parent_interest_id=None,
                interest_slug="sport.cricket",
                interest_label="Cricket",
                depth_level=1,
            ),
        }
        return profile, [junk, real], tags, nodes

    def test_affinity_junk_is_demoted_below_real_news(self) -> None:
        """AC1 (wired): the affinity-only story, though higher-scoring, ranks BELOW real news.

        Both clear the affinity-dominant Score threshold and the junk story even outscores
        the real one, so score-only admission would place the junk FIRST — the RC2 harm
        ("junk fills the very slots the user cares most about"). After within-category
        normalization the junk story's importance is the category tail (0.0) and the real
        story's is the leader (1.0); the floor demotes the junk below the real news, so real
        news wins the top (contested) slot while the junk merely fills a trailing one.
        """
        profile, stories, tags, nodes = self._one_category_pool()
        buckets = score_and_classify_for_user(
            profile_interests=profile,
            followed_entities=[],
            stories=stories,
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        order = [c.story_id for c in buckets["sport"]]
        assert order == ["real-news", "affinity-junk"], (
            "the importance-bearing story must win the top slot; affinity-only junk demoted"
        )

    def test_disabling_the_floor_lets_affinity_junk_outrank_real_news(self) -> None:
        """Rule 9: with the floor disabled (min_admission_importance=0) the junk wins the top.

        Proves the demotion is caused by the floor, not by classification/scoring — with the
        floor off the ranking reverts to score-only, and the higher-scoring affinity-only
        junk story takes the top slot ahead of the real news. This is exactly the weighting
        whose reversion the acceptance criteria guard against.
        """
        profile, stories, tags, nodes = self._one_category_pool()
        buckets = score_and_classify_for_user(
            profile_interests=profile,
            followed_entities=[],
            stories=stories,
            story_interest_tags=tags,
            interest_nodes=nodes,
            now_utc=_NOW,
            min_admission_importance=0.0,
        )
        order = [c.story_id for c in buckets["sport"]]
        assert order == ["affinity-junk", "real-news"], (
            "with the floor off, higher-scoring affinity-only junk takes the top slot (bug)"
        )

    def test_climb_does_not_stop_on_a_below_floor_leaf_story(self) -> None:
        """The fallback climb-stop requires importance too: an affinity-only leaf does not resolve.

        A below-floor single-outlet leaf story clears T on affinity, but must NOT stop the
        climb — the generator broadens to the parent, which carries a genuinely important
        (multi-outlet) story. Encodes that 'good enough to stop broadening' now needs
        importance, not affinity alone.
        """
        leaf_junk = _story("leaf-junk", outlet_count=1)  # importance 0.083, below floor
        parent_real = _story("parent-real", outlet_count=12)  # importance 1.0
        tags_by_story = {
            "leaf-junk": {"int-arsenal": 0},
            "parent-real": {"int-soccer": 0},
        }
        nodes = {
            "int-arsenal": InterestNode(
                interest_id="int-arsenal",
                parent_interest_id="int-soccer",
                interest_slug="sport.soccer.arsenal",
                interest_label="Arsenal",
                depth_level=2,
            ),
            "int-soccer": InterestNode(
                interest_id="int-soccer",
                parent_interest_id=None,
                interest_slug="sport.soccer",
                interest_label="Soccer",
                depth_level=1,
            ),
        }
        followed = UserProfileInterest(
            profile_interest_id="int-arsenal", profile_weight=1.0
        )
        candidates = generate_fallback_candidates(
            followed_interest=followed,
            affinity=1.0,
            stories=[leaf_junk, parent_real],
            tags_by_story=tags_by_story,
            interest_nodes=nodes,
            now_utc=_NOW,
        )
        # The climb broadened past the below-floor leaf to the important parent story.
        assert candidates[0].story_id == "parent-real"
        assert candidates[0].fallback_depth == 1
