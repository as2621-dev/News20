"""Tests for the worker daily-run's spend-safety + relevance wiring (#66, #65).

WHY (Rule 9 — encode the contract, not the call shape):
  • #68: the halt LADDER (shortlist -> scripts -> armed reels, founder decision
    2026-07-25) must resolve identically on the worker. The load-bearing assertion
    is that SHORTLIST_ONLY=0 alone lands on SCRIPTS, never on reels: a cron fire
    may not record media without the explicit per-run PRODUCE_REELS arm.
  • #66: the founder's shortlist-first rule (2026-07-19) was enforced ONLY in
    ``scripts/run_live_batch.py``. The deployed worker called
    ``run_daily_pipeline`` with no ``shortlist_only``, so the library default
    (``False``) meant every Railway cron fire PRODUCED reels/TTS/posters and wrote
    ``daily_feeds`` — with no env var anywhere that could stop it. These tests fail
    the moment the flag stops reaching the pipeline, or its default stops being
    "halt".
  • #65: the worker passed NEITHER ``llm_client`` NOR
    ``enable_semantic_relevance_key`` to ``ingest_active_interests`` (both default
    off, and the semantic key is additionally guarded on ``llm_client is not
    None``), so the two-key relevance lock was dead in production and the RC3
    false positives kept reaching real feeds. The forwarding assertions CANNOT
    pass if either argument is dropped again.

What these tests do NOT re-prove: that ``shortlist_only=True`` actually skips the
paid phases and writes no ``daily_feeds``. That is the LIBRARY's contract and is
covered by the parametrized flip in ``tests/agents/pipeline/test_shortlist.py``.
Here we prove the worker hands the library the right value — the seam that was
broken.

Every boundary is mocked: no Supabase, no BigQuery, no Gemini, no live batch.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from agents.ingestion import interest_keyed_pipeline
from agents.ingestion.models import (
    SEMANTIC_RELEVANCE_MODE_DEGRADED,
    SEMANTIC_RELEVANCE_MODE_SEMANTIC,
    IngestionResult,
    SemanticRelevanceRunStamp,
)
from agents.pipeline import daily_batch, llm_clients, persist_helpers, poster_gate
from agents.pipeline.production_selection import ProductionSelectionPlan
from agents.pipeline.scripts_artifact import ScriptEntry
from agents.pipeline.shortlist import ShortlistEntry
from agents.voice import gemini_tts
from agents.worker import pipeline_routes

_TARGET_DATE = date(2026, 7, 25)


class _FakeQuery:
    """Minimal PostgREST chain: ``.table(t).select(cols).execute().data``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_a: Any, **_k: Any) -> "_FakeQuery":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def table(self, table_name: str) -> _FakeQuery:
        if table_name == "interests":
            return _FakeQuery(
                [
                    {
                        "interest_id": "i-ai",
                        "parent_interest_id": None,
                        "interest_slug": "ai",
                        "interest_label": "AI",
                        "depth_level": 0,
                        "interest_segment_slug": "ai",
                        "interest_search_query": "artificial intelligence",
                    }
                ]
            )
        if table_name == "user_interest_profile":
            return _FakeQuery([{"profile_user_id": "u1", "profile_interest_id": "i-ai"}])
        return _FakeQuery([])


def _stamp(mode: str) -> SemanticRelevanceRunStamp:
    return SemanticRelevanceRunStamp(
        semantic_relevance_mode=mode,
        semantic_relevance_stories_checked=3,
        semantic_relevance_interests_checked=1,
    )


def _pipeline_result(
    *,
    shortlist: list[ShortlistEntry],
    scripts: list[ScriptEntry],
    stamp: SemanticRelevanceRunStamp,
) -> SimpleNamespace:
    """The subset of ``DailyPipelineResult`` the worker body reads back."""
    halted = bool(shortlist or scripts)
    return SimpleNamespace(
        feed_date=_TARGET_DATE.isoformat(),
        produced_story_count=0 if halted else 4,
        feeds=None if halted else SimpleNamespace(feeds_written=30),
        shortlist=shortlist,
        scripts=scripts,
        script_dedup_drops=[],
        script_dedup_enabled=True,
        semantic_relevance=stamp,
        # Issue #74: the worker's completion log reads the pre-production cut back,
        # so the double must carry it — a run that produced 4 reels selected 4.
        selection=ProductionSelectionPlan(
            selection_production_story_ids=[] if halted else [f"s{i}" for i in range(4)]
        ),
        promoted_story_count=0,
    )


