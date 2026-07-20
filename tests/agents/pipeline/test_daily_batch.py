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

from agents.ingestion.models import CanonicalStory, InterestNode
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
    the lookups were never passed before).

    ``interest_segment_lookup`` is the exception: it goes to the WRITE phase only,
    which resolves the segment ONCE onto ``segment_slug``; render must NOT receive it
    (it would re-resolve, the #61 double-resolution bug)."""
    render_captured: dict = {}
    write_captured: dict = {}

    async def fake_write(story, **kwargs):
        write_captured.update(kwargs)
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **kwargs):
        render_captured.update(kwargs)
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

    assert render_captured["enable_detail_enrichment"] is True
    assert render_captured["outlets_lookup"] == outlets_lookup
    assert render_captured["gdelt_adapter"] is adapter
    # The segment lookup is a WRITE input (resolve once), never re-passed to render.
    assert write_captured["interest_segment_lookup"] == segment_lookup
    assert "interest_segment_lookup" not in render_captured


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


def _pipeline_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the non-clustering pipeline seams so a run reaches the gate + assembler."""
    monkeypatch.setattr(
        daily_batch, "run_profile_update_job",
        lambda *_a, **_k: ProfileUpdateResult(users_processed=0, weights_changed=0),
    )
    monkeypatch.setattr(daily_batch, "_load_has_current_digest", lambda *_a, **_k: {})
    monkeypatch.setattr(daily_batch, "_load_active_user_ids", lambda *_a, **_k: ["u1"])
    monkeypatch.setattr(daily_batch, "_load_category_allocation", lambda *_a, **_k: {})
    monkeypatch.setattr(daily_batch, "_load_interest_nodes_by_user", lambda *_a, **_k: {})
    monkeypatch.setattr(
        daily_batch, "load_active_user_inputs",
        lambda *_a, **_k: [ActiveUserFeedInputs(active_user_id="u1")],
    )

    async def fake_write(story, **_k):
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
        return SimpleNamespace(published=True)

    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)


@pytest.mark.asyncio
async def test_semantic_clustering_flag_collapses_pool_before_gate_and_feeds_importance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring contract (the dedup fix): with enable_semantic_clustering True the batch
    reconciles the ingested pool BEFORE the gate — so the gate + assembler see collapsed
    stories, not the raw two-id pool — and the reconciled cluster-importance +
    category-override maps reach the assembler. If this regresses, same-event candidates
    survive to feed assembly and the user sees one event twice (or a cross-category
    merge flips the survivor's category unenforced — the #34 remainder)."""
    _pipeline_seams(monkeypatch)
    raw_pool = [_story("cand-egypt-a"), _story("cand-egypt-b")]
    collapsed = [_story("cand-egypt-a")]  # the two events collapsed to one shared id
    importance_map = {"cand-egypt-a": 0.9}
    override_map = {"cand-egypt-a": "sport"}

    async def fake_ingest():
        return raw_pool, []

    async def fake_reconcile(stories, tags, **_k):
        # Proves the RAW two-id pool is what gets reconciled (pre-gate).
        assert [s.canonical_story_id for s in stories] == ["cand-egypt-a", "cand-egypt-b"]
        return SimpleNamespace(
            reconciled_stories=collapsed,
            reconciled_tags=[],
            cluster_importance_by_story=importance_map,
            category_override_by_story=override_map,
        )

    gate_saw: dict = {}

    def fake_select(stories, _tags, _lookup, **_k):
        gate_saw["ids"] = [s.canonical_story_id for s in stories]
        decisions = [
            SimpleNamespace(story_id=s.canonical_story_id, should_produce=True,
                            importance_score=0.5, freshness_score=0.5)
            for s in stories
        ]
        return list(stories), decisions

    assemble_saw: dict = {}

    def fake_assemble(
        *, target_date, cluster_importance_by_story=None,
        category_override_by_story=None, **_k,
    ):
        assemble_saw["importance"] = cluster_importance_by_story
        assemble_saw["overrides"] = category_override_by_story
        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(), active_user_count=1, feeds_written=1
        )

    monkeypatch.setattr(daily_batch, "reconcile_story_ids_via_clustering", fake_reconcile)
    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)

    await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 5),
        supabase_client=object(),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=fake_ingest,
        interest_nodes={},
        enable_semantic_clustering=True,
    )

    # The gate saw ONE collapsed story, not the two raw candidates.
    assert gate_saw["ids"] == ["cand-egypt-a"]
    # The reconciled importance map was threaded into the assembler.
    assert assemble_saw["importance"] == importance_map
    # #34 remainder: the enforced category pins rode the same plumbing.
    assert assemble_saw["overrides"] == override_map


