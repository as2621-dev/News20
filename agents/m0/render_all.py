"""M0 driver: stage assets, write staticFile manifests, render the 5 MP4s.

Run as a module:

    python -m agents.m0.render_all              # render all 5 digests
    python -m agents.m0.render_all --dry-run    # print the per-digest plan only
    python -m agents.m0.render_all --digest digest-1   # one digest

Single-poster format. For each digest it:
  1. builds the ``DigestManifest`` (``agents.m0.build_render_manifest``) — which
     ffprobes the audio, sets ``durationInFrames``, and embeds the SP2 caption JSON;
  2. stages the audio mp3 + the single poster into ``remotion/public/m0/digest-<n>/``
     (Remotion's ``<Img>`` / ``<Audio>`` resolve via ``staticFile()`` from
     ``remotion/public/`` ONLY — see ``remotion/src/Digest.tsx::resolveSrc``);
  3. rewrites ``audioSrc`` / ``posterSrc`` to the staticFile-relative paths
     (``m0/digest-<n>/audio.mp3``, ``m0/digest-<n>/poster.<ext>``) and writes the
     staged manifest to ``remotion/public/m0/digest-<n>/manifest.json``;
  4. invokes ``npx remotion render src/index.ts Digest --props=<manifest>`` →
     ``agents/m0/output/video/digest-<n>.mp4``.

SCOPE NOTE — the 5 real posters are user-supplied (a billing-gated Nano Banana
Pro run produces them). Until they exist at ``assets/m0/digest-<n>/poster.png``
this driver detects the gap, logs a clear BLOCKED message with the exact expected
path, and exits WITHOUT rendering (it never fabricates an MP4). Resume by dropping
the posters and re-running the same command.

This driver reads existing audio + captions — it never re-renders audio.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from agents.m0.build_render_manifest import (
    AUDIO_FORMAT,
    FPS,
    INPUT_AUDIO_DIR,
    STILLS_ROOT,
    DigestManifest,
    MissingPosterError,
    build_render_manifest,
    probe_audio_duration_s,
    resolve_poster_path,
)
from agents.m0.digests_input import DIGESTS, Digest, get_digest_by_id
from agents.shared.logger import get_logger

logger = get_logger("m0.render_all")

_MODULE_DIR: Path = Path(__file__).parent
_REPO_ROOT: Path = _MODULE_DIR.parent.parent
REMOTION_DIR: Path = _REPO_ROOT / "remotion"
# Reason: Remotion resolves staticFile() ONLY from remotion/public/, so all
# render-time assets must be staged under this directory.
REMOTION_PUBLIC_M0_DIR: Path = REMOTION_DIR / "public" / "m0"
OUTPUT_VIDEO_DIR: Path = _MODULE_DIR / "output" / "video"
REMOTION_ENTRY = "src/index.ts"
REMOTION_COMPOSITION = "Digest"


def _staged_relative_audio_src(digest_id: str) -> str:
    """Return the staticFile()-relative audio path for a staged digest.

    Example:
        >>> _staged_relative_audio_src("digest-1")
        'm0/digest-1/audio.mp3'
    """
    return f"m0/{digest_id}/audio.mp3"


def _staged_relative_poster_src(digest_id: str, source_path: Path) -> str:
    """Return the staticFile()-relative poster path for a staged digest.

    Preserves the source extension (png/jpg) so the staged file matches what
    Remotion loads.

    Example:
        >>> _staged_relative_poster_src("digest-1", Path("a/poster.png"))
        'm0/digest-1/poster.png'
    """
    extension = source_path.suffix.lstrip(".").lower()
    return f"m0/{digest_id}/poster.{extension}"


def stage_assets_and_rewrite_manifest(manifest: DigestManifest) -> DigestManifest:
    """Copy a digest's audio + single poster into ``remotion/public/m0/<id>/``.

    Returns a NEW manifest whose ``audioSrc`` / ``posterSrc`` are the staticFile-
    relative paths (the on-disk source paths in the input manifest are used only
    as copy sources).

    Args:
        manifest: The freshly built manifest (with absolute source paths).

    Returns:
        A copy of the manifest with staticFile-relative asset paths.

    Example:
        >>> staged = stage_assets_and_rewrite_manifest(manifest)  # doctest: +SKIP
        >>> staged.posterSrc
        'm0/digest-1/poster.png'
    """
    digest_dir = REMOTION_PUBLIC_M0_DIR / manifest.digest_id
    digest_dir.mkdir(parents=True, exist_ok=True)

    # Stage the audio.
    source_audio = Path(manifest.audioSrc)
    staged_audio = digest_dir / "audio.mp3"
    shutil.copyfile(source_audio, staged_audio)
    relative_audio_src = _staged_relative_audio_src(manifest.digest_id)

    # Stage the single poster, preserving extension.
    source_poster = Path(manifest.posterSrc)
    relative_poster_src = _staged_relative_poster_src(manifest.digest_id, source_poster)
    staged_poster = REMOTION_DIR / "public" / relative_poster_src
    shutil.copyfile(source_poster, staged_poster)

    staged_manifest = manifest.model_copy(
        update={"audioSrc": relative_audio_src, "posterSrc": relative_poster_src}
    )

    logger.info(
        "assets_staged",
        digest_id=manifest.digest_id,
        staged_dir=str(digest_dir),
        audio_src=relative_audio_src,
        poster_src=relative_poster_src,
    )
    return staged_manifest


def write_staged_manifest(manifest: DigestManifest) -> Path:
    """Write the staged (staticFile-relative) manifest next to its assets.

    Args:
        manifest: The staged manifest (relative asset paths).

    Returns:
        Path to ``remotion/public/m0/<id>/manifest.json``.
    """
    digest_dir = REMOTION_PUBLIC_M0_DIR / manifest.digest_id
    digest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = digest_dir / "manifest.json"
    # Reason: exclude_none so the optional kenBurns key stays absent (SP3 default).
    manifest_path.write_text(
        json.dumps(manifest.model_dump(exclude_none=True), indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "staged_manifest_written",
        digest_id=manifest.digest_id,
        manifest_path=str(manifest_path),
    )
    return manifest_path


def build_render_command(manifest_path: Path, output_mp4: Path) -> list[str]:
    """Build the ``npx remotion render`` argv for one digest.

    Args:
        manifest_path: Path to the staged manifest JSON (--props).
        output_mp4: Destination MP4 path.

    Returns:
        The argv list to pass to subprocess (run with cwd=remotion/).

    Example:
        >>> build_render_command(Path("m.json"), Path("out.mp4"))
        ['npx', 'remotion', 'render', 'src/index.ts', 'Digest', '--props=m.json', 'out.mp4']
    """
    return [
        "npx",
        "remotion",
        "render",
        REMOTION_ENTRY,
        REMOTION_COMPOSITION,
        f"--props={manifest_path}",
        str(output_mp4),
    ]


def _print_dry_run_plan(digest: Digest) -> None:
    """Print the per-digest render plan WITHOUT requiring the poster to exist.

    Computes the timeline length from the real audio (ffprobe) so the plan is
    informative even in the current BLOCKED-ON-INPUT state where no poster is
    supplied yet. Separately reports whether the poster is present.

    Args:
        digest: The digest to plan.
    """
    audio_path = INPUT_AUDIO_DIR / f"{digest.digest_id}.{AUDIO_FORMAT}"
    audio_duration_s = probe_audio_duration_s(audio_path)
    duration_in_frames = round(audio_duration_s * FPS)
    output_mp4 = OUTPUT_VIDEO_DIR / f"{digest.digest_id}.mp4"
    planned_command = build_render_command(
        REMOTION_PUBLIC_M0_DIR / digest.digest_id / "manifest.json", output_mp4
    )

    print(
        f"[dry-run] {digest.digest_id}: "
        f"{duration_in_frames} frames ({duration_in_frames / FPS:.2f}s @ {FPS}fps), "
        f"single poster + headline intro + captions"
    )
    print(f"          stage  -> {REMOTION_PUBLIC_M0_DIR / digest.digest_id}/")
    print(f"          render -> {' '.join(planned_command)} (cwd={REMOTION_DIR})")
    logger.info(
        "render_dry_run_plan",
        digest_id=digest.digest_id,
        duration_in_frames=duration_in_frames,
        output_mp4=str(output_mp4),
    )

    # Reason: surface the poster status as part of the plan; resolve_poster_path
    # raises MissingPosterError which render_all catches and reports as blocked.
    poster_path = resolve_poster_path(digest.digest_id)
    print(f"          poster -> present at {poster_path}")


def render_one_digest(digest: Digest, dry_run: bool) -> Path | None:
    """Build, stage, and render a single digest to MP4.

    Args:
        digest: The digest to render.
        dry_run: When True, prints the plan + render command and skips the render.

    Returns:
        The output MP4 path when rendered; ``None`` on dry-run.

    Raises:
        MissingPosterError: If the digest's poster is missing (caller handles).
    """
    if dry_run:
        # Reason: dry-run must NOT touch remotion/public — print the plan
        # (computed from audio alone) then report the poster status.
        _print_dry_run_plan(digest)
        return None

    manifest = build_render_manifest(digest)
    OUTPUT_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    output_mp4 = OUTPUT_VIDEO_DIR / f"{digest.digest_id}.mp4"

    staged_manifest = stage_assets_and_rewrite_manifest(manifest)
    manifest_path = write_staged_manifest(staged_manifest)
    render_command = build_render_command(manifest_path, output_mp4)

    logger.info(
        "render_started",
        digest_id=digest.digest_id,
        duration_in_frames=manifest.durationInFrames,
        output_mp4=str(output_mp4),
    )
    completed = subprocess.run(render_command, cwd=str(REMOTION_DIR), check=False)
    if completed.returncode != 0:
        logger.error(
            "render_failed",
            digest_id=digest.digest_id,
            returncode=completed.returncode,
            fix_suggestion=(
                "Inspect the Remotion render output above; confirm `npx remotion` "
                "is installed (remotion/node_modules) and the staged manifest is valid."
            ),
        )
        raise RuntimeError(
            f"Remotion render failed for {digest.digest_id} (exit {completed.returncode})"
        )

    logger.info(
        "render_completed", digest_id=digest.digest_id, output_mp4=str(output_mp4)
    )
    return output_mp4


def _report_blocked(error: MissingPosterError) -> None:
    """Log + print the BLOCKED-ON-INPUT message for a missing-poster error."""
    expected_path = f"{STILLS_ROOT / error.digest_id}/poster.png"
    logger.error(
        "render_blocked_on_input",
        digest_id=error.digest_id,
        expected_path=expected_path,
        fix_suggestion=error.fix_suggestion,
    )
    print(f"\nBLOCKED-ON-INPUT: {error.digest_id} is missing its poster.")
    print("  Drop this file (from the billing-gated Nano Banana Pro run):")
    print(f"    {expected_path}")
    print("  Then re-run: python -m agents.m0.render_all\n")


def render_all(dry_run: bool, digest_id: str | None = None) -> list[Path]:
    """Render every digest (or a single one) to MP4.

    Poster-missing digests are reported as BLOCKED-ON-INPUT and skipped; the run
    exits non-zero only if NOTHING rendered because of blocked input (so the
    blocked state is distinguishable from success and from a code crash).

    Args:
        dry_run: When True, prints the per-digest plan and renders nothing.
        digest_id: Optional single digest id; defaults to all 5.

    Returns:
        The list of rendered MP4 paths (empty on dry-run or fully-blocked).
    """
    digests = [get_digest_by_id(digest_id)] if digest_id else DIGESTS
    rendered: list[Path] = []
    blocked: list[str] = []

    for digest in digests:
        try:
            result = render_one_digest(digest, dry_run=dry_run)
            if result is not None:
                rendered.append(result)
        except MissingPosterError as error:
            blocked.append(error.digest_id)
            _report_blocked(error)

    logger.info(
        "render_all_summary",
        dry_run=dry_run,
        rendered_count=len(rendered),
        blocked_digests=blocked,
        rendered_paths=[str(path) for path in rendered],
    )

    if blocked and not rendered and not dry_run:
        # Reason: fail loud (Rule 12) — a fully-blocked real render is NOT success.
        print(
            f"\nNothing rendered: {len(blocked)} digest(s) blocked on missing posters. "
            "Supply the posters listed above and re-run."
        )
        sys.exit(2)

    return rendered


def main() -> None:
    """CLI entry point for the M0 render-all driver."""
    parser = argparse.ArgumentParser(
        description="Render the M0 digest MP4s via Remotion."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the per-digest render plan and missing-poster status without rendering.",
    )
    parser.add_argument(
        "--digest",
        dest="digest_id",
        default=None,
        help="Render only this digest id (e.g. 'digest-1'); defaults to all 5.",
    )
    args = parser.parse_args()
    render_all(dry_run=args.dry_run, digest_id=args.digest_id)


if __name__ == "__main__":
    main()
