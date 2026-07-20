"""Unit tests for the notability gate (feed-quality reset WS-B, PRD RC2 / decision 4).

Every test encodes WHY the verdict matters (Rule 9), not merely that a function ran:

  1. ``≥2 distinct outlets`` OR ``authority-tier`` is the admission rule — one
     newsroom's word is a *claim*, two independent newsrooms is *news* (RC2).
  2. **Corroboration ≠ syndication.** N reprints of one PR-wire item across a dozen
     press-release distributors are ONE press release, not a dozen independent
     outlets — they must collapse to zero corroborating editorial outlets and FAIL.
  3. A THIN leaf niche drops to a *relaxed* rule rather than starving into permanent
     "Beyond your bubble", and the surviving slot is STAMPED ``relaxed`` so the
     honesty ladder can surface it (never a silent pad).
  4. If the authority-classification config is unavailable the gate FAILS LOUD — it
     must never silently pass everything through (a disabled gate is the RC2 bug).
  5. Every batch reports candidates-surviving-per-niche, so an over-tight gate that
     starves niches is visible on the first M2 run (PRD de-risking).

    >>> pytest tests/agents/pipeline/test_notability_gate.py -v
"""

from __future__ import annotations

import pytest

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline import notability_gate
from agents.pipeline.importance import source_tiers
from agents.pipeline.notability_gate import (
    NOTABILITY_RUNG_RELAXED,
    NOTABILITY_RUNG_SOURCE_ORIGIN,
    NOTABILITY_RUNG_STRICT,
    NotabilityConfigError,
    apply_notability_gate,
    count_distinct_corroborating_outlets,
    has_authority_outlet,
    is_notable,
)

# Reason: unknown domains resolve to source_tiers.DEFAULT_TIER (STANDARD) — ordinary
# credible editorial outlets. Using several distinct ones isolates the "≥2 distinct
# EDITORIAL outlets" branch from the authority branch (none of these is an authority
# tier), so a pass here proves corroboration, not authority.
_STANDARD_EDITORIAL_OUTLETS = [
    "denverpost.com",
    "sfchronicle.com",
    "chicagotribune.com",
    "bostonglobe.com",
    "seattletimes.com",
]

# Reason: twelve real PR-wire / press-release distributor domains — the canonical
# "corroboration ≠ syndication" case. Each is in the gate's non-editorial set, so a
# story carried ONLY by these has ZERO corroborating editorial outlets.
_PR_WIRE_DISTRIBUTORS = [
    "prnewswire.com",
    "globenewswire.com",
    "businesswire.com",
    "prweb.com",
    "einnews.com",
    "newswire.com",
    "accesswire.com",
    "prlog.org",
    "24-7pressrelease.com",
    "openpr.com",
    "pressreleasepoint.com",
    "issuewire.com",
]


def _story(
    story_id: str,
    covering_outlets: list[str],
    *,
    primary_domain: str | None = None,
) -> CanonicalStory:
    """Build a minimal CanonicalStory for the gate (only outlet fields matter here)."""
    primary = primary_domain or (
        covering_outlets[0] if covering_outlets else "example.com"
    )
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Story {story_id}",
        canonical_url=f"https://{primary}/{story_id}",
        canonical_normalized_url=f"https://{primary}/{story_id}",
        canonical_published_utc=__import__("datetime").datetime(
            2026, 7, 18, tzinfo=__import__("datetime").timezone.utc
        ),
        canonical_primary_outlet_domain=primary,
        covering_outlets=sorted(covering_outlets),
        story_outlet_count=len(covering_outlets),
    )


def _leaf_tag(story_id: str, interest_id: str) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=0,
    )