@pytest.mark.asyncio
async def test_semantic_clustering_disabled_by_default_leaves_pool_and_map_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-off rollout: with the flag unset, reconcile is NEVER called and the
    assembler's cluster-importance map is None (the un-clustered raw-importance fallback)
    — the legacy path is unchanged and costs no Gemini embeddings."""
    _pipeline_seams(monkeypatch)
    reconcile_called: list[int] = []

    async def fake_ingest():
        return [_story("s-1"), _story("s-2")], []

    async def fake_reconcile(*_a, **_k):
        reconcile_called.append(1)
        raise AssertionError("reconcile must not run when the flag is off")

    def fake_select(stories, _tags, _lookup, **_k):
        decisions = [
            SimpleNamespace(story_id=s.canonical_story_id, should_produce=True,
                            importance_score=0.5, freshness_score=0.5)
            for s in stories
        ]
        return list(stories), decisions

    assemble_saw: dict = {}

    def fake_assemble(
        *, target_date, cluster_importance_by_story=None,
        category_override_by_story=None, **_k,
    ):
        assemble_saw["importance"] = cluster_importance_by_story
        assemble_saw["overrides"] = category_override_by_story
        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(), active_user_count=1, feeds_written=1
        )

    monkeypatch.setattr(daily_batch, "reconcile_story_ids_via_clustering", fake_reconcile)
    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)

    await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 5),
        supabase_client=object(),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=fake_ingest,
        interest_nodes={},
    )

    assert reconcile_called == []
    assert assemble_saw["importance"] is None
    # Flag off → no override map either (classification byte-identical to legacy).
    assert assemble_saw["overrides"] is None


@pytest.mark.asyncio
async def test_semantic_clustering_failure_falls_back_to_legacy_pool_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #34 failure edge: a mid-batch embedding/DB failure inside reconcile must
    degrade the WHOLE run to the legacy un-clustered pool — never a half-reconciled
    feed — with a loud structured error (``fix_suggestion``) so the operator sees the
    paid dedup was skipped. If this regresses, one Gemini 5xx kills the entire nightly
    batch instead of costing only the dedup upgrade."""
    from unittest.mock import MagicMock

    _pipeline_seams(monkeypatch)
    raw_pool = [_story("cand-egypt-a"), _story("cand-egypt-b")]

    async def fake_ingest():
        return raw_pool, []

    async def fake_reconcile(*_a, **_k):
        raise RuntimeError("gemini embed 500 on batch 2 of 3")

    gate_saw: dict = {}

    def fake_select(stories, _tags, _lookup, **_k):
        gate_saw["ids"] = [s.canonical_story_id for s in stories]
        decisions = [
            SimpleNamespace(story_id=s.canonical_story_id, should_produce=True,
                            importance_score=0.5, freshness_score=0.5)
            for s in stories
        ]
        return list(stories), decisions

    assemble_saw: dict = {}

    def fake_assemble(*, target_date, cluster_importance_by_story=None, **_k):
        assemble_saw["importance"] = cluster_importance_by_story
        return DailyFeedsBatchResult(
            feed_date=target_date.isoformat(), active_user_count=1, feeds_written=1
        )

    fake_logger = MagicMock()
    monkeypatch.setattr(daily_batch, "reconcile_story_ids_via_clustering", fake_reconcile)
    monkeypatch.setattr(daily_batch, "select_stories_to_produce", fake_select)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)
    monkeypatch.setattr(daily_batch, "logger", fake_logger)

    await daily_batch.run_daily_pipeline(
        target_date=date(2026, 7, 5),
        supabase_client=object(),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=fake_ingest,
        interest_nodes={},
        enable_semantic_clustering=True,
    )

    # The run COMPLETED on the untouched legacy pool (both raw ids reach the gate)…
    assert gate_saw["ids"] == ["cand-egypt-a", "cand-egypt-b"]
    # …with the un-clustered raw-importance fallback (no half-reconciled map).
    assert assemble_saw["importance"] is None
    # …and the fallback was LOUD: structured error with a fix_suggestion.
    fallback_calls = [
        call for call in fake_logger.error.call_args_list
        if call.args and call.args[0] == "semantic_reconcile_failed_run_fallback"
    ]
    assert len(fallback_calls) == 1
    assert "fix_suggestion" in fallback_calls[0].kwargs


