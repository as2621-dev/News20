"""Unit tests for the grounded detail-enrichment stage (Phase 2c SP3).

DoD (phase file SP3): the LLM-drafted Detail payload is (a) structurally correct —
5 key points, an in-order timeline, a segment-correct ``SecondAnalytic`` — and
(b) NUMBER-SAFE — a market figure the source does not support is dropped to
direction-only and ``analytic_is_grounded`` is set False. The LLM is mocked at the
client boundary (no live call).

These tests encode WHY (Rule 9/12): the trust-critical invariant is that a
fabricated market number must NEVER publish as a fact. So the ungrounded-"+4%"
test fails if the number survives OR if ``analytic_is_grounded`` stays True; and
the segment→kind tests fail if the wrong analytic kind is emitted — not merely
"a function ran".

    >>> pytest tests/agents/pipeline/test_detail_enrichment.py -v
"""

from __future__ import annotations

import json

import pytest

from agents.pipeline.stages.detail_enrichment import (
    run_detail_enrichment,
    select_analytic_kind,
)
from agents.shared.exceptions import PipelineStageError

# The single source body the enrichment grounds against. Every grounded number a
# test asserts must appear here verbatim (digit-stream membership): "20" (Hormuz
# share), "81.6" (trade value), "1.2" (FX). A drafted "+4%" is deliberately ABSENT
# so the grounding gate must drop it.
_SOURCE_BODY = (
    "Iran threatened to close the Strait of Hormuz on Monday after fresh strikes. "
    "Roughly 20% of global seaborne oil transits the strait each day. "
    "Regional trade through the chokepoint was valued at $81.6 billion last year. "
    "The euro slipped to 1.20 against the dollar as the news broke. "
    "Shipping insurers met on Tuesday to reassess war-risk premiums."
)


def _enrichment_payload(
    *,
    analytic_rows: list[dict] | None = None,
    key_points: list[str] | None = None,
    key_figure: dict | None = None,
    timeline: list[dict] | None = None,
) -> str:
    """Build a detail-enrichment LLM JSON response with sensible defaults."""
    payload = {
        "key_figure": key_figure
        if key_figure is not None
        else {
            "key_figure_value": "20%",
            "key_figure_label": "of global oil transits Hormuz",
        },
        "timeline": timeline
        if timeline is not None
        else [
            {
                "timeline_when_label": "Mon",
                "timeline_what_text": "Iran threatened to close Hormuz.",
            },
            {
                "timeline_when_label": "Tue",
                "timeline_what_text": "Shipping insurers met to reassess premiums.",
            },
        ],
        "second_analytic": {
            "analytic_headline": "Oil routes twitch on the Hormuz threat",
            "analytic_summary_text": "A closure would choke a fifth of seaborne crude.",
            "analytic_rows": analytic_rows
            if analytic_rows is not None
            else [
                {
                    "analytic_row_label": "Seaborne oil via Hormuz",
                    "analytic_row_value": "20%",
                    "analytic_row_direction": "flat",
                    "analytic_row_note": None,
                },
                {
                    "analytic_row_label": "EUR/USD",
                    "analytic_row_value": "1.20",
                    "analytic_row_direction": "down",
                    "analytic_row_note": None,
                },
            ],
        },
        "key_points": key_points
        if key_points is not None
        else [
            "Iran threatened to close the Strait of Hormuz.",
            "About 20% of seaborne oil passes through the strait.",
            "Regional trade there was worth $81.6 billion last year.",
            "The euro slipped to 1.20 on the news.",
            "Insurers are reassessing war-risk premiums.",
        ],
    }
    return json.dumps(payload)


@pytest.fixture
def geopolitics_story(canonical_story):
    """The shared canonical story re-bodied as a geopolitics (Hormuz) story."""
    return canonical_story.model_copy(
        update={
            "canonical_title": "Iran threatens to close the Strait of Hormuz",
            "canonical_body_text": _SOURCE_BODY,
        }
    )


@pytest.fixture
def digest_script(canonical_story):
    """A minimal DigestScript carrying the story id (script body is not the corpus)."""
    from agents.pipeline.models import DialogueTurn, DigestScript

    return DigestScript(
        digest_story_id=canonical_story.canonical_story_id,
        turns=[DialogueTurn(speaker="ALEX", text="What just happened with Hormuz?")],
    )


