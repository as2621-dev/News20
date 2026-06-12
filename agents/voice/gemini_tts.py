"""Gemini 2.5 Flash multi-speaker TTS renderer.

Ported from TLDW (`agents/voice/gemini_tts.py`). The chunking and voice-mapping
logic is kept verbatim; the only adaptation is that the heavy TLDW ``LLMClient``
dependency (which drags in OpenAI / Pinecone-bound settings) is replaced by a
minimal in-module ``GeminiTTSClient`` carrying the exact proven Gemini
multi-speaker call shape. This keeps the M0 spike's dependency footprint to
``google-genai`` only (Rule 2/3).

ALEX maps to ``Person1`` at the prompt level and to the Gemini ``Leda`` voice.
JORDAN maps to ``Person2`` and to the ``Sadaltager`` voice. Documented fallback
pair: ``Aoede`` / ``Charon``. Internal labels stay ALEX/JORDAN everywhere; the
Person mapping happens only at the call boundary.

Chunking: Gemini's multi-speaker TTS has a per-call output budget. Long scripts
are split on ``<Person[12]>`` boundaries so each chunk stays under ~4000 input
bytes while preserving speaker continuity.

Example:
    >>> from agents.voice.gemini_tts import GeminiTTSClient, render_full_dialogue
    >>> client = GeminiTTSClient()
    >>> segments, speakers, turn_indices = await render_full_dialogue(
    ...     turns=digest.turns,
    ...     tts_client=client,
    ... )
"""

from __future__ import annotations

import asyncio
import io
import re
import wave
from typing import Sequence

from pydub import AudioSegment

from agents.shared.exceptions import TTSRenderError
from agents.shared.logger import get_logger
from agents.shared.settings import Settings
from agents.voice.models import DialogueTurn

logger = get_logger("voice.gemini_tts")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"

# Reason: ALEX -> Person1 -> Leda. JORDAN -> Person2 -> Sadaltager. Plan-locked.
VOICE_MAP_GEMINI: dict[str, str] = {
    "ALEX": "Leda",
    "JORDAN": "Sadaltager",
}

# Reason: documented fallback pair when the primary voices are unavailable —
# Aoede (ALEX) / Charon (JORDAN).
VOICE_MAP_FALLBACK: dict[str, str] = {
    "ALEX": "Aoede",
    "JORDAN": "Charon",
}

# Reason: prompt-level speaker labels Gemini multi-speaker TTS expects in the
# rendered text. Internal speaker labels stay ALEX/JORDAN everywhere; we map
# at the call boundary only.
SPEAKER_TO_PERSON: dict[str, str] = {"ALEX": "Person1", "JORDAN": "Person2"}
PERSON_TO_SPEAKER: dict[str, str] = {"Person1": "ALEX", "Person2": "JORDAN"}

# Reason: bytes (not chars) because Gemini limits the input on a per-call basis
# around ~5000 bytes of speakable text. 4000 leaves headroom for the preamble
# instruction we prepend to each chunk.
DEFAULT_BYTE_BUDGET = 4000

_TURN_TAG_REGEX = re.compile(r"<(Person[12])>(.*?)</\1>", re.DOTALL)


# ---------------------------------------------------------------------------
# Minimal Gemini TTS client (call boundary)
# ---------------------------------------------------------------------------


