"""Unit tests for the grounded, category-shaped detail-enrichment stage.

DoD: the LLM-drafted Detail payload is (a) structurally correct — 5 key points, an
in-order timeline (when the category has one), and the category's analytic panels
in slot order with the right kinds + labels — and (b) NUMBER-SAFE per panel: a
figure the source does not support is dropped to direction-only and THAT panel's
``analytic_is_grounded`` is set False (siblings unaffected). The LLM is mocked at
the client boundary (no live call).

These tests encode WHY (Rule 9/12): the trust-critical invariant is that a
fabricated number must NEVER publish as a fact, and the category template fixes the
panel set — so the ungrounded-"+4%" test fails if the number survives OR if the
panel stays grounded; the markets test fails if the 2 panels are not
market_impact + by_the_numbers; the source test fails if a timeline leaks in.

    >>> pytest tests/agents/pipeline/test_detail_enrichment.py -v
"""

from __future__ import annotations

import json

import pytest

from agents.pipeline.stages.detail_enrichment import run_detail_enrichment
from agents.shared.exceptions import PipelineStageError

# The single source body the enrichment grounds against. Every grounded number a
# test asserts must appear here verbatim (digit-stream membership): "20" (Hormuz
# share), "81.6" (trade value), "1.20" (FX). A drafted "+4%" is deliberately ABSENT
# so the grounding gate must drop it.
_SOURCE_BODY = (
    "Iran threatened to close the Strait of Hormuz on Monday after fresh strikes. "
    "Roughly 20% of global seaborne oil transits the strait each day. "
    "Regional trade through the chokepoint was valued at $81.6 billion last year. "
    "The euro slipped to 1.20 against the dollar as the news broke. "
    "Shipping insurers met on Tuesday to reassess war-risk premiums."
)

# Default analytic rows — both figures ("20%", "1.20") ARE in the source.
_DEFAULT_ROWS = [
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
]


def _panel(slot: int, *, rows: list[dict] | None = None, headline: str | None = None) -> dict:
    """Build one drafted analytic panel object (as the model would emit it)."""
    return {
        "analytic_slot_index": slot,
        "analytic_headline": headline or "Oil routes twitch on the Hormuz threat",
        "analytic_summary_text": "A closure would choke a fifth of seaborne crude.",
        "analytic_rows": rows if rows is not None else [dict(r) for r in _DEFAULT_ROWS],
    }