class TestSelectAnalyticKindPure:
    """The segment→kind map is a PURE deterministic function (Decision #2 / Rule 5)."""

    @pytest.mark.parametrize(
        ("segment_slug", "expected_kind"),
        [
            # 2026-06-12 product decision: MARKET IMPACT only for markets + tech;
            # geopolitics/sport/wildcard get the subject PROFILE.
            ("geopolitics", "subject_profile"),
            ("markets", "market_impact"),
            ("tech", "market_impact"),
            ("sport", "subject_profile"),
            ("wildcard", "subject_profile"),
        ],
    )
    def test_happy_path_every_segment_maps(self, segment_slug, expected_kind) -> None:
        """Each known segment maps to exactly its specified analytic kind."""
        assert select_analytic_kind(segment_slug) == expected_kind

    def test_edge_unknown_segment_falls_back_to_why_it_matters(self) -> None:
        """An unknown / future segment falls back to the always-safe why_it_matters.

        WHY: a kind that needs domain figures (market_impact) must never be chosen
        for a segment we cannot vouch for — the safe fallback asserts no number.
        """
        assert select_analytic_kind("health.policy") == "why_it_matters"
        assert select_analytic_kind("") == "why_it_matters"

    def test_subject_profile_instruction_declares_background_exemption(self) -> None:
        """The PROFILE prompt instruction exists and scopes the background exemption.

        WHY: the grounding gate exempts rows noted 'background' ONLY because the
        prompt instructs the model to mark general-knowledge facts that way — if
        the instruction loses that contract, the exemption becomes unsound.
        """
        from agents.pipeline.prompts import DETAIL_ANALYTIC_INSTRUCTIONS

        instruction = DETAIL_ANALYTIC_INSTRUCTIONS["subject_profile"]
        assert "PROFILE" in instruction
        assert "background" in instruction
        assert "SINGLE-SOURCE" in instruction


class TestGroundedHappyPath:
    """A clean grounded payload yields a fully-structured enrichment."""

    @pytest.mark.asyncio
    async def test_clean_payload_structure_and_segment_kind(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """5 key points, an in-order timeline, and the geopolitics→subject_profile kind.

        WHY: this is the structural contract SP4 persists. The analytic_kind MUST
        match the segment map (geopolitics→subject_profile, 2026-06-12 remap) — a
        wrong kind would skin the Detail tab incorrectly.
        """
        client = make_llm_client(_enrichment_payload())
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="geopolitics",
        )

        # Exactly 5 key points, contiguous 0-based order.
        assert len(enrichment.key_points) == 5
        assert [p.key_point_index for p in enrichment.key_points] == [0, 1, 2, 3, 4]

        # Timeline present and IN ORDER (0-based contiguous indices, earliest first).
        assert len(enrichment.timeline) >= 1
        assert [e.timeline_event_index for e in enrichment.timeline] == list(
            range(len(enrichment.timeline))
        )
        assert enrichment.timeline[0].timeline_when_label == "Mon"

        # Segment-correct analytic kind + label.
        assert enrichment.second_analytic.analytic_kind == "subject_profile"
        assert enrichment.second_analytic.analytic_tab_label == "PROFILE"

        # All drafted numbers ("20%", "1.20") are in the source → grounded.
        assert enrichment.second_analytic.analytic_is_grounded is True
        assert enrichment.key_figure.key_figure_value == "20%"

    @pytest.mark.asyncio
    async def test_markets_segment_yields_market_impact_kind(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """A markets story gets the market_impact kind (segment map, not the LLM)."""
        client = make_llm_client(_enrichment_payload())
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="markets",
        )
        assert enrichment.second_analytic.analytic_kind == "market_impact"
        assert enrichment.second_analytic.analytic_tab_label == "MARKET IMPACT"


