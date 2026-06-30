"""Unit tests for single-source dialogue scripting (Phase 1d SP2).

DoD (phase file SP2): scripting output is speaker-tagged (ALEX/JORDAN), within
the ~140-word / 55s budget, AND constrained to the single source — the prompt
forbids outside facts. The LLM is mocked at the client boundary (no live call).

These tests encode WHY (Rule 9): the single-source constraint is the locked
guardrail (Decision #4), so we assert the actual system prompt sent to Gemini
forbids outside facts AND carries only this one story's body — a regression that
dropped the constraint, or that fed multiple sources, fails here.

    >>> pytest tests/agents/pipeline/test_scripting.py -v
"""

from __future__ import annotations

import json

import pytest

from agents.pipeline.models import DigestScript
from agents.pipeline.prompts import SCRIPTING_SHAPE_LONG, SCRIPTING_SHAPE_SHORT
from agents.pipeline.stages.scripting import (
    HANDOFF_STYLES,
    MAX_WORDS,
    OPENER_ARCHETYPES,
    _build_system_prompt,
    _parse_json_dialogue,
    _select_handoff_style,
    _select_opener_archetype,
    run_single_source_scripting,
)
from agents.shared.exceptions import PipelineStageError


def _two_host_script_json(alex: str, jordan: str) -> str:
    """A minimal valid 2-turn ALEX/JORDAN JSON-array response."""
    return json.dumps(
        [
            {"speaker": "ALEX", "text": alex},
            {"speaker": "JORDAN", "text": jordan},
        ]
    )


class TestScriptingHappyPath:
    """A well-formed model response yields a speaker-tagged, length-bounded script."""

    @pytest.mark.asyncio
    async def test_output_is_speaker_tagged_alex_jordan(
        self, canonical_story, make_llm_client
    ) -> None:
        """DoD: every turn is tagged ALEX or JORDAN, and both hosts appear."""
        response = _two_host_script_json(
            alex="Wait, Arsenal beat Liverpool?",
            jordan="They did, two to one at the Emirates, with Saka scoring both.",
        )
        client = make_llm_client(response)

        script = await run_single_source_scripting(
            story=canonical_story, llm_client=client
        )

        assert isinstance(script, DigestScript)
        assert {turn.speaker for turn in script.turns} == {"ALEX", "JORDAN"}
        assert all(turn.speaker in ("ALEX", "JORDAN") for turn in script.turns)
        assert script.digest_story_id == canonical_story.canonical_story_id

    @pytest.mark.asyncio
    async def test_word_count_and_duration_within_budget(
        self, canonical_story, make_llm_client
    ) -> None:
        """DoD: a normal digest lands within the ~140-word / ~55s budget.

        The canned response is a realistic ~50-word two-host digest; the metrics
        must compute a word count under MAX_WORDS and an estimated duration in
        the digest range (not a 12-minute briefing). This fails if the budget
        constants regress upward to the donor's briefing scale.
        """
        alex = "So Arsenal just beat Liverpool? That's a big result."
        jordan = (
            "It is. Two to one at the Emirates on Saturday, Bukayo Saka scored both "
            "goals in the second half, and the win puts Arsenal top of the table on "
            "seventy-eight points."
        )
        client = make_llm_client(_two_host_script_json(alex, jordan))

        script = await run_single_source_scripting(
            story=canonical_story, llm_client=client
        )

        assert 0 < script.word_count <= MAX_WORDS
        # ~55s digest, not a multi-minute briefing.
        assert 0 < script.estimated_duration_seconds <= 90


class TestSingleSourceConstraint:
    """The single-source constraint must actually reach the model (Rule 9)."""

    @pytest.mark.asyncio
    async def test_prompt_forbids_outside_facts_and_carries_only_this_source(
        self, canonical_story, make_llm_client, source_body
    ) -> None:
        """The system prompt forbids outside facts AND embeds only this one source.

        We capture the exact system prompt passed to call_gemini and assert it
        (a) states the single-source / no-outside-facts rule, and (b) contains
        this story's body — proving the digest is grounded on ONE article, not a
        multi-source corpus. This is the locked Decision #4 guardrail.
        """
        client = make_llm_client(
            _two_host_script_json("Arsenal won?", "Yes, two to one, Saka scored both.")
        )

        await run_single_source_scripting(story=canonical_story, llm_client=client)

        # The mock recorded the call — inspect the system prompt it received.
        _, call_kwargs = client.call_gemini.call_args
        system_prompt = call_kwargs["system"]
        assert "SINGLE-SOURCE RULE" in system_prompt
        assert "Do NOT use outside knowledge" in system_prompt
        # Only this story's body is in the prompt (single source, not a corpus).
        assert source_body in system_prompt
        # The constraint is reinforced in the user prompt too.
        assert "ONLY the SOURCE_ARTICLE" in call_kwargs["prompt"]


