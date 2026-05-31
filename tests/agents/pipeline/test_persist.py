"""Unit tests for the service-role persist writer (Phase 1d SP3).

DoD (phase file SP3 / Rule 9):
  - the caption-JSON → ``caption_sentences`` mapping is LOSSLESS (one row per
    sentence; ``word_tokens`` with ms timings; exactly one highlight/sentence) —
    a dedicated mapping test.
  - persist maps each model field to the right column — a MOCKED supabase client
    captures the exact insert payloads and the test asserts on them.

The supabase client + storage are mocked at the boundary (CLAUDE.md mandate) —
no network, no key, no writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.models import DialogueTurn, DigestScript
from agents.pipeline.persist import persist_digest, upload_to_bucket
from agents.pipeline.persist_helpers import (
    bias_lean_for_domain,
    build_caption_sentence_rows,
    derive_blindspot_lean,
    derive_coverage_counts,
)
from agents.pipeline.stages.forced_alignment import align_transcript_to_audio

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


class FakeStorageBucket:
    """Captures uploads + returns a deterministic public URL."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.uploads: list[dict] = []

    def upload(self, path: str, file: bytes, file_options: dict) -> None:
        self.uploads.append({"path": path, "bytes": len(file), "options": file_options})

    def get_public_url(self, path: str) -> str:
        return f"https://storage.test/{self.bucket}/{path}"


class FakeTableQuery:
    """Captures an insert payload and returns rows with synthetic uuid PKs."""

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
        # Reason: echo the inserted rows back with a synthetic uuid PK so the
        # writer can capture digest_id (caption FK) and audit ids.
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
    """A mock supabase client capturing every insert payload + upload."""

    def __init__(self) -> None:
        self.captured_inserts: dict[str, list[dict]] = {}
        self.storage_buckets: dict[str, FakeStorageBucket] = {}
        self.storage = MagicMock()
        self.storage.from_ = self._from_bucket

    def table(self, table: str) -> FakeTableQuery:
        return FakeTableQuery(table, self.captured_inserts)

    def _from_bucket(self, bucket: str) -> FakeStorageBucket:
        return self.storage_buckets.setdefault(bucket, FakeStorageBucket(bucket))


@pytest.fixture
def canonical_story() -> CanonicalStory:
    """A 4-outlet (1 left / 2 center / 1 right) Arsenal story with a body."""
    return CanonicalStory(
        canonical_story_id="cand-arsenal-001",
        canonical_title="Arsenal beat Liverpool 2-1 at the Emirates",
        canonical_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_normalized_url="https://bbc.com/sport/arsenal-liverpool",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="bbc.com",
        canonical_primary_outlet_name="BBC",
        canonical_body_text=(
            "Arsenal beat Liverpool 2-1 at the Emirates Stadium on Saturday.\n\n"
            "Bukayo Saka scored both goals in the second half."
        ),
        covering_outlets=["bbc.com", "reuters.com", "cnn.com", "foxnews.com"],
        story_outlet_count=4,
    )


@pytest.fixture
def digest_script() -> DigestScript:
    return DigestScript(
        digest_story_id="cand-arsenal-001",
        turns=[
            DialogueTurn(speaker="ALEX", text="What happened at the Emirates?"),
            DialogueTurn(speaker="JORDAN", text="Arsenal beat Liverpool two-one."),
        ],
        word_count=10,
        estimated_duration_seconds=4,
        source_url="https://bbc.com/sport/arsenal-liverpool",
    )


@pytest.fixture
def story_interest_tags() -> list[StoryInterestTag]:
    return [
        StoryInterestTag(
            story_interest_story_id="cand-arsenal-001",
            story_interest_interest_id="int-arsenal",
            story_interest_match_depth=0,
        ),
        StoryInterestTag(
            story_interest_story_id="cand-arsenal-001",
            story_interest_interest_id="int-soccer",
            story_interest_match_depth=1,
        ),
    ]


