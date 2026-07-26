"""Tests for the SCRIPTS-ONLY halt — the second rung of the staged-production ladder (#68).

WHY (Rule 9): reels are the expensive tail of a run — the 2026-07-19 $24 burn was
58 reels' TTS + posters. Scripts on ``gemini-3.5-flash`` are pennies, so they are
affordable to write BEFORE the founder's go; TTS and posters are not. The founder
decision (2026-07-25) therefore stages the run:

    shortlist  →  founder review  →  scripts + similarity gate  →  founder go  →  reels

These tests encode the SPEND guarantee, not merely that a scripts list came back:
under the scripts halt the media clients must be touched ZERO times (asserted with
a spy that records every attribute access), and the reel stage must be unreachable
without an explicit per-run arm. A test that only checked "scripts were returned"
would still pass if TTS ran anyway.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.memory.session_processor import ProfileUpdateResult
from agents.pipeline import daily_batch
from agents.pipeline.models import DialogueTurn, DigestScript
from agents.pipeline.produce_dedup import dedupe_written_scripts
from agents.pipeline.scripts_artifact import build_scripts_artifact


def _story(story_id: str, outlet_count: int = 5) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=f"Title {story_id}",
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=datetime(2026, 7, 25, tzinfo=timezone.utc),
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
}


def _write_result(story: CanonicalStory, text: str) -> Any:
    """A minimal stand-in for ``WritePhaseResult`` with a real ``DigestScript``."""
    return SimpleNamespace(
        canonical_story_id=story.canonical_story_id,
        original_story=story,
        editorial_story=story,
        segment_slug="ai",
        resolved_category="ai",
        script=DigestScript(
            digest_story_id=story.canonical_story_id,
            turns=[DialogueTurn(speaker="ALEX", text=text)],
            word_count=len(text.split()),
            estimated_duration_seconds=10,
        ),
    )


class _MediaSpy:
    """Records EVERY attribute access, so any TTS/poster touch is provable.

    The scripts halt's whole value is that no media client is used. Asserting on a
    mock's call list would miss a client that was merely reached; recording the
    attribute access catches the reach itself.
    """

    def __init__(self, spy_name: str) -> None:
        self.spy_name = spy_name
        self.touches: list[str] = []

    def __getattr__(self, attribute_name: str) -> Any:
        self.touches.append(attribute_name)
        raise AssertionError(
            f"{self.spy_name} was touched under the scripts halt: .{attribute_name}"
        )


def _patch_pipeline_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every DB/selection edge so only the produce stages are under test."""
    monkeypatch.setattr(
        daily_batch,
        "run_profile_update_job",
        lambda *_a, **_k: ProfileUpdateResult(users_processed=1, weights_changed=0),
    )
    monkeypatch.setattr(daily_batch, "_load_has_current_digest", lambda *_a, **_k: {})
    monkeypatch.setattr(daily_batch, "_load_active_user_ids", lambda *_a, **_k: ["u1"])
    monkeypatch.setattr(daily_batch, "_load_category_allocation", lambda *_a, **_k: {})
    monkeypatch.setattr(
        daily_batch, "_load_interest_nodes_by_user", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(daily_batch, "load_active_user_inputs", lambda *_a, **_k: [])

    def fake_select(stories, _tags, _lookup, **_k):
        decisions = [
            SimpleNamespace(
                story_id=s.canonical_story_id,
                should_produce=True,
                importance_score=0.5,
                freshness_score=0.5,
            )
            for s in stories
        ]
        return list(stories), decisions

    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select)


@pytest.mark.asyncio
@pytest.mark.parametrize("scripts_only", [True, False])
async def test_scripts_only_writes_scripts_and_makes_zero_media_calls(
    monkeypatch: pytest.MonkeyPatch, scripts_only: bool, stub_production_selection: None
) -> None:
    """AC1 + AC5. ``scripts_only=True`` runs the WRITE wave and stops dead: the
    render wave (TTS → poster → persist) is never entered and the media clients are
    never even touched. The parametrized flip proves the flag is the cause — with it
    off the same pool renders and assembles (Rule 9)."""
    stages: list[str] = []
    pool = [_story("s1"), _story("s2")]

    async def fake_ingest():
        return pool, [_tag("s1", "i-ai"), _tag("s2", "i-ai")]

    async def fake_write(story, **_k):
        stages.append(f"write:{story.canonical_story_id}")
        return _write_result(story, f"Script for {story.canonical_story_id}")

    async def fake_render(write_result, *_a, **_k):
        stages.append(f"render:{write_result.canonical_story_id}")
        return SimpleNamespace(published=True)

    def fake_assemble(*, target_date, **_k):
        stages.append("assemble")
        from agents.pipeline.orchestrator import DailyFeedsBatchResult

        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(), active_user_count=1, feeds_written=1
        )

    _patch_pipeline_edges(monkeypatch)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)

    tts_spy = _MediaSpy("tts_client")
    poster_spy = _MediaSpy("poster_genai_client")

    result = await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 25),
        supabase_client=object(),
        llm_client=object(),
        tts_client=tts_spy if scripts_only else object(),
        ingest_fn=fake_ingest,
        interest_nodes=dict(_NODES),
        poster_genai_client=poster_spy if scripts_only else None,
        scripts_only=scripts_only,
    )

    if scripts_only:
        assert stages == ["write:s1", "write:s2"], f"unexpected stages: {stages}"
        assert tts_spy.touches == [], f"TTS client touched: {tts_spy.touches}"
        assert poster_spy.touches == [], f"poster client touched: {poster_spy.touches}"
        assert result.produced_story_count == 0
        assert result.feeds is None
        assert [e.script_story_id for e in result.scripts] == ["s1", "s2"]
        assert result.scripts[0].script_text.startswith("ALEX:")
    else:
        assert "render:s1" in stages and "assemble" in stages
        assert result.scripts == []