class GeminiTTSClient:
    """Thin wrapper around the Gemini multi-speaker TTS call.

    Holds the exact proven call shape from TLDW's
    ``LLMClient.call_gemini_multispeaker_tts`` without the rest of the TLDW LLM
    client. The ``google-genai`` client is created lazily so importing this
    module never triggers an API call or key read.

    Attributes:
        settings: Application settings carrying the Gemini key(s).

    Example:
        >>> client = GeminiTTSClient()
        >>> pcm_bytes, sample_rate = await client.call_gemini_multispeaker_tts(
        ...     prompt="Person1: Hello.\\nPerson2: Hi.",
        ...     speaker_voice_map={"Person1": "Leda", "Person2": "Sadaltager"},
        ... )
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        # Reason: lazy client — avoid import-time API key reads / network init.
        self._genai_client: object | None = None

    def _get_client(self) -> object:
        """Get or create the google-genai client using the resolved TTS key."""
        if self._genai_client is None:
            # Reason: import locally so the module imports without google-genai
            # installed (tests mock this client out entirely).
            from google import genai

            self._genai_client = genai.Client(
                api_key=self.settings.resolved_gemini_tts_key()
            )
        return self._genai_client

    async def call_gemini_multispeaker_tts(
        self,
        prompt: str,
        speaker_voice_map: dict[str, str],
        model: str = GEMINI_TTS_MODEL,
    ) -> tuple[bytes, int]:
        """Render a multi-speaker dialogue prompt to PCM audio via Gemini TTS.

        The Gemini SDK ``generate_content`` TTS call is synchronous; it is
        offloaded to a thread so callers stay async.

        Args:
            prompt: Dialogue text with explicit ``Person1:`` / ``Person2:``
                prefixes that match the keys of ``speaker_voice_map``.
            speaker_voice_map: Maps prompt prefix (e.g. ``"Person1"``) to a
                Gemini prebuilt voice name (e.g. ``"Leda"``).
            model: Gemini multi-speaker TTS model name.

        Returns:
            Tuple of ``(pcm_bytes, sample_rate)``. Sample rate parsed from the
            response ``mime_type`` (defaults to 24_000 if absent).
        """
        from google.genai import types as genai_types

        speaker_voice_configs = [
            genai_types.SpeakerVoiceConfig(
                speaker=speaker_label,
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=voice_name,
                    ),
                ),
            )
            for speaker_label, voice_name in speaker_voice_map.items()
        ]
        speech_config = genai_types.SpeechConfig(
            multi_speaker_voice_config=genai_types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=speaker_voice_configs,
            ),
        )

        def _call_sync() -> tuple[bytes, int]:
            client = self._get_client()
            response = client.models.generate_content(  # type: ignore[attr-defined]
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=speech_config,
                ),
            )
            audio_part = response.candidates[0].content.parts[0]
            pcm_bytes: bytes = audio_part.inline_data.data
            mime = audio_part.inline_data.mime_type or "audio/L16;rate=24000"
            sample_rate = 24000
            for chunk in mime.split(";"):
                stripped = chunk.strip()
                if stripped.startswith("rate="):
                    try:
                        sample_rate = int(stripped.split("=", 1)[1])
                    except ValueError:
                        pass
            return pcm_bytes, sample_rate

        return await asyncio.to_thread(_call_sync)


# ---------------------------------------------------------------------------
# XML assembly + chunking
# ---------------------------------------------------------------------------


def assemble_xml_dialogue(turns: Sequence[DialogueTurn]) -> str:
    """Render a sequence of ALEX/JORDAN turns as Person1/Person2 XML.

    Args:
        turns: Ordered dialogue turns transcribed from the digest script.

    Returns:
        A single string of alternating ``<Person1>...</Person1>`` /
        ``<Person2>...</Person2>`` tags, newline-separated.
    """
    parts: list[str] = []
    for turn in turns:
        person = SPEAKER_TO_PERSON.get(turn.speaker)
        if person is None:
            # Reason: only ALEX / JORDAN render via Gemini TTS.
            continue
        text = turn.text.strip()
        if not text:
            continue
        parts.append(f"<{person}>{text}</{person}>")
    return "\n".join(parts)


def chunk_by_byte_budget(
    xml: str,
    max_bytes: int = DEFAULT_BYTE_BUDGET,
) -> list[str]:
    """Split a Person1/Person2 XML dialogue into chunks under ``max_bytes``.

    Splits on ``<Person[12]>`` boundaries so each chunk stays a complete set of
    turns. A single turn longer than ``max_bytes`` is emitted as its own chunk
    (we never silently drop a speaker).

    Args:
        xml: The full assembled XML dialogue.
        max_bytes: Soft byte budget per chunk.

    Returns:
        Ordered list of XML chunks.
    """
    matches = _TURN_TAG_REGEX.findall(xml)
    if not matches:
        return [xml] if xml.strip() else []

    chunks: list[str] = []
    current_lines: list[str] = []
    current_bytes = 0
    for tag, body in matches:
        line = f"<{tag}>{body}</{tag}>"
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for the joining newline
        if current_lines and current_bytes + line_bytes > max_bytes:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_bytes = line_bytes
        else:
            current_lines.append(line)
            current_bytes += line_bytes

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks


# ---------------------------------------------------------------------------
# Render: PCM bytes -> WAV bytes -> pydub AudioSegment
# ---------------------------------------------------------------------------


def _pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    """Wrap raw 16-bit PCM mono in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


