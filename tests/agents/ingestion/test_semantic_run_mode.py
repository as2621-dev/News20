"""Tests for the semantic-relevance RUN MODE stamped onto the ingestion result (#67).

Encodes WHY (Rule 9): a fully-semantic run and an embedding-outage run admit
DIFFERENT stories, so a shortlist that cannot say which mode produced it is
unfalsifiable after the fact — it is what forced the 2026-07-24 M1 audit to say
"consistent with, but not proven". ``apply_semantic_relevance_key`` already
returns the ``fell_back_to_lexical`` signal; the pipeline call site used to
DISCARD it. These tests fail if anyone re-drops the return value.

All three states are asserted separately: an OFF run (flag disabled) must never
be reported as a DEGRADED one — "we skipped the paid gate on purpose" and "the
gate broke" are different audit verdicts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.ingestion.interest_keyed_pipeline import ingest_active_interests
from agents.ingestion.models import (
    SEMANTIC_RELEVANCE_MODE_DEGRADED,
    SEMANTIC_RELEVANCE_MODE_DISABLED,
    SEMANTIC_RELEVANCE_MODE_SEMANTIC,
)
from tests.agents.ingestion.test_interest_keyed_pipeline import _FakeAdapter
from tests.agents.ingestion.test_interest_semantic import _make_embed_mock

_ARSENAL_ID = "int-arsenal"


async def _run_ingestion(interest_nodes, *, adapter=None, **kwargs):
    """One deterministic Arsenal-only ingestion (fake adapter, no network)."""
    return await ingest_active_interests(
        followed_interest_ids=[_ARSENAL_ID],
        interest_nodes=interest_nodes,
        adapter=adapter or _FakeAdapter(),
        extract_bodies=False,
        llm_client=object(),
        **kwargs,
    )


class TestSemanticRunModeStamp:
    """The ingestion result must name which relevance mode actually ran."""

    @pytest.mark.asyncio
    async def test_healthy_run_is_stamped_semantic_with_counts(
        self, interest_nodes
    ) -> None:
        """WHY: a clean semantic run is the only state in which a shortlist's tags
        can be read as the paid gate's verdict — it must say so positively, with the
        cost counts that prove the gate actually looked at something."""
        with patch(
            "agents.ingestion.interest_semantic.embed_texts", _make_embed_mock()
        ):
            result = await _run_ingestion(
                interest_nodes, enable_semantic_relevance_key=True
            )
        assert (
            result.semantic_relevance.semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_SEMANTIC
        )
        assert result.semantic_relevance.semantic_relevance_stories_checked > 0
        assert result.semantic_relevance.semantic_relevance_interests_checked > 0

    @pytest.mark.asyncio
    async def test_embedding_outage_run_is_stamped_degraded(
        self, interest_nodes
    ) -> None:
        """WHY: THE regression guard. On an embedding outage the batch keeps every
        lexically-matched id (strict lexical) — a materially different admission set.
        If the call site goes back to a bare ``await`` that drops the stats, this
        assertion fails, because the mode can only come from the returned value."""
        failing_embed = AsyncMock(side_effect=RuntimeError("embedding API 503"))
        with patch("agents.ingestion.interest_semantic.embed_texts", failing_embed):
            result = await _run_ingestion(
                interest_nodes, enable_semantic_relevance_key=True
            )
        failing_embed.assert_awaited()
        assert (
            result.semantic_relevance.semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_DEGRADED
        )
        assert result.semantic_relevance.semantic_relevance_stories_checked > 0

    @pytest.mark.asyncio
    async def test_disabled_flag_is_not_reported_as_degraded(
        self, interest_nodes
    ) -> None:
        """WHY: the third state. A deliberately lexical-only run (flag off / no LLM
        client) costs nothing and is a known-quality run; collapsing it into DEGRADED
        would cry outage on every cheap run and hide the real ones."""
        embed_mock = _make_embed_mock()
        with patch("agents.ingestion.interest_semantic.embed_texts", embed_mock):
            result = await _run_ingestion(
                interest_nodes, enable_semantic_relevance_key=False
            )
        embed_mock.assert_not_called()
        assert (
            result.semantic_relevance.semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_DISABLED
        )
        assert result.semantic_relevance.semantic_relevance_stories_checked == 0

    @pytest.mark.asyncio
    async def test_empty_pool_with_the_key_on_still_reports_semantic(
        self, interest_nodes
    ) -> None:
        """WHY (edge): the mode reports CONFIGURATION, not just work done. A run whose
        pool came back empty had the gate ON — stamping it 'disabled' would read as a
        deliberately lexical-only run and quietly excuse whatever the next run admits.
        The zero counts, not the mode, are what say 'nothing was checked'."""
        embed_mock = _make_embed_mock()
        with patch("agents.ingestion.interest_semantic.embed_texts", embed_mock):
            result = await _run_ingestion(
                interest_nodes,
                adapter=_FakeAdapter(fail_queries={"Arsenal FC"}),
                enable_semantic_relevance_key=True,
            )
        assert result.canonical_stories == []
        embed_mock.assert_not_called()
        assert (
            result.semantic_relevance.semantic_relevance_mode
            == SEMANTIC_RELEVANCE_MODE_SEMANTIC
        )
        assert result.semantic_relevance.semantic_relevance_stories_checked == 0
