"""Pydantic v2 mirror of the Remotion ``DigestManifest`` contract.

These models mirror ``remotion/src/manifest.ts`` field-for-field — they are the
Python side of the Python -> Remotion render seam. They are split out of
``agents.m0.build_render_manifest`` so the contract (the *shape*) lives apart
from the assembly logic (ffprobe, duration, poster resolution) and each file
stays under the 500-LoC agent-file limit.

Single-poster format (supersedes the earlier 8-cut Ken Burns format): ONE
poster-grade 9:16 image is the full-timeline background; the headline card is a
brief intro overlay, and the SP2 caption track is a separate overlay layer.
``durationInFrames`` is the whole timeline (``round(audio_duration_s * fps)``);
``kenBurns`` is OPTIONAL — omit it to get SP3's static-first gentle default drift.

The locked composition geometry constants (``FPS`` / ``WIDTH`` / ``HEIGHT``) live
here too, since the models reference them.

Example:
    >>> from agents.m0.manifest_models import DigestManifest, FPS
    >>> FPS
    30
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Locked composition geometry (mirrors the literals in manifest.ts).
FPS = 30
WIDTH = 1080
HEIGHT = 1920


class KenBurns(BaseModel):
    """Ken Burns drift across the whole timeline — mirrors ``KenBurns`` in ``manifest.ts``.

    Static-first: the static drift is interpolated start -> end over the full
    timeline. Omitting ``kenBurns`` from the manifest lets SP3 apply its very
    gentle default zoom (no pan), so a long-held poster never feels dead while
    the caption band stays still.
    """

    startScale: float = Field(
        ..., description="Scale at the start of the timeline (1 = no zoom)"
    )
    endScale: float = Field(..., description="Scale at the end of the timeline")
    startTranslateX: float = Field(
        ..., description="Horizontal pan px at start (keep 0 static-first)"
    )
    endTranslateX: float = Field(
        ..., description="Horizontal pan px at end (keep 0 static-first)"
    )
    startTranslateY: float = Field(
        ..., description="Vertical pan px at start (keep 0 static-first)"
    )
    endTranslateY: float = Field(
        ..., description="Vertical pan px at end (keep 0 static-first)"
    )


class CaptionWord(BaseModel):
    """One forced-alignment word — mirrors ``CaptionWord`` in ``manifest.ts``."""

    word: str = Field(..., description="Verbatim token from the script")
    start_s: float = Field(..., ge=0, description="Word start time in seconds")
    end_s: float = Field(..., ge=0, description="Word end time in seconds")
    sentence_index: int = Field(
        ..., ge=0, description="Zero-based sentence this word belongs to"
    )
    is_highlight: bool = Field(
        ..., description="True for the single #FACC15 keyword in the sentence"
    )


class CaptionTrack(BaseModel):
    """Per-digest caption track — mirrors ``CaptionTrack`` in ``manifest.ts``.

    Embedded verbatim from sub-phase 2's ``digest-{n}.captions.json`` (unchanged
    by the single-poster pivot).
    """

    digest_id: str = Field(..., description="Digest identifier, e.g. 'digest-1'")
    audio_duration_s: float = Field(
        ..., gt=0, description="Total audio duration in seconds"
    )
    speech_end_s: float = Field(..., gt=0, description="Timestamp where speech ends")
    sentence_count: int = Field(
        ..., gt=0, description="Number of sentences in the script"
    )
    words: list[CaptionWord] = Field(
        ..., min_length=1, description="Flat, monotonic, contiguous words"
    )


class DigestManifest(BaseModel):
    """Single-poster render manifest — mirrors the ``DigestManifest`` type in ``manifest.ts``.

    Passed to the Remotion ``Digest`` composition via ``--props``. ``build_render_manifest``
    sets ``durationInFrames = round(audio_duration_s * fps)`` and omits ``kenBurns``
    (so SP3 applies its static-first default). Serialize with ``exclude_none=True``
    so the optional ``kenBurns`` key is absent from the emitted JSON when unset.
    """

    digest_id: str = Field(..., description="Digest identifier, e.g. 'digest-1'")
    audioSrc: str = Field(
        ..., description="Audio source (.mp3) — staticFile()-relative or absolute path"
    )
    posterSrc: str = Field(
        ..., description="Single 9:16 poster — staticFile()-relative or absolute path"
    )
    headlineText: str = Field(
        ...,
        min_length=1,
        description="Headline rendered on the brief intro overlay card",
    )
    durationInFrames: int = Field(
        ..., gt=0, description="Timeline length = round(audio_duration_s * fps)"
    )
    fps: Literal[30] = Field(
        default=FPS, description="Frames per second (locked at 30)"
    )
    width: Literal[1080] = Field(
        default=WIDTH, description="Composition width in px (locked at 1080)"
    )
    height: Literal[1920] = Field(
        default=HEIGHT, description="Composition height in px (locked at 1920)"
    )
    kenBurns: KenBurns | None = Field(
        default=None,
        description="Optional Ken Burns drift; omit (None) to use SP3's gentle static-first default",
    )
    captionTrack: CaptionTrack = Field(
        ..., description="Word-by-word caption track (SP2 shape — unchanged)"
    )
