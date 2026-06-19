"""Orchestrate the SERP-seeded poster pipeline for ONE digest.

search (Serper) -> gate -> download top N -> score (6 criteria) -> select ->
recast prompt (Gemini Flash) -> image-conditioned generate (Nano Banana Pro) ->
grade -> write ``assets/m0/<digest>/poster.webp`` + ``selection-report.json``.

Each story is independent; a failure is recorded in the report and surfaced
(Rule 12), it does not raise unless image generation itself errors.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from google import genai

from agents.m0.digests_input import Digest
from agents.m0.download_candidates import (
    DownloadedCandidate,
    _fetch,
    download_candidate,
)
from agents.m0.entity_reference_images import get_or_fetch_entity_reference_image
from agents.m0.generate_posters import _extract_image_bytes, generate_from_reference
from agents.m0.grade_and_brand import grade_and_brand
from agents.m0.image_scorer import score_candidates, select_winner
from agents.m0.poster_models import (
    CANDIDATE_LIMIT,
    DEFAULT_ACCENT_HEX,
    SEGMENT_ACCENT_BY_DIGEST,
    ScoredCandidate,
    SelectionReport,
    StoryConcept,
)
from agents.m0.reference_prompt_synthesizer import synthesize_prompt
from agents.m0.serper_image_search import search_images, youtube_thumbnail_candidate
from agents.m0.story_concept import extract_story_concept
from agents.shared.logger import get_logger

logger = get_logger("m0.build_poster_from_news")

ASSETS_M0_DIR: Path = Path(__file__).resolve().parents[2] / "assets" / "m0"


def _summary_from_digest(digest: Digest) -> str:
    """Join the dialogue turns into a compact plain-text summary."""
    return " ".join(turn.text for turn in digest.turns)


def _run_coroutine_blocking(coroutine: Any) -> Any:
    """Run an async coroutine to completion from a synchronous caller.

    The poster pipeline is synchronous but the canonical-photo lookup
    (:func:`get_or_fetch_entity_reference_image`) is ``async``. ``asyncio.run``
    cannot be called when a loop is already running (the orchestrator drives the
    sync builder from inside its own event loop), so when a running loop is
    detected the coroutine is executed in a throwaway thread with its own loop.
    With no running loop (e.g. ``fill_batch_posters`` worker threads, tests) it
    runs directly via ``asyncio.run``.

    Args:
        coroutine: The awaitable to drive to completion.

    Returns:
        Whatever the coroutine resolves to.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Reason: no loop running in this thread → the simple path is safe.
        return asyncio.run(coroutine)
    # Reason: a loop is already running here; offload to a fresh thread+loop so we
    # never raise "asyncio.run() cannot be called from a running event loop".
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coroutine)).result()