# ── X theme-of-the-day production wiring (slice #31) ─────────────────────────────


class _RoutedTableQuery:
    """Chainable query stub that applies eq/neq/in_/lt filters and records inserts."""

    def __init__(self, table_name: str, store: dict[str, list[dict]]) -> None:
        self._table_name = table_name
        self._store = store
        self._eq: list[tuple[str, object]] = []
        self._neq: list[tuple[str, object]] = []
        self._in: list[tuple[str, set[str]]] = []
        self._lt: list[tuple[str, str]] = []
        self._pending_insert: list[dict] | None = None

    def select(self, *_a, **_k) -> "_RoutedTableQuery":
        return self

    def eq(self, column: str, value: object) -> "_RoutedTableQuery":
        self._eq.append((column, value))
        return self

    def neq(self, column: str, value: object) -> "_RoutedTableQuery":
        self._neq.append((column, value))
        return self

    def in_(self, column: str, values: list) -> "_RoutedTableQuery":
        self._in.append((column, {str(v) for v in values}))
        return self

    def lt(self, column: str, value: str) -> "_RoutedTableQuery":
        self._lt.append((column, str(value)))
        return self

    def limit(self, *_a, **_k) -> "_RoutedTableQuery":
        return self

    def insert(self, rows: list[dict]) -> "_RoutedTableQuery":
        self._pending_insert = rows
        return self

    def execute(self) -> SimpleNamespace:
        rows = self._store.setdefault(self._table_name, [])
        if self._pending_insert is not None:
            rows.extend(self._pending_insert)
            return SimpleNamespace(data=list(self._pending_insert))
        out = list(rows)
        for column, value in self._eq:
            out = [r for r in out if str(r.get(column)) == str(value)]
        for column, value in self._neq:
            out = [r for r in out if str(r.get(column)) != str(value)]
        for column, values in self._in:
            out = [r for r in out if str(r.get(column)) in values]
        for column, value in self._lt:
            out = [r for r in out if str(r.get(column)) < value]
        return SimpleNamespace(data=out)


class _RoutedTableClient:
    def __init__(self, store: dict[str, list[dict]]) -> None:
        self.store = store

    def table(self, name: str) -> _RoutedTableQuery:
        return _RoutedTableQuery(name, self.store)


_X_TARGET_DATE = date(2026, 7, 7)


def _x_theme_store() -> dict[str, list[dict]]:
    """Production-shaped fixture rows: u1 follows an X cluster with 1 theme today."""
    return {
        "user_interest_profile": [
            {
                "profile_user_id": "u1",
                "profile_interest_id": "ipl",
                "profile_weight": 3.0,
                "profile_is_strict": False,
            }
        ],
        "user_feed_allocation": [
            {
                "follow_user_id": "u1",
                "allocation_category": "x",
                "allocation_interest_id": None,
                "allocation_section_label": None,
                "allocation_slot_count": 1,
                "allocation_sort_order": 0,
            },
            {
                "follow_user_id": "u1",
                "allocation_category": "sport",
                "allocation_interest_id": "ipl",
                "allocation_section_label": "IPL",
                "allocation_slot_count": 1,
                "allocation_sort_order": 1,
            },
        ],
        "user_entity_follows": [],
        "user_mute_terms": [],
        "daily_feeds": [],
        "digests": [],
        "user_content_sources": [
            {"user_id": "u1", "source_id": "src-1", "source_priority": "normal"}
        ],
        "content_sources": [
            {"source_id": "src-1", "content_source_type": "x_account"}
        ],
        "source_cluster_members": [{"cluster_id": "clu-1", "source_id": "src-1"}],
        "source_clusters": [{"cluster_id": "clu-1", "cluster_label": "AI Insiders"}],
        "x_cluster_sweeps": [
            {
                "cluster_id": "clu-1",
                "sweep_date": _X_TARGET_DATE.isoformat(),
                "handle_count": 3,
                "raw_post_count": 10,
                "original_post_count": 8,
                "themes": [
                    {
                        "theme_summary": "Everyone reacts to the AGI paper",
                        "supporting_handles": ["alice", "bob"],
                        "supporting_tweet_urls": [
                            "https://x.com/alice/status/1",
                            "https://x.com/bob/status/2",
                        ],
                    }
                ],
            }
        ],
    }