class TestCaptionMappingLossless:
    """The caption-JSON → caption_sentences mapping must lose nothing (DoD)."""

    def test_one_row_per_sentence_one_highlight_each(self) -> None:
        """Each sentence → one row; each row's word_tokens has exactly one highlight."""
        track = align_transcript_to_audio(
            digest_id="d1",
            sentences=["Arsenal won at the Emirates.", "Saka scored both goals."],
            audio_duration_s=10.0,
            preferred_keywords=["Emirates", "Saka"],
        )
        rows = build_caption_sentence_rows(
            digest_id="dig-1",
            story_id="s1",
            caption_track=track,
            turns_speaker_order=["ALEX", "JORDAN"],
        )
        assert len(rows) == 2
        for row in rows:
            highlights = [t for t in row["word_tokens"] if t["is_highlight"]]
            assert len(highlights) == 1, "exactly one highlight per sentence"

    def test_word_tokens_carry_ms_timings_in_order(self) -> None:
        """word_tokens preserve every word with monotonic ms start/end (lossless)."""
        track = align_transcript_to_audio(
            digest_id="d1",
            sentences=["Arsenal won at the Emirates."],
            audio_duration_s=5.0,
        )
        rows = build_caption_sentence_rows("dig-1", "s1", track, ["ALEX"])
        tokens = rows[0]["word_tokens"]
        # No word dropped: token count == source word count.
        assert [t["word_text"] for t in tokens] == [
            "Arsenal",
            "won",
            "at",
            "the",
            "Emirates.",
        ]
        # ms timings monotonic non-decreasing, derived from the track seconds.
        starts = [t["start_ms"] for t in tokens]
        assert starts == sorted(starts)
        assert all(t["end_ms"] >= t["start_ms"] for t in tokens)
        # Sentence span matches the first/last token ms.
        assert rows[0]["sentence_start_ms"] == tokens[0]["start_ms"]
        assert rows[0]["sentence_end_ms"] == tokens[-1]["end_ms"]

    def test_anchor_alternates_by_sentence_index(self) -> None:
        """anchor_speaker alternates ALEX/JORDAN per sentence (st.anchors[si % 2])."""
        track = align_transcript_to_audio(
            digest_id="d1",
            sentences=["One sentence here.", "Two sentence here.", "Three here now."],
            audio_duration_s=10.0,
        )
        rows = build_caption_sentence_rows("dig-1", "s1", track, ["ALEX", "JORDAN"])
        assert [r["anchor_speaker"] for r in rows] == ["ALEX", "JORDAN", "ALEX"]


class TestTrustDerivation:
    """Static outlet→bias coverage + blindspot derivation."""

    def test_coverage_counts_bucket_by_static_bias(self) -> None:
        """Covering outlets bucket into left/center/right by the static table."""
        counts = derive_coverage_counts(
            ["cnn.com", "reuters.com", "apnews.com", "foxnews.com"]
        )
        assert counts == {"left": 1, "center": 2, "right": 1, "total": 4}

    def test_blindspot_flags_undercovered_side(self) -> None:
        """A side with <30% coverage while others hold >70% is the blindspot."""
        # 8 left, 1 center, 0 right → right is the blindspot.
        counts = {"left": 8, "center": 1, "right": 0, "total": 9}
        assert derive_blindspot_lean(counts) == "right"

    def test_no_blindspot_when_balanced(self) -> None:
        """Edge: balanced coverage → no blindspot."""
        counts = derive_coverage_counts(
            ["cnn.com", "foxnews.com", "reuters.com", "apnews.com"]
        )
        assert derive_blindspot_lean(counts) is None

    def test_unknown_domain_defaults_center(self) -> None:
        """Edge: an outlet absent from the static table counts as center."""
        assert bias_lean_for_domain("unknown-blog.example") == "center"