@pytest.fixture(autouse=True)
def _unarmed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reason: the reel arm is an OPT-IN flag — a value inherited from the host shell
    would let these tests pass while the deployed default silently spends."""
    monkeypatch.delenv("PRODUCE_REELS", raising=False)
    monkeypatch.delenv("SCRIPTS_ONLY", raising=False)


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch every boundary ``_run_daily`` reaches and record the forwarded kwargs.

    The fake ``run_daily_pipeline`` AWAITS the worker's ``ingest_fn`` — that is what
    makes the #65 forwarding assertions real: the ingest kwargs are only recorded
    because the pipeline actually invoked the closure the worker built.
    """
    recorded: dict[str, Any] = {
        "ingest_kwargs": {},
        "pipeline_kwargs": {},
        "ingest_return": None,
        "logs": [],
    }
    ingest_stamp = _stamp(SEMANTIC_RELEVANCE_MODE_SEMANTIC)
    recorded["ingest_stamp"] = ingest_stamp
    recorded["shortlist"] = [
        ShortlistEntry(
            shortlist_story_id="s1",
            shortlist_headline="A headline the founder reviews",
            shortlist_primary_outlet="Reuters",
            shortlist_outlet_count=4,
            shortlist_category="ai",
            shortlist_matched_interest_slugs=["ai"],
        )
    ]

    recorded["scripts"] = [
        ScriptEntry(
            script_story_id="s1",
            script_headline="A headline the founder reviews",
            script_segment_slug="ai",
            script_resolved_category="ai",
            script_text="ALEX: Here is the thing.\nJORDAN: And here is why.",
            script_word_count=9,
        )
    ]

    async def fake_ingest(**kwargs: Any) -> IngestionResult:
        recorded["ingest_kwargs"] = kwargs
        return IngestionResult(
            canonical_stories=[],
            story_interest_tags=[],
            semantic_relevance=recorded["ingest_stamp"],
        )

    async def fake_run_daily_pipeline(**kwargs: Any) -> SimpleNamespace:
        recorded["pipeline_kwargs"] = kwargs
        ingested = await kwargs["ingest_fn"]()
        recorded["ingest_return"] = ingested
        # Reason: mirrors the real run_daily_pipeline's optional-third-element
        # contract (issue #67) — a worker ingest_fn that went back to a 2-tuple must
        # surface here as the 'disabled' default, not as the mode it really ran.
        stamp = ingested[2] if len(ingested) > 2 else SemanticRelevanceRunStamp()
        shortlist = recorded["shortlist"] if kwargs.get("shortlist_only") else []
        scripts = recorded["scripts"] if kwargs.get("scripts_only") else []
        return _pipeline_result(shortlist=shortlist, scripts=scripts, stamp=stamp)

    class _SpyLogger:
        def info(self, event: str, **fields: Any) -> None:
            recorded["logs"].append((event, fields))

        def warning(self, event: str, **fields: Any) -> None:
            recorded["logs"].append((event, fields))

        def error(self, event: str, **fields: Any) -> None:
            recorded["logs"].append((event, fields))

    monkeypatch.setattr(
        pipeline_routes, "_build_service_role_supabase", lambda: _FakeSupabase()
    )
    monkeypatch.setattr(pipeline_routes, "logger", _SpyLogger())
    monkeypatch.setattr(interest_keyed_pipeline, "ingest_active_interests", fake_ingest)
    monkeypatch.setattr(daily_batch, "run_daily_pipeline", fake_run_daily_pipeline)
    monkeypatch.setattr(
        daily_batch, "build_story_id_resolver", lambda *_a, **_k: (lambda *_x: None)
    )
    monkeypatch.setattr(persist_helpers, "load_outlets_lookup", lambda *_a, **_k: {})
    monkeypatch.setattr(
        llm_clients, "LLMClient", lambda *_a, **_k: SimpleNamespace(kind="llm")
    )
    monkeypatch.setattr(
        gemini_tts, "GeminiTTSClient", lambda *_a, **_k: SimpleNamespace(kind="tts")
    )
    # Reason: kill posters so the body never constructs a real genai client (which
    # would demand GEMINI_API_KEY) — orthogonal to what these tests assert.
    monkeypatch.setattr(poster_gate, "poster_generation_disabled", lambda: True)

    from agents.ingestion.adapters import gdelt_bigquery, gdelt_doc

    monkeypatch.setattr(
        gdelt_bigquery, "GdeltBigQueryAdapter", lambda *_a, **_k: SimpleNamespace()
    )
    monkeypatch.setattr(gdelt_doc, "GdeltDocAdapter", lambda *_a, **_k: SimpleNamespace())
    return recorded