class TestNumberGroundingGate:
    """Trust-critical: an ungrounded number NEVER publishes as a fact (Decision #5)."""

    @pytest.mark.asyncio
    async def test_unsupported_number_dropped_and_marked_ungrounded(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """A drafted "+4%" absent from the source is dropped → ungrounded.

        WHY (Rule 9/12): "+4%" is the canonical fabricated-market-number case. The
        source never says 4% — so the value MUST be stripped to direction-only AND
        ``analytic_is_grounded`` MUST flip to False. A regression that published the
        number, or left grounded=True, fails here.
        """
        client = make_llm_client(
            _enrichment_payload(
                analytic_rows=[
                    {
                        "analytic_row_label": "Seaborne oil via Hormuz",
                        "analytic_row_value": "20%",
                        "analytic_row_direction": "flat",
                        "analytic_row_note": None,
                    },
                    {
                        "analytic_row_label": "Brent crude",
                        "analytic_row_value": "+4%",
                        "analytic_row_direction": "up",
                        "analytic_row_note": None,
                    },
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="geopolitics",
        )

        rows = {
            r.analytic_row_label: r for r in enrichment.second_analytic.analytic_rows
        }
        brent = rows["Brent crude"]
        # The fabricated number is gone; the direction survives (direction-only).
        assert brent.analytic_row_value is None
        assert brent.analytic_row_direction == "up"
        # The grounded "20%" row keeps its value.
        assert rows["Seaborne oil via Hormuz"].analytic_row_value == "20%"
        # The whole analytic is flagged ungrounded — never published as fact.
        assert enrichment.second_analytic.analytic_is_grounded is False
        # No surviving row carries the fabricated figure anywhere.
        assert all(
            (r.analytic_row_value or "") != "+4%"
            for r in enrichment.second_analytic.analytic_rows
        )

    @pytest.mark.asyncio
    async def test_ungrounded_key_figure_is_dropped(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """A hero key figure not in the source is dropped (no fabricated headline number)."""
        client = make_llm_client(
            _enrichment_payload(
                key_figure={
                    "key_figure_value": "$500 billion",
                    "key_figure_label": "in daily losses",
                }
            )
        )
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="geopolitics",
        )
        assert enrichment.key_figure.key_figure_value is None

    @pytest.mark.asyncio
    async def test_pure_text_value_is_kept_without_grounding(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """A non-numeric value ("record high") is kept — there is no number to ground."""
        client = make_llm_client(
            _enrichment_payload(
                analytic_rows=[
                    {
                        "analytic_row_label": "Tanker rates",
                        "analytic_row_value": "record high",
                        "analytic_row_direction": "up",
                        "analytic_row_note": None,
                    },
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="geopolitics",
        )
        assert (
            enrichment.second_analytic.analytic_rows[0].analytic_row_value
            == "record high"
        )
        assert enrichment.second_analytic.analytic_is_grounded is True

    @pytest.mark.asyncio
    async def test_profile_background_row_keeps_non_article_number(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """A subject_profile row noted 'background' keeps a number absent from the article.

        WHY: the PROFILE section is the ONE sanctioned exception to the
        single-source rule (2026-06-12 decision) — a famous figure's birth year
        ("1955") will legitimately not appear in the day's article and must NOT
        be stripped, nor flip analytic_is_grounded.
        """
        client = make_llm_client(
            _enrichment_payload(
                analytic_rows=[
                    {
                        "analytic_row_label": "BORN",
                        "analytic_row_value": "1955, Chicago",
                        "analytic_row_direction": None,
                        "analytic_row_note": "background",
                    },
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="wildcard",
        )
        assert enrichment.second_analytic.analytic_kind == "subject_profile"
        assert (
            enrichment.second_analytic.analytic_rows[0].analytic_row_value
            == "1955, Chicago"
        )
        assert enrichment.second_analytic.analytic_is_grounded is True

    @pytest.mark.asyncio
    async def test_background_note_does_not_exempt_market_impact_rows(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """The 'background' note exempts ONLY subject_profile — market rows stay gated.

        WHY (failure case): the exemption must not become a model-controlled
        backdoor — a market_impact row claiming 'background' with a fabricated
        figure is still dropped to direction-only and flagged ungrounded.
        """
        client = make_llm_client(
            _enrichment_payload(
                analytic_rows=[
                    {
                        "analytic_row_label": "Brent crude",
                        "analytic_row_value": "+4%",
                        "analytic_row_direction": "up",
                        "analytic_row_note": "background",
                    },
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=geopolitics_story,
            script=digest_script,
            llm_client=client,
            segment_slug="markets",
        )
        assert enrichment.second_analytic.analytic_kind == "market_impact"
        assert enrichment.second_analytic.analytic_rows[0].analytic_row_value is None
        assert enrichment.second_analytic.analytic_is_grounded is False


class TestEnrichmentFailureModes:
    """Failure handling — fail loud, never half-publish (Rule 12)."""

    @pytest.mark.asyncio
    async def test_fewer_than_five_key_points_raises(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """Fewer than 5 bullets is a hard failure (the design requires exactly 5)."""
        client = make_llm_client(
            _enrichment_payload(key_points=["only", "three", "bullets"])
        )
        with pytest.raises(PipelineStageError):
            await run_detail_enrichment(
                story=geopolitics_story,
                script=digest_script,
                llm_client=client,
                segment_slug="geopolitics",
            )

    @pytest.mark.asyncio
    async def test_missing_source_body_raises(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """No source body means nothing to ground against → loud error."""
        bodyless = geopolitics_story.model_copy(update={"canonical_body_text": ""})
        client = make_llm_client(_enrichment_payload())
        with pytest.raises(PipelineStageError):
            await run_detail_enrichment(
                story=bodyless,
                script=digest_script,
                llm_client=client,
                segment_slug="geopolitics",
            )

    @pytest.mark.asyncio
    async def test_non_object_response_raises(
        self, geopolitics_story, digest_script, make_llm_client
    ) -> None:
        """A response that is an array (not an object) is rejected."""
        client = make_llm_client('["not", "an", "object"]')
        with pytest.raises(PipelineStageError):
            await run_detail_enrichment(
                story=geopolitics_story,
                script=digest_script,
                llm_client=client,
                segment_slug="geopolitics",
            )