def resolve_canonical_reference_seed(
    concept: StoryConcept,
    supabase_client: Any | None,
    genai_client: genai.Client,
) -> tuple[bytes, str] | None:
    """Return verified canonical reference bytes for a resolved person, else None.

    Phase 0c Sub-phase 4 (L5 wire + L3 redirect entry point). When the resolved
    primary subject is a **person** and we hold a VERIFIED canonical photo, those
    bytes — NOT the best-effort SERP winner — should condition the image model so
    the poster depicts the correct CURRENT person (never a stale-prior substitute).

    This is the single selection seam shared by both the synchronous
    (:func:`build_poster_for_digest`) and batch
    (:func:`agents.m0.batch_posters.prepare_poster_generation`) paths so the two
    never diverge. It returns ``None`` — and the caller runs the UNCHANGED SERP
    seed path — whenever any of these hold:

      * no ``supabase_client`` was injected (today's default production wiring, so
        behaviour is byte-for-byte unchanged until the store is threaded through);
      * the concept's ``entity_kind`` is not ``"person"`` or its ``entity_key`` is
        empty (no person to ground);
      * :func:`get_or_fetch_entity_reference_image` returns ``None`` (no verified
        photo exists — we only stop guessing when we hold a trusted face);
      * the verified photo's public URL could not be fetched.

    No symbolic / faceless fallback is ever introduced (explicitly rejected by the
    user) — the only fallback is the existing SERP path.

    Args:
        concept: The extracted story concept (carries ``entity_kind`` /
            ``entity_key`` / ``entity_name`` / ``entity_as_of`` from SP2).
        supabase_client: Service-role Supabase client for the reference store, or
            ``None`` to skip the canonical lookup entirely (SERP path unchanged).
        genai_client: Gemini client for the SP3 identity-verification call.

    Returns:
        ``(reference_image_bytes, reference_mime_type)`` of the verified canonical
        photo, or ``None`` to fall back to the unchanged SERP seed path.
    """
    if supabase_client is None:
        return None
    if concept.entity_kind != "person" or not concept.entity_key:
        return None

    reference = _run_coroutine_blocking(
        get_or_fetch_entity_reference_image(
            concept.entity_key,
            concept.entity_name,
            concept.entity_kind,
            concept.entity_as_of,
            supabase_client,
            genai_client,
        )
    )
    if reference is None:
        logger.info(
            "canonical_reference_absent_serp_fallback",
            entity_key=concept.entity_key,
            entity_name=concept.entity_name,
            fix_suggestion="No verified canonical photo for this person; using the unchanged SERP seed.",
        )
        return None

    reference_bytes = _fetch(reference.reference_public_url)
    if not reference_bytes:
        logger.warning(
            "canonical_reference_fetch_failed",
            entity_key=concept.entity_key,
            reference_public_url=reference.reference_public_url,
            fix_suggestion="The verified reference URL did not download; falling back to the SERP seed.",
        )
        return None

    logger.info(
        "canonical_reference_used",
        entity_key=concept.entity_key,
        entity_name=concept.entity_name,
        reference_public_url=reference.reference_public_url,
        verification_confidence=round(reference.verification_confidence, 3),
    )
    # Reason: SP3 always uploads JPEG bytes (entity_reference_images._REFERENCE_CONTENT_TYPE).
    return reference_bytes, "image/jpeg"