@pytest.mark.asyncio
async def test_script_similarity_gate_drops_near_duplicate_and_records_it(
    monkeypatch: pytest.MonkeyPatch, stub_production_selection: None
) -> None:
    """AC2. Two scripts telling the same story must not both become reels. The gate
    reuses the produce_dedup judge seam (one LLM call over the FINAL scripts), and
    the artifact records which script was dropped, which was kept, and why —
    a silent drop would be unreviewable."""
    pool = [_story("s1", outlet_count=9), _story("s2", outlet_count=2)]

    async def fake_ingest():
        return pool, [_tag("s1", "i-ai"), _tag("s2", "i-ai")]

    async def fake_write(story, **_k):
        return _write_result(story, "The central bank raised rates again today.")

    async def fake_render(*_a, **_k):
        raise AssertionError("render must not run under the scripts halt")

    class _JudgeClient:
        def __init__(self) -> None:
            self.call_count = 0

        async def call_gemini(self, _prompt: str, **_k) -> str:
            self.call_count += 1
            return "[[1, 2]]"

    judge_client = _JudgeClient()
    _patch_pipeline_edges(monkeypatch)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)

    result = await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 25),
        supabase_client=object(),
        llm_client=judge_client,
        tts_client=object(),
        ingest_fn=fake_ingest,
        interest_nodes=dict(_NODES),
        # Reason: the STORY-level dedup is off here so the assertion isolates the
        # SCRIPT gate — otherwise the earlier judge would drop the twin on
        # headlines alone and this test would pass without the new gate existing.
        enable_produce_dedup=False,
        scripts_only=True,
        enable_script_dedup=True,
    )

    assert judge_client.call_count == 1
    # The higher-coverage story is the representative that survives.
    assert [e.script_story_id for e in result.scripts] == ["s1"]
    artifact = build_scripts_artifact(result)
    assert artifact.scripts_run.run_script_dedup_dropped_count == 1
    drop = artifact.script_dedup_drops[0]
    assert drop.dropped_story_id == "s2"
    assert drop.kept_story_id == "s1"
    assert drop.reason == "near_duplicate"


@pytest.mark.asyncio
async def test_empty_shortlist_is_a_clean_noop_with_no_llm_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3. An approved-but-empty pool must cost nothing: no script LLM call, no
    similarity judge call, no crash — an empty artifact and a structured log."""

    async def fake_ingest():
        return [], []

    async def fake_write(story, **_k):
        raise AssertionError("write_phase called on an empty pool")

    class _NoCallClient:
        async def call_gemini(self, *_a, **_k) -> str:
            raise AssertionError("LLM judge called on an empty pool")

    _patch_pipeline_edges(monkeypatch)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)

    result = await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 25),
        supabase_client=object(),
        llm_client=_NoCallClient(),
        tts_client=_MediaSpy("tts_client"),
        ingest_fn=fake_ingest,
        interest_nodes=dict(_NODES),
        scripts_only=True,
        enable_script_dedup=True,
    )

    assert result.scripts == []
    assert result.produced_story_count == 0
    artifact = build_scripts_artifact(result)
    assert artifact.scripts_run.run_written_script_count == 0
    assert artifact.script_entries == []


@pytest.mark.asyncio
async def test_one_script_llm_failure_skips_that_story_and_run_completes(
    monkeypatch: pytest.MonkeyPatch, stub_production_selection: None
) -> None:
    """AC4. One story's script LLM blowing up must not cost the whole run — the
    founder still reviews the scripts that DID write. The failure is logged (the
    write wave's existing fail-soft), never swallowed into a silent short list."""
    pool = [_story("s-ok"), _story("s-boom")]

    async def fake_ingest():
        return pool, []

    async def fake_write(story, **_k):
        if story.canonical_story_id == "s-boom":
            raise RuntimeError("gemini 503")
        return _write_result(story, "Fine script.")

    _patch_pipeline_edges(monkeypatch)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)

    result = await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 25),
        supabase_client=object(),
        llm_client=object(),
        tts_client=_MediaSpy("tts_client"),
        ingest_fn=fake_ingest,
        interest_nodes=dict(_NODES),
        scripts_only=True,
    )

    assert [e.script_story_id for e in result.scripts] == ["s-ok"]


class TestScriptDedupSeam:
    @pytest.mark.asyncio
    async def test_singleton_pool_makes_no_judge_call(self) -> None:
        """Edge: one script cannot duplicate anything — spending a judge call on it
        is pure waste, so the seam short-circuits (same contract as the story-level
        dedup it reuses)."""

        class _Client:
            async def call_gemini(self, *_a, **_k) -> str:
                raise AssertionError("judge called on a singleton pool")

        kept, decisions = await dedupe_written_scripts(
            [_write_result(_story("s1"), "solo")], _Client()
        )

        assert len(kept) == 1
        assert decisions == []

    @pytest.mark.asyncio
    async def test_judge_error_fails_open_and_keeps_every_script(self) -> None:
        """Failure case: a dedup miss is a cost nicety, never a correctness gate.
        A judge error must leave the pool untouched rather than block the run
        (fail-open, identical to dedupe_produce_shortlist)."""

        class _BoomClient:
            async def call_gemini(self, *_a, **_k) -> str:
                raise RuntimeError("quota exhausted")

        write_results = [
            _write_result(_story("s1"), "same story"),
            _write_result(_story("s2"), "same story"),
        ]

        kept, decisions = await dedupe_written_scripts(write_results, _BoomClient())

        assert [w.canonical_story_id for w in kept] == ["s1", "s2"]
        assert decisions == []
