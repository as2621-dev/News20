"""Issue #70 regression — replay the 2026-07-25 scrambled shortlist rows.

WHY these tests exist (Rule 9): the 2026-07-25 SHORTLIST_ONLY run produced 55 rows
whose ``shortlist_category`` chips were scrambled (cricket→tech, Bollywood→tech,
Netflix horror→geopolitics, Premier League→environment) and whose
``shortlist_matched_interest_slugs`` carried phantom segment ROOTS the founder does
not follow (box-office story → [business, environment, politics, tech]). Audit:
``docs/ops/m1-live-audit-2026-07-25.md``; artifact:
``.agents/shortlists/2026-07-25-shortlist.json``.

Root cause: M2 SP3 emitted the THEME-derived category as a depth-0 ROOT tag in the
``story_interests`` stream and shifted every keyword tag to depth >= 1. Two lies
followed: (a) the shortlist's "depth 0 == leaf-matched interest" read returned theme
roots nobody follows, and (b) the noisy theme whitelist (health→tech pin,
ARMEDCONFLICT, disaster codes) outranked the two-key-verified fetching interest in
``assign_category`` — and reconcile's merge pins froze the wrong verdict.

The fix keeps the channels separate: ``story_interests`` holds ONLY verified interest
matches at natural depth; the theme category rides beside as an explicit
tiebreak/fallback (``theme_category_by_story``). These fixtures replay every scrambled
row through the REAL chain (``merge_story_tags`` → reconcile collapse/remap →
``build_produce_shortlist``) — no mocks on the logic under test, zero network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from structlog.testing import capture_logs

from agents.ingestion.ancestor_tagging import merge_story_tags
from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.clustering.reconcile import _collapse_stories, _remap_tags
from agents.pipeline.shortlist import build_produce_shortlist

_NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)

# The founder's followed set on 2026-07-25 (26 sub-niche interests — the invariant
# universe; from the run session's interest-query snapshot).
_FOLLOWED_SLUGS = [
    "sport",
    "crypto",
    "sport.soccer",
    "sport.cricket.india",
    "business.gdp-growth",
    "business.jobs",
    "business.interest-rates-fed",
    "business.inflation",
    "geopolitics.natural-gas-pipelines",
    "tech.launches-missions",
    "business.recession-risk",
    "entertainment",
    "ai.alignment-research",
    "sport.cricket",
    "sport.fifa",
    "sport.fifa-world-cup",
    "ai.interpretability",
    "geopolitics.oil-opec",
    "geopolitics.critical-minerals",
    "ai.data-center-buildout",
    "ai.compute-energy-demand",
    "ai.evals-red-teaming",
    "ai.catastrophic-risk",
    "arts.box-office",
    "arts.bollywood",
    "arts.ai-workforce",
]

_ROOT_SLUGS = [
    "ai",
    "geopolitics",
    "business",
    "environment",
    "politics",
    "tech",
    "sport",
    "arts",
]


def _interest_id(slug: str) -> str:
    return f"int-{slug}"


def _build_interest_nodes() -> dict[str, InterestNode]:
    """The taxonomy: 8 roots + the followed leaves with real parent chains."""
    nodes: dict[str, InterestNode] = {}
    for root_slug in _ROOT_SLUGS:
        nodes[_interest_id(root_slug)] = InterestNode(
            interest_id=_interest_id(root_slug),
            parent_interest_id=None,
            interest_slug=root_slug,
            interest_label=root_slug,
            depth_level=0,
            interest_search_query=f"{root_slug} news",
        )
    for slug in _FOLLOWED_SLUGS:
        if slug in _ROOT_SLUGS or slug in ("crypto", "entertainment"):
            continue
        parts = slug.split(".")
        parent_slug = ".".join(parts[:-1])
        if parent_slug and parent_slug not in _ROOT_SLUGS and _interest_id(parent_slug) not in nodes:
            # Intermediate node (e.g. sport.cricket under sport) added by its own
            # entry in _FOLLOWED_SLUGS or minted here for the chain.
            nodes[_interest_id(parent_slug)] = InterestNode(
                interest_id=_interest_id(parent_slug),
                parent_interest_id=_interest_id(parent_slug.split(".")[0]),
                interest_slug=parent_slug,
                interest_label=parent_slug,
                depth_level=1,
                interest_search_query=f"{parent_slug} news",
            )
        nodes[_interest_id(slug)] = InterestNode(
            interest_id=_interest_id(slug),
            parent_interest_id=_interest_id(parent_slug) if parent_slug else None,
            interest_slug=slug,
            interest_label=slug,
            depth_level=len(parts) - 1,
            interest_search_query=f"{slug} news",
        )
    # Followed root-level standalone interests (no dot, not one of the 8 roots).
    for slug in ("crypto", "entertainment"):
        nodes[_interest_id(slug)] = InterestNode(
            interest_id=_interest_id(slug),
            parent_interest_id=None,
            interest_slug=slug,
            interest_label=slug,
            depth_level=0,
            interest_search_query=f"{slug} news",
        )
    return nodes


_FOLLOWED_IDS = frozenset(_interest_id(slug) for slug in _FOLLOWED_SLUGS)


def _story(
    story_id: str,
    title: str,
    matched_slugs: list[str],
    themes: list[str],
) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title,
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"example.com/{story_id}",
        canonical_primary_outlet_name="Example Outlet",
        canonical_primary_outlet_domain="example.com",
        canonical_published_utc=_NOW,
        covering_outlets=["example.com"],
        story_outlet_count=1,
        canonical_matched_interest_ids=[_interest_id(s) for s in matched_slugs],
        canonical_themes=themes,
        member_candidate_ids=[f"https://example.com/{story_id}"],
        canonical_representative_external_id=f"https://example.com/{story_id}",
    )


def _tags_for(stories: list[CanonicalStory], nodes: dict[str, InterestNode]) -> list[StoryInterestTag]:
    """The REAL ingestion tag build: keyword ancestor tags per story."""
    tags: list[StoryInterestTag] = []
    for story in stories:
        tags.extend(
            merge_story_tags(
                story.canonical_story_id,
                story.canonical_matched_interest_ids,
                nodes,
            )
        )
    return tags


def _shortlist_single(
    story: CanonicalStory, nodes: dict[str, InterestNode]
) -> tuple[str, list[str]]:
    """Run one un-merged story through the real shortlist path."""
    entries = build_produce_shortlist(
        [story],
        _tags_for([story], nodes),
        nodes,
        None,
        followed_interest_ids=_FOLLOWED_IDS,
    )
    assert len(entries) == 1
    return entries[0].shortlist_category, entries[0].shortlist_matched_interest_slugs


def _shortlist_merged(
    members: list[CanonicalStory], nodes: dict[str, InterestNode]
) -> tuple[str, list[str]]:
    """Collapse same-event members through the REAL reconcile pieces, then shortlist.

    The clusterer's embedding step is the (paid) boundary — the merge decision is
    fabricated (all members share one id), but the collapse, tag remap and shortlist
    logic under test are the real functions.
    """
    shared_id = members[0].canonical_story_id
    shared_id_by_index = {index: shared_id for index in range(len(members))}
    tags = _tags_for(members, nodes)
    collapsed = _collapse_stories(members, shared_id_by_index)
    remapped = _remap_tags(tags, stories=members, shared_id_by_index=shared_id_by_index)
    entries = build_produce_shortlist(
        collapsed, remapped, nodes, None, followed_interest_ids=_FOLLOWED_IDS
    )
    assert len(entries) == 1
    return entries[0].shortlist_category, entries[0].shortlist_matched_interest_slugs


@pytest.fixture(scope="module")
def nodes() -> dict[str, InterestNode]:
    return _build_interest_nodes()


class TestScrambledChipRowsResolveCorrectly:
    """Each audited BAD row, replayed: the verified fetching interest owns the chip."""

    def test_row_27_cricket_injury_chips_sport_not_tech(self, nodes) -> None:
        """Audit row 27: 'Pakistan opener ruled out…' chipped tech via the
        MEDICAL/GENERAL_HEALTH→tech theme pin. The sport.cricket match must win."""
        category, slugs = _shortlist_single(
            _story(
                "cand-4977846fbb54",
                "Pakistan opener Abdullah Fazal ruled out of West Indies Test series",
                ["sport.cricket"],
                ["MEDICAL", "GENERAL_HEALTH", "WB_621_HEALTH_NUTRITION_AND_POPULATION"],
            ),
            nodes,
        )
        assert category == "sport"
        assert slugs == ["sport.cricket"]

    def test_row_36_bollywood_spotting_chips_arts_not_tech(self, nodes) -> None:
        category, slugs = _shortlist_single(
            _story(
                "cand-cd171e07c278",
                "Abhishek Bachchan and Aishwarya Rai spotted clicking pictures",
                ["arts.bollywood"],
                ["WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES"],
            ),
            nodes,
        )
        assert category == "arts"
        assert slugs == ["arts.bollywood"]

    def test_row_43_bieber_super_bowl_chips_arts_not_tech(self, nodes) -> None:
        category, slugs = _shortlist_single(
            _story(
                "cand-e66a83d671cb",
                "Justin Bieber Reportedly Lands Super Bowl 2027 Role",
                ["entertainment"],
                ["WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES", "SOC_INNOVATION"],
            ),
            nodes,
        )
        assert category == "arts"
        assert slugs == ["entertainment"]

    def test_row_47_ariana_foundation_chips_arts_not_tech(self, nodes) -> None:
        category, slugs = _shortlist_single(
            _story(
                "cand-7be016c6ff86",
                "Celebrity giving is evolving: Ariana Grande's foundation shows how",
                ["entertainment"],
                ["GENERAL_HEALTH", "WB_621_HEALTH_NUTRITION_AND_POPULATION"],
            ),
            nodes,
        )
        assert category == "arts"
        assert slugs == ["entertainment"]

    def test_row_48_netflix_horror_chips_arts_not_geopolitics(self, nodes) -> None:
        category, slugs = _shortlist_single(
            _story(
                "cand-7c9bae3dbae3",
                "3 Courteney Cox Horror Movies Including Scream Join Netflix",
                ["entertainment"],
                ["WB_2432_FRAGILITY_CONFLICT_AND_VIOLENCE"],
            ),
            nodes,
        )
        assert category == "arts"
        assert slugs == ["entertainment"]

    def test_row_49_oasis_documentary_chips_arts_not_geopolitics(self, nodes) -> None:
        category, slugs = _shortlist_single(
            _story(
                "cand-a4c30464e797",
                "Steven Knight's Oasis documentary to premiere at Venice Film Festival",
                ["entertainment"],
                ["WB_2432_FRAGILITY_CONFLICT_AND_VIOLENCE"],
            ),
            nodes,
        )
        assert category == "arts"
        assert slugs == ["entertainment"]

    def test_row_34_medieval_combat_chips_sport_not_geopolitics(self, nodes) -> None:
        """ARMEDCONFLICT/MILITARY themes on a combat-sport story must not beat the
        followed root 'sport' interest that fetched it."""
        category, slugs = _shortlist_single(
            _story(
                "cand-c01f37f55a84",
                "With swords, axes and shields, fighters go head-to-head",
                ["sport"],
                ["ARMEDCONFLICT", "MILITARY"],
            ),
            nodes,
        )
        assert category == "sport"
        assert slugs == ["sport"]

    def test_row_19_maeda_merge_chips_sport_not_environment(self, nodes) -> None:
        """Audit row 19: a Premier League transfer chipped environment with phantom
        [environment, politics] slugs — theme-only merge members polluted the union.
        Post-fix the sport.soccer member owns the merged story."""
        category, slugs = _shortlist_merged(
            [
                _story(
                    "cand-00ad62fd66f9",
                    "Maeda: Happy to Fulfil Premier League Dream With Town",
                    ["sport.soccer"],
                    [],
                ),
                _story(
                    "cand-merge-19b",
                    "Maeda happy to fulfil Premier League dream with Ipswich",
                    [],
                    ["UNGP_FORESTS_RIVERS_OCEANS"],
                ),
                _story(
                    "cand-merge-19c",
                    "Maeda fulfils Premier League dream at Ipswich Town",
                    [],
                    ["USPEC_POLICY1"],
                ),
            ],
            nodes,
        )
        assert category == "sport"
        assert slugs == ["sport.soccer"]

    def test_row_4_aff_cup_merge_ties_break_to_sport_by_theme(self, nodes) -> None:
        """Audit row 4: football-tournament tickets chipped arts. Post-fix the
        equal-depth arts.box-office vs sport.fifa tie breaks on the merged story's
        SPORT themes (aboutness as tiebreak, never as override)."""
        category, slugs = _shortlist_merged(
            [
                _story(
                    "cand-53ff796b23ed",
                    "2026 AFF Cup Garuda Home Matches: Ticket Sales, Price List",
                    ["arts.box-office"],
                    ["SPORT"],
                ),
                _story(
                    "cand-merge-4b",
                    "AFF Cup 2026 Garuda home matches ticket sales open",
                    ["sport.fifa"],
                    ["SPORT", "WB_1953_SPORTS"],
                ),
            ],
            nodes,
        )
        assert category == "sport"
        assert slugs == ["arts.box-office", "sport.fifa"]

    def test_row_5_mls_tickets_tie_breaks_to_sport_by_theme(self, nodes) -> None:
        category, slugs = _shortlist_single(
            _story(
                "cand-e499579dcbbd",
                "MLS Teams See Ticket Sales Jump More Than 150% After World Cup",
                ["arts.box-office", "sport.fifa-world-cup"],
                ["SPORT", "WB_1953_SPORTS", "SOC_SPORTS"],
            ),
            nodes,
        )
        assert category == "sport"
        assert slugs == ["arts.box-office", "sport.fifa-world-cup"]


class TestPhantomRootRowsCarryOnlyFollowedSlugs:
    """The phantom-root rows: matched slugs must be the verified fetching leaves."""

    def test_row_2_odyssey_box_office_merge(self, nodes) -> None:
        """Audit row 2: matched [business, environment, politics, tech] — all
        phantom theme roots from merged members. Post-fix: [arts.box-office]."""
        category, slugs = _shortlist_merged(
            [
                _story(
                    "cand-314c66bcb157",
                    "'The Odyssey' Tops 'Oppenheimer' Box Office Opening With $84.5M",
                    ["arts.box-office"],
                    ["ENTERTAINMENT"],
                ),
                _story(
                    "cand-merge-2b",
                    "The Odyssey posts an $84.5M box-office opening weekend",
                    [],
                    ["EPU_ECONOMY", "USPEC_POLICY1"],
                ),
                _story(
                    "cand-merge-2c",
                    "Odyssey opening tops Oppenheimer at the weekend box office",
                    [],
                    ["UNGP_FORESTS_RIVERS_OCEANS", "WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES"],
                ),
            ],
            nodes,
        )
        assert category == "arts"
        assert slugs == ["arts.box-office"]

    def test_row_3_dhamaal_box_office_merge(self, nodes) -> None:
        """Audit row 3: matched [business, politics, tech] — phantom theme roots.
        The verified fetch is arts.box-office → arts chip (the audit judged the
        business chip MARG, 'arts more natural, cf. row 2'), single honest slug."""
        category, slugs = _shortlist_merged(
            [
                _story(
                    "cand-819b4a93aae9",
                    "'Dhamaal 4' box office collection Day 16 (Live): Ajay Devgn",
                    ["arts.box-office"],
                    ["ENTERTAINMENT"],
                ),
                _story(
                    "cand-merge-3b",
                    "Dhamaal 4 box office day 16 live collection update",
                    [],
                    ["EPU_ECONOMY", "USPEC_POLICY1", "SOC_INNOVATION"],
                ),
            ],
            nodes,
        )
        assert category == "arts"
        assert slugs == ["arts.box-office"]

    def test_row_30_starship_merge_chips_tech_not_environment(self, nodes) -> None:
        category, slugs = _shortlist_merged(
            [
                _story(
                    "cand-183c533aa7e1",
                    "Musk celebra il volo di prova di Starship mentre la Nasa valuta",
                    ["tech.launches-missions"],
                    ["MANMADE_DISASTER_IMPLIED"],
                ),
                _story(
                    "cand-merge-30b",
                    "Starship test flight celebrated by Musk as NASA evaluates",
                    [],
                    ["EPU_ECONOMY", "USPEC_POLICY1"],
                ),
            ],
            nodes,
        )
        assert category == "tech"
        assert slugs == ["tech.launches-missions"]

    def test_row_31_openai_incident_merge_chips_ai(self, nodes) -> None:
        category, slugs = _shortlist_merged(
            [
                _story(
                    "cand-c46e14fc8785",
                    "OpenAI and Hugging Face partner to address security incident",
                    ["ai.evals-red-teaming"],
                    ["TECH_ARTIFICIAL_INTELLIGENCE"],
                ),
                _story(
                    "cand-merge-31b",
                    "OpenAI, Hugging Face respond jointly to security incident",
                    [],
                    ["EPU_ECONOMY", "UNGP_FORESTS_RIVERS_OCEANS", "USPEC_POLICY1"],
                ),
            ],
            nodes,
        )
        assert category == "ai"
        assert slugs == ["ai.evals-red-teaming"]

    def test_row_37_pradhan_resignation_never_chips_tech(self, nodes) -> None:
        """Audit row 37: chipped tech (phantom). Its verified match is
        arts.bollywood (the story broke via Bollywood coverage) → arts, and the
        tech phantom must be gone. Aboutness (politics) may only ever appear as a
        tiebreak, never as an unfollowed matched slug."""
        category, slugs = _shortlist_single(
            _story(
                "cand-a00bb9c9c102",
                "Dharmendra Pradhan Resigns From Education Ministry; Bollywood reacts",
                ["arts.bollywood"],
                ["USPEC_POLICY1", "WB_696_PUBLIC_SECTOR_MANAGEMENT"],
            ),
            nodes,
        )
        assert category != "tech"
        assert slugs == ["arts.bollywood"]


class TestShortlistInvariant:
    """No shortlist row may carry a matched slug outside the followed set."""

    def test_every_replayed_row_satisfies_followed_subset(self, nodes) -> None:
        stories = [
            _story("inv-1", "Cricket story", ["sport.cricket"], ["MEDICAL"]),
            _story("inv-2", "Bollywood story", ["arts.bollywood"], []),
            _story("inv-3", "Untagged story", [], ["SPORT"]),
        ]
        entries = build_produce_shortlist(
            stories,
            _tags_for(stories, nodes),
            nodes,
            None,
            followed_interest_ids=_FOLLOWED_IDS,
        )
        followed_slugs = set(_FOLLOWED_SLUGS)
        for entry in entries:
            assert set(entry.shortlist_matched_interest_slugs) <= followed_slugs

    def test_phantom_slug_fires_loud_runtime_warning(self, nodes) -> None:
        """A depth-0 tag on an unfollowed interest (the #70 corruption shape) must
        surface as a structured warning naming the phantom slugs — never silent."""
        story = _story("inv-phantom", "Corrupted-tag story", [], [])
        phantom_tag = StoryInterestTag(
            story_interest_story_id="inv-phantom",
            story_interest_interest_id=_interest_id("environment"),
            story_interest_match_depth=0,
        )
        with capture_logs() as captured:
            entries = build_produce_shortlist(
                [story],
                [phantom_tag],
                nodes,
                None,
                followed_interest_ids=_FOLLOWED_IDS,
            )
        warning = next(
            (
                event
                for event in captured
                if event.get("event") == "shortlist_matched_slug_outside_followed_set"
            ),
            None,
        )
        assert warning is not None, "phantom matched slug must warn loudly"
        assert warning["phantom_slugs"] == ["environment"]
        assert "fix_suggestion" in warning
        # The row is still listed (review must see everything) — flagged, not dropped.
        assert len(entries) == 1

    def test_no_followed_set_given_skips_the_invariant_check(self, nodes) -> None:
        """Callers without a followed universe (fixture paths) stay warning-free."""
        story = _story("inv-nofollow", "Any story", ["sport.cricket"], [])
        with capture_logs() as captured:
            build_produce_shortlist([story], _tags_for([story], nodes), nodes, None)
        assert not [
            event
            for event in captured
            if event.get("event") == "shortlist_matched_slug_outside_followed_set"
        ]
