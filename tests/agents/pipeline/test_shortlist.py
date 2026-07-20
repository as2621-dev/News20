"""Tests for the shortlist-only halt (founder rule 2026-07-19: shortlist-first).

Encodes WHY the mode exists (Rule 9): reel production is the expensive tail of
the batch (script LLM → TTS → poster), so a run must be able to stop at story
selection and surface the would-be-produced list for founder review — spending
ZERO production credits. The wiring test asserts the paid phases are literally
never invoked, not merely that a shortlist came back.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.memory.session_processor import ProfileUpdateResult
from agents.pipeline import daily_batch
from agents.pipeline.shortlist import build_produce_shortlist


def _story(story_id: str, outlet_count: int = 5) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Title {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=datetime(2026, 7, 19, tzinfo=timezone.utc),
        canonical_primary_outlet_domain="reuters.com",
        canonical_primary_outlet_name="Reuters",
        canonical_representative_external_id=f"ext-{story_id}",
        story_outlet_count=outlet_count,
    )


def _tag(story_id: str, interest_id: str, depth: int = 0) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id=story_id,
        story_interest_interest_id=interest_id,
        story_interest_match_depth=depth,
    )


_NODES = {
    "i-ai": InterestNode(interest_id="i-ai", interest_slug="ai", interest_label="AI"),
    "i-llm": InterestNode(
        interest_id="i-llm",
        parent_interest_id="i-ai",
        interest_slug="ai.llms",
        interest_label="LLMs",
        depth_level=1,
    ),
}


class TestBuildProduceShortlist:
    def test_happy_path_maps_story_tags_and_category(self) -> None:
        """A leaf-tagged story surfaces its headline, outlets, category and the
        matched interest slugs — the exact fields the founder reviews."""
        stories = [_story("s1")]
        tags = [_tag("s1", "i-llm", depth=0), _tag("s1", "i-ai", depth=1)]

        entries = build_produce_shortlist(stories, tags, _NODES, None)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.shortlist_story_id == "s1"
        assert entry.shortlist_headline == "Title s1"
        assert entry.shortlist_primary_outlet == "Reuters"
        assert entry.shortlist_outlet_count == 5
        # Category resolves through the SAME assign_category the produce caps
        # use (resolve-once): leaf ai.llms → root category "ai".
        assert entry.shortlist_category == "ai"
        assert entry.shortlist_matched_interest_slugs == ["ai.llms"]

    def test_untagged_story_still_listed_never_dropped(self) -> None:
        """Failure case: a story with no resolvable tag must still appear
        (operator-visible with empty slugs) — a silently missing shortlist row
        would hide exactly the junk the founder needs to veto."""
        entries = build_produce_shortlist([_story("s2")], [], _NODES, None)

        assert len(entries) == 1
        assert entries[0].shortlist_matched_interest_slugs == []
        # Falls back to assign_category's DEFAULT_CATEGORY — present, not empty.
        assert entries[0].shortlist_category

    def test_category_override_pins_the_category(self) -> None:
        """Edge case: the reconcile stage's cross-category pin must win, exactly
        as it does in the produce caps (same override map, same precedence)."""
        stories = [_story("s3")]
        tags = [_tag("s3", "i-llm", depth=0)]

        entries = build_produce_shortlist(stories, tags, _NODES, {"s3": "tech"})

        assert entries[0].shortlist_category == "tech"


@pytest.mark.asyncio
@pytest.mark.parametrize("shortlist_only", [True, False])
async def test_shortlist_only_halts_before_any_paid_production(
    monkeypatch: pytest.MonkeyPatch, shortlist_only: bool
) -> None:
    """shortlist_only=True returns the would-produce list and NEVER touches the
    paid phases (write/render/assemble); False (the default path) still produces.
    The parametrized flip proves the flag is the cause (Rule 9)."""
    order: list[str] = []
    pool = [_story("s-keep"), _story("s-drop")]

    async def fake_ingest():
        return pool, [_tag("s-keep", "i-ai", depth=0)]

    def fake_select(stories, _tags, _lookup, **_k):
        keep = [s for s in stories if s.canonical_story_id == "s-keep"]
        decisions = [
            SimpleNamespace(
                story_id=s.canonical_story_id,
                should_produce=True,
                importance_score=0.5,
                freshness_score=0.5,
            )
            for s in keep
        ]
        return keep, decisions

    async def fake_write(story, **_k):
        order.append(f"write:{story.canonical_story_id}")
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
        order.append("render")
        return SimpleNamespace(published=True)

    def fake_assemble(*, target_date, **_k):
        order.append("assemble")
        from agents.pipeline.orchestrator import DailyFeedsBatchResult

        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(), active_user_count=1, feeds_written=1
        )

    monkeypatch.setattr(
        daily_batch,
        "run_profile_update_job",
        lambda *_a, **_k: ProfileUpdateResult(users_processed=1, weights_changed=0),
    )
    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    monkeypatch.setattr(daily_batch, "_load_has_current_digest", lambda *_a, **_k: {})
    monkeypatch.setattr(daily_batch, "_load_active_user_ids", lambda *_a, **_k: ["u1"])
    monkeypatch.setattr(daily_batch, "_load_category_allocation", lambda *_a, **_k: {})
    monkeypatch.setattr(
        daily_batch, "_load_interest_nodes_by_user", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        daily_batch,
        "load_active_user_inputs",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)

    result = await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 19),
        supabase_client=object(),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=fake_ingest,
        interest_nodes=dict(_NODES),
        shortlist_only=shortlist_only,
    )

    if shortlist_only:
        # The halt: zero paid phases, zero feed writes — only the shortlist.
        assert order == [], f"paid phases ran in shortlist mode: {order}"
        assert result.produced_story_count == 0
        assert result.feeds is None
        assert [e.shortlist_story_id for e in result.shortlist] == ["s-keep"]
        assert result.shortlist[0].shortlist_category == "ai"
        # The gate still ran: the rejected story is counted, not shortlisted.
        assert result.skipped_by_gate_count == 1
    else:
        # Flip: the default path still produces and assembles.
        assert "write:s-keep" in order
        assert "assemble" in order
        assert result.shortlist == []