class TestLongVsShortSummaryShape:
    """M7 SP2: the long/short summary-shape block is selected in code by outlet domain.

    WHY (Rule 9): a long-form video and a short-form tweet need different summary
    shapes (PRD US-19/US-20, Decision #10). The mode is decided in code from the
    outlet domain (Rule 5) and interpolated into the scripting prompt — these
    assert the RIGHT shape reaches the mocked client, and that a NEWS story's
    prompt is BYTE-FOR-BYTE unchanged (the regression guard). Selecting the wrong
    shape, or regressing the news path, fails here.
    """

    def _restory(self, story, domain: str):
        return story.model_copy(update={"canonical_primary_outlet_domain": domain})

    def test_youtube_story_gets_long_form_shape(self, canonical_story) -> None:
        """A youtube.com story carries the long-form key-points shaping (not short)."""
        prompt = _build_system_prompt(self._restory(canonical_story, "youtube.com"))
        assert SCRIPTING_SHAPE_LONG in prompt
        assert SCRIPTING_SHAPE_SHORT not in prompt
        assert "{SUMMARY_SHAPE}" not in prompt

    def test_x_story_gets_tight_shape(self, canonical_story) -> None:
        """An x.com story carries the tight shaping and NOT the long-form block."""
        prompt = _build_system_prompt(self._restory(canonical_story, "x.com"))
        assert SCRIPTING_SHAPE_SHORT in prompt
        assert SCRIPTING_SHAPE_LONG not in prompt
        assert "{SUMMARY_SHAPE}" not in prompt

    def test_news_prompt_is_byte_identical_to_no_shape(self, canonical_story) -> None:
        """A news story's prompt equals the template with the shape slot emptied.

        This is the regression guard: the news (non-source) path must be
        byte-for-byte what it was before M7 added the {SUMMARY_SHAPE} slot. We
        rebuild the expected prompt by emptying that one slot and assert equality.
        """
        from agents.pipeline.prompts import DIGEST_SCRIPTING_PROMPT
        from agents.pipeline.stages import scripting as scripting_mod

        news_prompt = _build_system_prompt(canonical_story)  # bbc.com → news
        # Neither shape block leaks into a news prompt.
        assert SCRIPTING_SHAPE_LONG not in news_prompt
        assert SCRIPTING_SHAPE_SHORT not in news_prompt
        assert "{SUMMARY_SHAPE}" not in news_prompt

        # Rebuild the EXACT bytes by substituting "" for the shape slot, exactly as
        # the template did before the slot existed (empty news variant).
        published = canonical_story.canonical_published_utc.strftime("%B %d, %Y")
        outlet = (
            canonical_story.canonical_primary_outlet_name
            or canonical_story.canonical_primary_outlet_domain
        )
        expected = (
            DIGEST_SCRIPTING_PROMPT.replace(
                "{TARGET_WORDS}", str(scripting_mod.TARGET_WORDS)
            )
            .replace("{MAX_WORDS}", str(scripting_mod.MAX_WORDS))
            .replace("{TARGET_SECONDS}", str(scripting_mod.TARGET_SECONDS))
            .replace("{MIN_TURNS}", str(scripting_mod.MIN_TURNS))
            .replace("{MAX_TURNS}", str(scripting_mod.MAX_TURNS))
            .replace("{SUMMARY_SHAPE}", "")
            .replace(
                "{OPENER_ARCHETYPE}", scripting_mod._select_opener_archetype(None)
            )
            .replace("{HANDOFF_STYLE}", scripting_mod._select_handoff_style(None))
            .replace("{SOURCE_HEADLINE}", canonical_story.canonical_title)
            .replace("{SOURCE_OUTLET}", outlet)
            .replace("{SOURCE_PUBLISHED}", published)
            .replace("{SOURCE_BODY}", (canonical_story.canonical_body_text or "").strip())
        )
        assert news_prompt == expected

    @pytest.mark.asyncio
    async def test_shape_reaches_mocked_client_for_youtube(
        self, canonical_story, make_llm_client
    ) -> None:
        """End-to-end: the long-form shape is in the system prompt actually sent."""
        story = self._restory(canonical_story, "youtube.com")
        client = make_llm_client(
            _two_host_script_json("Big interview?", "Yes — here's the gist.")
        )
        await run_single_source_scripting(story=story, llm_client=client)
        system_prompt = client.call_gemini.call_args.kwargs["system"]
        assert SCRIPTING_SHAPE_LONG in system_prompt
        assert SCRIPTING_SHAPE_SHORT not in system_prompt


