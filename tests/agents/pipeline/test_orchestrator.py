"""Unit tests for the per-story orchestrator (Phase 1d SP3).

DoD (phase file SP3 / Rule 9): the orchestrator chains
script → verify → TTS → caption → poster → persist; a ``VerificationHaltError``
SKIPS the story (never publishes). Every external (LLM, TTS, supabase, poster
client) is mocked at the boundary — no network, no key, no cost, no writes.

The happy-path test asserts the *wiring*: a grounded story produces a persisted
digest with caption rows and an audio URL; the halt test asserts the guardrail
is honored (no persist call).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydub import AudioSegment

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline import orchestrator as orch
from agents.pipeline.llm_clients import LLMClient
from agents.voice.gemini_tts import GeminiTTSClient

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

# A grounded two-host script the mocked scripting LLM "returns".
_SCRIPT_JSON = (
    '[{"speaker": "ALEX", "text": "What happened at the Emirates?"},'
    ' {"speaker": "JORDAN", "text": "Arsenal beat Liverpool two to one."}]'
)
# A fully-grounded verification verdict the mocked verification LLM "returns".
_VERIFY_GROUNDED = (
    '{"claims": [{"claim": "Arsenal beat Liverpool", "status": "SUPPORTED",'
    ' "source_evidence": "Arsenal beat Liverpool 2-1"}]}'
)
# An ungrounded verdict → triggers the halt.
_VERIFY_UNGROUNDED = (
    '{"claims": [{"claim": "Arsenal signed Mbappe", "status": "UNSUPPORTED",'
    ' "source_evidence": ""}]}'
)


@pytest.fixture
def canonical_story() -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id="cand-arsenal-001",
        canonical_title="Arsenal beat Liverpool 2-1 at the Emirates",
        canonical_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_normalized_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        canonical_primary_outlet_name="BBC",
        canonical_body_text=(
            "Arsenal beat Liverpool 2-1 at the Emirates Stadium on Saturday. "
            "Bukayo Saka scored both goals in the second half."
        ),
        covering_outlets=["bbc.com", "reuters.com", "cnn.com", "foxnews.com"],
        story_outlet_count=4,
    )


@pytest.fixture
def story_interest_tags() -> list[StoryInterestTag]:
    return [
        StoryInterestTag(
            story_interest_story_id="cand-arsenal-001",
            story_interest_interest_id="int-arsenal",
            story_interest_match_depth=0,
        )
    ]


def _llm_returning(*responses: str) -> LLMClient:
    """An LLMClient whose call_gemini returns the given responses in order."""
    client = LLMClient.__new__(LLMClient)
    client.call_gemini = AsyncMock(side_effect=list(responses))  # type: ignore[method-assign]
    return client


def _tts_returning_audio() -> GeminiTTSClient:
    """A GeminiTTSClient whose multispeaker call returns 1s of silent PCM."""
    client = GeminiTTSClient.__new__(GeminiTTSClient)
    # 1 second of 24kHz 16-bit mono silence.
    pcm = b"\x00\x00" * 24000
    client.call_gemini_multispeaker_tts = AsyncMock(return_value=(pcm, 24000))  # type: ignore[method-assign]
    return client


class FakeStorageBucket:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def upload(self, path: str, file: bytes, file_options: dict) -> None:  # noqa: ARG002
        return None

    def get_public_url(self, path: str) -> str:
        return f"https://storage.test/{self.bucket}/{path}"


class FakeTableQuery:
    _PK_BY_TABLE = {
        "digests": "digest_id",
        "caption_sentences": "caption_sentence_id",
        "detail_chunks": "detail_chunk_id",
        "story_trust": "story_trust_id",
        "story_sources": "story_source_id",
        "story_interests": "story_interest_id",
        "suggested_questions": "suggested_question_id",
    }

    def __init__(self, table: str, captured: dict) -> None:
        self.table = table
        self.captured = captured
        self._rows: list[dict] = []

    def insert(self, rows: list[dict]) -> "FakeTableQuery":
        self.captured.setdefault(self.table, []).extend(rows)
        pk = self._PK_BY_TABLE.get(self.table)
        self._rows = [
            {**row, **({pk: f"{self.table}-uuid-{i}"} if pk else {})}
            for i, row in enumerate(rows)
        ]
        return self

    def execute(self) -> MagicMock:
        response = MagicMock()
        response.data = self._rows
        return response


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.captured_inserts: dict[str, list[dict]] = {}
        self.storage = MagicMock()
        self.storage.from_ = lambda bucket: FakeStorageBucket(bucket)

    def table(self, table: str) -> FakeTableQuery:
        return FakeTableQuery(table, self.captured_inserts)


class TestOrchestrateHappyPath:
    """A grounded story → script → TTS → caption → persist → published."""

    @pytest.mark.asyncio
    async def test_grounded_story_is_published(
        self, canonical_story, story_interest_tags
    ) -> None:
        """The full chain publishes: persist called, caption rows + audio URL present."""
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED)
        tts = _tts_returning_audio()
        supabase = FakeSupabaseClient()

        result = await orch.orchestrate_story(
            story=canonical_story,
            story_interest_tags=story_interest_tags,
            llm_client=llm,
            tts_client=tts,
            supabase_client=supabase,
            poster_genai_client=None,  # posters disabled — must still publish
            story_id="FIXTURE-SP3-orch",
        )

        assert result.published is True
        assert result.skip_reason == ""
        assert result.persist_result is not None
        assert result.persist_result.story_id == "FIXTURE-SP3-orch"
        assert result.persist_result.digest_id
        assert result.persist_result.audio_url.endswith("digest.mp3")
        # caption_sentences were written.
        assert supabase.captured_inserts.get("caption_sentences")
        # story_interests propagated.
        assert (
            supabase.captured_inserts["story_interests"][0]["story_interest_story_id"]
            == "FIXTURE-SP3-orch"
        )

    @pytest.mark.asyncio
    async def test_poster_bytes_persisted_when_builder_succeeds(
        self, canonical_story, story_interest_tags, tmp_path
    ) -> None:
        """An injected poster builder's PNG is read + uploaded (poster_url set)."""
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED)
        tts = _tts_returning_audio()
        supabase = FakeSupabaseClient()

        poster_file = tmp_path / "poster.png"
        poster_file.write_bytes(b"\x89PNG-FAKE")

        def fake_builder(digest, client):  # noqa: ARG001
            report = MagicMock()
            report.poster_path = str(poster_file)
            return report

        result = await orch.orchestrate_story(
            story=canonical_story,
            story_interest_tags=story_interest_tags,
            llm_client=llm,
            tts_client=tts,
            supabase_client=supabase,
            poster_genai_client=MagicMock(),  # non-None enables the builder
            poster_builder=fake_builder,
            story_id="FIXTURE-SP3-poster",
        )
        assert result.published is True
        assert result.persist_result.poster_url is not None
        assert (
            result.persist_result.poster_object_path == "FIXTURE-SP3-poster/poster.png"
        )


