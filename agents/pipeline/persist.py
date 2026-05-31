"""Persist (Phase 1d SP3): service-role writer for one produced digest.

NEW module (reference/reuse-map.md: persist = NEW — no TLDW analog; the donor
wrote per-user briefing rows, News20 writes shared story+digest content). Uses
``supabase-py`` with the **service-role** key (bypasses RLS — only the worker
writes content; clients are read-only per ``reference/supabase-schema.md`` §6).

Given one produced digest (the canonical story + grounded script + rendered
audio bytes + caption track + poster bytes + interest tags), this:

  1. Uploads the audio to the ``digest-audio`` bucket and (if present) the poster
     to ``story-posters`` → public URLs.
  2. INSERTs the content rows: ``stories`` → ``digests`` →
     ``caption_sentences`` / ``detail_chunks`` / ``story_trust`` /
     ``story_sources`` / ``suggested_questions`` / ``story_interests``.

Mapping to columns lives in ``persist_helpers.py`` (pure builders); this module
owns the Supabase client + the insert/upload ordering + blast-radius discipline.

BLAST-RADIUS DISCIPLINE (SP3 brief)
-----------------------------------
- INSERT only. Never UPDATE/DELETE an existing row.
- Every created row id and storage object path is collected on the returned
  ``PersistResult`` so a live run is fully auditable / cleanable.
- The Supabase client is INJECTED (the live e2e script builds the real
  service-role client; tests inject a mock) so this module never reads a secret
  itself and the test suite never touches the network.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.models import DigestScript
from agents.pipeline.persist_helpers import (
    build_caption_sentence_rows,
    build_detail_chunk_rows,
    build_digest_row,
    build_story_interest_rows,
    build_story_row,
    build_story_source_rows,
    build_story_trust_row,
    build_suggested_question_rows,
    derive_blindspot_lean,
    derive_coverage_counts,
    script_speaker_order,
)
from agents.pipeline.stages.forced_alignment import CaptionTrack
from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.persist")

# Reason: the public storage buckets (reference/supabase-schema.md §0: both
# public). Audio → digest-audio, poster → story-posters.
AUDIO_BUCKET = "digest-audio"
POSTER_BUCKET = "story-posters"

# Reason: the default segment when the canonical story carries no resolved
# segment — 'wildcard' is the catch-all editorial segment (segment_slug enum).
DEFAULT_SEGMENT_SLUG = "wildcard"

_VALID_SEGMENT_SLUGS = frozenset(
    {"geopolitics", "markets", "tech", "sport", "wildcard"}
)


class PersistResult(BaseModel):
    """Audit record of every row + storage object one persist created.

    Every id/path here was INSERTED/uploaded by this run (never an update) — the
    SP3 brief requires this so a live run is cleanable.

    Attributes:
        story_id: The ``stories.story_id`` created.
        digest_id: The ``digests.digest_id`` created.
        audio_url: Public URL of the uploaded digest audio.
        poster_url: Public URL of the uploaded poster (None if no poster).
        audio_object_path: ``digest-audio`` object path uploaded.
        poster_object_path: ``story-posters`` object path uploaded (None if none).
        caption_sentence_count: Number of ``caption_sentences`` rows inserted.
        detail_chunk_count: Number of ``detail_chunks`` rows inserted.
        story_source_count: Number of ``story_sources`` rows inserted.
        story_interest_count: Number of ``story_interests`` rows inserted.
        suggested_question_count: Number of ``suggested_questions`` rows inserted.
        created_table_row_ids: ``{table: [row_id, ...]}`` for every uuid PK
            returned by an insert (for auditable cleanup).

    Example:
        >>> result = PersistResult(
        ...     story_id="FIXTURE-SP3-abc", digest_id="d-uuid",
        ...     audio_url="https://.../audio.mp3",
        ...     audio_object_path="FIXTURE-SP3-abc/digest.mp3",
        ... )
        >>> result.story_id
        'FIXTURE-SP3-abc'
    """

    story_id: str = Field(..., description="The stories.story_id created")
    digest_id: str = Field(default="", description="The digests.digest_id created")
    audio_url: str = Field(default="", description="Public URL of the uploaded audio")
    poster_url: str | None = Field(
        default=None, description="Public URL of the uploaded poster"
    )
    audio_object_path: str = Field(
        default="", description="digest-audio object path uploaded"
    )
    poster_object_path: str | None = Field(
        default=None, description="story-posters object path uploaded"
    )
    caption_sentence_count: int = Field(default=0, ge=0)
    detail_chunk_count: int = Field(default=0, ge=0)
    story_source_count: int = Field(default=0, ge=0)
    story_interest_count: int = Field(default=0, ge=0)
    suggested_question_count: int = Field(default=0, ge=0)
    created_table_row_ids: dict[str, list[str]] = Field(
        default_factory=dict,
        description="{table: [uuid PK, ...]} for auditable cleanup",
    )


def _resolve_segment_slug(story: CanonicalStory) -> str:
    """Pick a valid ``segment_slug`` for the story (defaults to wildcard).

    Reason: ``stories.story_segment_slug`` is a NOT NULL enum FK. The canonical
    story does not carry a resolved segment yet (segment resolution is a
    later-phase join through the interest tree), so default to ``wildcard`` to
    keep the insert valid. FLAGGED for SP4 to backfill from the matched
    interest's ``interest_segment_slug``.
    """
    return DEFAULT_SEGMENT_SLUG


def _insert_rows(
    supabase_client: Any,
    table: str,
    rows: list[dict[str, Any]],
    result: PersistResult,
    pk_column: str | None,
) -> list[dict[str, Any]]:
    """Insert rows into one table and record returned PKs on the audit result.

    Args:
        supabase_client: The (real or mocked) supabase client.
        table: Target table name.
        rows: Column payloads to insert.
        result: The audit record to append returned PKs to.
        pk_column: The uuid PK column name to record (None to skip recording,
            e.g. text-PK ``stories`` recorded separately).

    Returns:
        The inserted rows as returned by Supabase (carry generated PKs).

    Raises:
        PipelineStageError: When the insert returns no data (fail loud).
    """
    if not rows:
        return []
    response = supabase_client.table(table).insert(rows).execute()
    data = getattr(response, "data", None) or []
    if not data:
        raise PipelineStageError(
            stage="persist",
            message=f"Insert into {table} returned no rows",
            fix_suggestion=f"Confirm service-role key writes {table}; check NOT NULL/FK constraints.",
        )
    if pk_column:
        ids = [str(row[pk_column]) for row in data if pk_column in row]
        if ids:
            result.created_table_row_ids.setdefault(table, []).extend(ids)
    return data


def upload_to_bucket(
    supabase_client: Any,
    bucket: str,
    object_path: str,
    content: bytes,
    content_type: str,
) -> str:
    """Upload bytes to a public Supabase storage bucket and return its URL.

    Args:
        supabase_client: The supabase client (service role).
        bucket: Bucket name (``digest-audio`` / ``story-posters``).
        object_path: Path within the bucket (unique per story).
        content: The file bytes.
        content_type: MIME type (e.g. ``audio/mpeg``, ``image/png``).

    Returns:
        The public URL for the uploaded object.

    Raises:
        PipelineStageError: When the upload fails.

    Example:
        >>> url = upload_to_bucket(client, "digest-audio", "s1/a.mp3", b"...", "audio/mpeg")  # doctest: +SKIP
    """
    storage = supabase_client.storage.from_(bucket)
    try:
        storage.upload(
            path=object_path,
            file=content,
            file_options={"content-type": content_type, "upsert": "false"},
        )
    except Exception as exc:  # noqa: BLE001 — re-raised as a typed stage error
        raise PipelineStageError(
            stage="persist",
            message=f"Upload to {bucket}/{object_path} failed: {exc}",
            fix_suggestion="Confirm the bucket exists, is public, and the service-role key can write it.",
        ) from exc
    public_url = storage.get_public_url(object_path)
    logger.info(
        "persist_upload_completed",
        bucket=bucket,
        object_path=object_path,
        content_type=content_type,
        bytes=len(content),
    )
    return public_url


def persist_digest(
    supabase_client: Any,
    story: CanonicalStory,
    script: DigestScript,
    caption_track: CaptionTrack,
    audio_bytes: bytes,
    audio_duration_ms: int,
    story_interest_tags: list[StoryInterestTag],
    poster_bytes: bytes | None = None,
    suggested_questions: list[str] | None = None,
    story_id: str | None = None,
    audio_content_type: str = "audio/mpeg",
    poster_content_type: str = "image/png",
) -> PersistResult:
    """Persist one produced digest end-to-end (uploads + content INSERTs).

    Ordering: upload audio (+ poster) → insert ``stories`` → ``digests`` → all
    child tables. Children FK to the persisted ``story_id`` / ``digest_id``, so
    the parents go first. INSERT only — never updates an existing row.

    Args:
        supabase_client: A service-role supabase client (injected; mocked in
            tests, real in the live e2e).
        story: The canonical story to persist.
        script: The grounded digest script (verification passed before this).
        caption_track: The aligned caption track (forced_alignment output).
        audio_bytes: The rendered digest audio file bytes.
        audio_duration_ms: Real assembled audio duration in ms.
        story_interest_tags: The story's ``story_interests`` tag payloads (SP1).
        poster_bytes: The graded poster PNG bytes (None if generation failed).
        suggested_questions: Optional suggested-question strings.
        story_id: Optional explicit ``stories.story_id`` (defaults to a stable
            slug derived from the canonical id). The live e2e passes a
            ``FIXTURE-SP3-`` prefixed id so the row is recognizable/cleanable.
        audio_content_type: Audio MIME type.
        poster_content_type: Poster MIME type.

    Returns:
        A :class:`PersistResult` listing every created row id + storage path.

    Raises:
        PipelineStageError: When a required insert/upload fails.

    Example:
        >>> result = persist_digest(client, story, script, track, b"...", 55000, tags)  # doctest: +SKIP
        >>> result.audio_url.startswith("http")
        True
    """
    resolved_story_id = story_id or f"sp3-{story.canonical_story_id}"[:255]
    segment_slug = _resolve_segment_slug(story)
    if segment_slug not in _VALID_SEGMENT_SLUGS:
        segment_slug = DEFAULT_SEGMENT_SLUG

    # Reason: confirm both anchors render (audit only; non-fatal).
    speaker_order = script_speaker_order(script)

    logger.info(
        "persist_digest_started",
        story_id=resolved_story_id,
        outlet_count=story.story_outlet_count,
        caption_word_count=len(caption_track.words),
        has_poster=poster_bytes is not None,
        speakers=sorted(set(speaker_order)),
    )

    result = PersistResult(story_id=resolved_story_id)

    # ── 1. Storage uploads (audio required, poster optional) ──
    audio_object_path = f"{resolved_story_id}/digest.mp3"
    result.audio_object_path = audio_object_path
    result.audio_url = upload_to_bucket(
        supabase_client,
        AUDIO_BUCKET,
        audio_object_path,
        audio_bytes,
        audio_content_type,
    )

    poster_url: str | None = None
    if poster_bytes:
        poster_object_path = f"{resolved_story_id}/poster.png"
        result.poster_object_path = poster_object_path
        poster_url = upload_to_bucket(
            supabase_client,
            POSTER_BUCKET,
            poster_object_path,
            poster_bytes,
            poster_content_type,
        )
        result.poster_url = poster_url

    # ── 2. Derive trust/coverage from covering outlets (static bias map) ──
    coverage_counts = derive_coverage_counts(story.covering_outlets)
    blindspot_lean = derive_blindspot_lean(coverage_counts)

    # ── 3. Insert stories (text PK — record explicitly) ──
    story_row = build_story_row(
        story=story,
        story_id=resolved_story_id,
        segment_slug=segment_slug,
        poster_url=poster_url,
        coverage_counts=coverage_counts,
        blindspot_lean=blindspot_lean,
    )
    _insert_rows(supabase_client, "stories", [story_row], result, pk_column=None)
    result.created_table_row_ids.setdefault("stories", []).append(resolved_story_id)

    # ── 4. Insert digests (uuid PK — capture digest_id for the caption FK) ──
    digest_row = build_digest_row(
        digest_story_id=resolved_story_id,
        audio_url=result.audio_url,
        duration_ms=audio_duration_ms,
        poster_url=poster_url,
    )
    inserted_digests = _insert_rows(
        supabase_client, "digests", [digest_row], result, pk_column="digest_id"
    )
    digest_id = str(inserted_digests[0]["digest_id"])
    result.digest_id = digest_id

    # ── 5. Insert caption_sentences (the karaoke hero table) ──
    caption_rows = build_caption_sentence_rows(
        digest_id=digest_id,
        story_id=resolved_story_id,
        caption_track=caption_track,
        turns_speaker_order=speaker_order,
    )
    _insert_rows(
        supabase_client,
        "caption_sentences",
        caption_rows,
        result,
        pk_column="caption_sentence_id",
    )
    result.caption_sentence_count = len(caption_rows)

    # ── 6. Insert detail_chunks ──
    detail_rows = build_detail_chunk_rows(
        resolved_story_id, story.canonical_body_text or story.canonical_title
    )
    _insert_rows(
        supabase_client,
        "detail_chunks",
        detail_rows,
        result,
        pk_column="detail_chunk_id",
    )
    result.detail_chunk_count = len(detail_rows)

    # ── 7. Insert story_trust (1:1) ──
    trust_row = build_story_trust_row(
        resolved_story_id, coverage_counts, blindspot_lean
    )
    _insert_rows(
        supabase_client, "story_trust", [trust_row], result, pk_column="story_trust_id"
    )

    # ── 8. Insert story_sources ──
    source_rows = build_story_source_rows(resolved_story_id, story)
    _insert_rows(
        supabase_client,
        "story_sources",
        source_rows,
        result,
        pk_column="story_source_id",
    )
    result.story_source_count = len(source_rows)

    # ── 9. Insert story_interests ──
    interest_rows = build_story_interest_rows(resolved_story_id, story_interest_tags)
    _insert_rows(
        supabase_client,
        "story_interests",
        interest_rows,
        result,
        pk_column="story_interest_id",
    )
    result.story_interest_count = len(interest_rows)

    # ── 10. Insert suggested_questions (optional) ──
    question_rows = build_suggested_question_rows(
        resolved_story_id, suggested_questions or []
    )
    if question_rows:
        _insert_rows(
            supabase_client,
            "suggested_questions",
            question_rows,
            result,
            pk_column="suggested_question_id",
        )
        result.suggested_question_count = len(question_rows)

    logger.info(
        "persist_digest_completed",
        story_id=resolved_story_id,
        digest_id=digest_id,
        audio_url=result.audio_url,
        poster_url=poster_url,
        caption_sentence_count=result.caption_sentence_count,
        detail_chunk_count=result.detail_chunk_count,
        story_source_count=result.story_source_count,
        story_interest_count=result.story_interest_count,
    )
    return result


def make_story_id(prefix: str = "") -> str:
    """Make a stable text ``story_id`` (optionally prefixed for fixtures).

    Args:
        prefix: A recognizable prefix (e.g. ``"FIXTURE-SP3-"``) so a live-run row
            is auditable/cleanable.

    Returns:
        A unique text id.

    Example:
        >>> make_story_id("FIXTURE-SP3-").startswith("FIXTURE-SP3-")
        True
    """
    return f"{prefix}{uuid.uuid4().hex[:12]}"
