"""Unit tests for the pre-generation produce-dedup stage (LLM judge).

WHY these tests (Rule 9): the stage exists to stop the pipeline paying the full
per-story generation cost (script → verify → TTS → poster → editorial) twice for
ONE real event that the ingestion clusterer missed (it merges only on URL or
title similarity >= 0.85; the real 2026-06-16 Nvidia pair was 0.759 apart). So
the tests encode the economic contract:
  - a judged duplicate group collapses to the HIGHEST-coverage story (the rest are
    never generated),
  - genuinely distinct stories the judge keeps separate are ALL retained,
  - and a judge/parse failure FAILS OPEN (produces the un-deduped shortlist)
    rather than blocking the daily run (Rule 12).

The LLM is mocked at the ``call_gemini`` boundary — no network, no key, no cost.

    >>> pytest tests/agents/pipeline/test_produce_dedup.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.ingestion.models import CanonicalStory
from agents.pipeline.produce_dedup import dedupe_produce_shortlist

_NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _story(
    story_id: str,
    title: str,
    *,
    outlet_count: int = 4,
    body: str = "",
    published: datetime = _NOW,
) -> CanonicalStory:
    """A minimal CanonicalStory for the dedup judge (title + coverage + lead)."""
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title,
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=published,
        canonical_primary_outlet_domain="bbc.com",
        canonical_body_text=body or title,
        covering_outlets=[f"outlet{i}.com" for i in range(outlet_count)],
        story_outlet_count=outlet_count,
    )


@pytest.mark.asyncio
async def test_collapses_near_duplicate_to_highest_coverage(make_llm_client) -> None:
    """The real Nvidia pair (which clustering missed at 0.759 title similarity) is
    collapsed to ONE story — the higher-coverage outlet count is kept, the other
    is dropped so it is never generated.

    This is the core economic contract: the judge groups [1,2]; the code keeps the
    8-outlet story over the 5-outlet one (deterministic representative), regardless
    of input order.
    """
    stories = [
        _story(
            "nvidia-a",
            "Nvidia CEO Jensen Huang Urges Society to Adapt to AI",
            outlet_count=5,
        ),
        _story(
            "nvidia-b",
            "Nvidia CEO Jensen Huang urges societal change for AI era",
            outlet_count=8,
        ),
        _story("spacex", "SpaceX IPO draws record options volume", outlet_count=6),
    ]
    # Judge groups the two Nvidia stories (n=1, n=2) as duplicates; spacex is a singleton.
    client = make_llm_client("[[1, 2]]")

    kept, decisions = await dedupe_produce_shortlist(stories, client)

    kept_ids = {s.canonical_story_id for s in kept}
    assert kept_ids == {"nvidia-b", "spacex"}, "higher-coverage Nvidia + the distinct SpaceX"
    assert len(decisions) == 1
    assert decisions[0].dropped_story_id == "nvidia-a"
    assert decisions[0].kept_story_id == "nvidia-b"
    assert set(decisions[0].cluster_story_ids) == {"nvidia-a", "nvidia-b"}


@pytest.mark.asyncio
async def test_distinct_stories_all_kept(make_llm_client) -> None:
    """When the judge returns NO duplicate groups, every story survives — the stage
    must not invent duplicates (the lexical heuristic falsely merged 'Q1' vs 'Q3'
    earnings at 0.96; the judge is trusted to keep distinct events separate).
    """
    stories = [
        _story("aapl-q1", "Apple reports Q1 earnings beat"),
        _story("aapl-q3", "Apple reports Q3 earnings beat"),
        _story("msft", "Microsoft raises cloud guidance"),
    ]
    client = make_llm_client("[]")  # no duplicate groups

    kept, decisions = await dedupe_produce_shortlist(stories, client)

    assert [s.canonical_story_id for s in kept] == ["aapl-q1", "aapl-q3", "msft"]
    assert decisions == []


@pytest.mark.asyncio
async def test_fails_open_on_judge_error(make_llm_client) -> None:
    """A judge error must NOT block the daily run: the original shortlist is returned
    unchanged (Rule 12 fail-open). Here the mock raises on call.
    """
    stories = [
        _story("a", "Story A"),
        _story("b", "Story B"),
    ]
    client = make_llm_client("unused")
    # Force the call to raise, simulating an API/quota failure after retries.
    client.call_gemini.side_effect = RuntimeError("gemini 503")

    kept, decisions = await dedupe_produce_shortlist(stories, client)

    assert [s.canonical_story_id for s in kept] == ["a", "b"]
    assert decisions == []


@pytest.mark.asyncio
async def test_singleton_shortlist_skips_llm(make_llm_client) -> None:
    """A 0/1-story shortlist returns immediately WITHOUT an LLM call (nothing to
    dedup, no cost)."""
    client = make_llm_client("[[1]]")
    one = [_story("only", "The only story")]

    kept, decisions = await dedupe_produce_shortlist(one, client)

    assert kept == one
    assert decisions == []
    client.call_gemini.assert_not_called()


@pytest.mark.asyncio
async def test_hallucinated_and_out_of_range_ids_ignored(make_llm_client) -> None:
    """A group referencing an out-of-range index is sanitized: only valid, in-range
    members survive, and a group with fewer than 2 valid members is dropped (the
    story is NOT removed). Guards against a hallucinating model dropping real
    stories.
    """
    stories = [
        _story("a", "Story A", outlet_count=3),
        _story("b", "Story B", outlet_count=9),
    ]
    # n=99 is out of range; only n=1 survives the group → fewer than 2 → ignored.
    client = make_llm_client("[[1, 99]]")

    kept, decisions = await dedupe_produce_shortlist(stories, client)

    assert [s.canonical_story_id for s in kept] == ["a", "b"]
    assert decisions == []