def build_poster_for_digest(
    digest: Digest,
    client: genai.Client,
    *,
    supplied_poster_image_url: str | None = None,
    supabase_client: Any | None = None,
) -> SelectionReport:
    """Run the full seed→generate→grade pipeline for one digest.

    **Source-origin skip (Phase 5d SP3):** when ``supplied_poster_image_url`` is
    given (a followed YouTube channel's video thumbnail, or a rendered X tweet
    screenshot — ``CandidateStory.candidate_social_image_url`` carried through the
    orchestrator), the SERP→score→Nano-Banana generation is SKIPPED entirely: the
    supplied image is downloaded and put through the SAME deterministic house grade
    (so it matches the reel format), then written as the poster. The real
    thumbnail/tweet is more trustworthy + recognisable than a synthetic poster
    (plans/phase-5d-source-ingestion.md "Locked decisions"). The Gemini ``client``
    is then unused (no generation, no SERP). On any download/grade failure the
    report records it (Rule 12 — no silent skip) and returns posterless rather than
    falling through to a (wrong) generated poster for a source item.

    Args:
        digest: The story (headline + dialogue turns).
        client: Initialized google-genai client (unused on the supplied-image path).
        supplied_poster_image_url: Optional source-origin image URL/path. When set,
            generation is skipped and this image becomes the poster.
        supabase_client: Optional service-role Supabase client for the canonical
            entity-reference-image store (phase 0c SP4). When provided AND the
            resolved primary subject is a person with a VERIFIED canonical photo,
            that photo conditions generation instead of the SERP winner. ``None``
            (the default) keeps the unchanged SERP seed path — no regression.

    Returns:
        A SelectionReport (also written to disk as selection-report.json).
    """
    headline = digest.digest_headline
    summary = _summary_from_digest(digest)
    accent_hex = SEGMENT_ACCENT_BY_DIGEST.get(digest.digest_id, DEFAULT_ACCENT_HEX)
    output_dir = ASSETS_M0_DIR / digest.digest_id
    refs_dir = output_dir / "refs"

    report = SelectionReport(
        digest_id=digest.digest_id,
        headline=headline,
        accent_hex=accent_hex,
        refined_query="",
        candidate_count=0,
    )

    # (S) Source-origin short-circuit: a followed-source story supplies its own
    # image (thumbnail / tweet screenshot) — grade it directly, skip generation.
    if supplied_poster_image_url:
        return _build_poster_from_supplied_image(
            report=report,
            supplied_poster_image_url=supplied_poster_image_url,
            accent_hex=accent_hex,
            output_dir=output_dir,
        )

    # (0) concept-first (poster-pipeline §4): drives query, scoring, and synthesis.
    # Reason: the joined narration IS the full story body; Digest carries no
    # separate date field, so story_date stays None (resolution relies on text).
    concept = extract_story_concept(
        headline, summary, client, story_body=summary, story_date=None
    )
    report.story_concept = concept.model_dump()

    # (1) search on the concept query  (2) gate is inside search_images
    report.refined_query = concept.image_search_query
    candidates = search_images(concept.image_search_query, digest.digest_id)[
        :CANDIDATE_LIMIT
    ]
    # (1b) YouTube-sourced story: the video's own thumbnail is the most
    # on-subject seed — PREPEND it ahead of the SERP results (scoring still
    # decides the winner; the size gate still applies after download).
    youtube_candidate = youtube_thumbnail_candidate(
        digest.digest_source_url or "", digest.digest_id
    )
    if youtube_candidate is not None:
        candidates = [youtube_candidate, *candidates]
    report.candidate_count = len(candidates)
    if not candidates:
        report.notes = "no candidates returned from SERP after gating"
        logger.error(
            "poster_pipeline_no_candidates",
            digest_id=digest.digest_id,
            fix_suggestion="Broaden the query or relax the size gate.",
        )
        _write_report(report, output_dir)
        return report

    # (4) download
    downloaded: list[DownloadedCandidate] = [
        d for c in candidates if (d := download_candidate(c, refs_dir)) is not None
    ]
    if not downloaded:
        report.notes = "all candidate downloads failed"
        logger.error(
            "poster_pipeline_no_downloads",
            digest_id=digest.digest_id,
            fix_suggestion="Candidate hosts blocked fetch; try other results or thumbnails.",
        )
        _write_report(report, output_dir)
        return report

    # (5) score (concept-aware)  (6) select
    scored: list[ScoredCandidate] = score_candidates(downloaded, concept, client)
    report.scored = scored
    winner = select_winner(scored)
    if winner is None:
        report.notes = "scoring produced no winner"
        _write_report(report, output_dir)
        return report
    report.winner_candidate_id = winner.candidate.candidate_id
    winner_downloaded = next(
        d
        for d in downloaded
        if d.candidate.candidate_id == winner.candidate.candidate_id
    )

    # (7) recast prompt  (8) image-conditioned generate  (9) grade
    synthesized_prompt = synthesize_prompt(
        winner_downloaded, concept, accent_hex, client
    )
    report.synthesized_prompt = synthesized_prompt

    # (8a) Identity grounding (phase 0c SP4): when the resolved subject is a person
    # with a VERIFIED canonical photo, condition on THAT photo; otherwise the SERP
    # winner's bytes are used unchanged (no regression).
    canonical_seed = resolve_canonical_reference_seed(concept, supabase_client, client)
    if canonical_seed is not None:
        reference_image_bytes, reference_mime_type = canonical_seed
    else:
        reference_image_bytes = winner_downloaded.image_bytes
        reference_mime_type = winner_downloaded.mime_type

    response = generate_from_reference(
        client,
        synthesized_prompt,
        reference_image_bytes,
        reference_mime_type,
    )
    raw_bytes, _mime = _extract_image_bytes(response)
    if not raw_bytes:
        report.notes = "Nano Banana Pro returned no image part (safety filter or empty)"
        logger.error(
            "poster_pipeline_generation_empty",
            digest_id=digest.digest_id,
            fix_suggestion="Rephrase the synthesized prompt to be less literal/sensitive.",
        )
        _write_report(report, output_dir)
        return report

    graded_webp = grade_and_brand(raw_bytes, accent_hex)
    output_dir.mkdir(parents=True, exist_ok=True)
    poster_path = output_dir / "poster.webp"
    poster_path.write_bytes(graded_webp)
    report.poster_path = str(poster_path)

    logger.info(
        "poster_pipeline_completed",
        digest_id=digest.digest_id,
        winner_candidate_id=report.winner_candidate_id,
        poster_path=str(poster_path),
    )
    _write_report(report, output_dir)
    return report