class TestPersistColumnMapping:
    """persist maps each model field to the right column (mocked client)."""

    def test_persist_inserts_all_tables_with_correct_columns(
        self, canonical_story, digest_script, story_interest_tags
    ) -> None:
        """The full persist writes every table with the schema's columns (DoD)."""
        client = FakeSupabaseClient()
        track = align_transcript_to_audio(
            digest_id=canonical_story.canonical_story_id,
            sentences=["Arsenal won at the Emirates."],
            audio_duration_s=5.0,
        )

        result = persist_digest(
            supabase_client=client,
            story=canonical_story,
            script=digest_script,
            caption_track=track,
            audio_bytes=b"FAKE-MP3-BYTES",
            audio_duration_ms=55000,
            story_interest_tags=story_interest_tags,
            poster_bytes=b"FAKE-PNG-BYTES",
            suggested_questions=["What happened?"],
            story_id="FIXTURE-SP3-test",
        )

        inserts = client.captured_inserts

        # stories: text PK + headline + outlet count + blindspot.
        story_row = inserts["stories"][0]
        assert story_row["story_id"] == "FIXTURE-SP3-test"
        assert story_row["story_headline"] == canonical_story.canonical_title
        assert story_row["story_outlet_count"] == 4
        assert story_row["story_ambient_poster_url"].startswith("https://storage.test/")

        # digests: FK to the story + the uploaded audio URL + current flag.
        digest_row = inserts["digests"][0]
        assert digest_row["digest_story_id"] == "FIXTURE-SP3-test"
        assert digest_row["digest_audio_url"].endswith("digest.mp3")
        assert digest_row["digest_duration_ms"] == 55000
        assert digest_row["digest_is_current"] is True

        # caption_sentences: FK to the captured digest uuid + word_tokens.
        caption_row = inserts["caption_sentences"][0]
        assert caption_row["caption_digest_id"] == result.digest_id
        assert caption_row["caption_story_id"] == "FIXTURE-SP3-test"
        assert "word_tokens" in caption_row and caption_row["word_tokens"]

        # story_trust: 1:1 coverage counts.
        trust_row = inserts["story_trust"][0]
        assert trust_row["trust_story_id"] == "FIXTURE-SP3-test"
        assert trust_row["coverage_outlet_count"] == 4

        # story_sources: one per covering outlet, with bias lean.
        assert len(inserts["story_sources"]) == 4
        assert all("source_bias_lean" in r for r in inserts["story_sources"])

        # story_interests: leaf + parent depths preserved.
        depths = {r["story_interest_match_depth"] for r in inserts["story_interests"]}
        assert depths == {0, 1}
        assert all(
            r["story_interest_story_id"] == "FIXTURE-SP3-test"
            for r in inserts["story_interests"]
        )

        # suggested_questions inserted.
        assert inserts["suggested_questions"][0]["question_text"] == "What happened?"

        # Audit result lists the created ids + storage paths.
        assert result.story_id == "FIXTURE-SP3-test"
        assert result.digest_id
        assert result.audio_object_path == "FIXTURE-SP3-test/digest.mp3"
        assert result.poster_object_path == "FIXTURE-SP3-test/poster.png"
        assert result.audio_url and result.poster_url

    def test_persist_without_poster_still_publishes(
        self, canonical_story, digest_script, story_interest_tags
    ) -> None:
        """A missing poster does not block publishing — audio + captions persist."""
        client = FakeSupabaseClient()
        track = align_transcript_to_audio(
            digest_id=canonical_story.canonical_story_id,
            sentences=["Arsenal won."],
            audio_duration_s=3.0,
        )
        result = persist_digest(
            supabase_client=client,
            story=canonical_story,
            script=digest_script,
            caption_track=track,
            audio_bytes=b"FAKE",
            audio_duration_ms=46000,
            story_interest_tags=story_interest_tags,
            poster_bytes=None,
            story_id="FIXTURE-SP3-nopost",
        )
        assert result.poster_url is None
        assert result.poster_object_path is None
        assert result.audio_url  # audio still uploaded
        assert "story_posters" not in client.storage_buckets
        assert client.captured_inserts["stories"][0]["story_ambient_poster_url"] is None

    def test_failed_insert_raises_loud(
        self, canonical_story, digest_script, story_interest_tags
    ) -> None:
        """Failure case: an insert that returns no rows raises (fail loud, Rule 12)."""
        client = FakeSupabaseClient()

        # Reason: force the stories insert to return empty data.
        empty_response = MagicMock()
        empty_response.data = []

        def _empty_table(table: str):
            q = MagicMock()
            q.insert.return_value = q
            q.execute.return_value = empty_response
            return q

        client.table = _empty_table  # type: ignore[assignment]
        track = align_transcript_to_audio(
            digest_id="x", sentences=["A b."], audio_duration_s=2.0
        )
        from agents.shared.exceptions import PipelineStageError

        with pytest.raises(PipelineStageError):
            persist_digest(
                supabase_client=client,
                story=canonical_story,
                script=digest_script,
                caption_track=track,
                audio_bytes=b"FAKE",
                audio_duration_ms=46000,
                story_interest_tags=story_interest_tags,
                story_id="FIXTURE-SP3-fail",
            )


class TestUploadToBucket:
    """The storage upload boundary returns a public URL + raises on failure."""

    def test_upload_returns_public_url(self) -> None:
        client = FakeSupabaseClient()
        url = upload_to_bucket(
            client, "digest-audio", "s1/digest.mp3", b"bytes", "audio/mpeg"
        )
        assert url == "https://storage.test/digest-audio/s1/digest.mp3"
        assert client.storage_buckets["digest-audio"].uploads[0]["bytes"] == 5

    def test_upload_failure_raises_stage_error(self) -> None:
        from agents.shared.exceptions import PipelineStageError

        client = MagicMock()
        bucket = MagicMock()
        bucket.upload.side_effect = RuntimeError("bucket gone")
        client.storage.from_.return_value = bucket
        with pytest.raises(PipelineStageError):
            upload_to_bucket(client, "digest-audio", "x", b"b", "audio/mpeg")