async def render_chunk(
    xml_chunk: str,
    tts_client: GeminiTTSClient,
    voice_alex: str = VOICE_MAP_GEMINI["ALEX"],
    voice_jordan: str = VOICE_MAP_GEMINI["JORDAN"],
    model: str = GEMINI_TTS_MODEL,
) -> AudioSegment:
    """Render a Person1/Person2 XML chunk to a single ``AudioSegment``.

    Args:
        xml_chunk: A piece of the XML dialogue to render.
        tts_client: Initialized GeminiTTSClient.
        voice_alex: Gemini voice name bound to Person1 (ALEX).
        voice_jordan: Gemini voice name bound to Person2 (JORDAN).
        model: Gemini TTS model name.

    Returns:
        A pydub ``AudioSegment`` of the synthesized chunk.

    Raises:
        TTSRenderError: When Gemini returns no audio bytes.
    """
    # Reason: Gemini multi-speaker TTS infers turn-taking from "Speaker:"
    # prefixes that match the speaker_voice_configs. Convert the XML tags to
    # bare Person1/Person2 prefixes so the renderer binds the right voices.
    prompt_lines: list[str] = [
        "TTS the following conversation between Person1 and Person2 — two "
        "podcast co-hosts who clearly enjoy each other's company. Person1 is "
        "playful and quick-witted: audible smiles, surprise, comic timing, "
        "genuine reactions. Person2 is warm and sincere — amused by Person1, "
        "occasionally laughing along, but grounded and clear on the facts. "
        "Perform it with lively conversational energy and real emotional "
        "range: varied pacing, natural emphasis, reactive turn-taking where "
        "each host audibly responds to the other — never flat, never a "
        "monotone read. Keep a brief natural beat at each speaker hand-off. "
        "Do not read the speaker labels aloud.",
    ]
    for tag, body in _TURN_TAG_REGEX.findall(xml_chunk):
        text = body.strip()
        if not text:
            continue
        prompt_lines.append(f"{tag}: {text}")
    prompt = "\n".join(prompt_lines)

    speaker_voice_map = {
        "Person1": voice_alex,
        "Person2": voice_jordan,
    }

    pcm_bytes, sample_rate = await tts_client.call_gemini_multispeaker_tts(
        prompt=prompt,
        speaker_voice_map=speaker_voice_map,
        model=model,
    )

    if not pcm_bytes:
        raise TTSRenderError(
            message="Gemini multi-speaker TTS returned no audio bytes",
            audio_step="gemini_multispeaker_tts",
            fix_suggestion=(
                "Check Gemini TTS quota, GEMINI_API_KEY / GEMINI_API_KEY_TTS validity, "
                "and that the chunk does not exceed the byte budget."
            ),
        )

    wav_bytes = _pcm_to_wav_bytes(pcm_bytes, sample_rate=sample_rate)
    return AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")


# ---------------------------------------------------------------------------
# Top-level entry point: render_full_dialogue
# ---------------------------------------------------------------------------