def _read_supplied_image_bytes(supplied_poster_image_url: str) -> bytes | None:
    """Read a supplied source image's bytes from a local path or a URL.

    The X adapter saves a rendered tweet screenshot to the local assets dir (a
    filesystem path), while the YouTube adapter supplies a remote thumbnail URL —
    so accept both: a path that exists on disk is read directly; otherwise the
    value is fetched over HTTP. Returns None (logged) on any failure.

    Args:
        supplied_poster_image_url: A local file path or an http(s) image URL.

    Returns:
        The image bytes, or None when the path is missing / the fetch failed.
    """
    candidate_path = Path(supplied_poster_image_url)
    try:
        if candidate_path.is_file():
            return candidate_path.read_bytes()
    except OSError:
        # Reason: a URL string can raise on is_file() on some platforms; fall
        # through to the HTTP fetch rather than crash the poster step.
        pass
    return _fetch(supplied_poster_image_url)


def _build_poster_from_supplied_image(
    report: SelectionReport,
    supplied_poster_image_url: str,
    accent_hex: str,
    output_dir: Path,
) -> SelectionReport:
    """Grade a supplied source image into the poster, skipping generation.

    Downloads/reads the supplied source-origin image (YouTube thumbnail or X tweet
    screenshot), runs it through the SAME deterministic house grade as a generated
    poster (cover-fit 1080x1920 + brand pass), and writes it as ``poster.webp``.
    A failure is recorded on the report and returned posterless (Rule 12 — no
    silent fall-through to a generated poster for a source item).

    Args:
        report: The in-progress selection report to annotate + return.
        supplied_poster_image_url: The source image path/URL.
        accent_hex: The brand accent for the grade pass.
        output_dir: ``assets/m0/<digest_id>/`` output dir.

    Returns:
        The selection report with ``poster_path`` set on success, else with
        ``notes`` explaining the failure.
    """
    report.refined_query = "source_origin_supplied_image"
    report.notes = (
        f"source-origin poster from supplied image: {supplied_poster_image_url}"
    )

    raw_bytes = _read_supplied_image_bytes(supplied_poster_image_url)
    if not raw_bytes:
        report.notes = f"source-origin supplied image could not be read: {supplied_poster_image_url}"
        logger.error(
            "poster_source_image_unavailable",
            digest_id=report.digest_id,
            supplied_poster_image_url=supplied_poster_image_url,
            fix_suggestion="Verify the thumbnail URL / screenshot path is reachable; "
            "the digest will publish without a poster.",
        )
        _write_report(report, output_dir)
        return report

    try:
        graded_webp = grade_and_brand(raw_bytes, accent_hex)
    except Exception as exc:  # noqa: BLE001 — grading must not crash the run.
        report.notes = f"source-origin image grade failed: {type(exc).__name__}"
        logger.error(
            "poster_source_image_grade_failed",
            digest_id=report.digest_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            fix_suggestion="The supplied image bytes did not decode/grade; "
            "publishing without a poster.",
        )
        _write_report(report, output_dir)
        return report

    output_dir.mkdir(parents=True, exist_ok=True)
    poster_path = output_dir / "poster.webp"
    poster_path.write_bytes(graded_webp)
    report.poster_path = str(poster_path)
    logger.info(
        "poster_source_image_used",
        digest_id=report.digest_id,
        supplied_poster_image_url=supplied_poster_image_url,
        poster_path=str(poster_path),
        generation_skipped=True,
    )
    _write_report(report, output_dir)
    return report


def _write_report(report: SelectionReport, output_dir: Path) -> None:
    """Persist the selection report as JSON for auditability."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "selection-report.json").write_text(
        json.dumps(report.model_dump(), indent=2), encoding="utf-8"
    )
