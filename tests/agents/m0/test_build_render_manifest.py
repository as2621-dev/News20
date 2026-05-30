"""Unit tests for the Python -> Remotion render-manifest builder (sub-phase 4).

Single-poster format. These tests are OFFLINE and self-contained: they use a
TEMP fixture dir with a single dummy poster, a dummy audio file, and a
hand-written sample caption JSON. ffprobe is monkeypatched so the tests never
depend on the real rendered mp3s and never shell out. Per Rule 9 they encode WHY
the manifest invariants matter and must fail if the business logic regresses:

  (a) the emitted manifest validates against the ``DigestManifest`` contract
      shape mirrored from ``remotion/src/manifest.ts`` — a single resolvable
      ``posterSrc``, a caption track that embeds the SP2 words verbatim, a
      non-empty headlineText, and locked fps/width/height (a drift here means the
      Remotion ``Digest`` composition silently fails to receive valid props);
  (b) ``durationInFrames == round(audio_duration_s * fps)`` — SP3's
      ``calculateMetadata`` derives the composition length from this value, so an
      off-by-N would clip the narration audio or leave a black-silent tail and
      desync the captions;
  (c) the builder RAISES ``MissingPosterError`` when the poster is absent —
      sub-phase 4 must refuse to render a digest with no poster rather than emit
      a manifest pointing at a non-existent image;
  (d) ``kenBurns`` is omitted from the emitted JSON (so SP3 applies its
      static-first default), never serialized as ``null``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.m0 import build_render_manifest as brm
from agents.m0.build_render_manifest import (
    FPS,
    HEIGHT,
    WIDTH,
    DigestManifest,
    MissingPosterError,
    build_render_manifest,
)
from agents.m0.digests_input import get_digest_by_id

# A fixed fake duration used across tests (keeps the frame math deterministic).
_FAKE_AUDIO_DURATION_S = 50.611
_EXPECTED_DURATION_IN_FRAMES = round(_FAKE_AUDIO_DURATION_S * FPS)  # 1518

# A tiny hand-written caption track (two sentences, one highlight each) that
# matches the SP2 / manifest.ts CaptionTrack shape verbatim.
_SAMPLE_CAPTION_TRACK = {
    "digest_id": "digest-1",
    "audio_duration_s": _FAKE_AUDIO_DURATION_S,
    "speech_end_s": _FAKE_AUDIO_DURATION_S,
    "sentence_count": 2,
    "words": [
        {
            "word": "Breaking",
            "start_s": 0.0,
            "end_s": 0.5,
            "sentence_index": 0,
            "is_highlight": True,
        },
        {
            "word": "news",
            "start_s": 0.5,
            "end_s": 1.0,
            "sentence_index": 0,
            "is_highlight": False,
        },
        {
            "word": "today.",
            "start_s": 1.0,
            "end_s": 1.5,
            "sentence_index": 0,
            "is_highlight": False,
        },
        {
            "word": "Stay",
            "start_s": 1.5,
            "end_s": 2.0,
            "sentence_index": 1,
            "is_highlight": False,
        },
        {
            "word": "tuned.",
            "start_s": 2.0,
            "end_s": 2.5,
            "sentence_index": 1,
            "is_highlight": True,
        },
    ],
}


def _lay_down_audio_and_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Create the audio + captions fixture and repoint the builder constants.

    Returns the digest stills dir so individual tests can place or omit the
    poster. ffprobe is stubbed to the fixed fake duration so no real mp3 and no
    ffprobe subprocess is needed.
    """
    audio_dir = tmp_path / "audio"
    captions_dir = tmp_path / "captions"
    stills_root = tmp_path / "stills"
    digest_dir = stills_root / "digest-1"
    audio_dir.mkdir()
    captions_dir.mkdir()
    digest_dir.mkdir(parents=True)

    (audio_dir / "digest-1.mp3").write_bytes(b"\x00fake-mp3\x00")
    (captions_dir / "digest-1.captions.json").write_text(
        json.dumps(_SAMPLE_CAPTION_TRACK), encoding="utf-8"
    )

    monkeypatch.setattr(brm, "INPUT_AUDIO_DIR", audio_dir)
    monkeypatch.setattr(brm, "INPUT_CAPTIONS_DIR", captions_dir)
    monkeypatch.setattr(brm, "STILLS_ROOT", stills_root)
    monkeypatch.setattr(
        brm, "probe_audio_duration_s", lambda _path: _FAKE_AUDIO_DURATION_S
    )

    return digest_dir


