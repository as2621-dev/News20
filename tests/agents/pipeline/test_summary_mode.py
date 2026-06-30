"""Unit tests for the code-side long-vs-short summary-mode selector (M7 SP1).

DoD (phase file SP1): ``summary_mode_for`` maps a ``youtube.com`` story → "long",
an ``x.com`` story → "short", any other (news) outlet → "news"; and the long/short
prompt variant constants are non-empty and DIFFER in shape.

These tests encode WHY (Rule 9): the milestone's riskiest assumption is that the
long/short signal even exists to branch on — so we prove it is derivable from
existing ``CanonicalStory`` data (the outlet domain), deterministically, in code
(Rule 5). A regression that collapsed the two summary shapes to one string, or
mis-routed youtube/x, fails here.

    >>> pytest tests/agents/pipeline/test_summary_mode.py -v
"""

from __future__ import annotations

import pytest

from agents.pipeline.prompts import (
    KEY_POINTS_LONG,
    KEY_POINTS_NEWS,
    KEY_POINTS_SHORT,
    SCRIPTING_SHAPE_LONG,
    SCRIPTING_SHAPE_NEWS,
    SCRIPTING_SHAPE_SHORT,
)
from agents.pipeline.summary_mode import summary_mode_for


def _restory(canonical_story, domain: str):
    """Re-stamp the shared canonical story onto a given primary outlet domain."""
    return canonical_story.model_copy(
        update={"canonical_primary_outlet_domain": domain}
    )


class TestSummaryModeSelection:
    """The mode is a deterministic transform of the outlet domain (Rule 5)."""

    def test_youtube_domain_is_long(self, canonical_story) -> None:
        """A youtube.com followed-source story is long-form → "long"."""
        assert summary_mode_for(_restory(canonical_story, "youtube.com")) == "long"

    def test_x_domain_is_short(self, canonical_story) -> None:
        """An x.com followed-source story is short-form → "short"."""
        assert summary_mode_for(_restory(canonical_story, "x.com")) == "short"

    def test_generic_news_domain_is_news(self, canonical_story) -> None:
        """A generic outlet domain (the default fixture is bbc.com) → "news"."""
        assert summary_mode_for(canonical_story) == "news"
        assert summary_mode_for(_restory(canonical_story, "cnn.com")) == "news"

    def test_domain_match_is_case_insensitive(self, canonical_story) -> None:
        """Edge: the domain comparison is case-insensitive (adapters vary case)."""
        assert summary_mode_for(_restory(canonical_story, "YouTube.com")) == "long"
        assert summary_mode_for(_restory(canonical_story, "X.COM")) == "short"

    def test_empty_domain_is_news(self, canonical_story) -> None:
        """Edge: a missing outlet domain is not a source item → "news" (fail safe)."""
        assert summary_mode_for(_restory(canonical_story, "")) == "news"


class TestVariantConstantsDiffer:
    """The long and short variant blocks must be distinct, non-empty shapes.

    WHY (Rule 9): the whole point of M7 is that long-form and short-form get
    DIFFERENT summary shapes. A regression that set both to the same string (or
    emptied one) collapses the feature — this test fails when that happens. The
    news variant must stay EMPTY so a news prompt is byte-for-byte unchanged.
    """

    def test_scripting_variants_are_distinct_and_shaped(self) -> None:
        """long = key-points/substance; short = tight/no-padding; news = empty."""
        assert SCRIPTING_SHAPE_LONG and SCRIPTING_SHAPE_SHORT
        assert SCRIPTING_SHAPE_LONG != SCRIPTING_SHAPE_SHORT
        assert SCRIPTING_SHAPE_NEWS == ""
        assert "KEY POINTS" in SCRIPTING_SHAPE_LONG
        assert "substance" in SCRIPTING_SHAPE_LONG
        assert "TIGHT" in SCRIPTING_SHAPE_SHORT
        assert "pad" in SCRIPTING_SHAPE_SHORT.lower()

    def test_key_points_variants_are_distinct_and_shaped(self) -> None:
        """long draws fuller key-points; short asks a tight set; news is empty."""
        assert KEY_POINTS_LONG and KEY_POINTS_SHORT
        assert KEY_POINTS_LONG != KEY_POINTS_SHORT
        assert KEY_POINTS_NEWS == ""
        assert "key points" in KEY_POINTS_LONG.lower()
        assert "substance" in KEY_POINTS_LONG.lower()
        assert "tight" in KEY_POINTS_SHORT.lower()
        assert "pad" in KEY_POINTS_SHORT.lower()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