class TestStrictAdmissionRule:
    """Criterion 1: ≥2 distinct outlets passes; 1 non-authority fails; 1 authority passes."""

    def test_two_distinct_editorial_outlets_pass(self) -> None:
        """Two independent editorial outlets corroborate → notable (on corroboration, not authority)."""
        outlets = _STANDARD_EDITORIAL_OUTLETS[:2]
        assert count_distinct_corroborating_outlets(outlets) == 2
        assert (
            has_authority_outlet(outlets) is False
        )  # proves it is NOT the authority branch
        assert is_notable(outlets) is True

    def test_single_non_authority_outlet_fails(self) -> None:
        """One ordinary outlet is an uncorroborated claim → not notable."""
        outlets = _STANDARD_EDITORIAL_OUTLETS[:1]
        assert count_distinct_corroborating_outlets(outlets) == 1
        assert has_authority_outlet(outlets) is False
        assert is_notable(outlets) is False

    def test_single_authority_outlet_passes(self) -> None:
        """One authority outlet (Reuters) alone clears the gate via the OR branch."""
        outlets = ["reuters.com"]
        assert count_distinct_corroborating_outlets(outlets) == 1
        assert has_authority_outlet(outlets) is True
        assert is_notable(outlets) is True

    def test_standard_tier_is_not_authority(self) -> None:
        """A single UNKNOWN→STANDARD outlet must not count as authority (else the gate is a no-op)."""
        assert has_authority_outlet(["some-unknown-local-blog.example"]) is False


class TestSyndicationIsNotCorroboration:
    """Criterion 2: 12 syndicated copies of one PR-wire item count as (at most) 1 and FAIL."""

    def test_pr_wire_burst_has_zero_corroborating_outlets(self) -> None:
        """12 press-release distributors carrying one wire item → ZERO independent editorial outlets."""
        assert len(_PR_WIRE_DISTRIBUTORS) == 12
        assert count_distinct_corroborating_outlets(_PR_WIRE_DISTRIBUTORS) == 0

    def test_pr_wire_burst_fails_strict_and_relaxed(self) -> None:
        """A pure syndication burst is never news — it fails even the relaxed single-outlet rule."""
        assert is_notable(_PR_WIRE_DISTRIBUTORS, min_distinct_outlets=2) is False
        assert is_notable(_PR_WIRE_DISTRIBUTORS, min_distinct_outlets=1) is False
        assert has_authority_outlet(_PR_WIRE_DISTRIBUTORS) is False

    def test_wire_reprints_of_one_real_outlet_count_as_one(self) -> None:
        """One real outlet + 11 wire reprints of its item count as 1 distinct outlet → fails strict.

        This is the literal "counts as 1 outlet and fails": the syndication does not
        turn a single newsroom's story into multi-outlet corroboration.
        """
        outlets = ["denverpost.com", *_PR_WIRE_DISTRIBUTORS[:11]]
        assert count_distinct_corroborating_outlets(outlets) == 1
        assert is_notable(outlets, min_distinct_outlets=2) is False


class TestDistinctnessNormalization:
    """Distinctness is real: regional editions / casing / www collapse to one outlet."""

    def test_regional_and_casing_variants_dedupe(self) -> None:
        outlets = ["bbc.com", "www.bbc.com", "BBC.COM", "https://bbc.com/news"]
        # Reason: all four normalize to bbc.com — one authoritative outlet, not four.
        assert count_distinct_corroborating_outlets(outlets) == 1
        assert has_authority_outlet(outlets) is True


class TestThinNicheRelaxation:
    """Criterion 3: a thin niche invokes the relaxed rule AND stamps the rung."""

    def test_thin_niche_relaxes_and_stamps_rung(self) -> None:
        """A niche where every candidate fails strict drops to relaxed, stamped ``relaxed``."""
        # One single-editorial-outlet story in niche n1 — fails strict (1 < 2 outlets).
        story = _story("s-thin", _STANDARD_EDITORIAL_OUTLETS[:1])
        tags = [_leaf_tag("s-thin", "n1")]

        result = apply_notability_gate([story], tags)

        assert "s-thin" in result.notable_story_ids  # relaxation saved it from starving
        decision = next(d for d in result.decisions if d.story_id == "s-thin")
        assert decision.is_notable is True
        assert decision.notability_rung == NOTABILITY_RUNG_RELAXED
        assert "n1" in result.relaxed_niche_ids

    def test_healthy_niche_does_not_relax_a_single_outlet_story(self) -> None:
        """Relaxation is niche-gated, never a blanket pad: a healthy niche keeps the strict cut."""
        # Three strict-passing stories (2 editorial outlets each) make n1 healthy.
        strict_stories = [
            _story(f"s-strict-{i}", _STANDARD_EDITORIAL_OUTLETS[i : i + 2])
            for i in range(3)
        ]
        weak_story = _story("s-weak", _STANDARD_EDITORIAL_OUTLETS[:1])
        stories = [*strict_stories, weak_story]
        tags = [_leaf_tag(s.canonical_story_id, "n1") for s in stories]

        result = apply_notability_gate(stories, tags)

        # The three strict stories pass; the lone single-outlet story does NOT (niche not thin).
        assert "s-weak" not in result.notable_story_ids
        assert "n1" not in result.relaxed_niche_ids
        weak_decision = next(d for d in result.decisions if d.story_id == "s-weak")
        assert weak_decision.is_notable is False
        assert weak_decision.notability_rung == ""

    def test_strict_survivor_is_stamped_strict(self) -> None:
        story = _story("s-strict", _STANDARD_EDITORIAL_OUTLETS[:2])
        tags = [_leaf_tag("s-strict", "n1")]
        result = apply_notability_gate([story], tags)
        decision = next(d for d in result.decisions if d.story_id == "s-strict")
        assert decision.is_notable is True
        assert decision.notability_rung == NOTABILITY_RUNG_STRICT