async def _run(monkeypatch: pytest.MonkeyPatch) -> None:
    await pipeline_routes._run_daily(
        target_date=_TARGET_DATE,
        max_total_productions=8,
        lookback_days=1,
        run_id="test-run",
    )


# ── #66 — shortlist-first reaches the deployed worker ─────────────────────────


@pytest.mark.asyncio
async def test_worker_halts_at_the_shortlist_by_default(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production must be OPT-IN on the worker exactly as it is in the script: a
    Railway cron fire with no SHORTLIST_ONLY set spends zero production credits."""
    monkeypatch.delenv("SHORTLIST_ONLY", raising=False)

    await _run(monkeypatch)

    assert wiring["pipeline_kwargs"]["shortlist_only"] is True


@pytest.mark.asyncio
async def test_worker_shortlist_opt_out_buys_scripts_not_reels(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#68 ladder: approving the shortlist (SHORTLIST_ONLY=0) advances the worker
    exactly ONE rung — to scripts. A cron fire must not start recording reels just
    because the story list was approved; the media stage has its own arm."""
    monkeypatch.setenv("SHORTLIST_ONLY", "0")

    await _run(monkeypatch)

    kwargs = wiring["pipeline_kwargs"]
    assert kwargs["shortlist_only"] is False
    assert kwargs["scripts_only"] is True


@pytest.mark.asyncio
async def test_worker_produces_only_when_the_reel_stage_is_armed(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge (#66 + #68 AC): with BOTH halts opted out the worker produces exactly as
    it does today — the flags are the ONLY difference, every other pipeline argument
    is unchanged (asserted field by field, so a silent regression on the approved
    path fails here)."""
    monkeypatch.setenv("SHORTLIST_ONLY", "0")
    monkeypatch.setenv("PRODUCE_REELS", "1")

    await _run(monkeypatch)

    kwargs = wiring["pipeline_kwargs"]
    assert kwargs["shortlist_only"] is False
    assert kwargs["scripts_only"] is False
    assert kwargs["target_date"] == _TARGET_DATE
    assert kwargs["max_total_productions"] == 8
    assert kwargs["enable_detail_enrichment"] is True
    assert kwargs["enable_editorial_rewrite"] is True
    assert kwargs["enable_batch_review"] is True
    assert kwargs["enable_semantic_clustering"] is True
    assert kwargs["poster_genai_client"] is None
    assert kwargs["llm_client"].kind == "llm"
    assert kwargs["tts_client"].kind == "tts"


@pytest.mark.asyncio
async def test_script_halted_worker_run_emits_the_scripts_for_review(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#68 artifact decision: the worker has no repo ``.agents/`` to write to, so the
    scripts are emitted as structured logs. A script halt that surfaced nothing would
    be a silent no-op run — the founder must be able to read the scripts (and what the
    similarity gate dropped) out of the Railway log before arming the reels."""
    monkeypatch.setenv("SHORTLIST_ONLY", "0")

    await _run(monkeypatch)

    events = {event for event, _ in wiring["logs"]}
    assert "pipeline_daily_scripts_run" in events
    assert "pipeline_daily_scripts_entry" in events
    assert "pipeline_daily_shortlist_entry" not in events
    header = next(f for e, f in wiring["logs"] if e == "pipeline_daily_scripts_run")
    assert header["run_written_script_count"] == 1
    assert header["run_script_dedup_enabled"] is True


@pytest.mark.asyncio
async def test_halted_worker_run_emits_the_shortlist_for_review(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#66 artifact decision: the worker has no repo ``.agents/`` to write to, so
    the review list is emitted as structured logs. A halt that produced nothing AND
    surfaced nothing would be a silent no-op run — the founder must be able to read
    the would-produce list out of the Railway log."""
    monkeypatch.setenv("SHORTLIST_ONLY", "1")

    await _run(monkeypatch)

    events = {event for event, _ in wiring["logs"]}
    assert "pipeline_daily_shortlist_run" in events
    assert "pipeline_daily_shortlist_entry" in events
    header = next(f for e, f in wiring["logs"] if e == "pipeline_daily_shortlist_run")
    assert header["run_shortlist_story_count"] == 1
    assert header["run_semantic_relevance_mode"] == SEMANTIC_RELEVANCE_MODE_SEMANTIC
    entry = next(f for e, f in wiring["logs"] if e == "pipeline_daily_shortlist_entry")
    assert entry["shortlist_headline"] == "A headline the founder reviews"
    assert entry["shortlist_category"] == "ai"


# ── #65 — the semantic half of the two-key lock is armed on the worker ────────


@pytest.mark.asyncio
async def test_worker_arms_both_halves_of_the_relevance_lock(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#65 AC: BOTH arguments must reach ``ingest_active_interests``. The semantic
    key is guarded on ``llm_client is not None`` as well as the flag, so dropping
    EITHER silently reverts production to lexical-only admission (the RC3
    zoning/'data center' false positives). This test cannot pass if either is
    dropped."""
    monkeypatch.delenv("ENABLE_SEMANTIC_RELEVANCE_KEY", raising=False)

    await _run(monkeypatch)

    ingest_kwargs = wiring["ingest_kwargs"]
    assert ingest_kwargs["enable_semantic_relevance_key"] is True
    assert ingest_kwargs["llm_client"] is not None
    # The SAME client instance the pipeline got — one Gemini client per run.
    assert ingest_kwargs["llm_client"] is wiring["pipeline_kwargs"]["llm_client"]


@pytest.mark.asyncio
async def test_worker_semantic_key_is_flippable_without_a_deploy(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure case: ENABLE_SEMANTIC_RELEVANCE_KEY=0 must reach the worker's ingest
    call so a bad embedding day can be de-armed from the Railway dashboard rather
    than by shipping code."""
    monkeypatch.setenv("ENABLE_SEMANTIC_RELEVANCE_KEY", "0")

    await _run(monkeypatch)

    assert wiring["ingest_kwargs"]["enable_semantic_relevance_key"] is False


@pytest.mark.asyncio
async def test_worker_forwards_the_run_mode_stamp_so_an_outage_is_visible(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#65 + #67 composition: an embedding outage degrades ingestion to strict
    lexical. On the worker that fallback is now REACHABLE, so it must also be
    OBSERVABLE — the stamp rides the ingest_fn's third element to the pipeline and
    the run's completion log names the mode. Absence of an error event proves
    nothing (docs/solutions: proving-run-mode-needs-positive-evidence)."""
    monkeypatch.delenv("ENABLE_SEMANTIC_RELEVANCE_KEY", raising=False)
    wiring["ingest_stamp"] = _stamp(SEMANTIC_RELEVANCE_MODE_DEGRADED)

    await _run(monkeypatch)

    assert wiring["ingest_return"][2].semantic_relevance_mode == (
        SEMANTIC_RELEVANCE_MODE_DEGRADED
    )
    completed = next(
        f for e, f in wiring["logs"] if e == "pipeline_daily_run_completed"
    )
    assert completed["semantic_relevance_mode"] == SEMANTIC_RELEVANCE_MODE_DEGRADED


@pytest.mark.asyncio
async def test_healthy_worker_run_stamps_semantic(
    wiring: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path for the #67 composition: with the key armed and embeddings
    healthy the worker's run stamps ``semantic`` — the positive evidence an audit
    needs to distinguish a quality miss from a cheap run."""
    monkeypatch.delenv("ENABLE_SEMANTIC_RELEVANCE_KEY", raising=False)

    await _run(monkeypatch)

    assert len(wiring["ingest_return"]) == 3
    assert wiring["ingest_return"][2].semantic_relevance_mode == (
        SEMANTIC_RELEVANCE_MODE_SEMANTIC
    )
    completed = next(
        f for e, f in wiring["logs"] if e == "pipeline_daily_run_completed"
    )
    assert completed["semantic_relevance_mode"] == SEMANTIC_RELEVANCE_MODE_SEMANTIC
    assert completed["shortlist_only"] is True
