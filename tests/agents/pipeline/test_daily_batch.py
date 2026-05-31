"""Tests for the daily pipeline runner's orchestration (stage order + gating).

Asserts the runner runs the §4 weight-update FIRST (so today's feed reflects
yesterday), ingests, then produces ONLY the produce-gate's selection, then
allocates — the contract that makes the batch correct, not merely that each stage
was reachable (Rule 9). Mutation note: producing before the gate, or before the
weight-update, breaks the order/selection assertions below.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from agents.ingestion.models import CanonicalStory
from agents.memory.session_processor import ProfileUpdateResult
from agents.pipeline import daily_batch
from agents.pipeline.orchestrator import ActiveUserFeedInputs, DailyFeedsBatchResult


def _story(story_id: str) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Title {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=datetime(2026, 5, 31, tzinfo=timezone.utc),
        canonical_primary_outlet_domain="reuters.com",
        canonical_representative_external_id=f"ext-{story_id}",
        story_outlet_count=5,
    )


@pytest.mark.asyncio
async def test_run_daily_pipeline_updates_weights_first_then_produces_only_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    pool = [_story("s-keep"), _story("s-drop")]

    def fake_profile_update(*_a, **_k) -> ProfileUpdateResult:
        order.append("profile")
        return ProfileUpdateResult(users_processed=1, weights_changed=1)

    async def fake_ingest():
        order.append("ingest")
        return pool, []

    def fake_select(stories, _tags, _lookup, **_k):
        order.append("gate")
        # The gate keeps only s-keep; s-drop is rejected.
        keep = [s for s in stories if s.canonical_story_id == "s-keep"]
        decisions = [SimpleNamespace(should_produce=True) for _ in keep]
        return keep, decisions

    async def fake_orchestrate(*, story, **_k):
        order.append(f"produce:{story.canonical_story_id}")
        return SimpleNamespace(published=True)

    def fake_has_current_digest(*_a, **_k) -> dict[str, bool]:
        return {}

    def fake_load_inputs(*_a, **_k) -> list[ActiveUserFeedInputs]:
        order.append("load_inputs")
        return [ActiveUserFeedInputs(active_user_id="u1")]

    def fake_assemble(*, target_date, **_k) -> DailyFeedsBatchResult:
        order.append("assemble")
        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(), active_user_count=1, feeds_written=1
        )

    monkeypatch.setattr(daily_batch, "run_profile_update_job", fake_profile_update)
    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select)
    monkeypatch.setattr(daily_batch, "orchestrate_story", fake_orchestrate)
    monkeypatch.setattr(
        daily_batch, "_load_has_current_digest", fake_has_current_digest
    )
    monkeypatch.setattr(daily_batch, "load_active_user_inputs", fake_load_inputs)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)

    result = await daily_batch.run_daily_pipeline(
        target_date=date(2026, 5, 31),
        supabase_client=object(),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=fake_ingest,
        interest_nodes={},
    )

    # Weight-update runs FIRST, before ingest; production runs only after gating.
    assert order.index("profile") < order.index("ingest")
    assert order.index("ingest") < order.index("gate")
    assert order.index("gate") < order.index("produce:s-keep")
    assert order.index("produce:s-keep") < order.index("assemble")
    # Only the gated story was produced; the rejected one never was.
    assert "produce:s-drop" not in order
    assert result.candidate_story_count == 2
    assert result.produced_story_count == 1
    assert result.skipped_by_gate_count == 1
    assert result.feeds is not None and result.feeds.feeds_written == 1