class TestAuthorityConfigUnavailableFailsLoud:
    """Criterion 4: authority config unavailable → loud failure, gate NOT silently disabled."""

    def test_empty_authority_map_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty source-tier map means the gate cannot identify authority outlets → fail loud."""
        monkeypatch.setattr(source_tiers, "SOURCE_TIER_BY_DOMAIN", {})
        story = _story("s1", _STANDARD_EDITORIAL_OUTLETS[:2])
        with pytest.raises(NotabilityConfigError):
            apply_notability_gate([story], [_leaf_tag("s1", "n1")])

    def test_gate_is_not_disabled_on_missing_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure must be an EXCEPTION, never a silent pass-through of every story."""
        monkeypatch.setattr(source_tiers, "SOURCE_TIER_BY_DOMAIN", {})
        # A junk single-outlet story would sail through a disabled gate; assert it does not.
        junk = _story("junk", _STANDARD_EDITORIAL_OUTLETS[:1])
        raised = False
        try:
            apply_notability_gate([junk], [_leaf_tag("junk", "n1")])
        except NotabilityConfigError:
            raised = True
        assert raised is True


class TestBatchReportsPerNicheSurvivors:
    """Criterion 5: batch output includes candidates-surviving-per-niche."""

    def test_surviving_by_niche_counts(self) -> None:
        # niche A: 2 strict survivors; niche B: 1 strict survivor + 1 fail (B healthy? no).
        a1 = _story("a1", _STANDARD_EDITORIAL_OUTLETS[:2])
        a2 = _story("a2", ["reuters.com"])  # authority single-outlet
        b1 = _story("b1", _STANDARD_EDITORIAL_OUTLETS[:2])
        stories = [a1, a2, b1]
        tags = [
            _leaf_tag("a1", "A"),
            _leaf_tag("a2", "A"),
            _leaf_tag("b1", "B"),
        ]

        result = apply_notability_gate(stories, tags)

        assert result.surviving_by_niche["A"] == 2
        assert result.surviving_by_niche["B"] == 1
        # strict-survivor map is reported separately so the M2 audit can see relaxations.
        assert result.strict_survivors_by_niche["A"] == 2


class TestSourceOriginExemption:
    """Followed-source (YouTube/X) stories bypass notability, mirroring the produce gate."""

    def test_source_origin_story_bypasses_gate(self) -> None:
        """A single-source YouTube upload is intrinsically wanted — exempt, stamped source_origin."""
        story = _story("yt1", ["youtube.com"], primary_domain="youtube.com")
        result = apply_notability_gate(
            [story], []
        )  # source items carry no interest tag
        assert "yt1" in result.notable_story_ids
        decision = next(d for d in result.decisions if d.story_id == "yt1")
        assert decision.is_notable is True
        assert decision.notability_rung == NOTABILITY_RUNG_SOURCE_ORIGIN


def test_module_constants_present() -> None:
    """The thresholds are module constants (no config surface) — guards a silent removal."""
    assert notability_gate._MIN_DISTINCT_OUTLETS_STRICT == 2
    assert notability_gate._MIN_DISTINCT_OUTLETS_RELAXED == 1
    assert notability_gate._THIN_NICHE_SURVIVOR_FLOOR >= 1