class TestOrchestrateVerificationHalt:
    """The guardrail: an ungrounded story is skipped, never persisted."""

    @pytest.mark.asyncio
    async def test_ungrounded_story_skipped_not_persisted(
        self, canonical_story, story_interest_tags
    ) -> None:
        """A VerificationHaltError → published=False, no persist insert (Rule 12)."""
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_UNGROUNDED)
        tts = _tts_returning_audio()
        supabase = FakeSupabaseClient()

        result = await orch.orchestrate_story(
            story=canonical_story,
            story_interest_tags=story_interest_tags,
            llm_client=llm,
            tts_client=tts,
            supabase_client=supabase,
            poster_genai_client=None,
            story_id="FIXTURE-SP3-halt",
        )

        assert result.published is False
        assert result.skip_reason == "verification_halt"
        assert result.persist_result is None
        # Nothing was written — the guardrail fired before persist.
        assert supabase.captured_inserts == {}

    @pytest.mark.asyncio
    async def test_poster_failure_does_not_block_publish(
        self, canonical_story, story_interest_tags
    ) -> None:
        """A poster builder that raises is non-fatal — the digest still publishes."""
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED)
        tts = _tts_returning_audio()
        supabase = FakeSupabaseClient()

        def exploding_builder(digest, client):  # noqa: ARG001
            raise RuntimeError("nano banana down")

        result = await orch.orchestrate_story(
            story=canonical_story,
            story_interest_tags=story_interest_tags,
            llm_client=llm,
            tts_client=tts,
            supabase_client=supabase,
            poster_genai_client=MagicMock(),
            poster_builder=exploding_builder,
            story_id="FIXTURE-SP3-posterfail",
        )
        assert result.published is True
        assert result.persist_result.poster_url is None