_X_INTEREST_NODES = {
    "sport": InterestNode(
        interest_id="sport",
        parent_interest_id=None,
        interest_slug="sport",
        interest_label="Sport",
        depth_level=0,
    ),
    "cricket": InterestNode(
        interest_id="cricket",
        parent_interest_id="sport",
        interest_slug="sport.cricket",
        interest_label="Cricket",
        depth_level=1,
    ),
    "ipl": InterestNode(
        interest_id="ipl",
        parent_interest_id="cricket",
        interest_slug="sport.cricket.ipl",
        interest_label="IPL",
        depth_level=2,
    ),
}


def _x_theme_pipeline_stubs(
    monkeypatch: pytest.MonkeyPatch,
    store: dict[str, list[dict]],
    theme_publishes: bool = True,
) -> list[str]:
    """Stub ONLY the paid/external seams (profile job, write/render); return the
    produced-story-id call log. The gather, gate, merge, FK guard, assembly, and
    writer all run REAL."""
    produced: list[str] = []

    def fake_profile_update(*_a, **_k) -> ProfileUpdateResult:
        return ProfileUpdateResult()

    async def fake_write(story, **_k):
        produced.append(story.canonical_story_id)
        return SimpleNamespace(
            canonical_story_id=story.canonical_story_id, original_story=story
        )

    async def fake_render(write_result, *_a, **_k):
        is_theme = write_result.canonical_story_id.startswith("xtheme-")
        if is_theme and not theme_publishes:
            return SimpleNamespace(published=False)
        # Mimic render persistence: a published story carries a current digest.
        store.setdefault("digests", []).append(
            {
                "digest_story_id": write_result.canonical_story_id,
                "digest_is_current": True,
            }
        )
        return SimpleNamespace(published=True)

    monkeypatch.setattr(daily_batch, "run_profile_update_job", fake_profile_update)
    monkeypatch.setattr(daily_batch, "write_phase", fake_write)
    monkeypatch.setattr(daily_batch, "render_phase", fake_render)
    return produced


async def _empty_ingest():
    return [], []


async def _no_screenshot(_tweet_url: str) -> None:
    return None


@pytest.mark.asyncio
async def test_x_theme_reels_flow_end_to_end_into_daily_feeds_with_rung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 (end-to-end): the REAL run_daily_pipeline path — followed-cluster join →
    today's sweep themes → theme reel produced IN THIS RUN → honest ladder → a
    daily_feeds row stamped with the rung + attribution.

    WHY: #24 proved the assembly seam; this proves the PRODUCTION wiring feeds it —
    only external seams (profile job, paid write/render, the tweet screenshot) are
    stubbed; the gather, produce merge, FK guard, assembler, and writer are real.
    """
    store = _x_theme_store()
    produced = _x_theme_pipeline_stubs(monkeypatch, store)

    result = await daily_batch.run_daily_pipeline(
        target_date=_X_TARGET_DATE,
        supabase_client=_RoutedTableClient(store),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=_empty_ingest,
        interest_nodes=_X_INTEREST_NODES,
        enable_produce_dedup=False,
        enable_x_theme_reels=True,
        tweet_screenshot_renderer=_no_screenshot,
    )

    # The theme reel was produced IN THIS RUN (daily_feeds fills same-run only).
    assert any(sid.startswith("xtheme-clu-1-") for sid in produced)
    assert result.feeds is not None and result.feeds.feeds_written == 1
    theme_rows = [
        r for r in store["daily_feeds"] if r["feed_x_theme_rung"] is not None
    ]
    assert len(theme_rows) == 1
    assert theme_rows[0]["feed_x_theme_rung"] == "theme"
    assert theme_rows[0]["feed_story_id"].startswith("xtheme-clu-1-")
    assert theme_rows[0]["feed_x_theme_attribution"]["supporting_handles"] == [
        "alice",
        "bob",
    ]


@pytest.mark.asyncio
async def test_x_theme_rerun_same_day_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3 (idempotency): re-running the day re-produces NOTHING and duplicates no
    theme reel — the deterministic story id hits the current-digest skip, and
    write_daily_feed's produce-once guard leaves daily_feeds untouched.
    """
    store = _x_theme_store()
    produced = _x_theme_pipeline_stubs(monkeypatch, store)

    common_kwargs = dict(
        target_date=_X_TARGET_DATE,
        supabase_client=_RoutedTableClient(store),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=_empty_ingest,
        interest_nodes=_X_INTEREST_NODES,
        enable_produce_dedup=False,
        enable_x_theme_reels=True,
        tweet_screenshot_renderer=_no_screenshot,
    )
    first = await daily_batch.run_daily_pipeline(**common_kwargs)
    rows_after_first = list(store["daily_feeds"])
    second = await daily_batch.run_daily_pipeline(**common_kwargs)

    assert first.feeds is not None and first.feeds.feeds_written == 1
    # Second run: the theme reel was NOT re-produced (digest gate) …
    theme_produce_calls = [s for s in produced if s.startswith("xtheme-")]
    assert len(theme_produce_calls) == 1
    # … and no daily_feeds row was duplicated (produce-once per user/date).
    assert store["daily_feeds"] == rows_after_first
    assert second.feeds is not None and second.feeds.users_skipped_idempotent == 1