@pytest.fixture
def m0_fixture_with_poster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Full happy-path fixture: audio + captions + a single dummy poster.png.

    Returns the digest stills dir so a test can delete the poster to exercise the
    missing-poster path.
    """
    digest_dir = _lay_down_audio_and_captions(tmp_path, monkeypatch)
    (digest_dir / "poster.png").write_bytes(b"\x89PNG\x00fake")
    return digest_dir


def test_build_render_manifest_validates_against_contract(
    m0_fixture_with_poster: Path,
) -> None:
    """Happy path: emitted manifest matches the single-poster DigestManifest shape.

    WHY: SP4's whole job is to hand the Remotion composition valid props. If any
    contract field drifts (poster, caption embedding, dimensions, duration), the
    render receives malformed input and the composition fails silently.
    """
    digest = get_digest_by_id("digest-1")

    manifest = build_render_manifest(digest)

    assert isinstance(manifest, DigestManifest)
    # Locked composition geometry.
    assert manifest.fps == FPS == 30
    assert manifest.width == WIDTH == 1080
    assert manifest.height == HEIGHT == 1920
    # headlineText comes from digests_input and must be non-empty.
    assert manifest.headlineText == digest.digest_headline
    assert manifest.headlineText.strip() != ""
    # audioSrc + posterSrc resolve to real files at build time (source paths).
    assert Path(manifest.audioSrc).exists()
    assert Path(manifest.posterSrc).exists()
    assert manifest.posterSrc.endswith("poster.png")
    # captionTrack is embedded verbatim — same words, order, highlight flags.
    assert manifest.captionTrack.digest_id == "digest-1"
    assert [word.word for word in manifest.captionTrack.words] == [
        word["word"] for word in _SAMPLE_CAPTION_TRACK["words"]
    ]
    assert (
        manifest.captionTrack.sentence_count == _SAMPLE_CAPTION_TRACK["sentence_count"]
    )
    assert [w.is_highlight for w in manifest.captionTrack.words] == [
        w["is_highlight"] for w in _SAMPLE_CAPTION_TRACK["words"]
    ]


def test_duration_in_frames_matches_audio(m0_fixture_with_poster: Path) -> None:
    """``durationInFrames`` equals ``round(audio_duration_s * fps)``.

    WHY: SP3's calculateMetadata sets the composition length to this value. If it
    != round(audio_duration_s * fps), the audio is clipped or a black-silent tail
    is rendered and the captions desync past that point.
    """
    manifest = build_render_manifest(get_digest_by_id("digest-1"))

    assert manifest.durationInFrames == _EXPECTED_DURATION_IN_FRAMES
    assert manifest.durationInFrames > 0


def test_kenburns_omitted_from_emitted_json(
    m0_fixture_with_poster: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The optional ``kenBurns`` key is absent from the written JSON (not null).

    WHY: SP3 applies its static-first default drift only when ``kenBurns`` is
    ABSENT. Emitting ``kenBurns: null`` (or a value) would override that default
    and risk motion under the caption band — the explicit failure mode. Also
    asserts the old 8-cut ``cuts`` key is gone.
    """
    manifest = build_render_manifest(get_digest_by_id("digest-1"))

    # In-memory: kenBurns defaults to None (omitted intent).
    assert manifest.kenBurns is None

    # On-disk JSON (the actual props handed to Remotion): the key is absent.
    manifests_dir = tmp_path / "manifests"
    monkeypatch.setattr(brm, "OUTPUT_MANIFESTS_DIR", manifests_dir)
    output_path = brm.write_render_manifest(manifest)
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert "kenBurns" not in written
    assert "cuts" not in written  # 8-cut format is gone
    assert written["posterSrc"].endswith("poster.png")
    assert written["durationInFrames"] == _EXPECTED_DURATION_IN_FRAMES


def test_build_raises_when_poster_missing(m0_fixture_with_poster: Path) -> None:
    """Failure case: a digest with no poster raises (no render).

    WHY: SP4 must refuse to emit a manifest that points at a non-existent image
    — rendering it would crash deep inside Remotion. The error must name the
    digest + carry an actionable fix so the user knows exactly what to supply.
    """
    # Remove the only poster to simulate the current pre-Nano-Banana state.
    (m0_fixture_with_poster / "poster.png").unlink()

    with pytest.raises(MissingPosterError) as exc_info:
        build_render_manifest(get_digest_by_id("digest-1"))

    error = exc_info.value
    assert error.digest_id == "digest-1"
    # The error must carry an actionable fix_suggestion (CLAUDE.md mandate).
    assert error.fix_suggestion
    assert "poster" in str(error)


def test_build_accepts_jpg_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge case: a ``poster.jpg`` is accepted as a fallback when no .png exists.

    WHY: posters come from an image tool; the builder should not hard-require
    .png. The resolved posterSrc must reflect the actual extension so staging
    copies the right file.
    """
    digest_dir = _lay_down_audio_and_captions(tmp_path, monkeypatch)
    (digest_dir / "poster.jpg").write_bytes(b"\xff\xd8\xff-fake-jpg")

    manifest = build_render_manifest(get_digest_by_id("digest-1"))

    assert manifest.posterSrc.endswith("poster.jpg")
    assert Path(manifest.posterSrc).exists()
