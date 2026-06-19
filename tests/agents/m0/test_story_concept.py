"""Tests for real-name identity resolution in concept extraction (phase 0c SP2).

These tests MOCK the Gemini client — no network — and verify the WIRING that the
LLM eval cannot: that the full story body and the story date are actually threaded
into the prompt sent to the model, and that ``entity_key``/``entity_as_of`` are
populated from the model output + the supplied date.

Per CLAUDE.md Rule 9 the body/date-threading tests are written so they FAIL if the
body or date is dropped before reaching the model — that is the whole point of
sub-phase 2 (resolve the person the story names AS OF its date).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agents.m0.poster_models import StoryConcept
from agents.m0.story_concept import _normalize_entity_key, extract_story_concept


def _make_concept(entity_name: str = "Kevin Warsh") -> StoryConcept:
    """Build a valid StoryConcept the mocked SDK 'returns' as response.parsed."""
    return StoryConcept(
        image_search_query="Kevin Warsh Federal Reserve",
        key_subject="Kevin Warsh",
        defining_object_or_action="a gavel over the Fed seal",
        emotional_valence="weighty, consequential",
        gist="Trump names Kevin Warsh as the new Fed chair.",
        is_person_driven=True,
        central_subject_count=1,
        directional_sentiment="none",
        entity_kind="person",
        entity_name=entity_name,
        # Reason: leave these as the schema defaults so the test proves the CODE
        # (not the LLM payload) populates them.
        entity_key="",
        entity_as_of=None,
    )


def _mock_client(returned_concept: StoryConcept) -> tuple[MagicMock, dict[str, Any]]:
    """Return a mock genai client plus a dict capturing the call kwargs.

    The mock records the ``contents``/``model`` passed to ``generate_content`` so a
    test can inspect the exact prompt text that would hit the live model.
    """
    captured: dict[str, Any] = {}

    def _fake_generate_content(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        response = MagicMock()
        response.parsed = returned_concept
        return response

    client = MagicMock()
    client.models.generate_content.side_effect = _fake_generate_content
    return client, captured


def _prompt_text_from_capture(captured: dict[str, Any]) -> str:
    """Extract the single user-part text from a captured generate_content call."""
    contents = captured["contents"]
    # contents=[Content(role="user", parts=[Part(text=...)])]
    return contents[0].parts[0].text


class TestEntityKeyNormalization:
    """The store-lookup key is derived deterministically in code, not by the LLM."""

    def test_lowercases_and_collapses_whitespace(self) -> None:
        """WHY: one person must map to one key regardless of casing/spacing."""
        assert _normalize_entity_key("  Kevin   Warsh ") == "kevin warsh"

    def test_empty_name_yields_empty_key(self) -> None:
        """WHY: no named entity (entity_kind 'other') must not invent a key."""
        assert _normalize_entity_key("") == ""


class TestBodyAndDateThreadedIntoPrompt:
    """Rule 9: these FAIL if the body or date never reaches the model."""

    def test_story_body_is_in_the_prompt(self) -> None:
        """The FULL body — the only source of truth for WHO — must be sent."""
        client, captured = _mock_client(_make_concept())
        body = "President Trump named Kevin Warsh as the next chair of the Federal Reserve."
        extract_story_concept(
            headline="Trump names new Fed chair",
            summary="short summary",
            client=client,
            story_body=body,
            story_date="2026-02-02",
        )
        prompt_text = _prompt_text_from_capture(captured)
        assert body in prompt_text

    def test_story_date_is_in_the_prompt(self) -> None:
        """The date anchors office-holder resolution; it must be sent verbatim."""
        client, captured = _mock_client(_make_concept())
        extract_story_concept(
            headline="Trump names new Fed chair",
            summary="short summary",
            client=client,
            story_body="body text",
            story_date="2026-02-02",
        )
        prompt_text = _prompt_text_from_capture(captured)
        assert "2026-02-02" in prompt_text

    def test_identity_resolution_rule_present_in_prompt(self) -> None:
        """The 'never use your own knowledge of who holds the office' rule ships."""
        client, captured = _mock_client(_make_concept())
        extract_story_concept(
            headline="h",
            summary="s",
            client=client,
            story_body="b",
            story_date="2026-02-02",
        )
        prompt_text = _prompt_text_from_capture(captured).lower()
        assert "named in this story" in prompt_text
        assert "never use your own knowledge" in prompt_text


class TestEntityKeyAndAsOfPopulated:
    """entity_key + entity_as_of are populated from model output + supplied date."""

    def test_entity_key_normalized_from_model_entity_name(self) -> None:
        """The code derives entity_key from the resolved name, not the LLM."""
        client, _ = _mock_client(_make_concept(entity_name="Kevin Warsh"))
        concept = extract_story_concept(
            headline="h",
            summary="s",
            client=client,
            story_body="b",
            story_date="2026-02-02",
        )
        assert concept.entity_name == "Kevin Warsh"
        assert concept.entity_key == "kevin warsh"

    def test_entity_as_of_set_to_story_date(self) -> None:
        """entity_as_of must equal the supplied story_date (the resolution anchor)."""
        client, _ = _mock_client(_make_concept())
        concept = extract_story_concept(
            headline="h",
            summary="s",
            client=client,
            story_body="b",
            story_date="2026-02-02",
        )
        assert concept.entity_as_of == "2026-02-02"


class TestDefaultsKeepCallersWorking:
    """Headline+summary-only callers must not break (safe defaults)."""

    def test_body_defaults_to_summary_when_not_supplied(self) -> None:
        """Omitting story_body sends the summary as the body (back-compat)."""
        client, captured = _mock_client(_make_concept())
        extract_story_concept(
            headline="Trump names new Fed chair",
            summary="President Trump named Kevin Warsh as the next Fed chair.",
            client=client,
        )
        prompt_text = _prompt_text_from_capture(captured)
        assert "President Trump named Kevin Warsh as the next Fed chair." in prompt_text

    def test_missing_date_yields_none_as_of(self) -> None:
        """No date supplied -> entity_as_of is None (resolution relies on text)."""
        client, _ = _mock_client(_make_concept())
        concept = extract_story_concept(
            headline="h",
            summary="s",
            client=client,
        )
        assert concept.entity_as_of is None


class TestFallbackOnLlmFailure:
    """A model failure still returns a usable concept (Rule 12, fail-open)."""

    def test_fallback_sets_entity_as_of_from_date(self) -> None:
        """Even the headline fallback carries the date anchor through."""
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("model down")
        concept = extract_story_concept(
            headline="Some headline",
            summary="s",
            client=client,
            story_body="b",
            story_date="2026-02-02",
        )
        assert concept.entity_name == ""
        assert concept.entity_key == ""
        assert concept.entity_as_of == "2026-02-02"
        assert concept.image_search_query == "Some headline"