class TestOpenerHandoffRotation:
    """Cross-reel diversity (Layer 2): the opener/handoff SHAPE rotates by pool index.

    WHY (Rule 9): every reel is scripted in isolation, so without a per-reel shape
    the model converges on one favorite opener + handoff across the whole pool — the
    repetition the listener hears. These assert the rotation actually reaches the
    system prompt and is deterministic by index, so two adjacent reels get different
    shapes; a regression that stopped injecting the archetype would fail here.
    """

    def test_selection_is_deterministic_modulo_deck_length(self) -> None:
        """Edge: the same pool index always maps to the same shape; it wraps cleanly."""
        assert _select_opener_archetype(0) == _select_opener_archetype(
            len(OPENER_ARCHETYPES)
        )
        assert _select_handoff_style(0) == _select_handoff_style(len(HANDOFF_STYLES))
        # Adjacent indices differ (the whole point — no two neighbors share a shape).
        assert _select_opener_archetype(0) != _select_opener_archetype(1)

    def test_none_index_falls_back_to_generic_guidance(self) -> None:
        """Edge: single-story callers (pool_index=None) get the pre-rotation hook."""
        generic_opener = _select_opener_archetype(None)
        generic_handoff = _select_handoff_style(None)
        assert "curiosity hook" in generic_opener
        assert generic_opener not in OPENER_ARCHETYPES
        assert generic_handoff not in HANDOFF_STYLES

    @pytest.mark.asyncio
    async def test_pool_index_injects_rotated_shapes_into_system_prompt(
        self, canonical_story, make_llm_client
    ) -> None:
        """The archetype + handoff for this pool index reach the model, placeholders gone."""
        client = make_llm_client(
            _two_host_script_json("Arsenal won?", "Yes, two to one, Saka scored both.")
        )

        await run_single_source_scripting(
            story=canonical_story, llm_client=client, pool_index=1
        )

        system_prompt = client.call_gemini.call_args.kwargs["system"]
        assert _select_opener_archetype(1) in system_prompt
        assert _select_handoff_style(1) in system_prompt
        # The placeholders must be fully substituted (no leftover template tokens).
        assert "{OPENER_ARCHETYPE}" not in system_prompt
        assert "{HANDOFF_STYLE}" not in system_prompt

    @pytest.mark.asyncio
    async def test_default_call_uses_generic_opener(
        self, canonical_story, make_llm_client
    ) -> None:
        """Back-compat: omitting pool_index keeps the generic opener (prior behavior)."""
        client = make_llm_client(
            _two_host_script_json("Arsenal won?", "Yes, two to one.")
        )

        await run_single_source_scripting(story=canonical_story, llm_client=client)

        system_prompt = client.call_gemini.call_args.kwargs["system"]
        assert _select_opener_archetype(None) in system_prompt


class TestScriptingFailureAndEdge:
    """Failure + edge handling of the model's response."""

    @pytest.mark.asyncio
    async def test_missing_body_text_raises(
        self, canonical_story, make_llm_client
    ) -> None:
        """Failure: a story with no extracted body cannot be scripted single-source."""
        bodyless = canonical_story.model_copy(update={"canonical_body_text": None})
        client = make_llm_client(_two_host_script_json("hi", "hello"))

        with pytest.raises(PipelineStageError):
            await run_single_source_scripting(story=bodyless, llm_client=client)
        # The LLM must not even be called when there's nothing to ground on.
        client.call_gemini.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_array_response_raises(
        self, canonical_story, make_llm_client
    ) -> None:
        """Failure: a model that returns an object (not an array) is rejected loudly."""
        client = make_llm_client('{"speaker": "ALEX", "text": "oops, not an array"}')
        with pytest.raises(PipelineStageError):
            await run_single_source_scripting(story=canonical_story, llm_client=client)

    def test_parse_drops_invalid_speakers_and_empty_turns(self) -> None:
        """Edge: unknown speakers and blank-text turns are filtered out, not kept."""
        raw = json.dumps(
            [
                {"speaker": "ALEX", "text": "Real turn."},
                {"speaker": "NARRATOR", "text": "Should be dropped."},
                {"speaker": "JORDAN", "text": ""},
                {"speaker": "JORDAN", "text": "Kept analyst turn."},
            ]
        )
        turns = _parse_json_dialogue(raw)
        assert [t.speaker for t in turns] == ["ALEX", "JORDAN"]
        assert all(t.text for t in turns)

    def test_parse_strips_forbidden_bracket_tags(self) -> None:
        """Edge: bracket tags the prompt forbids are stripped before TTS sees them."""
        raw = json.dumps(
            [
                {"speaker": "ALEX", "text": "Wow [LAUGH] that's wild."},
                {"speaker": "JORDAN", "text": "[EMPHASIS] Indeed it is."},
            ]
        )
        turns = _parse_json_dialogue(raw)
        assert "[LAUGH]" not in turns[0].text
        assert "[EMPHASIS]" not in turns[1].text