class TestBuildCaptionTrack:
    """The caption-timing step time-slices the script across the audio duration."""

    def test_caption_track_spans_audio_duration(self) -> None:
        """The track's last word ends at the audio duration (no caption over silence)."""
        from agents.pipeline.models import DialogueTurn, DigestScript

        script = DigestScript(
            digest_story_id="s1",
            turns=[
                DialogueTurn(speaker="ALEX", text="Arsenal won at the Emirates."),
                DialogueTurn(speaker="JORDAN", text="Saka scored both goals."),
            ],
            word_count=8,
            estimated_duration_seconds=4,
        )
        track = orch.build_caption_track(script, audio_duration_ms=10_000)
        assert track.words
        assert track.words[-1].end_s == pytest.approx(10.0, abs=0.01)
        # one highlight per sentence
        highlights_per_sentence: dict[int, int] = {}
        for word in track.words:
            if word.is_highlight:
                highlights_per_sentence[word.sentence_index] = (
                    highlights_per_sentence.get(word.sentence_index, 0) + 1
                )
        assert set(highlights_per_sentence.values()) == {1}

    def test_caption_track_anchors_to_segment_timings(self) -> None:
        """With real turn windows, no word starts inside an inter-turn silence gap.

        Why this matters: the whole point of per-turn anchoring is that captions
        re-lock to the audio at every speaker turn instead of drifting across
        the assembler's silence gaps — a word timed inside a gap would highlight
        while nobody is speaking.
        """
        from agents.pipeline.models import DialogueTurn, DigestScript
        from agents.voice.models import SegmentTiming

        script = DigestScript(
            digest_story_id="s1",
            turns=[
                DialogueTurn(speaker="ALEX", text="Arsenal won at the Emirates."),
                DialogueTurn(speaker="JORDAN", text="Saka scored both goals."),
            ],
            word_count=8,
            estimated_duration_seconds=4,
        )
        timings = [
            SegmentTiming(turn_index=0, speaker="ALEX", start_ms=0, end_ms=4_000),
            SegmentTiming(turn_index=1, speaker="JORDAN", start_ms=4_500, end_ms=9_000),
        ]
        track = orch.build_caption_track(
            script, audio_duration_ms=10_000, segment_timings=timings
        )

        # Speech ends at the last window's boundary, not the padded audio end.
        assert track.speech_end_s == pytest.approx(9.0, abs=0.001)
        # No word interval intrudes into the 4.0s–4.5s silence gap.
        for word in track.words:
            assert not (4.0 < word.start_s < 4.5)
            assert not (4.0 < word.end_s < 4.5)
        # Sentences never span windows: Jordan's words start at the second window.
        jordan_words = [w for w in track.words if w.start_s >= 4.5]
        assert {w.sentence_index for w in jordan_words} == {1}
        # Still exactly one highlight per sentence.
        highlights_per_sentence: dict[int, int] = {}
        for word in track.words:
            if word.is_highlight:
                highlights_per_sentence[word.sentence_index] = (
                    highlights_per_sentence.get(word.sentence_index, 0) + 1
                )
        assert set(highlights_per_sentence.values()) == {1}


class TestRenderAudioBytes:
    """The TTS step assembles PCM into MP3 bytes + a real duration."""

    @pytest.mark.asyncio
    async def test_render_returns_mp3_bytes_and_duration(self) -> None:
        from agents.pipeline.models import DialogueTurn, DigestScript

        script = DigestScript(
            digest_story_id="s1",
            turns=[
                DialogueTurn(speaker="ALEX", text="Hello there."),
                DialogueTurn(speaker="JORDAN", text="Hi back."),
            ],
            word_count=4,
            estimated_duration_seconds=2,
        )
        audio_bytes, duration_ms, segment_timings = await orch.render_audio_bytes(
            script, _tts_returning_audio()
        )
        assert isinstance(audio_bytes, bytes) and len(audio_bytes) > 0
        assert duration_ms > 0
        # Real per-chunk speech boundaries come back for caption anchoring.
        assert segment_timings
        assert segment_timings[-1].end_ms <= duration_ms
        # The bytes decode back to an audio segment of the reported length.
        import io

        seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        assert abs(len(seg) - duration_ms) < 200