@pytest.mark.asyncio
async def test_x_theme_unpersisted_candidate_dropped_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (R3 FK guard): a theme reel whose render does NOT publish never reaches
    daily_feeds — the candidate is dropped BEFORE assembly, the batch completes, and
    no row references the unpersisted story id (which would fail the FK insert).
    """
    store = _x_theme_store()
    _x_theme_pipeline_stubs(monkeypatch, store, theme_publishes=False)

    result = await daily_batch.run_daily_pipeline(
        target_date=_X_TARGET_DATE,
        supabase_client=_RoutedTableClient(store),
        llm_client=object(),
        tts_client=object(),
        ingest_fn=_empty_ingest,
        interest_nodes=_X_INTEREST_NODES,
        enable_produce_dedup=False,
        enable_x_theme_reels=True,
        tweet_screenshot_renderer=_no_screenshot,
    )

    # The batch survived; nothing referencing the unpersisted reel was written.
    assert result.feeds is not None
    assert all(
        not str(r["feed_story_id"]).startswith("xtheme-")
        for r in store["daily_feeds"]
    )


@pytest.mark.asyncio
async def test_x_theme_gate_off_by_default_and_gather_failure_degrades_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate + degrade: with the flag OFF the gather NEVER runs and assembly sees
    ``None`` (legacy x fill); with the flag ON a gather failure degrades the run to
    ``None`` LOUDLY instead of killing the nightly batch (Rule 12).
    """
    store = _x_theme_store()
    _x_theme_pipeline_stubs(monkeypatch, store)
    gather_calls: list[int] = []
    assemble_saw: dict[str, object] = {}

    async def exploding_gather(*_a, **_k):
        gather_calls.append(1)
        raise RuntimeError("sweeps table unavailable")

    def fake_assemble(*, target_date, x_theme_candidates_by_user=None, **_k):
        assemble_saw["candidates"] = x_theme_candidates_by_user
        return DailyFeedsBatchResult(feed_date=target_date.isoformat())

    monkeypatch.setattr(daily_batch, "gather_x_theme_candidates", exploding_gather)
    monkeypatch.setattr(daily_batch, "assemble_daily_feeds", fake_assemble)

    common_kwargs = dict(
        target_date=_X_TARGET_DATE,
        llm_client=object(),
        tts_client=object(),
        ingest_fn=_empty_ingest,
        interest_nodes=_X_INTEREST_NODES,
        enable_produce_dedup=False,
    )
    # Flag OFF (the default): the gather must never run; assembly sees None.
    await daily_batch.run_daily_pipeline(
        supabase_client=_RoutedTableClient(store), **common_kwargs
    )
    assert gather_calls == []
    assert assemble_saw["candidates"] is None

    # Flag ON + gather failure: the run completes on the legacy fill (None), loudly.
    await daily_batch.run_daily_pipeline(
        supabase_client=_RoutedTableClient(store),
        enable_x_theme_reels=True,
        **common_kwargs,
    )
    assert gather_calls == [1]
    assert assemble_saw["candidates"] is None
