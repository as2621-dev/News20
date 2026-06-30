"""Unit tests for the source-tier authority table (FSR-M3 SP1).

DoD (Rule 9 — assert the *reason*, not "returns a float"):

  (a) a known high-authority domain returns a strictly HIGHER tier weight than a known
      content-farm domain — authority ordering;
  (b) an unknown domain falls to the documented DEFAULT tier (no crash) — graceful
      degradation;
  (c) ``authority_and_diversity`` over {3 distinct high-authority outlets} scores
      strictly HIGHER than over {10 identical-tier content-farm outlets} — the headline
      E1 property: authority + diversity beats raw volume.

Each test fails if the tier ordering or the diversity-over-volume property regresses.
All inputs are pure data — no DB, no clock, no network.
"""

from __future__ import annotations

from agents.pipeline.importance.source_tiers import (
    DEFAULT_TIER,
    TIER_CONTENT_FARM,
    TIER_PREMIER,
    TIER_WEIGHT,
    authority_and_diversity,
    normalize_domain,
    outlet_authority,
    outlet_tier,
)


def test_high_authority_domain_outweighs_content_farm() -> None:
    """DoD (a): a premier outlet's authority weight strictly exceeds a content farm's.

    WHY: the E1 ``authority`` term must let a high-authority outlet count for more than a
    low-authority reprint mill; if this ordering ever flips, a content-farm story could
    out-authority a paper of record. Asserting strict ``>`` (not just ``!=``) pins the
    direction.
    """
    assert outlet_authority("reuters.com") > outlet_authority("contentfarm.example")
    assert outlet_tier("reuters.com") == TIER_PREMIER
    assert outlet_tier("contentfarm.example") == TIER_CONTENT_FARM
    # The ladder itself is strictly decreasing premier → content_farm.
    assert TIER_WEIGHT[TIER_PREMIER] > TIER_WEIGHT[TIER_CONTENT_FARM]


def test_unknown_domain_falls_to_default_tier_no_crash() -> None:
    """DoD (b): an unknown / empty / None domain resolves to the default tier, no crash.

    WHY: the seed map names only the exceptional ends of the spectrum; the broad middle
    (and every uncurated-but-legitimate outlet) must degrade to a documented neutral tier
    rather than crashing or being treated as a content farm.
    """
    assert outlet_tier("some-unknown-blog.example") == DEFAULT_TIER
    assert outlet_authority("some-unknown-blog.example") == TIER_WEIGHT[DEFAULT_TIER]
    # None / empty must not crash and must take the default tier.
    assert outlet_tier(None) == DEFAULT_TIER
    assert outlet_tier("") == DEFAULT_TIER
    # An unknown domain must NOT be silently demoted to content_farm.
    assert outlet_authority("some-unknown-blog.example") > outlet_authority(
        "contentfarm.example"
    )


def test_domain_normalization_resolves_scheme_and_www() -> None:
    """A full URL / www-prefixed / cased domain resolves to the same tier as the bare host.

    WHY: outlet domains arrive in mixed shapes; if normalisation regresses, a real premier
    outlet written as a URL would silently fall to the default tier and lose authority.
    """
    assert normalize_domain("https://www.Reuters.com/world") == "reuters.com"
    assert outlet_authority("https://www.Reuters.com/world") == outlet_authority(
        "reuters.com"
    )


def test_three_varied_authoritative_outlets_beat_ten_content_farms() -> None:
    """DoD (c): 3 distinct high-authority outlets beat 10 identical content-farm reprints.

    WHY (the headline E1 property): authority + diversity must beat raw volume. A
    syndication pile of one content farm's reprints — even ten of them — should not
    out-authority three varied papers of record. If diversity stopped mattering or the
    soft-cap inverted, this would flip; the test pins the direction.
    """
    three_authoritative = ["reuters.com", "apnews.com", "bbc.com"]
    ten_content_farms = ["contentfarm.example"] * 10
    assert authority_and_diversity(three_authoritative) > authority_and_diversity(
        ten_content_farms
    )


def test_repeated_same_outlet_does_not_inflate_authority() -> None:
    """Reprints from the SAME outlet contribute that outlet's authority only ONCE.

    WHY (syndication dampening at the authority layer): a single premier outlet
    reprinted 10 times is not more authoritative than that outlet once — diversity is
    about *distinct* newsrooms, not reprint count.
    """
    once = authority_and_diversity(["reuters.com"])
    ten_times = authority_and_diversity(["reuters.com"] * 10)
    assert once == ten_times


def test_authority_and_diversity_bounded_and_empty_safe() -> None:
    """The aggregate stays in [0, 1) and an empty outlet set scores 0.0 (no crash)."""
    assert authority_and_diversity([]) == 0.0
    assert authority_and_diversity([None, ""]) == 0.0
    score = authority_and_diversity(["reuters.com", "apnews.com", "bbc.com"])
    assert 0.0 < score < 1.0
