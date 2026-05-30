"""M0 builder: assemble one single-poster Remotion ``DigestManifest`` per digest.

Run as a module (writes all 5 manifests):

    python -m agents.m0.build_render_manifest

Single-poster format (supersedes the earlier 8-cut Ken Burns format): ONE
poster-grade 9:16 image is the full-timeline background. For each digest in
``agents.m0.digests_input.DIGESTS`` it:
  1. ffprobes the real rendered audio at
     ``agents/m0/output/audio/digest-{1..5}.mp3`` for its true duration;
  2. sets ``durationInFrames = round(audio_duration_s * FPS)`` (FPS=30) — the
     poster holds for the full audio; SP3's ``calculateMetadata`` derives the
     composition length from this exact value (see ``remotion/src/Root.tsx``);
  3. resolves the single poster at ``assets/m0/digest-<n>/poster.png`` — RAISES
     ``MissingPosterError`` if it is absent;
  4. omits ``kenBurns`` so SP3 applies its static-first gentle default drift;
  5. embeds the sub-phase-2 caption JSON verbatim (read from
     ``agents/m0/output/captions/digest-<n>.captions.json``);
  6. takes ``headlineText`` from ``digests_input`` (the digest headline).

The emitted Pydantic model mirrors the TypeScript ``DigestManifest`` contract in
``remotion/src/manifest.ts`` field-for-field. The ``audioSrc`` / ``posterSrc``
written here are *source filesystem paths* used for staging; ``render_all.py``
rewrites them to the ``staticFile()``-relative paths Remotion resolves from
``remotion/public/`` before invoking the render.

This builder reads ONLY existing audio + caption files — it never re-renders
audio and never calls an external API.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agents.m0.digests_input import DIGESTS, Digest
from agents.m0.manifest_models import (
    FPS,
    HEIGHT,
    WIDTH,
    CaptionTrack,
    DigestManifest,
)
from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger

logger = get_logger("m0.build_render_manifest")

# Reason: resolve paths relative to this module so the builder runs from any cwd.
_MODULE_DIR: Path = Path(__file__).parent
_REPO_ROOT: Path = _MODULE_DIR.parent.parent
INPUT_AUDIO_DIR: Path = _MODULE_DIR / "output" / "audio"
INPUT_CAPTIONS_DIR: Path = _MODULE_DIR / "output" / "captions"
OUTPUT_MANIFESTS_DIR: Path = _MODULE_DIR / "output" / "manifests"
# Reason: the user-supplied posters live under <repo>/assets/m0/digest-<n>/.
STILLS_ROOT: Path = _REPO_ROOT / "assets" / "m0"

AUDIO_FORMAT = "mp3"
# Reason: one poster per digest; .png is the format the Nano Banana Pro run emits.
# .jpg/.jpeg are accepted as fallbacks (ordered: png wins when several exist).
POSTER_BASENAME = "poster"
POSTER_EXTENSIONS: tuple[str, ...] = ("png", "jpg", "jpeg")


class MissingPosterError(PipelineStageError):
    """Raised when a digest is missing its single poster image.

    Attributes:
        digest_id: The digest whose poster is absent.
        searched_dir: The directory searched for ``poster.<ext>``.

    Example:
        >>> raise MissingPosterError(
        ...     digest_id="digest-1",
        ...     searched_dir="/repo/assets/m0/digest-1",
        ... )
    """

    def __init__(self, digest_id: str, searched_dir: str) -> None:
        self.digest_id = digest_id
        self.searched_dir = searched_dir
        expected = f"{POSTER_BASENAME}.{{{'|'.join(POSTER_EXTENSIONS)}}}"
        super().__init__(
            stage="build_render_manifest",
            message=(
                f"{digest_id} is missing its poster under {searched_dir}; expected {expected}"
            ),
            fix_suggestion=(
                f"Drop the poster into {searched_dir} as {POSTER_BASENAME}.png "
                "(produced by the billing-gated Nano Banana Pro run), then re-run."
            ),
        )


def probe_audio_duration_s(audio_path: Path) -> float:
    """Return the true duration (seconds) of an audio file via ffprobe.

    Args:
        audio_path: Path to the audio file to probe.

    Returns:
        Duration in seconds (float).

    Raises:
        FileNotFoundError: If the audio file does not exist.
        RuntimeError: If ffprobe fails or returns an unparseable duration.

    Example:
        >>> probe_audio_duration_s(Path("agents/m0/output/audio/digest-1.mp3"))
        50.610975
    """
    if not audio_path.exists():
        logger.error(
            "audio_file_missing",
            audio_path=str(audio_path),
            fix_suggestion="Run `python -m agents.m0.render_audio` first to produce the mp3s",
        )
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        logger.error(
            "ffprobe_failed",
            audio_path=str(audio_path),
            returncode=completed.returncode,
            stderr=completed.stderr.strip(),
            fix_suggestion="Confirm ffprobe (ffmpeg) is installed and the file is valid audio",
        )
        raise RuntimeError(
            f"ffprobe failed for {audio_path}: {completed.stderr.strip()}"
        )

    raw_duration = completed.stdout.strip()
    try:
        return float(raw_duration)
    except ValueError as exc:
        logger.error(
            "ffprobe_duration_unparseable",
            audio_path=str(audio_path),
            raw_value=raw_duration,
            fix_suggestion="ffprobe returned a non-numeric duration; re-render the audio file",
        )
        raise RuntimeError(
            f"Unparseable ffprobe duration {raw_duration!r} for {audio_path}"
        ) from exc


def resolve_poster_path(digest_id: str) -> Path:
    """Resolve the single poster file path for a digest.

    Looks under ``STILLS_ROOT/<digest_id>/poster.<ext>``, trying each extension
    in ``POSTER_EXTENSIONS`` in order.

    Args:
        digest_id: e.g. "digest-1".

    Returns:
        The absolute poster :class:`Path`.

    Raises:
        MissingPosterError: If no ``poster.<ext>`` exists for the digest.

    Example:
        >>> resolve_poster_path("digest-1")  # doctest: +SKIP
        PosixPath('.../assets/m0/digest-1/poster.png')
    """
    digest_dir = STILLS_ROOT / digest_id
    for extension in POSTER_EXTENSIONS:
        candidate = digest_dir / f"{POSTER_BASENAME}.{extension}"
        if candidate.exists():
            return candidate

    logger.error(
        "poster_missing",
        digest_id=digest_id,
        searched_dir=str(digest_dir),
        fix_suggestion=(
            "Drop the poster.png into the digest folder (produced by the "
            "billing-gated Nano Banana Pro run), then re-run."
        ),
    )
    raise MissingPosterError(digest_id=digest_id, searched_dir=str(digest_dir))


def load_caption_track(digest_id: str) -> CaptionTrack:
    """Load and validate the sub-phase-2 caption JSON for a digest.

    Args:
        digest_id: e.g. "digest-1".

    Returns:
        The :class:`CaptionTrack` parsed verbatim from the SP2 JSON.

    Raises:
        FileNotFoundError: If the caption JSON does not exist.

    Example:
        >>> load_caption_track("digest-1").digest_id  # doctest: +SKIP
        'digest-1'
    """
    caption_path = INPUT_CAPTIONS_DIR / f"{digest_id}.captions.json"
    if not caption_path.exists():
        logger.error(
            "caption_json_missing",
            digest_id=digest_id,
            caption_path=str(caption_path),
            fix_suggestion="Run `python -m agents.m0.align_captions` first to produce the caption JSONs",
        )
        raise FileNotFoundError(f"Caption JSON not found: {caption_path}")

    raw = json.loads(caption_path.read_text(encoding="utf-8"))
    # Reason: re-validate against our mirror of the contract — the JSON must stay
    # in lockstep with manifest.ts; a drift fails loud here, not silently at render.
    return CaptionTrack.model_validate(raw)


def build_render_manifest(digest: Digest) -> DigestManifest:
    """Assemble the full single-poster ``DigestManifest`` for one digest.

    The ``audioSrc`` / ``posterSrc`` are the *source filesystem paths* (used for
    staging); ``render_all.py`` rewrites them to staticFile-relative paths before
    the render. ``kenBurns`` is omitted so SP3 applies its static-first default.

    Args:
        digest: The digest to build a manifest for.

    Returns:
        A validated :class:`DigestManifest`.

    Raises:
        MissingPosterError: If the digest's poster is absent.
        FileNotFoundError: If the audio or caption JSON is missing.

    Example:
        >>> from agents.m0.digests_input import get_digest_by_id
        >>> build_render_manifest(get_digest_by_id("digest-1"))  # doctest: +SKIP
    """
    audio_path = INPUT_AUDIO_DIR / f"{digest.digest_id}.{AUDIO_FORMAT}"
    audio_duration_s = probe_audio_duration_s(audio_path)
    # Reason: the poster holds for the whole audio; SP3 derives the composition
    # length from this exact value via calculateMetadata.
    duration_in_frames = round(audio_duration_s * FPS)

    poster_path = resolve_poster_path(digest.digest_id)
    caption_track = load_caption_track(digest.digest_id)

    manifest = DigestManifest(
        digest_id=digest.digest_id,
        audioSrc=str(audio_path),
        posterSrc=str(poster_path),
        headlineText=digest.digest_headline,
        durationInFrames=duration_in_frames,
        fps=FPS,
        width=WIDTH,
        height=HEIGHT,
        # Reason: omit kenBurns -> SP3's gentle static-first default drift applies.
        captionTrack=caption_track,
    )

    logger.info(
        "render_manifest_built",
        digest_id=digest.digest_id,
        audio_duration_s=round(audio_duration_s, 3),
        duration_in_frames=duration_in_frames,
        poster_src=str(poster_path),
        caption_word_count=len(caption_track.words),
    )
    return manifest


def write_render_manifest(manifest: DigestManifest) -> Path:
    """Write a manifest to its JSON file and return the path.

    Args:
        manifest: The manifest to serialize.

    Returns:
        The path the JSON was written to.
    """
    OUTPUT_MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_MANIFESTS_DIR / f"{manifest.digest_id}.manifest.json"
    # Reason: exclude_none so the optional kenBurns key is absent from the JSON
    # (SP3 then applies its default) rather than emitted as null.
    output_path.write_text(
        json.dumps(manifest.model_dump(exclude_none=True), indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "render_manifest_written",
        digest_id=manifest.digest_id,
        output_path=str(output_path),
    )
    return output_path


def build_all_manifests() -> list[Path]:
    """Build and write render manifests for every digest in ``DIGESTS``.

    Returns:
        One JSON output path per digest, in DIGESTS order.

    Raises:
        MissingPosterError: If any digest is missing its poster (build halts).
    """
    output_paths: list[Path] = []
    for digest in DIGESTS:
        manifest = build_render_manifest(digest)
        output_paths.append(write_render_manifest(manifest))

    logger.info(
        "all_render_manifests_written",
        digest_count=len(output_paths),
        output_paths=[str(path) for path in output_paths],
    )
    return output_paths


def main() -> None:
    """Module entry point: build and write all 5 render manifests."""
    build_all_manifests()


if __name__ == "__main__":
    main()