def _enrichment_payload(
    *,
    analytic_rows: list[dict] | None = None,
    analytic_panels: list[dict] | None = None,
    key_points: list[str] | None = None,
    key_figure: dict | None = None,
    timeline: list[dict] | None = None,
) -> str:
    """Build a detail-enrichment LLM JSON response with sensible defaults.

    ``analytic_rows`` (back-compat) sets the rows of a SINGLE slot-0 panel;
    ``analytic_panels`` supplies the full multi-panel array explicitly.
    """
    if analytic_panels is None:
        analytic_panels = [_panel(0, rows=analytic_rows)]
    payload = {
        "key_figure": key_figure
        if key_figure is not None
        else {"key_figure_value": "20%", "key_figure_label": "of global oil transits Hormuz"},
        "timeline": timeline
        if timeline is not None
        else [
            {"timeline_when_label": "Mon", "timeline_what_text": "Iran threatened to close Hormuz."},
            {"timeline_when_label": "Tue", "timeline_what_text": "Shipping insurers met to reassess premiums."},
        ],
        "analytic_panels": analytic_panels,
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
def hormuz_story(canonical_story):
    """The shared canonical story re-bodied as the Hormuz story (source corpus)."""
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


class TestTemplateDrivenPanels:
    """The category template fixes the panel set + kinds + labels (never the LLM)."""

    @pytest.mark.asyncio
    async def test_world_category_yields_single_stakes_panel(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A World story draws ONE analytic panel — STAKES — plus 5 key points + timeline."""
        client = make_llm_client(_enrichment_payload())
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="world"
        )

        assert len(enrichment.key_points) == 5
        assert [p.key_point_index for p in enrichment.key_points] == [0, 1, 2, 3, 4]
        assert [e.timeline_event_index for e in enrichment.timeline] == list(range(len(enrichment.timeline)))

        assert [p.analytic_kind for p in enrichment.analytic_panels] == ["stakes"]
        assert enrichment.analytic_panels[0].analytic_tab_label == "STAKES"
        assert enrichment.analytic_panels[0].analytic_slot_index == 0
        # Drafted numbers ("20%", "1.20") are in the source → grounded.
        assert enrichment.analytic_panels[0].analytic_is_grounded is True
        assert enrichment.key_figure.key_figure_value == "20%"

    @pytest.mark.asyncio
    async def test_markets_category_yields_two_ordered_panels(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A Markets story draws MARKET IMPACT + BY THE NUMBERS, in slot order."""
        client = make_llm_client(_enrichment_payload(analytic_panels=[_panel(0), _panel(1)]))
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="markets"
        )
        assert [p.analytic_slot_index for p in enrichment.analytic_panels] == [0, 1]
        assert [p.analytic_kind for p in enrichment.analytic_panels] == ["market_impact", "by_the_numbers"]
        assert [p.analytic_tab_label for p in enrichment.analytic_panels] == ["MARKET IMPACT", "BY THE NUMBERS"]

    @pytest.mark.asyncio
    async def test_source_category_has_three_panels_and_no_timeline(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A YouTube story draws 3 source panels and NO timeline (even if drafted)."""
        client = make_llm_client(_enrichment_payload(analytic_panels=[_panel(0), _panel(1), _panel(2)]))
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="youtube"
        )
        # Timeline is omitted for source categories even though the payload carries one.
        assert enrichment.timeline == []
        assert [p.analytic_kind for p in enrichment.analytic_panels] == [
            "source_context",
            "key_points",
            "implications",
        ]

    @pytest.mark.asyncio
    async def test_missing_panel_still_fills_the_slot(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """Fewer drafted panels than the template → the missing slot is still produced.

        WHY (Rule 12): the slot count must stay stable so the UI tab set is correct;
        a missing panel renders a placeholder, never a silently dropped tab.
        """
        # Markets needs 2 panels; the model drafts only slot 0.
        client = make_llm_client(_enrichment_payload(analytic_panels=[_panel(0)]))
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="markets"
        )
        assert [p.analytic_kind for p in enrichment.analytic_panels] == ["market_impact", "by_the_numbers"]
        # The placeholder panel falls back to a title-cased label headline.
        assert enrichment.analytic_panels[1].analytic_headline


class TestPerPanelGroundingGate:
    """Trust-critical: an ungrounded number NEVER publishes, and grounding is PER PANEL."""

    @pytest.mark.asyncio
    async def test_unsupported_number_dropped_and_marked_ungrounded(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A drafted "+4%" absent from the source is dropped → that panel ungrounded."""
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
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="world"
        )
        panel = enrichment.analytic_panels[0]
        rows = {r.analytic_row_label: r for r in panel.analytic_rows}
        assert rows["Brent crude"].analytic_row_value is None
        assert rows["Brent crude"].analytic_row_direction == "up"
        assert rows["Seaborne oil via Hormuz"].analytic_row_value == "20%"
        assert panel.analytic_is_grounded is False
        assert all((r.analytic_row_value or "") != "+4%" for r in panel.analytic_rows)

    @pytest.mark.asyncio
    async def test_grounding_is_isolated_per_panel(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """One panel's ungrounded number must NOT flip a sibling panel's verdict.

        WHY (Rule 9): grounding is per-panel — a fabricated figure in BY THE NUMBERS
        cannot taint MARKET IMPACT, and vice-versa. A regression that flagged all
        panels off one bad number fails here.
        """
        clean = _panel(0)  # both figures grounded
        dirty = _panel(
            1,
            rows=[
                {
                    "analytic_row_label": "Brent crude",
                    "analytic_row_value": "+4%",
                    "analytic_row_direction": "up",
                    "analytic_row_note": None,
                }
            ],
        )
        client = make_llm_client(_enrichment_payload(analytic_panels=[clean, dirty]))
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="markets"
        )
        assert enrichment.analytic_panels[0].analytic_is_grounded is True
        assert enrichment.analytic_panels[1].analytic_is_grounded is False

    @pytest.mark.asyncio
    async def test_ungrounded_key_figure_is_dropped(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A hero key figure not in the source is dropped (no fabricated headline number)."""
        client = make_llm_client(
            _enrichment_payload(key_figure={"key_figure_value": "$500 billion", "key_figure_label": "in daily losses"})
        )
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="world"
        )
        assert enrichment.key_figure.key_figure_value is None

    @pytest.mark.asyncio
    async def test_pure_text_value_is_kept_without_grounding(
        self, hormuz_story, digest_script, make_llm_client
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
                    }
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="world"
        )
        assert enrichment.analytic_panels[0].analytic_rows[0].analytic_row_value == "record high"
        assert enrichment.analytic_panels[0].analytic_is_grounded is True

    @pytest.mark.asyncio
    async def test_profile_background_row_keeps_non_article_number(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A subject_profile (Culture slot 0) row noted 'background' keeps a non-article number.

        WHY: PROFILE/RECENT FORM/SOURCE CONTEXT are the sanctioned exceptions to the
        single-source rule — a famous figure's birth year ("1955") will legitimately
        not appear in the day's article and must NOT be stripped, nor flip grounded.
        """
        client = make_llm_client(
            _enrichment_payload(
                analytic_rows=[
                    {
                        "analytic_row_label": "BORN",
                        "analytic_row_value": "1955, Chicago",
                        "analytic_row_direction": None,
                        "analytic_row_note": "background",
                    }
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="culture"
        )
        assert enrichment.analytic_panels[0].analytic_kind == "subject_profile"
        assert enrichment.analytic_panels[0].analytic_rows[0].analytic_row_value == "1955, Chicago"
        assert enrichment.analytic_panels[0].analytic_is_grounded is True

    @pytest.mark.asyncio
    async def test_background_note_does_not_exempt_market_impact_rows(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """The 'background' note exempts ONLY background-kind panels — market rows stay gated.

        WHY (failure case): the exemption must not become a model-controlled
        backdoor — a market_impact row claiming 'background' with a fabricated figure
        is still dropped to direction-only and flagged ungrounded.
        """
        client = make_llm_client(
            _enrichment_payload(
                analytic_rows=[
                    {
                        "analytic_row_label": "Brent crude",
                        "analytic_row_value": "+4%",
                        "analytic_row_direction": "up",
                        "analytic_row_note": "background",
                    }
                ]
            )
        )
        enrichment = await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="markets"
        )
        assert enrichment.analytic_panels[0].analytic_kind == "market_impact"
        assert enrichment.analytic_panels[0].analytic_rows[0].analytic_row_value is None
        assert enrichment.analytic_panels[0].analytic_is_grounded is False


class TestLongVsShortKeyPointsShape:
    """M7 SP3: the key_points instruction is shaped long-vs-short by outlet domain.

    WHY (Rule 9): a long-form video's key points should draw out the substance; a
    short-form tweet's should stay a tight set (PRD US-19/US-20, Decision #10). The
    mode is chosen in code (Rule 5). These assert the right shape reaches the
    mocked client and that a NEWS story's enrichment prompt is unchanged — and the
    "exactly 5" + numeric-grounding invariants (covered by the other classes) are
    untouched. A wrong key-points shape OR a regressed news path fails here.
    """

    def _restory(self, story, domain: str):
        return story.model_copy(update={"canonical_primary_outlet_domain": domain})

    @pytest.mark.asyncio
    async def test_youtube_story_gets_long_key_points_shape(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A youtube.com story's prompt carries the long-form key-points shaping."""
        from agents.pipeline.prompts import KEY_POINTS_LONG, KEY_POINTS_SHORT

        story = self._restory(hormuz_story, "youtube.com")
        client = make_llm_client(
            _enrichment_payload(analytic_panels=[_panel(0), _panel(1), _panel(2)])
        )
        await run_detail_enrichment(
            story=story, script=digest_script, llm_client=client, detail_category="youtube"
        )
        system_prompt = client.call_gemini.call_args.kwargs["system"]
        assert KEY_POINTS_LONG in system_prompt
        assert KEY_POINTS_SHORT not in system_prompt
        assert "{KEY_POINTS_SHAPE}" not in system_prompt

    @pytest.mark.asyncio
    async def test_x_story_gets_tight_key_points_shape(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """An x.com story's prompt carries the tight shaping, NOT the long block."""
        from agents.pipeline.prompts import KEY_POINTS_LONG, KEY_POINTS_SHORT

        story = self._restory(hormuz_story, "x.com")
        client = make_llm_client(
            _enrichment_payload(analytic_panels=[_panel(0), _panel(1), _panel(2)])
        )
        await run_detail_enrichment(
            story=story, script=digest_script, llm_client=client, detail_category="x"
        )
        system_prompt = client.call_gemini.call_args.kwargs["system"]
        assert KEY_POINTS_SHORT in system_prompt
        assert KEY_POINTS_LONG not in system_prompt
        assert "{KEY_POINTS_SHAPE}" not in system_prompt

    @pytest.mark.asyncio
    async def test_news_prompt_is_byte_identical_to_no_shape(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A news story's enrichment prompt equals the template with the slot emptied.

        Regression guard: the news (non-source) path must be byte-for-byte what it
        was before M7 added {KEY_POINTS_SHAPE}. hormuz_story's domain is bbc.com
        (news). We rebuild the expected prompt by emptying that one slot.
        """
        from agents.pipeline.prompts import KEY_POINTS_LONG, KEY_POINTS_SHORT
        from agents.pipeline.stages import detail_enrichment as de_mod

        client = make_llm_client(_enrichment_payload())
        await run_detail_enrichment(
            story=hormuz_story, script=digest_script, llm_client=client, detail_category="markets"
        )
        news_prompt = client.call_gemini.call_args.kwargs["system"]
        assert KEY_POINTS_LONG not in news_prompt
        assert KEY_POINTS_SHORT not in news_prompt
        assert "{KEY_POINTS_SHAPE}" not in news_prompt

        # Rebuild the exact bytes a news story would have produced before the slot
        # existed: same builder, but with the shape slot forced empty.
        from agents.pipeline.detail_templates import (
            DETAIL_TEMPLATES,
            analytic_panel_specs,
        )

        template = DETAIL_TEMPLATES["markets"]
        analytic_specs = analytic_panel_specs("markets")
        include_timeline = any(spec.panel_kind == "timeline" for spec in template)

        from agents.pipeline.prompts import (
            DETAIL_ENRICHMENT_PROMPT,
            DETAIL_TIMELINE_CONTRACT,
            DETAIL_TIMELINE_PRODUCE,
        )

        body = (hormuz_story.canonical_body_text or "").strip()
        published = hormuz_story.canonical_published_utc.strftime("%B %d, %Y")
        outlet = (
            hormuz_story.canonical_primary_outlet_name
            or hormuz_story.canonical_primary_outlet_domain
        )
        expected = (
            DETAIL_ENRICHMENT_PROMPT.replace(
                "{TIMELINE_PRODUCE}", DETAIL_TIMELINE_PRODUCE if include_timeline else ""
            )
            .replace(
                "{TIMELINE_CONTRACT}",
                DETAIL_TIMELINE_CONTRACT if include_timeline else "",
            )
            .replace("{KEY_POINTS_SHAPE}", "")
            .replace(
                "{PANEL_INSTRUCTIONS}",
                de_mod._build_panel_instructions(analytic_specs),
            )
            .replace("{SOURCE_HEADLINE}", hormuz_story.canonical_title)
            .replace("{SOURCE_OUTLET}", outlet)
            .replace("{SOURCE_PUBLISHED}", published)
            .replace("{SOURCE_BODY}", body)
        )
        assert news_prompt == expected


class TestAnalyticInstructions:
    """The per-kind prompt instructions back the grounding contract."""

    def test_background_kinds_declare_the_exemption(self) -> None:
        """subject_profile / recent_form / source_context instructions scope 'background'.

        WHY: the grounding gate exempts rows noted 'background' ONLY because the
        prompt instructs the model to mark general-knowledge facts that way — if an
        instruction loses that contract, the exemption becomes unsound.
        """
        from agents.pipeline.prompts import DETAIL_ANALYTIC_INSTRUCTIONS

        for kind in ("subject_profile", "recent_form", "source_context"):
            instruction = DETAIL_ANALYTIC_INSTRUCTIONS[kind]
            assert "background" in instruction
            assert "SINGLE-SOURCE" in instruction

    def test_every_template_kind_has_an_instruction(self) -> None:
        """Every analytic kind any template uses has a drafting instruction (no KeyError)."""
        from agents.pipeline.detail_templates import DETAIL_TEMPLATES
        from agents.pipeline.prompts import DETAIL_ANALYTIC_INSTRUCTIONS

        for specs in DETAIL_TEMPLATES.values():
            for spec in specs:
                if spec.panel_kind == "analytic":
                    assert spec.analytic_kind in DETAIL_ANALYTIC_INSTRUCTIONS


class TestEnrichmentFailureModes:
    """Failure handling — fail loud, never half-publish (Rule 12)."""

    @pytest.mark.asyncio
    async def test_fewer_than_five_key_points_raises(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """Fewer than 5 bullets is a hard failure (the design requires exactly 5)."""
        client = make_llm_client(_enrichment_payload(key_points=["only", "three", "bullets"]))
        with pytest.raises(PipelineStageError):
            await run_detail_enrichment(
                story=hormuz_story, script=digest_script, llm_client=client, detail_category="world"
            )

    @pytest.mark.asyncio
    async def test_missing_source_body_raises(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """No source body means nothing to ground against → loud error."""
        bodyless = hormuz_story.model_copy(update={"canonical_body_text": ""})
        client = make_llm_client(_enrichment_payload())
        with pytest.raises(PipelineStageError):
            await run_detail_enrichment(
                story=bodyless, script=digest_script, llm_client=client, detail_category="world"
            )

    @pytest.mark.asyncio
    async def test_non_object_response_raises(
        self, hormuz_story, digest_script, make_llm_client
    ) -> None:
        """A response that is an array (not an object) is rejected."""
        client = make_llm_client('["not", "an", "object"]')
        with pytest.raises(PipelineStageError):
            await run_detail_enrichment(
                story=hormuz_story, script=digest_script, llm_client=client, detail_category="world"
            )
