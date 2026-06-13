"""Shared fixtures for the SP2 pipeline test suite (produce-gate + scripting + verification).

All externals are mocked at the boundary (CLAUDE.md mandate): the Gemini call is
mocked at the ``LLMClient.call_gemini`` boundary — no network, no key, no cost.
Fixtures build News20-native ``CanonicalStory`` / ``StoryInterestTag`` payloads
(SP1 output shapes) so the SP2 stages are exercised with realistic inputs.

Fixtures:
    fixed_now            -- a deterministic UTC "now" for freshness math
    canonical_story      -- a fresh, multi-outlet CanonicalStory with body text
    story_interest_tags  -- two story_interests tags for that story (leaf + parent)
    make_llm_client      -- factory: an LLMClient whose call_gemini returns canned text
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.llm_clients import LLMClient

_STORY_ID = "cand-arsenal-001"
_FIXED_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_real_acoustic_aligner(monkeypatch: pytest.MonkeyPatch):
    """Keep the Wav2Vec2 alignment model out of EVERY pipeline test.

    Reason: ``build_caption_track(..., audio_bytes=...)`` tries acoustic forced
    alignment first; without this patch a happy-path orchestrate test would
    download the ~360MB model from the network (CLAUDE.md mandate: mock
    external services). Returning None exercises the heuristic fallback path.
    Tests that need a different aligner behavior patch over this themselves.
    """
    from agents.pipeline.stages import acoustic_alignment

    monkeypatch.setattr(acoustic_alignment, "_load_aligner", lambda: None)

# Reason: the single source body the scripting + verification stages ground on.
# Every fact a grounded digest may assert must appear here verbatim/implied.
_SOURCE_BODY = (
    "Arsenal beat Liverpool 2-1 at the Emirates Stadium on Saturday. "
    "Bukayo Saka scored both goals for Arsenal in the second half. "
    "The win moved Arsenal to the top of the Premier League table with 78 points."
)


@pytest.fixture
def fixed_now() -> datetime:
    """A deterministic tz-aware UTC 'now' so freshness decay is reproducible."""
    return _FIXED_NOW


@pytest.fixture
def source_body() -> str:
    """The single source article body the SP2 stages ground on."""
    return _SOURCE_BODY


@pytest.fixture
def canonical_story() -> CanonicalStory:
    """A fresh, 4-outlet canonical Arsenal story with extracted body text.

    Reported at the fixed 'now' (freshness ~1.0) and covered by 4 outlets
    (importance well above the floor), so the produce-gate's floor checks pass
    by default — individual tests override outlet count / publish time to probe
    the floor edges.
    """
    return CanonicalStory(
        canonical_story_id=_STORY_ID,
        canonical_title="Arsenal beat Liverpool 2-1 at the Emirates",
        canonical_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_normalized_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_published_utc=_FIXED_NOW,
        canonical_primary_outlet_domain="bbc.com",
        canonical_primary_outlet_name="BBC",
        canonical_body_text=_SOURCE_BODY,
        canonical_representative_external_id="https://bbc.com/sport/arsenal-liverpool",
        covering_outlets=["bbc.com", "cnn.com", "reuters.com", "theguardian.com"],
        story_outlet_count=4,
        canonical_matched_interest_ids=["int-arsenal"],
    )


@pytest.fixture
def story_interest_tags() -> list[StoryInterestTag]:
    """Two story_interests tags for the canonical story: Arsenal (leaf) + Soccer (parent)."""
    return [
        StoryInterestTag(
            story_interest_story_id=_STORY_ID,
            story_interest_interest_id="int-arsenal",
            story_interest_match_depth=0,
        ),
        StoryInterestTag(
            story_interest_story_id=_STORY_ID,
            story_interest_interest_id="int-soccer",
            story_interest_match_depth=1,
        ),
    ]


@pytest.fixture
def make_llm_client():
    """Factory for an ``LLMClient`` whose ``call_gemini`` returns canned text.

    Mocks the LLM at the client boundary (never instantiates the real Gemini
    SDK). Pass the raw string the model should "return".
    """

    def _make(canned_response: str) -> LLMClient:
        client = LLMClient.__new__(LLMClient)  # skip __init__ (no Settings/key needed)
        client.call_gemini = AsyncMock(return_value=canned_response)  # type: ignore[method-assign]
        return client

    return _make
