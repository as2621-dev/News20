"""Stage: Python -> Remotion render handoff — builds the render manifest.

PATTERN ported from the TLDW donor
(``~/TLDW-Phase2/.../agents/pipeline/stages/tts_handoff.py``). The donor's job
was the TTS-render handoff for an audio episode: render dialogue, ffprobe the
final audio for a duration that matches what ships, assemble a packaging dict,
and hand it off downstream. We keep that *shape* — ffprobe-the-real-audio +
assemble-a-typed-handoff-package — but rewrite the body for News20's M0 seam:
the handoff is the Remotion single-poster ``DigestManifest`` (audio path + single
poster + duration + caption track + headline text), not a Supabase briefing row.

The heavy lifting (ffprobe, duration, poster resolution, caption embedding)
lives in ``agents.m0.build_render_manifest`` (the pure builder). This module is
the thin stage wrapper: it produces the handoff *package* a renderer consumes —
the manifest plus the exact ``npx remotion render`` argv — without invoking the
render itself (that is ``agents.m0.render_all``'s job). This keeps the
manifest-assembly logic reusable and the stage focused on wiring.

Input:  a ``Digest`` (from ``agents.m0.digests_input``)
Output: a ``RenderHandoff`` with the manifest, output path, and render argv

Example:
    >>> from agents.m0.digests_input import get_digest_by_id
    >>> from agents.pipeline.stages.tts_handoff import build_render_handoff
    >>> handoff = build_render_handoff(get_digest_by_id("digest-1"))  # doctest: +SKIP
    >>> handoff.manifest.digest_id
    'digest-1'
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from agents.m0.build_render_manifest import DigestManifest, build_render_manifest
from agents.m0.digests_input import Digest
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.tts_handoff")

_MODULE_DIR: Path = Path(__file__).parent
_REPO_ROOT: Path = _MODULE_DIR.parent.parent.parent
OUTPUT_VIDEO_DIR: Path = _REPO_ROOT / "agents" / "m0" / "output" / "video"
REMOTION_ENTRY = "src/index.ts"
REMOTION_COMPOSITION = "Digest"


class RenderHandoff(BaseModel):
    """The Python -> Remotion handoff package for one digest.

    Mirrors the donor's "return a packaging dict" output, typed for our seam:
    the manifest the composition consumes plus where its MP4 should land and the
    exact render argv a renderer would invoke.

    Attributes:
        digest_id: The digest this handoff is for.
        manifest: The full ``DigestManifest`` (source asset paths).
        output_mp4_path: Where the rendered MP4 should be written.
        render_command: The ``npx remotion render`` argv (run with cwd=remotion/).
    """

    digest_id: str = Field(..., description="Digest identifier, e.g. 'digest-1'")
    manifest: DigestManifest = Field(
        ..., description="The render manifest the Digest composition consumes"
    )
    output_mp4_path: str = Field(
        ..., description="Destination MP4 path for the rendered digest"
    )
    render_command: list[str] = Field(
        ..., description="The npx remotion render argv (cwd=remotion/)"
    )


def build_render_handoff(digest: Digest) -> RenderHandoff:
    """Build the Remotion render handoff package for one digest.

    Assembles the ``DigestManifest`` (ffprobe duration -> single poster ->
    embedded caption track -> headline text) and pairs it with the output MP4
    path and the render argv. Does NOT stage assets or invoke the render —
    ``agents.m0.render_all`` owns those side effects.

    Args:
        digest: The digest to build a handoff for.

    Returns:
        A validated :class:`RenderHandoff`.

    Raises:
        MissingPosterError: If the digest is missing its poster.
        FileNotFoundError: If the audio or caption JSON is missing.

    Example:
        >>> from agents.m0.digests_input import get_digest_by_id
        >>> handoff = build_render_handoff(get_digest_by_id("digest-1"))  # doctest: +SKIP
        >>> handoff.render_command[1]
        'remotion'
    """
    logger.info("render_handoff_started", digest_id=digest.digest_id)

    manifest = build_render_manifest(digest)
    output_mp4_path = OUTPUT_VIDEO_DIR / f"{digest.digest_id}.mp4"
    render_command = [
        "npx",
        "remotion",
        "render",
        REMOTION_ENTRY,
        REMOTION_COMPOSITION,
        f"--props={output_mp4_path}",
        str(output_mp4_path),
    ]

    handoff = RenderHandoff(
        digest_id=digest.digest_id,
        manifest=manifest,
        output_mp4_path=str(output_mp4_path),
        render_command=render_command,
    )

    logger.info(
        "render_handoff_completed",
        digest_id=digest.digest_id,
        duration_in_frames=manifest.durationInFrames,
        output_mp4_path=str(output_mp4_path),
    )
    return handoff
