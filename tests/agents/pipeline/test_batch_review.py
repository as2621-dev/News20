"""Unit tests for the pool-level cross-reel diversity review (Layer 3 batch pass).

WHY (Rule 9): the pass exists to remove cross-reel scaffolding repetition WITHOUT
ever publishing ungrounded content. So the tests pin the two guards that make that
true — the freeze guard (only edit the turn the model actually echoed) and the
re-verify-then-revert safety net (a rewrite that breaks grounding is rolled back) —
plus the fail-open contract (a judge error never blocks the run). The Gemini SDK and
the verifier are mocked at their boundaries (no live call, no cost).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from agents.ingestion.models import CanonicalStory
from agents.pipeline import stages
from agents.pipeline.models import DialogueTurn, DigestScript, WritePhaseResult
from agents.pipeline.stages import batch_review
from agents.pipeline.stages.batch_review import review_reel_pool
from agents.shared.exceptions import VerificationHaltError


def _story(story_id: str, title: str) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title,
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=datetime(2026, 6, 16, tzinfo=timezone.utc),
        canonical_primary_outlet_domain="reuters.com",
        canonical_body_text="A grounded source body.",
        canonical_representative_external_id=f"ext-{story_id}",
        story_outlet_count=5,
    )


def _wr(story_id: str, opener: str, *, title: str = "A story") -> WritePhaseResult:
    """A WritePhaseResult whose script opens with *opener* (turn 0) + one fact turn."""
    story = _story(story_id, title)
    script = DigestScript(
        digest_story_id=story_id,
        turns=[
            DialogueTurn(speaker="ALEX", text=opener),
            DialogueTurn(speaker="JORDAN", text="They won two to one at the Emirates."),
        ],
        word_count=10,
        estimated_duration_seconds=4,
    )
    return WritePhaseResult(
        canonical_story_id=story_id,
        story_id=story_id,
        script=script,
        editorial_story=story,
        original_story=story,
        segment_slug="wildcard",
    )


def _client(review_json: str) -> object:
    client = object.__new__(type("FakeLLM", (), {}))
    client.call_gemini = AsyncMock(return_value=review_json)  # type: ignore[attr-defined]
    return client


def _pass_verification(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub the re-verify so a modified reel always grounds (returns normally)."""
    verifier = AsyncMock(return_value=None)
    monkeypatch.setattr(batch_review, "run_single_source_verification", verifier)
    return verifier


@pytest.mark.asyncio
async def test_applies_scaffolding_rewrite_to_repeated_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a valid revision rewrites the repeating opener; the fact turn and
    the other reel are untouched."""
    _pass_verification(monkeypatch)
    survivors = [_wr("r1", "Wait, what?"), _wr("r2", "Wait, what?")]
    review_json = json.dumps(
        [
            {
                "reel_n": 2,
                "turn_index": 0,
                "original_text": "Wait, what?",
                "revised_text": "Okay, this one's strange.",
                "turn_role": "opener",
            }
        ]
    )

    result = await review_reel_pool(survivors, _client(review_json))

    assert result[1].script.turns[0].text == "Okay, this one's strange."
    # The fact turn in the modified reel is frozen; reel 1 is untouched.
    assert result[1].script.turns[1].text == "They won two to one at the Emirates."
    assert result[0].script.turns[0].text == "Wait, what?"


@pytest.mark.asyncio
async def test_freeze_guard_drops_revision_with_mismatched_original_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revision whose echoed original_text does not match the live turn is dropped
    (it could otherwise overwrite the wrong — possibly factual — turn)."""
    verifier = _pass_verification(monkeypatch)
    survivors = [_wr("r1", "Wait, what?"), _wr("r2", "Wait, what?")]
    review_json = json.dumps(
        [
            {
                "reel_n": 2,
                "turn_index": 0,
                "original_text": "Something the model imagined.",
                "revised_text": "Should never be applied.",
                "turn_role": "opener",
            }
        ]
    )

    result = await review_reel_pool(survivors, _client(review_json))

    assert result[1].script.turns[0].text == "Wait, what?"  # unchanged
    verifier.assert_not_awaited()  # nothing modified → no re-verify


@pytest.mark.asyncio
async def test_out_of_range_targets_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edge: a reel_n or turn_index outside the pool is ignored, not applied."""
    _pass_verification(monkeypatch)
    survivors = [_wr("r1", "Wait, what?"), _wr("r2", "Wait, what?")]
    review_json = json.dumps(
        [
            {
                "reel_n": 9,
                "turn_index": 0,
                "original_text": "Wait, what?",
                "revised_text": "x",
                "turn_role": "opener",
            },
            {
                "reel_n": 1,
                "turn_index": 7,
                "original_text": "Wait, what?",
                "revised_text": "y",
                "turn_role": "opener",
            },
        ]
    )

    result = await review_reel_pool(survivors, _client(review_json))

    assert [t.text for t in result[0].script.turns] == [
        "Wait, what?",
        "They won two to one at the Emirates.",
    ]
    assert result[1].script.turns[0].text == "Wait, what?"


@pytest.mark.asyncio
async def test_fail_open_when_judge_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open: an LLM error leaves every reel unchanged (never blocks the run)."""
    _pass_verification(monkeypatch)
    survivors = [_wr("r1", "Wait, what?"), _wr("r2", "Wait, what?")]
    client = object.__new__(type("FakeLLM", (), {}))
    client.call_gemini = AsyncMock(side_effect=RuntimeError("gemini down"))  # type: ignore[attr-defined]

    result = await review_reel_pool(survivors, client)

    assert [r.script.turns[0].text for r in result] == ["Wait, what?", "Wait, what?"]


@pytest.mark.asyncio
async def test_revert_when_rewrite_fails_reverification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safety net: a rewrite that no longer grounds is reverted to the pre-revision
    (already-verified) script — the reel never publishes ungrounded content."""
    monkeypatch.setattr(
        batch_review,
        "run_single_source_verification",
        AsyncMock(
            side_effect=VerificationHaltError(unsupported_count=1, contradicted_count=0)
        ),
    )
    survivors = [_wr("r1", "Wait, what?"), _wr("r2", "Wait, what?")]
    review_json = json.dumps(
        [
            {
                "reel_n": 2,
                "turn_index": 0,
                "original_text": "Wait, what?",
                "revised_text": "A rewrite that smuggled in a bad fact.",
                "turn_role": "opener",
            }
        ]
    )

    result = await review_reel_pool(survivors, _client(review_json))

    # Reverted to the original opener, not the (ungrounded) rewrite.
    assert result[1].script.turns[0].text == "Wait, what?"


@pytest.mark.asyncio
async def test_pool_smaller_than_two_returns_unchanged_without_calling_llm() -> None:
    """A single reel can't have cross-reel repetition — return immediately, no call."""
    survivors = [_wr("r1", "Wait, what?")]
    client = object.__new__(type("FakeLLM", (), {}))
    client.call_gemini = AsyncMock()  # type: ignore[attr-defined]

    result = await review_reel_pool(survivors, client)

    assert result == survivors
    client.call_gemini.assert_not_awaited()


def test_module_is_exposed_under_stages_package() -> None:
    """Guard: the new stage imports cleanly as part of the pipeline stages package."""
    assert hasattr(stages.batch_review, "review_reel_pool")
