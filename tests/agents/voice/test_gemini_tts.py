"""Unit tests for the Gemini multi-speaker TTS spine.

These tests mock the Gemini TTS client at the call boundary — they NEVER hit
the real Gemini API. Per Rule 9 they encode WHY the business logic matters and
must fail if it regresses:

  (a) the ALEX -> Leda / JORDAN -> Sadaltager voice mapping reaches the call
      boundary (a wrong mapping is the difference between the locked format and
      the wrong-sounding hosts);
  (b) text is chunked on <Person[12]> boundaries and every chunk stays under
      the byte budget (over-budget chunks get silently truncated by Gemini,
      dropping speech);
  (c) the assembled segments preserve the script's turn order (out-of-order
      audio would scramble the digest).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.voice.gemini_tts import (
    DEFAULT_BYTE_BUDGET,
    SPEAKER_TO_PERSON,
    VOICE_MAP_GEMINI,
    GeminiTTSClient,
    assemble_xml_dialogue,
    chunk_by_byte_budget,
    render_full_dialogue,
)
from agents.voice.models import DialogueTurn


def _make_silent_pcm(
    num_samples: int = 2400, sample_rate: int = 24000
) -> tuple[bytes, int]:
    """Return (pcm_bytes, sample_rate) for a short silent 16-bit mono clip.

    100ms of silence by default — enough for pydub to decode a real segment so
    ``render_full_dialogue`` exercises the genuine PCM->WAV->AudioSegment path
    without touching the network.
    """
    pcm_bytes = b"\x00\x00" * num_samples  # 16-bit samples
    return pcm_bytes, sample_rate


def _mock_tts_client() -> GeminiTTSClient:
    """A GeminiTTSClient whose only network method is an AsyncMock."""
    client = GeminiTTSClient.__new__(GeminiTTSClient)  # bypass __init__/Settings
    client.call_gemini_multispeaker_tts = AsyncMock(return_value=_make_silent_pcm())  # type: ignore[method-assign]
    return client


# ---------------------------------------------------------------------------
# (a) Voice mapping at the call boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_binds_alex_to_leda_and_jordan_to_sadaltager() -> None:
    """ALEX must reach the call boundary as Leda and JORDAN as Sadaltager.

    Asserts the actual speaker_voice_map passed to the (mocked) Gemini call —
    so flipping the VOICE_MAP or the Person mapping fails this test.
    """
    turns = [
        DialogueTurn(speaker="ALEX", text="Alpha line from ALEX."),
        DialogueTurn(speaker="JORDAN", text="Beta line from JORDAN."),
    ]
    client = _mock_tts_client()

    await render_full_dialogue(turns=turns, tts_client=client)

    client.call_gemini_multispeaker_tts.assert_awaited()
    call_kwargs = client.call_gemini_multispeaker_tts.await_args.kwargs
    speaker_voice_map = call_kwargs["speaker_voice_map"]
    assert speaker_voice_map["Person1"] == "Leda", "ALEX (Person1) must map to Leda"
    assert speaker_voice_map["Person2"] == "Sadaltager", (
        "JORDAN (Person2) must map to Sadaltager"
    )
    # Guard the source-of-truth constants too, so the mapping intent is pinned.
    assert VOICE_MAP_GEMINI == {"ALEX": "Leda", "JORDAN": "Sadaltager"}
    assert SPEAKER_TO_PERSON == {"ALEX": "Person1", "JORDAN": "Person2"}


@pytest.mark.asyncio
async def test_render_prompt_uses_person_labels_not_internal_labels() -> None:
    """The prompt sent to Gemini must use Person1/Person2, never ALEX/JORDAN.

    Internal labels stay ALEX/JORDAN; the mapping happens only at the boundary.
    """
    turns = [DialogueTurn(speaker="ALEX", text="Internal label must not leak.")]
    client = _mock_tts_client()

    await render_full_dialogue(turns=turns, tts_client=client)

    prompt = client.call_gemini_multispeaker_tts.await_args.kwargs["prompt"]
    assert "Person1:" in prompt
    assert "ALEX" not in prompt and "JORDAN" not in prompt


# ---------------------------------------------------------------------------
# (b) Chunking on <Person[12]> boundaries under the byte budget
# ---------------------------------------------------------------------------


def test_chunking_splits_on_person_boundaries_under_budget() -> None:
    """Each chunk must stay under the byte budget and never split mid-turn."""
    # Build a dialogue whose total comfortably exceeds one small budget so we
    # force multiple chunks; each turn is well under the budget itself.
    turns = [
        DialogueTurn(
            speaker="ALEX" if i % 2 == 0 else "JORDAN",
            text=f"Turn number {i} " + "word " * 30,
        )
        for i in range(10)
    ]
    xml = assemble_xml_dialogue(turns)
    small_budget = 600

    chunks = chunk_by_byte_budget(xml, max_bytes=small_budget)

    assert len(chunks) > 1, (
        "expected the dialogue to span multiple chunks under a small budget"
    )
    for chunk in chunks:
        # No partial tags: every <PersonN> has its matching close tag.
        assert chunk.count("<Person1>") == chunk.count("</Person1>")
        assert chunk.count("<Person2>") == chunk.count("</Person2>")
        # Multi-turn chunks must respect the budget (a lone over-budget turn is
        # allowed its own chunk; that's the documented single-turn exception).
        if chunk.count("<Person1>") + chunk.count("<Person2>") > 1:
            assert len(chunk.encode("utf-8")) <= small_budget


def test_chunking_keeps_whole_turns_and_preserves_order() -> None:
    """Concatenating the chunks back must reproduce the full ordered dialogue."""
    turns = [
        DialogueTurn(speaker="ALEX", text="First."),
        DialogueTurn(speaker="JORDAN", text="Second."),
        DialogueTurn(speaker="ALEX", text="Third."),
    ]
    xml = assemble_xml_dialogue(turns)
    chunks = chunk_by_byte_budget(xml, max_bytes=DEFAULT_BYTE_BUDGET)
    assert "\n".join(chunks) == xml


def test_chunking_does_not_drop_an_oversized_single_turn() -> None:
    """A single turn larger than the budget is emitted, never silently dropped."""
    big_text = "x" * 5000
    turns = [DialogueTurn(speaker="ALEX", text=big_text)]
    xml = assemble_xml_dialogue(turns)

    chunks = chunk_by_byte_budget(xml, max_bytes=DEFAULT_BYTE_BUDGET)

    assert len(chunks) == 1
    assert big_text in chunks[0]


# ---------------------------------------------------------------------------
# (c) Assembled turn order matches the script
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_preserves_turn_order_across_chunks() -> None:
    """With a tiny budget each turn becomes its own chunk; order must hold.

    Asserts the returned speakers + turn_indices track the script order, and
    that one segment is produced per renderable turn.
    """
    turns = [
        DialogueTurn(speaker="ALEX", text="One."),
        DialogueTurn(speaker="JORDAN", text="Two."),
        DialogueTurn(speaker="ALEX", text="Three."),
        DialogueTurn(speaker="JORDAN", text="Four."),
    ]
    client = _mock_tts_client()

    segments, speakers, turn_indices = await render_full_dialogue(
        turns=turns,
        tts_client=client,
        max_bytes=20,  # forces one turn per chunk
    )

    assert len(segments) == len(turns)
    assert speakers == ["ALEX", "JORDAN", "ALEX", "JORDAN"]
    assert turn_indices == [0, 1, 2, 3]
    assert client.call_gemini_multispeaker_tts.await_count == len(turns)


# ---------------------------------------------------------------------------
# Failure case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_empty_turns_raises() -> None:
    """An empty script must fail loud, not produce silent empty audio."""
    from agents.shared.exceptions import TTSRenderError

    client = _mock_tts_client()
    with pytest.raises(TTSRenderError):
        await render_full_dialogue(turns=[], tts_client=client)
