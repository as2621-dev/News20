"""Unit tests for the GDELT coverage census stage (Phase 2c SP2).

DoD (phase file SP2): one function turns a story into a populated, mode-correct
``CoverageReport`` from a (mocked) GDELT call. These tests encode WHY each branch
matters (Rule 9/12), not merely that code ran:

  (a) affiliate/foreign/aggregator noise is FILTERED from the census — counting
      six regional editions of one outlet as six outlets would lie about reach.
  (b) partisan counts match the RATED domains in the injected lookup — an
      unrated domain must not invent a lean.
  (c) the >70%-one-side blindspot branch fires on a concentrated rated set — the
      whole point of partisan mode is to surface the under-covered side.
  (d) a markets/sport story yields ``coverage_mode='reach'`` with momentum + an
      originating outlet — a partisan L/R split on a football score is meaningless.
  (e) a GDELT ``AdapterFetchError`` degrades to the ``covering_outlets`` fallback,
      NEVER a raise and NEVER a silent zero (Decision #3).

The GDELT HTTP client is mocked at the **adapter boundary** (``adapter.search``)
— no network, no key, no throttle wait. A fabricated count, a wrong mode, or a
silent-empty-on-error would fail one of these assertions.

    >>> pytest tests/agents/pipeline/test_coverage_gdelt.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from agents.ingestion.models import CandidateStory, CanonicalStory
from agents.pipeline.models import CoverageReport
from agents.pipeline.stages.coverage_gdelt import (
    build_coverage_report,
    coverage_mode_for_segment,
    normalize_coverage_domain,
)
from agents.shared.exceptions import AdapterFetchError

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

# A static AllSides/Ad Fontes-style domain→lean lookup (the SEEDED outlets table,
# injected — NOT read live). Mirrors the real seed's shape.
_OUTLETS_LOOKUP: dict[str, str] = {
    "cnn.com": "left",
    "nytimes.com": "left",
    "washingtonpost.com": "left",
    "theguardian.com": "left",
    "msnbc.com": "left",
    "vox.com": "left",
    "reuters.com": "center",
    "apnews.com": "center",
    "bbc.com": "center",
    "foxnews.com": "right",
    "wsj.com": "right",
    "nypost.com": "right",
}


def _candidate(
    domain: str,
    *,
    title: str = "Strait tensions escalate",
    seen: datetime = _NOW,
    name: str | None = None,
) -> CandidateStory:
    """Build a metadata-only GDELT-style CandidateStory for the census."""
    url = f"https://{domain}/article"
    return CandidateStory(
        candidate_external_id=url,
        candidate_title=title,
        candidate_url=url,
        candidate_outlet_domain=domain.lower(),
        candidate_outlet_name=name or domain.lower(),
        candidate_published_utc=seen,
    )


def _story(
    *,
    title: str = "Strait tensions escalate as tankers reroute",
    covering: list[str] | None = None,
) -> CanonicalStory:
    """A canonical story carrying clustered ``covering_outlets`` for the fallback."""
    covering = covering or ["cnn.com", "foxnews.com", "reuters.com", "bbc.com"]
    return CanonicalStory(
        canonical_story_id="cand-hormuz-001",
        canonical_title=title,
        canonical_url="https://reuters.com/hormuz",
        canonical_normalized_url="https://reuters.com/hormuz",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="reuters.com",
        canonical_primary_outlet_name="Reuters",
        covering_outlets=covering,
        story_outlet_count=len(covering),
    )


def _adapter_returning(candidates: list[CandidateStory]) -> AsyncMock:
    """A mock GdeltDocAdapter whose ``search`` returns the given candidates."""
    adapter = AsyncMock()
    adapter.search = AsyncMock(return_value=candidates)
    return adapter


def _adapter_raising(exc: Exception) -> AsyncMock:
    """A mock GdeltDocAdapter whose ``search`` raises the given exception."""
    adapter = AsyncMock()
    adapter.search = AsyncMock(side_effect=exc)
    return adapter


# ── Pure-function unit tests (segment→mode, domain normalization) ────────────


def test_coverage_mode_for_segment_geopolitics_is_partisan() -> None:
    """Geopolitics is the contested segment → partisan framing (Decision #3)."""
    assert coverage_mode_for_segment("geopolitics") == "partisan"


@pytest.mark.parametrize("segment", ["markets", "sport", "tech", "wildcard", "unknown"])
def test_coverage_mode_for_segment_defaults_to_reach(segment: str) -> None:
    """Every non-contested segment (incl. unknown) frames coverage as reach."""
    assert coverage_mode_for_segment(segment) == "reach"


def test_normalize_drops_foreign_and_noise_collapses_affiliate() -> None:
    """Foreign ccTLD + aggregator noise drop; affiliate subdomain collapses to apex."""
    assert normalize_coverage_domain("rt.ru") is None  # foreign edition
    assert normalize_coverage_domain("news.google.com") is None  # aggregator noise
    assert normalize_coverage_domain("finance.yahoo.com") == "yahoo.com"  # affiliate
    assert normalize_coverage_domain("www.cnn.com") == "cnn.com"  # www stripped


# ── (a)+(b)+(c) Partisan happy path: noise filtered, counts + blindspot ──────


@pytest.mark.asyncio
async def test_partisan_filters_noise_counts_leans_and_fires_blindspot() -> None:
    """A geopolitics census: noise filtered, rated counts correct, blindspot fires.

    Census = 6 left + 1 center rated outlets, PLUS three pieces of noise that must
    be excluded: a foreign ccTLD, an aggregator, and two affiliate editions of one
    outlet (collapse to a single apex). Right = 0 of 7 rated (<30%) with left
    dominant (>50%), so the >70%-on-the-other-sides blindspot must name 'right'.
    """
    candidates = [
        _candidate("cnn.com"),
        _candidate("nytimes.com"),
        _candidate("washingtonpost.com"),
        _candidate("theguardian.com"),
        _candidate("msnbc.com"),
        _candidate("vox.com"),
        _candidate("reuters.com"),  # 1 center
        # ── noise that MUST be filtered ──
        _candidate("rt.ru"),  # foreign edition → dropped
        _candidate("news.google.com"),  # aggregator → dropped
        _candidate("finance.yahoo.com"),  # affiliate → collapses to yahoo.com
        _candidate("sports.yahoo.com"),  # same outlet → must NOT double-count
    ]
    report = await build_coverage_report(
        _story(), "geopolitics", _OUTLETS_LOOKUP, _adapter_returning(candidates)
    )

    assert isinstance(report, CoverageReport)
    assert report.coverage_mode == "partisan"
    # (b) rated counts match exactly the lookup-rated domains.
    assert report.coverage_left_count == 6
    assert report.coverage_center_count == 1
    assert report.coverage_right_count == 0
    # (a) noise filtered: 7 rated + 1 yahoo apex (the two yahoo editions collapse,
    # rt.ru + news.google.com dropped) = 8 distinct outlets, NOT 11.
    assert report.coverage_outlet_count == 8
    # (c) the under-covered side is named.
    assert report.blindspot_lean == "right"


@pytest.mark.asyncio
async def test_partisan_balanced_set_fires_no_blindspot() -> None:
    """A balanced rated set must NOT fabricate a blindspot (edge / no-false-positive)."""
    candidates = [
        _candidate("cnn.com"),  # left
        _candidate("nytimes.com"),  # left
        _candidate("reuters.com"),  # center
        _candidate("apnews.com"),  # center
        _candidate("foxnews.com"),  # right
        _candidate("wsj.com"),  # right
    ]
    report = await build_coverage_report(
        _story(), "geopolitics", _OUTLETS_LOOKUP, _adapter_returning(candidates)
    )
    assert report.coverage_mode == "partisan"
    assert (
        report.coverage_left_count,
        report.coverage_center_count,
        report.coverage_right_count,
    ) == (2, 2, 2)
    assert report.blindspot_lean is None


# ── (d) Reach mode: momentum + originating outlet + notable strip ────────────


@pytest.mark.asyncio
async def test_reach_mode_for_sport_has_momentum_and_originating_outlet() -> None:
    """A sport story → reach mode with momentum + who-broke-it + notable outlets.

    Earliest seendate = Reuters → it is the originating outlet. The seendate spread
    is wide (~30h) so momentum reads 'developing', not a fabricated 'breaking'.
    """
    candidates = [
        _candidate("reuters.com", seen=_NOW - timedelta(hours=30), name="Reuters"),
        _candidate("bbc.com", seen=_NOW - timedelta(hours=20), name="BBC News"),
        _candidate("cnn.com", seen=_NOW - timedelta(hours=2), name="CNN"),
        _candidate("foxnews.com", seen=_NOW, name="Fox News"),
        _candidate("rt.ru", seen=_NOW),  # foreign noise → filtered
    ]
    report = await build_coverage_report(
        _story(title="Arsenal win the league"),
        "sport",
        _OUTLETS_LOOKUP,
        _adapter_returning(candidates),
    )

    assert report.coverage_mode == "reach"
    # rt.ru filtered → 4 distinct outlets, not 5.
    assert report.coverage_outlet_count == 4
    assert report.coverage_momentum == "developing"
    assert report.coverage_originating_outlet_name == "Reuters"
    # Notable strip is capped at 5 and led by the earliest-surfaced outlets.
    assert report.coverage_notable_outlet_names[0] == "Reuters"
    assert len(report.coverage_notable_outlet_names) <= 5
    # Reach mode does NOT populate partisan counts.
    assert report.coverage_left_count == 0


@pytest.mark.asyncio
async def test_reach_breaking_momentum_for_tight_seendate_burst() -> None:
    """A tight burst of fresh seendates reads 'breaking' (edge of the momentum scale)."""
    candidates = [
        _candidate("reuters.com", seen=_NOW - timedelta(hours=1), name="Reuters"),
        _candidate("bbc.com", seen=_NOW, name="BBC News"),
    ]
    report = await build_coverage_report(
        _story(), "markets", _OUTLETS_LOOKUP, _adapter_returning(candidates)
    )
    assert report.coverage_mode == "reach"
    assert report.coverage_momentum == "breaking"


# ── (e) GDELT failure → graceful covering_outlets fallback ───────────────────


@pytest.mark.asyncio
async def test_gdelt_error_falls_back_to_covering_outlets_partisan() -> None:
    """GDELT AdapterFetchError must NOT raise and must NOT report zero (Decision #3).

    The census fails, so the report degrades to the story's own clustered
    covering_outlets — here 6 left + 2 center + 1 right, concentrated left with a
    UNIQUE minimum on the right → the blindspot still surfaces 'right'. (A
    center/right tie would correctly name no blindspot, so the fixture avoids it.)
    A silent empty/zero would fail here.
    """
    covering = [
        "cnn.com",
        "nytimes.com",
        "washingtonpost.com",
        "theguardian.com",
        "msnbc.com",
        "vox.com",
        "reuters.com",
        "apnews.com",
        "foxnews.com",
    ]
    report = await build_coverage_report(
        _story(covering=covering),
        "geopolitics",
        _OUTLETS_LOOKUP,
        _adapter_raising(
            AdapterFetchError(message="rate limited", adapter_name="gdelt_doc")
        ),
    )

    assert report.coverage_mode == "partisan"
    # NOT a silent zero — the fallback counts the known covering outlets.
    assert report.coverage_outlet_count == 9
    assert report.coverage_left_count == 6
    assert report.coverage_center_count == 2
    assert report.coverage_right_count == 1
    assert report.blindspot_lean == "right"


@pytest.mark.asyncio
async def test_gdelt_error_falls_back_to_covering_outlets_reach() -> None:
    """Reach-mode fallback still reports the distinct outlet count, never zero."""
    report = await build_coverage_report(
        _story(covering=["reuters.com", "bbc.com", "cnn.com"]),
        "sport",
        _OUTLETS_LOOKUP,
        _adapter_raising(AdapterFetchError(message="boom", adapter_name="gdelt_doc")),
    )
    assert report.coverage_mode == "reach"
    assert report.coverage_outlet_count == 3
    assert report.coverage_notable_outlet_names  # non-empty


@pytest.mark.asyncio
async def test_empty_census_falls_back_not_zero() -> None:
    """A valid-but-empty GDELT response degrades to covering_outlets, not zero."""
    report = await build_coverage_report(
        _story(covering=["reuters.com", "bbc.com"]),
        "sport",
        _OUTLETS_LOOKUP,
        _adapter_returning([]),
    )
    assert report.coverage_mode == "reach"
    assert report.coverage_outlet_count == 2