async def render_full_dialogue(
    turns: Sequence[DialogueTurn],
    tts_client: GeminiTTSClient,
    voice_alex: str = VOICE_MAP_GEMINI["ALEX"],
    voice_jordan: str = VOICE_MAP_GEMINI["JORDAN"],
    model: str = GEMINI_TTS_MODEL,
    max_bytes: int = DEFAULT_BYTE_BUDGET,
) -> tuple[list[AudioSegment], list[str], list[int]]:
    """Render the full ALEX/JORDAN dialogue via Gemini multi-speaker TTS.

    Returns the ``(segments, speakers, turn_indices)`` triple expected by
    ``agents/voice/audio.py:assemble_episode``. Because Gemini renders each
    chunk as a single audio blob, ``segments`` carries one segment per rendered
    chunk and ``speakers`` / ``turn_indices`` annotate the leading speaker /
    first-turn-in-chunk respectively.

    Args:
        turns: Ordered dialogue turns from the digest script.
        tts_client: Initialized GeminiTTSClient.
        voice_alex: Gemini voice for Person1 (ALEX).
        voice_jordan: Gemini voice for Person2 (JORDAN).
        model: Gemini TTS model name.
        max_bytes: Soft byte budget for chunking.

    Returns:
        Tuple of ``(segments, speakers, turn_indices)`` aligned by index.

    Raises:
        TTSRenderError: When the dialogue is empty or rendering fails.
    """
    if not turns:
        raise TTSRenderError(
            message="No dialogue turns provided to Gemini multi-speaker TTS",
            audio_step="render_full_dialogue",
            fix_suggestion="Verify the digest script has at least one ALEX/JORDAN turn.",
        )

    full_xml = assemble_xml_dialogue(turns)
    if not full_xml.strip():
        raise TTSRenderError(
            message="Dialogue turns produced no Person1/Person2 XML",
            audio_step="render_full_dialogue",
            fix_suggestion="Inspect the turns — speaker labels must be ALEX/JORDAN.",
        )

    chunks = chunk_by_byte_budget(full_xml, max_bytes=max_bytes)

    # Reason: precompute per-turn speaker order for chunk leader / index
    # bookkeeping. Skips any speaker that isn't ALEX or JORDAN.
    renderable_turns: list[tuple[int, DialogueTurn]] = [
        (idx, turn)
        for idx, turn in enumerate(turns)
        if turn.speaker in SPEAKER_TO_PERSON
    ]

    segments: list[AudioSegment] = []
    speakers: list[str] = []
    turn_indices: list[int] = []

    cursor = 0  # Reason: walks renderable_turns to map each chunk to its turns.
    for chunk_index, xml_chunk in enumerate(chunks):
        chunk_turn_count = len(_TURN_TAG_REGEX.findall(xml_chunk))
        if chunk_turn_count == 0:
            continue

        chunk_turns = renderable_turns[cursor : cursor + chunk_turn_count]
        cursor += chunk_turn_count
        if not chunk_turns:
            continue

        first_turn_index, first_turn = chunk_turns[0]

        logger.info(
            "gemini_tts_chunk_started",
            chunk_index=chunk_index,
            chunk_turns=chunk_turn_count,
            chunk_bytes=len(xml_chunk.encode("utf-8")),
            voice_alex=voice_alex,
            voice_jordan=voice_jordan,
            first_turn_index=first_turn_index,
            first_turn_speaker=first_turn.speaker,
        )

        try:
            segment = await render_chunk(
                xml_chunk=xml_chunk,
                tts_client=tts_client,
                voice_alex=voice_alex,
                voice_jordan=voice_jordan,
                model=model,
            )
        except TTSRenderError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "gemini_tts_chunk_failed",
                chunk_index=chunk_index,
                error_message=str(exc)[:300],
                fix_suggestion="Check Gemini TTS API status and the GEMINI_API_KEY_TTS quota.",
            )
            raise TTSRenderError(
                message=f"Gemini TTS chunk {chunk_index} failed: {exc}",
                audio_step="render_chunk",
                fix_suggestion="Check Gemini TTS quota and key validity.",
            ) from exc

        segments.append(segment)
        speakers.append(first_turn.speaker)
        turn_indices.append(first_turn_index)

    logger.info(
        "gemini_tts_render_completed",
        total_chunks=len(segments),
        total_duration_ms=sum(len(s) for s in segments),
    )

    return segments, speakers, turn_indices
