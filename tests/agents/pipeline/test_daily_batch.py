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
        order.append(f"produce:{story.canonical_story_id}")
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
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
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    monkeypatch.setattr(
        daily_batch, "_load_has_current_digest", fake_has_current_digest
    )
    # The per-category cap loads the active-user allocations before the gate; this
    # test exercises ordering/gating, so stub those seams (no allocations → the
    # default per-category cap keeps the single gated story).
    monkeypatch.setattr(daily_batch, "_load_active_user_ids", lambda *_a, **_k: ["u1"])
    monkeypatch.setattr(daily_batch, "_load_category_allocation", lambda *_a, **_k: {})
    # M2 (SP4): the observe-only pool-target step also loads followed interest nodes
    # before the gate; this test exercises ordering/gating, so stub that seam too.
    monkeypatch.setattr(
        daily_batch, "_load_interest_nodes_by_user", lambda *_a, **_k: {}
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
    # M2 (SP4): the observe-only shopping list is surfaced on the result for M3.
    # With no allocations + no follows, the default user u1 yields floored "_all"
    # cells — so the list is non-empty (the value is observable, additive only).
    assert result.pool_target, "pool_target must be surfaced on the batch result"


class _FakeQuery:
    """Chainable Supabase query stub: every builder method returns self; execute
    returns the seeded rows and (optionally) bumps a call counter."""

    def __init__(self, data: list[dict], on_execute=None) -> None:
        self._data = data
        self._on_execute = on_execute

    def select(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def lt(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        if self._on_execute is not None:
            self._on_execute()
        return SimpleNamespace(data=self._data)


def test_load_active_user_inputs_batches_prior_feeds_in_one_query() -> None:
    """D2/N+1: prior daily_feeds for ALL users must load in ONE query, grouped in
    memory — not one query per user. The old per-user loop was O(users) round-trips
    (~100 at 100 users); this asserts it's now O(1)."""
    profile_rows = [
        {
            "profile_user_id": uid,
            "profile_interest_id": "int-a",
            "profile_weight": 1.0,
            "profile_is_strict": False,
        }
        for uid in ("u1", "u2", "u3")
    ]
    prior_rows = [
        {"feed_user_id": "u1", "feed_story_id": "s-old-1"},
        {"feed_user_id": "u3", "feed_story_id": "s-old-3"},
    ]
    daily_feed_query_count: list[int] = []

    class _Client:
        def table(self, name: str):
            if name == "user_interest_profile":
                return _FakeQuery(profile_rows)
            if name == "daily_feeds":
                return _FakeQuery(
                    prior_rows, on_execute=lambda: daily_feed_query_count.append(1)
                )
            return _FakeQuery([])

    inputs = daily_batch.load_active_user_inputs(_Client(), date(2026, 6, 1))

    # Exactly ONE daily_feeds query for all 3 users (the N+1 is gone).
    assert len(daily_feed_query_count) == 1
    by_user = {i.active_user_id: i.prior_feed_story_ids for i in inputs}
    assert by_user["u1"] == ["s-old-1"]
    assert by_user["u3"] == ["s-old-3"]
    assert by_user["u2"] == []  # a user with no prior feed gets an empty exclusion


def test_story_id_resolver_queries_aliases_and_maps_urls() -> None:
    """The cross-day resolver returns {normalized_url: existing_story_id} from one
    story_url_aliases lookup (the seam ingest injects)."""
    alias_rows = [
        {"alias_normalized_url": "https://bbc.com/x", "alias_story_id": "story-7"},
    ]

    class _Client:
        def table(self, name: str):
            assert name == "story_url_aliases"
            return _FakeQuery(alias_rows)

    resolve = daily_batch.build_story_id_resolver(_Client())
    assert resolve([]) == {}  # empty input short-circuits (no query needed)
    assert resolve(["https://bbc.com/x", "https://unknown.com/y"]) == {
        "https://bbc.com/x": "story-7"
    }


@pytest.mark.asyncio
async def test_produce_pool_passes_canonical_id_as_story_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Produce-once identity contract (D2 regression guard): the batch MUST pass
    ``story_id == canonical_story_id`` into orchestrate_story, because persist
    writes ``digests.digest_story_id = story_id`` and the produce-once gate looks
    up by ``canonical_story_id``. If story_id were left to default (the 'sp3-'
    prefix), the gate would NEVER match and every story would re-produce daily."""
    story = _story("cand-abc123")
    captured: dict = {}

    async def fake_write(story, *, story_id, **_k):
        captured["story_id"] = story_id
        captured["canonical_story_id"] = story.canonical_story_id
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
        return SimpleNamespace(published=True)

    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)

    produced = await daily_batch._produce_story_pool(
        stories_to_produce=[story],
        story_interest_tags=[],
        llm_client=object(),
        tts_client=object(),
        supabase_client=object(),
        poster_genai_client=None,
        max_concurrent=2,
    )

    assert len(produced) == 1
    assert captured["story_id"] == captured["canonical_story_id"] == "cand-abc123"


@pytest.mark.asyncio
async def test_produce_pool_forwards_detail_enrichment_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: the batch must thread the Phase 2c enrichment flag + lookups through to
    the RENDER phase, so the pipeline is enrichment-capable (it defaulted OFF and
    the lookups were never passed before)."""
    captured: dict = {}

    async def fake_write(story, **_k):
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(published=True)

    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    segment_lookup = {"int-a": "geopolitics"}
    outlets_lookup = {"cnn.com": "left"}
    adapter = object()

    await daily_batch._produce_story_pool(
        stories_to_produce=[_story("cand-1")],
        story_interest_tags=[],
        llm_client=object(),
        tts_client=object(),
        supabase_client=object(),
        poster_genai_client=None,
        max_concurrent=2,
        enable_detail_enrichment=True,
        interest_segment_lookup=segment_lookup,
        outlets_lookup=outlets_lookup,
        gdelt_adapter=adapter,
    )

    assert captured["enable_detail_enrichment"] is True
    assert captured["interest_segment_lookup"] == segment_lookup
    assert captured["outlets_lookup"] == outlets_lookup
    assert captured["gdelt_adapter"] is adapter


@pytest.mark.asyncio
async def test_produce_pool_runs_write_then_review_barrier_then_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Layer 3 contract: the pool writes ALL reels, then (only when enabled) runs the
    pool-level review barrier ONCE, then renders. The barrier must sit between the
    two waves — running it before all writes finish, or per-story, would defeat the
    cross-reel diversity it exists for."""
    order: list[str] = []

    async def fake_write(story, **_k):
        order.append(f"write:{story.canonical_story_id}")
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
        order.append(f"render:{write_result.canonical_story_id}")
        return SimpleNamespace(published=True)

    async def fake_review(survivors, _llm, **_k):
        order.append(f"review:{len(survivors)}")
        return survivors

    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    monkeypatch.setattr(daily_batch, "review_reel_pool", fake_review)

    produced = await daily_batch._produce_story_pool(
        stories_to_produce=[_story("a"), _story("b")],
        story_interest_tags=[],
        llm_client=object(),
        tts_client=object(),
        supabase_client=object(),
        poster_genai_client=None,
        max_concurrent=2,
        enable_batch_review=True,
    )

    assert len(produced) == 2
    # The single review barrier sees BOTH survivors, AFTER every write, BEFORE any render.
    assert "review:2" in order
    write_indices = [i for i, step in enumerate(order) if step.startswith("write:")]
    render_indices = [i for i, step in enumerate(order) if step.startswith("render:")]
    review_index = order.index("review:2")
    assert max(write_indices) < review_index < min(render_indices)


@pytest.mark.asyncio
async def test_produce_pool_skips_review_barrier_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-off rollout: with enable_batch_review False the barrier never runs, so
    the legacy produce path is byte-for-byte unchanged."""
    called: list[int] = []

    async def fake_write(story, **_k):
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
        return SimpleNamespace(published=True)

    async def fake_review(survivors, _llm, **_k):
        called.append(len(survivors))
        return survivors

    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    monkeypatch.setattr(daily_batch, "review_reel_pool", fake_review)

    produced = await daily_batch._produce_story_pool(
        stories_to_produce=[_story("a"), _story("b")],
        story_interest_tags=[],
        llm_client=object(),
        tts_client=object(),
        supabase_client=object(),
        poster_genai_client=None,
        max_concurrent=2,
        enable_batch_review=False,
    )

    assert len(produced) == 2
    assert called == []  # review pass never invoked
